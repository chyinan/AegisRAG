from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from time import perf_counter
from typing import Any

from packages.common.logging import get_request_logger
from packages.retrieval.dto import RetrievalCandidate, RetrievalFilterSet, RetrievalRequest
from packages.retrieval.ports import CandidateRetriever

_logger = get_request_logger()


class RetrievalCache:
    """LRU cache for retrieval results. Supports in-memory and Redis backends.

    Only identical retrieval semantics and complete authorization filters share
    entries. This is an optimization, never a replacement for upstream ACLs.
    Singleflight is restricted to a wrapper's identical request identity.
    """

    def __init__(
        self,
        *,
        max_size: int = 1024,
        ttl_seconds: float = 300.0,
        redis_url: str | None = None,
    ) -> None:
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._redis_url = redis_url
        self._redis: Any = None  # aioredis.Redis | None; lazy init
        self._cache: dict[str, _CacheEntry] = {}
        self._access_order: list[str] = []
        self._hits: int = 0
        self._misses: int = 0
        self._closed = False

    def _redis_client(self) -> Any:
        """Return a shared aioredis.Redis client backed by a connection pool.

        The pool is created once on first access and reused for all
        subsequent ``get`` / ``set`` calls (W3 fix — avoids creating a
        brand-new TCP connection on every cache operation).
        """
        if self._closed:
            return None
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as aioredis
        except ImportError:
            return None
        if not self._redis_url:
            return None
        self._redis = aioredis.from_url(
            self._redis_url,
            max_connections=10,
            socket_connect_timeout=3.0,
            socket_timeout=3.0,
        )
        return self._redis

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    def cache_key(self, request: RetrievalRequest, filters: RetrievalFilterSet) -> str:
        # Version the namespace so old tenant-only Redis entries are never
        # reused. Request/trace IDs are observational, not retrieval semantics.
        raw = json.dumps(
            _cache_payload(
                [
                    {
                        name: getattr(request, name)
                        for name in type(request).model_fields
                        if name not in {"request_id", "trace_id"}
                    },
                    {name: getattr(filters, name) for name in type(filters).model_fields},
                ]
            ),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        return f"retrieval:v2:{hashlib.sha256(raw.encode()).hexdigest()}"

    async def aclose(self) -> None:
        """Release the shared Redis pool at application shutdown, once."""
        self._closed = True
        self._cache.clear()
        self._access_order.clear()
        client, self._redis = self._redis, None
        if client is not None:
            close = getattr(client, "aclose", None) or getattr(client, "close", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result

    async def get(self, key: str) -> list[RetrievalCandidate] | None:
        if self._closed:
            return None
        if self._redis_url:
            return await self._redis_get(key)

        entry = self._cache.get(key)
        if entry is None:
            self._misses += 1
            return None
        if perf_counter() - entry.timestamp > self._ttl_seconds:
            self._cache.pop(key, None)
            if key in self._access_order:
                self._access_order.remove(key)
            self._misses += 1
            return None
        self._hits += 1
        self._touch_lru(key)
        return list(entry.candidates)

    async def set(self, key: str, candidates: Sequence[RetrievalCandidate]) -> None:
        if self._closed:
            return
        if self._redis_url:
            await self._redis_set(key, candidates)
            return

        if key not in self._cache and len(self._cache) >= self._max_size:
            self._evict_lru()
        self._cache[key] = _CacheEntry(
            candidates=list(candidates),
            timestamp=perf_counter(),
        )
        self._touch_lru(key)

    def _touch_lru(self, key: str) -> None:
        if key in self._access_order:
            self._access_order.remove(key)
        self._access_order.append(key)

    def _evict_lru(self) -> None:
        while self._access_order and len(self._cache) >= self._max_size:
            oldest = self._access_order.pop(0)
            self._cache.pop(oldest, None)

    async def _redis_get(self, key: str) -> list[RetrievalCandidate] | None:
        r = self._redis_client()
        if r is None:
            return None
        try:
            data = await r.get(key)
            if data is None:
                self._misses += 1
                return None
            raw = json.loads(data)
            candidates = [RetrievalCandidate(**item) for item in raw]
            self._hits += 1
            return candidates
        except Exception as exc:
            _logger.warning("redis_cache_get_failed", extra={"key": key, "error": str(exc)})
            self._misses += 1
            return None

    async def _redis_set(self, key: str, candidates: Sequence[RetrievalCandidate]) -> None:
        r = self._redis_client()
        if r is None:
            return
        try:
            data = json.dumps([c.model_dump(mode="json") for c in candidates])
            await r.setex(key, int(self._ttl_seconds), data)
        except Exception as exc:
            _logger.warning("redis_cache_set_failed", extra={"key": key, "error": str(exc)})


class CachedRetriever:
    """Decorates a CandidateRetriever with caching."""

    def __init__(
        self,
        *,
        upstream: CandidateRetriever,
        cache: RetrievalCache,
    ) -> None:
        self._upstream = upstream
        self._cache = cache
        # Never share a flight across wrappers: upstream may own request-scoped
        # database sessions and authorization context not present in the DTOs.
        self._inflight: dict[tuple[str, str, str], _RetrievalFlight] = {}

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        try:
            key = self._cache.cache_key(request, filters)
        except (TypeError, ValueError):
            # Unknown filter objects cannot be safely canonicalized. Preserve
            # upstream behavior without inventing an ambiguous cache identity.
            return await self._upstream.retrieve(request=request, filters=filters)

        identity = (key, request.request_id, request.trace_id)
        while True:
            flight = self._inflight.get(identity)
            if flight is None:
                task = asyncio.create_task(
                    self._retrieve_cached(key=key, request=request, filters=filters)
                )
                flight = _RetrievalFlight(task=task)
                self._inflight[identity] = flight
                task.add_done_callback(partial(self._finished, identity))
            if not flight.abandoned:
                break
            # A last-waiter cancellation is still cleaning up its request-owned
            # upstream. Do not attach a new caller to that cancelled operation.
            await asyncio.wait({flight.task})

        flight.waiters += 1
        try:
            result = await asyncio.shield(flight.task)
            return list(result)
        finally:
            flight.waiters -= 1
            if flight.waiters == 0 and not flight.task.done():
                flight.abandoned = True
                flight.task.cancel()
                # Do not let the request's session exit while its own upstream
                # is still using it. Other waiters retain a shielded operation.
                while not flight.task.done():
                    try:
                        await asyncio.wait({flight.task})
                    except asyncio.CancelledError:
                        continue

    def _finished(
        self,
        identity: tuple[str, str, str],
        task: asyncio.Task[list[RetrievalCandidate]],
    ) -> None:
        flight = self._inflight.get(identity)
        if flight is not None and flight.task is task:
            self._inflight.pop(identity, None)
        if not task.cancelled():
            task.exception()

    async def _retrieve_cached(
        self, *, key: str, request: RetrievalRequest, filters: RetrievalFilterSet
    ) -> list[RetrievalCandidate]:
        cached = await self._cache.get(key)
        if cached is not None:
            return cached

        result = await self._upstream.retrieve(request=request, filters=filters)
        await self._cache.set(key, result)
        return result


@dataclass
class _RetrievalFlight:
    task: asyncio.Task[list[RetrievalCandidate]]
    waiters: int = 0
    abandoned: bool = False


def _cache_payload(value: object) -> object:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("Retrieval filters must have string keys")
        return {key: _cache_payload(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_cache_payload(item) for item in value]
    if value is None or isinstance(value, str | bool | int | float):
        return value
    raise TypeError("Retrieval filters cannot be safely cached")


class _CacheEntry:
    __slots__ = ("candidates", "timestamp")

    def __init__(self, candidates: list[RetrievalCandidate], timestamp: float) -> None:
        self.candidates = candidates
        self.timestamp = timestamp
