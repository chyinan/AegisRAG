from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from time import perf_counter
from typing import Any

from packages.common.config import AppSettings, load_settings
from packages.common.logging import get_request_logger
from packages.retrieval.dto import RetrievalCandidate, RetrievalFilterSet, RetrievalRequest
from packages.retrieval.ports import CandidateRetriever

_logger = get_request_logger()


class RetrievalCache:
    """LRU cache for retrieval results. Supports in-memory and Redis backends.

    Hot query detection: same (query, tenant_id, top_k) tuple within TTL.
    Expected gain: -90% latency for repeated/hot queries (Phase 1 P0).
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
        self._cache: dict[str, _CacheEntry] = {}
        self._access_order: list[str] = []
        self._hits: int = 0
        self._misses: int = 0

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    def cache_key(self, request: RetrievalRequest, filters: RetrievalFilterSet) -> str:
        raw = json.dumps(
            [request.query, filters.tenant_id, request.top_k],
            sort_keys=True,
            ensure_ascii=False,
        )
        return f"retrieval:{hashlib.sha256(raw.encode()).hexdigest()[:24]}"

    async def get(
        self, key: str
    ) -> list[RetrievalCandidate] | None:
        if self._redis_url:
            return await self._redis_get(key)

        entry = self._cache.get(key)
        if entry is None:
            self._misses += 1
            return None
        if perf_counter() - entry.timestamp > self._ttl_seconds:
            self._cache.pop(key, None)
            self._misses += 1
            return None
        self._hits += 1
        self._touch_lru(key)
        return entry.candidates

    async def set(self, key: str, candidates: Sequence[RetrievalCandidate]) -> None:
        if self._redis_url:
            await self._redis_set(key, candidates)
            return

        if len(self._cache) >= self._max_size:
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
        try:
            import redis.asyncio as aioredis
        except ImportError:
            _logger.warning("redis_not_installed", extra={"key": key})
            return None
        try:
            r = aioredis.from_url(self._redis_url)  # type: ignore[arg-type]
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
        try:
            import redis.asyncio as aioredis
        except ImportError:
            return
        try:
            r = aioredis.from_url(self._redis_url)  # type: ignore[arg-type]
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

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        key = self._cache.cache_key(request, filters)
        cached = await self._cache.get(key)
        if cached is not None:
            return cached

        result = await self._upstream.retrieve(request=request, filters=filters)
        await self._cache.set(key, result)
        return result


class _CacheEntry:
    __slots__ = ("candidates", "timestamp")

    def __init__(self, candidates: list[RetrievalCandidate], timestamp: float) -> None:
        self.candidates = candidates
        self.timestamp = timestamp
