from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from packages.providers.runtime import (
    ProviderCapacityError,
    ProviderDeadline,
    ProviderRuntime,
    ProviderRuntimeClosedError,
    _retry_after,
)


@pytest.mark.asyncio
async def test_admission_bounds_queue_and_cancelled_waiter_reclaims_capacity() -> None:
    runtime = ProviderRuntime(max_in_flight=1, max_waiters=1)
    active = await runtime.acquire(ProviderDeadline(1))
    waiting = asyncio.create_task(runtime.acquire(ProviderDeadline(1)))
    await asyncio.sleep(0)
    assert (runtime.in_flight, runtime.waiting) == (1, 1)
    with pytest.raises(ProviderCapacityError, match="queue is full"):
        await runtime.acquire(ProviderDeadline(1))
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert (runtime.in_flight, runtime.waiting) == (1, 0)
    active.release()
    active.release()
    assert runtime.in_flight == 0
    replacement = await runtime.acquire(ProviderDeadline(1))
    replacement.release()
    await runtime.aclose()


@pytest.mark.asyncio
async def test_cancel_racing_slot_handoff_does_not_lose_slot() -> None:
    runtime = ProviderRuntime(max_in_flight=1)
    active = await runtime.acquire(ProviderDeadline(1))
    waiting = asyncio.create_task(runtime.acquire(ProviderDeadline(1)))
    await asyncio.sleep(0)
    active.release()  # future owns the handed-off slot but task has not resumed
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert (runtime.in_flight, runtime.waiting) == (0, 0)
    await runtime.aclose()


@pytest.mark.asyncio
async def test_queue_timeout_and_total_deadline_are_finite_and_distinct() -> None:
    runtime = ProviderRuntime(max_in_flight=1, queue_timeout_seconds=0.01)
    active = await runtime.acquire(ProviderDeadline(1))
    with pytest.raises(ProviderCapacityError, match="wait timed out"):
        await runtime.acquire(ProviderDeadline(1))
    with pytest.raises(TimeoutError):
        await runtime.acquire(ProviderDeadline(0.001))
    assert runtime.waiting == 0
    active.release()
    await runtime.aclose()


@pytest.mark.asyncio
async def test_shutdown_wakes_waiters_rejects_new_requests_keeps_injected_client() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    runtime = ProviderRuntime(client=client, max_in_flight=1)
    active = await runtime.acquire(ProviderDeadline(1))
    waiting = asyncio.create_task(runtime.acquire(ProviderDeadline(1)))
    await asyncio.sleep(0)
    await runtime.aclose()
    await runtime.aclose()
    with pytest.raises(ProviderRuntimeClosedError):
        await waiting
    with pytest.raises(ProviderRuntimeClosedError):
        await runtime.acquire(ProviderDeadline(1))
    with pytest.raises(ProviderRuntimeClosedError):
        _ = runtime.client
    assert not client.is_closed
    active.release()
    assert runtime.in_flight == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_full_jitter_backoff_caps_growth_and_honors_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    samples: list[tuple[float, float]] = []
    sleeps: list[float] = []

    def uniform(low: float, high: float) -> float:
        samples.append((low, high))
        return high / 2

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("packages.providers.runtime.random.uniform", uniform)
    monkeypatch.setattr("packages.providers.runtime.asyncio.sleep", sleep)
    runtime = ProviderRuntime(retry_backoff_seconds=0.1, retry_max_backoff_seconds=0.4)
    for attempt in range(4):
        await runtime.backoff(attempt=attempt, deadline=ProviderDeadline(10), response=None)
    await runtime.backoff(
        attempt=0,
        deadline=ProviderDeadline(10),
        response=httpx.Response(429, headers={"Retry-After": "2"}),
    )
    assert samples == [(0.0, 0.1), (0.0, 0.2), (0.0, 0.4), (0.0, 0.4), (0.0, 0.1)]
    assert sleeps == [0.05, 0.1, 0.2, 0.2, 2.0]
    await runtime.aclose()


@pytest.mark.parametrize("value", ["nonsense", "NaN", "inf", "-inf"])
def test_invalid_retry_after_is_ignored(value: str) -> None:
    assert _retry_after(httpx.Response(429, headers={"Retry-After": value})) is None


def test_http_date_retry_after_and_past_date() -> None:
    future = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
    delay = _retry_after(httpx.Response(503, headers={"Retry-After": future}))
    assert delay is not None and 28 < delay <= 30
    assert _retry_after(httpx.Response(503, headers={"Retry-After": "-1"})) == 0
    assert _retry_after(httpx.Response(503, headers={"Retry-After": "0"})) == 0


@pytest.mark.asyncio
async def test_retry_after_cannot_extend_total_deadline() -> None:
    runtime = ProviderRuntime()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            runtime.backoff(
                attempt=0,
                deadline=ProviderDeadline(0.01),
                response=httpx.Response(429, headers={"Retry-After": "3600"}),
            ),
            timeout=0.5,
        )
    await runtime.aclose()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_in_flight": 0},
        {"max_in_flight": True},
        {"max_waiters": -1},
        {"queue_timeout_seconds": float("inf")},
        {"request_timeout_seconds": float("nan")},
        {"retry_backoff_seconds": -1},
        {"retry_max_backoff_seconds": 0},
        {"retry_backoff_seconds": 2, "retry_max_backoff_seconds": 1},
    ],
)
def test_runtime_rejects_invalid_budgets(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ProviderRuntime(**kwargs)  # type: ignore[arg-type]
