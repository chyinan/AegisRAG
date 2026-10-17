"""Cover expiration, abandoned-flight cleanup, and safe inference failures."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from packages.retrieval.rerank import cache as cache_module
from packages.retrieval.rerank.adapters import bge_local
from packages.retrieval.rerank.cache import CachedRetriever, RetrievalCache
from tests.unit.retrieval.test_cache_concurrency import _candidate, _filters, _request


async def test_expired_entry_is_removed_from_lru_and_can_be_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    monkeypatch.setattr(cache_module, "perf_counter", lambda: now[0])
    cache = RetrievalCache(ttl_seconds=1, max_size=1)
    await cache.set("expired", [_candidate()])
    now[0] = 102.0
    assert await cache.get("expired") is None
    assert "expired" not in cache._cache
    assert "expired" not in cache._access_order
    await cache.set("new", [_candidate()])
    assert await cache.get("new") == [_candidate()]
    await cache.aclose()


async def test_new_caller_waits_for_abandoned_flight_before_starting_its_own() -> None:
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    class Upstream:
        async def retrieve(self, **kwargs: Any) -> list[Any]:
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaning.set()
                    await release.wait()
            return [_candidate()]

    cache = RetrievalCache()
    retriever = CachedRetriever(upstream=Upstream(), cache=cache)
    first = asyncio.create_task(retriever.retrieve(request=_request(), filters=_filters()))
    second = None
    try:
        await asyncio.wait_for(started.wait(), 1)
        first.cancel()
        await asyncio.wait_for(cleaning.wait(), 1)
        second = asyncio.create_task(retriever.retrieve(request=_request(), filters=_filters()))
        await asyncio.sleep(0)
        assert not second.done()
        assert calls == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert await asyncio.wait_for(second, 1) == [_candidate()]
        assert calls == 2
        assert not retriever._inflight
    finally:
        release.set()
        for task in (first, second):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(t for t in (first, second) if t is not None), return_exceptions=True)
        await cache.aclose()


@pytest.mark.parametrize("batch_size", [0, -1])
def test_bge_rejects_nonpositive_batches_before_allocating_executor(batch_size: int) -> None:
    with pytest.raises(ValueError, match="batch_size must be positive"):
        bge_local.BGELocalReranker(batch_size=batch_size)


async def test_bge_retries_failed_scoring_without_logging_private_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reranker = bge_local.BGELocalReranker(model_name="coverage-fake", max_retries=1)
    calls = 0
    sleeps: list[float] = []
    logs: list[tuple[str, dict[str, Any]]] = []

    def score(*args: Any) -> list[float]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("PRIVATE-INFERENCE-FAILURE")
        return [0.5]

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(reranker, "_ensure_model_loaded", lambda: (object(), object()))
    monkeypatch.setattr(bge_local, "_score_pairs_sync", score)
    monkeypatch.setattr(bge_local, "asyncio", SimpleNamespace(sleep=sleep, timeout=asyncio.timeout))
    monkeypatch.setattr(
        bge_local,
        "_logger",
        SimpleNamespace(
            info=lambda event, **fields: logs.append((event, fields)),
        ),
    )
    try:
        result = await reranker.rerank(
            request=_request(query="PRIVATE-QUERY"),
            filters=_filters(),
            candidates=[_candidate()],
        )
        assert calls == 2
        assert sleeps == [0.5]
        assert len(result.candidates) == 1
        assert logs[0][0] == "bge_local_compute_scores_failed"
        assert logs[0][1]["extra"]["query_len"] == len("PRIVATE-QUERY")
        assert "PRIVATE-QUERY" not in repr(logs)
        assert "PRIVATE-INFERENCE-FAILURE" not in repr(logs)
    finally:
        await reranker.aclose()
