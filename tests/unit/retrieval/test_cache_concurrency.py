"""Cache identity, instance-local singleflight and cancellation ownership."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from packages.retrieval.dto import RetrievalCandidate, RetrievalFilterSet, RetrievalRequest
from packages.retrieval.rerank.cache import CachedRetriever, RetrievalCache


def _request(**changes: Any) -> RetrievalRequest:
    return RetrievalRequest(query="policy", request_id="req", trace_id="trace").model_copy(
        update=changes
    )


def _filters(**changes: Any) -> RetrievalFilterSet:
    return RetrievalFilterSet(tenant_id="tenant", user_id="user").model_copy(update=changes)


def _candidate(user: str = "user") -> RetrievalCandidate:
    return RetrievalCandidate(
        document_id=f"doc-{user}",
        version_id="v1",
        chunk_id=f"chunk-{user}",
        source_type="text",
        title_path=("policy",),
        score=0.5,
        retrieval_method="hybrid",
        tenant_id="tenant",
    )


@dataclass
class _Upstream:
    calls: list[tuple[RetrievalRequest, RetrievalFilterSet]] = field(default_factory=list)
    started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    error: bool = False

    async def retrieve(
        self, *, request: RetrievalRequest, filters: RetrievalFilterSet
    ) -> list[RetrievalCandidate]:
        self.calls.append((request, filters))
        self.started.set()
        await self.release.wait()
        if self.error:
            raise RuntimeError("upstream failed")
        return [_candidate(filters.user_id)]


@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": "other"},
        {"user_id": "other"},
        {"roles": ("admin",)},
        {"department": "finance"},
        {"permissions": ("read:restricted",)},
        {"metadata_filter": {"department": "hr"}},
        {"acl_filter": {"allowed_users": ["different"]}},
        {"include_deleted": True},
    ],
)
def test_cache_key_preserves_every_authorization_filter(changes: dict[str, Any]) -> None:
    cache = RetrievalCache()
    assert cache.cache_key(_request(), _filters()) != cache.cache_key(
        _request(), _filters(**changes)
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"query": "other"},
        {"top_k": 2},
        {"score_threshold": 0.8},
        {"metadata_filter": {"source_type": "pdf"}},
    ],
)
def test_cache_key_preserves_every_request_semantic(changes: dict[str, Any]) -> None:
    cache = RetrievalCache()
    assert cache.cache_key(_request(), _filters()) != cache.cache_key(
        _request(**changes), _filters()
    )


def test_cache_key_ignores_observation_ids_and_canonicalizes_mapping_order() -> None:
    cache = RetrievalCache()
    assert cache.cache_key(_request(), _filters()) == cache.cache_key(
        _request(request_id="next", trace_id="next"), _filters()
    )
    assert cache.cache_key(_request(), _filters(acl_filter={"a": 1, "b": 2})) == (
        cache.cache_key(_request(), _filters(acl_filter={"b": 2, "a": 1}))
    )
    assert cache.cache_key(_request(), _filters()).startswith("retrieval:v2:")


async def test_cold_miss_wave_joins_once_and_result_lists_are_independent() -> None:
    upstream = _Upstream()
    cache = RetrievalCache()
    retriever = CachedRetriever(upstream=upstream, cache=cache)
    tasks = [
        asyncio.create_task(retriever.retrieve(request=_request(), filters=_filters()))
        for _ in range(20)
    ]
    await upstream.started.wait()
    assert len(upstream.calls) == 1
    upstream.release.set()
    results = await asyncio.gather(*tasks)
    assert len(upstream.calls) == 1
    assert all(result == [_candidate()] for result in results)
    results[0].clear()
    assert results[1] == [_candidate()]
    assert await retriever.retrieve(request=_request(), filters=_filters()) == [_candidate()]
    assert len(upstream.calls) == 1
    assert not retriever._inflight


@pytest.mark.parametrize("field_name", ["request_id", "trace_id"])
async def test_observation_identity_is_not_merged_inflight(field_name: str) -> None:
    upstream = _Upstream()
    retriever = CachedRetriever(upstream=upstream, cache=RetrievalCache())
    first = asyncio.create_task(retriever.retrieve(request=_request(), filters=_filters()))
    second = asyncio.create_task(
        retriever.retrieve(request=_request(**{field_name: "different"}), filters=_filters())
    )
    await upstream.started.wait()
    await asyncio.sleep(0)
    assert len(upstream.calls) == 2
    upstream.release.set()
    await asyncio.gather(first, second)


async def test_separate_wrappers_never_join_request_owned_upstreams() -> None:
    cache = RetrievalCache()
    first, second = _Upstream(), _Upstream()
    tasks = [
        asyncio.create_task(
            CachedRetriever(upstream=upstream, cache=cache).retrieve(
                request=_request(), filters=_filters()
            )
        )
        for upstream in (first, second)
    ]
    await asyncio.gather(first.started.wait(), second.started.wait())
    assert len(first.calls) == len(second.calls) == 1
    first.release.set()
    second.release.set()
    await asyncio.gather(*tasks)


async def test_shared_cache_never_returns_another_users_candidates() -> None:
    cache = RetrievalCache()
    upstream = _Upstream()
    upstream.release.set()
    retriever = CachedRetriever(upstream=upstream, cache=cache)
    assert await retriever.retrieve(request=_request(), filters=_filters()) == [_candidate()]
    assert await retriever.retrieve(request=_request(), filters=_filters(user_id="other")) == [
        _candidate("other")
    ]
    assert len(upstream.calls) == 2


async def test_cancelled_leader_does_not_cancel_a_remaining_waiter() -> None:
    upstream = _Upstream()
    retriever = CachedRetriever(upstream=upstream, cache=RetrievalCache())
    first = asyncio.create_task(retriever.retrieve(request=_request(), filters=_filters()))
    second = asyncio.create_task(retriever.retrieve(request=_request(), filters=_filters()))
    await upstream.started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not second.done()
    upstream.release.set()
    assert await second == [_candidate()]
    assert len(upstream.calls) == 1


async def test_last_waiter_cancel_awaits_owned_upstream_cleanup_before_returning() -> None:
    started, cleanup_started, cleanup_release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class CleaningUpstream:
        async def retrieve(
            self, *, request: RetrievalRequest, filters: RetrievalFilterSet
        ) -> list[RetrievalCandidate]:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                await cleanup_release.wait()
            return []

    retriever = CachedRetriever(upstream=CleaningUpstream(), cache=RetrievalCache())
    task = asyncio.create_task(retriever.retrieve(request=_request(), filters=_filters()))
    await started.wait()
    task.cancel()
    await cleanup_started.wait()
    assert not task.done()
    # Repeated cancellation must not detach session-owned cleanup either.
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    cleanup_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not retriever._inflight


async def test_shared_failure_is_removed_so_later_calls_can_retry() -> None:
    upstream = _Upstream(error=True)
    retriever = CachedRetriever(upstream=upstream, cache=RetrievalCache())
    tasks = [
        asyncio.create_task(retriever.retrieve(request=_request(), filters=_filters()))
        for _ in range(2)
    ]
    await upstream.started.wait()
    upstream.release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(isinstance(result, RuntimeError) for result in results)
    assert len(upstream.calls) == 1
    assert not retriever._inflight
    upstream.error = False
    assert await retriever.retrieve(request=_request(), filters=_filters()) == [_candidate()]
    assert len(upstream.calls) == 2


@pytest.mark.parametrize("acl", [{"opaque": object()}, {"nested": {1: "value"}}])
async def test_unsupported_filter_identity_bypasses_cache_safely(acl: dict[str, Any]) -> None:
    upstream = _Upstream()
    upstream.release.set()
    retriever = CachedRetriever(upstream=upstream, cache=RetrievalCache())
    for _ in range(2):
        assert await retriever.retrieve(request=_request(), filters=_filters(acl_filter=acl)) == [
            _candidate()
        ]
    assert len(upstream.calls) == 2


async def test_memory_cache_keeps_old_get_set_api_and_does_not_evict_on_update() -> None:
    cache = RetrievalCache(max_size=2)
    await cache.set("first", [_candidate()])
    await cache.set("second", [_candidate("other")])
    await cache.set("second", [_candidate("updated")])
    first = await cache.get("first")
    assert first == [_candidate()]
    assert first is not None
    first.clear()
    assert await cache.get("first") == [_candidate()]
    assert await cache.get("second") == [_candidate("updated")]


async def test_cache_lifespan_closes_redis_pool_exactly_once() -> None:
    class RedisPool:
        closed = 0

        async def aclose(self) -> None:
            self.closed += 1

    cache = RetrievalCache(redis_url="redis://test.invalid")
    pool = RedisPool()
    cache._redis = pool
    await cache.aclose()
    await cache.aclose()
    assert pool.closed == 1
    assert await cache.get("key") is None
    await cache.set("key", [_candidate()])
    assert cache._redis_client() is None
