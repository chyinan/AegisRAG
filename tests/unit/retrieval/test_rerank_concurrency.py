"""Deterministic concurrency contracts; no torch, model downloads or network."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from packages.retrieval.dto import RetrievalCandidate, RetrievalFilterSet, RetrievalRequest
from packages.retrieval.exceptions import RETRIEVAL_RERANK_DEGRADED, RetrievalError
from packages.retrieval.rerank.adapters import bge_local
from packages.retrieval.rerank.resources import BoundedInferenceExecutor, InferenceCapacityError


async def _started(event: threading.Event) -> None:
    assert await asyncio.to_thread(event.wait, 2), "worker did not start"


def _request() -> RetrievalRequest:
    return RetrievalRequest(query="policy", request_id="req", trace_id="trace")


def _candidate() -> RetrievalCandidate:
    return RetrievalCandidate(
        document_id="doc",
        version_id="v1",
        chunk_id="chunk",
        source_type="text",
        title_path=("policy",),
        score=0.5,
        retrieval_method="hybrid",
        tenant_id="tenant",
    )


async def test_cancelled_running_thread_keeps_capacity_until_real_completion() -> None:
    executor = BoundedInferenceExecutor(max_concurrency=1, max_queue_size=0)
    started, release = threading.Event(), threading.Event()

    def blocked() -> int:
        started.set()
        assert release.wait(2)
        return 1

    task = asyncio.create_task(executor.run(blocked))
    try:
        await _started(started)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(InferenceCapacityError):
            await executor.run(lambda: 2)
    finally:
        release.set()
        await executor.aclose()


async def test_cancelled_queued_job_keeps_budget_until_worker_consumes_it() -> None:
    executor = BoundedInferenceExecutor(max_concurrency=1, max_queue_size=1)
    started, release = threading.Event(), threading.Event()
    queued_ran = False

    def blocked() -> int:
        started.set()
        assert release.wait(2)
        return 1

    def queued() -> int:
        nonlocal queued_ran
        queued_ran = True
        return 2

    first = asyncio.create_task(executor.run(blocked))
    try:
        await _started(started)
        second = asyncio.create_task(executor.run(queued))
        await asyncio.sleep(0)
        with pytest.raises(InferenceCapacityError):
            await executor.run(lambda: 3)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        with pytest.raises(InferenceCapacityError):
            await executor.run(lambda: 4)
        retained = tuple(executor._pending)
        release.set()
        assert await first == 1
        await asyncio.gather(
            *(asyncio.wrap_future(future) for future in retained), return_exceptions=True
        )
        assert await executor.run(lambda: 4) == 4
        assert queued_ran is False
    finally:
        release.set()
        await executor.aclose()


async def test_queued_cancel_storm_cannot_grow_executor_work_queue() -> None:
    executor = BoundedInferenceExecutor(max_concurrency=1, max_queue_size=1)
    started, release = threading.Event(), threading.Event()
    queued_ran = False

    def blocked() -> int:
        started.set()
        assert release.wait(5)
        return 1

    def queued() -> int:
        nonlocal queued_ran
        queued_ran = True
        return 2

    first = asyncio.create_task(executor.run(blocked))
    cancelled = rejected = 0
    try:
        await _started(started)
        for _ in range(100):
            task = asyncio.create_task(executor.run(queued))
            await asyncio.sleep(0)
            if task.done():
                with pytest.raises(InferenceCapacityError):
                    await task
                rejected += 1
            else:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                cancelled += 1
            # Check the actual underlying WorkItems, not merely our accounting.
            assert executor._executor._work_queue.qsize() <= 1
            assert len(executor._pending) <= 2
        assert cancelled == 1
        assert rejected == 99
        assert len(executor._pending) == 2
        retained = tuple(executor._pending)
        release.set()
        assert await first == 1
        await asyncio.gather(
            *(asyncio.wrap_future(future) for future in retained), return_exceptions=True
        )
        assert queued_ran is False
        assert await executor.run(lambda: 3) == 3
    finally:
        release.set()
        await executor.aclose()


async def test_shutdown_waits_for_worker_without_blocking_event_loop() -> None:
    executor = BoundedInferenceExecutor(max_queue_size=0)
    started, release = threading.Event(), threading.Event()

    def blocked() -> int:
        started.set()
        assert release.wait(2)
        return 1

    operation = asyncio.create_task(executor.run(blocked))
    closing: asyncio.Task[None] | None = None
    try:
        await _started(started)
        closing = asyncio.create_task(executor.aclose())
        await asyncio.sleep(0.01)
        assert not closing.done()
        with pytest.raises(InferenceCapacityError):
            await executor.run(lambda: 2)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        release.set()
        assert await operation == 1
        await executor.aclose()
    finally:
        release.set()
        await executor.aclose()


async def test_max_concurrency_and_queue_are_bounded_under_a_wave() -> None:
    executor = BoundedInferenceExecutor(max_concurrency=2, max_queue_size=2)
    release, two_started = threading.Event(), threading.Event()
    lock = threading.Lock()
    active = peak = 0

    def score() -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                two_started.set()
        try:
            assert release.wait(2)
            return 1
        finally:
            with lock:
                active -= 1

    tasks = [asyncio.create_task(executor.run(score)) for _ in range(4)]
    try:
        await _started(two_started)
        with pytest.raises(InferenceCapacityError):
            await executor.run(score)
        assert peak == 2
        release.set()
        assert await asyncio.gather(*tasks) == [1, 1, 1, 1]
        assert peak == 2
    finally:
        release.set()
        await executor.aclose()


async def test_cold_model_load_runs_off_loop_and_is_shared_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bge_local._model_cache.clear()
    started, release = threading.Event(), threading.Event()
    caller_thread = threading.get_ident()
    load_threads: list[int] = []

    def load(model_name: str, device: str | None = None) -> tuple[object, object]:
        load_threads.append(threading.get_ident())
        started.set()
        assert release.wait(2)
        return object(), object()

    monkeypatch.setattr(bge_local, "_load_model", load)
    monkeypatch.setattr(bge_local, "_score_pairs_sync", lambda *args: [0.5])
    first, second = bge_local.BGELocalReranker(), bge_local.BGELocalReranker()
    tasks = [
        asyncio.create_task(reranker._compute_scores(query="q", documents=["doc"]))
        for reranker in (first, second)
    ]
    try:
        await _started(started)
        await asyncio.sleep(0)
        assert not tasks[0].done()
        assert load_threads == [load_threads[0]]
        assert load_threads[0] != caller_thread
        release.set()
        assert await asyncio.gather(*tasks) == [
            [pytest.approx(0.622459)],
            [pytest.approx(0.622459)],
        ]
        assert len(load_threads) == 1
    finally:
        release.set()
        await asyncio.gather(first.aclose(), second.aclose())
        bge_local._model_cache.clear()


async def test_device_is_part_of_shared_model_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    bge_local._model_cache.clear()
    devices: list[str | None] = []

    def load(model_name: str, device: str | None = None) -> tuple[object, object]:
        devices.append(device)
        return object(), object()

    monkeypatch.setattr(bge_local, "_load_model", load)
    monkeypatch.setattr(bge_local, "_score_pairs_sync", lambda *args: [0.0])
    first = bge_local.BGELocalReranker(device="cpu")
    second = bge_local.BGELocalReranker(device="cuda")
    try:
        await first._compute_scores(query="q", documents=["doc"])
        await second._compute_scores(query="q", documents=["doc"])
        assert devices == ["cpu", "cuda"]
    finally:
        await asyncio.gather(first.aclose(), second.aclose())
        bge_local._model_cache.clear()


async def test_timeout_keeps_running_inference_slot_and_reports_overload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started, release = threading.Event(), threading.Event()
    reranker = bge_local.BGELocalReranker(
        timeout_seconds=0.02,
        max_retries=0,
        max_queue_size=0,
    )

    def blocked(*, query: str, documents: list[str]) -> list[float]:
        started.set()
        assert release.wait(2)
        return [0.5]

    monkeypatch.setattr(reranker, "_compute_scores_sync", blocked)
    try:
        with pytest.raises(TimeoutError):
            await reranker._compute_scores(query="q", documents=["doc"])
        assert started.is_set()
        with pytest.raises(RetrievalError) as error:
            await reranker.rerank(
                request=_request(),
                filters=RetrievalFilterSet(tenant_id="tenant", user_id="u"),
                candidates=[_candidate()],
            )
        assert error.value.code == RETRIEVAL_RERANK_DEGRADED
        assert error.value.status_code == 503
    finally:
        release.set()
        await reranker.aclose()


@pytest.mark.parametrize("kwargs", [{"max_concurrency": 0}, {"max_queue_size": -1}])
def test_invalid_executor_limits_fail_early(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        BoundedInferenceExecutor(**kwargs)


async def test_shared_model_is_not_scored_concurrently_by_separate_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bge_local._model_cache.clear()
    both_loaded, scoring_started, release = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    lock = threading.Lock()
    loaded = active = peak = 0
    original_ensure = bge_local.BGELocalReranker._ensure_model_loaded

    def ensure(self: bge_local.BGELocalReranker) -> tuple[object, object]:
        nonlocal loaded
        result = original_ensure(self)
        with lock:
            loaded += 1
            if loaded == 2:
                both_loaded.set()
        return result

    def score(*args: object) -> list[float]:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        scoring_started.set()
        try:
            assert release.wait(2)
            return [0.0]
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(bge_local, "_load_model", lambda *args, **kwargs: (object(), object()))
    monkeypatch.setattr(bge_local.BGELocalReranker, "_ensure_model_loaded", ensure)
    monkeypatch.setattr(bge_local, "_score_pairs_sync", score)
    first, second = bge_local.BGELocalReranker(), bge_local.BGELocalReranker()
    tasks = [
        asyncio.create_task(reranker._compute_scores(query="q", documents=["doc"]))
        for reranker in (first, second)
    ]
    try:
        await _started(scoring_started)
        await _started(both_loaded)
        assert peak == 1
        release.set()
        assert await asyncio.gather(*tasks) == [[0.5], [0.5]]
        assert peak == 1
    finally:
        release.set()
        await asyncio.gather(first.aclose(), second.aclose())
        bge_local._model_cache.clear()


async def test_abandoned_worker_failure_is_observed_without_loop_warning() -> None:
    executor = BoundedInferenceExecutor(max_queue_size=0)
    started, release = threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    old_handler = loop.get_exception_handler()
    errors: list[dict[str, Any]] = []
    loop.set_exception_handler(lambda loop, context: errors.append(context))

    def fail_late() -> int:
        started.set()
        assert release.wait(2)
        raise RuntimeError("late failure")

    operation = asyncio.create_task(executor.run(fail_late))
    try:
        await _started(started)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        release.set()
        await executor.aclose()
        await asyncio.sleep(0)
        assert errors == []
    finally:
        release.set()
        await executor.aclose()
        loop.set_exception_handler(old_handler)
