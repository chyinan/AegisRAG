"""Independent load-measurement regressions; no sockets or provider requests."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from evaluation import load_test as load


@dataclass
class ReviewClock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.now += delay
        await asyncio.sleep(0)


@pytest.mark.parametrize("prefix", ["", "/api"])
@pytest.mark.parametrize("suffix", ["?", "#", "?#"])
def test_empty_url_delimiters_are_rejected(prefix: str, suffix: str) -> None:
    with pytest.raises(ValueError, match="api-url"):
        load.LoadConfig(api_url=f"https://chosen.test{prefix}{suffix}")


@pytest.mark.parametrize("suffix", ["?", "#"])
async def test_cli_empty_url_delimiters_fail_before_io(
    suffix: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def forbidden_measurement(config: load.LoadConfig) -> load.LoadReport:
        nonlocal calls
        calls += 1
        raise AssertionError("invalid configuration must not begin measurement")

    monkeypatch.setattr(load, "run_load", forbidden_measurement)
    with pytest.raises(SystemExit) as raised:
        await load.main([
            "--api-url", f"https://chosen.test{suffix}", "--output-dir", str(tmp_path),
        ])
    assert raised.value.code == 2
    assert calls == 0
    assert not list(tmp_path.iterdir())


async def test_percent_encoded_prefix_characters_preserve_wire_destination() -> None:
    paths: list[bytes] = []
    clock = ReviewClock()

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.raw_path)
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await load.run_load(
            load.LoadConfig(users=1, duration_s=0.5, api_url="https://chosen.test/a%3Fb%23c/"),
            client=client, clock=clock, sleep=clock.sleep,
        )
    assert paths == [b"/a%3Fb%23c/retrieve", b"/a%3Fb%23c/query"]
    assert report["status"] == "completed"
    assert report["config"]["api_url"] == "https://chosen.test/a%3Fb%23c"


async def test_partial_worker_startup_failure_joins_tasks_and_closes_coroutine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[asyncio.Task[None]] = []
    rejected: list[Coroutine[Any, Any, None]] = []
    original_create_task = asyncio.create_task

    def fail_second_create(coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        if created:
            rejected.append(coro)
            raise RuntimeError("synthetic worker startup failure")
        task = original_create_task(coro)
        created.append(task)
        return task

    def never_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError("startup failure must not leave traffic behind")

    monkeypatch.setattr(
        load, "asyncio", SimpleNamespace(**{**vars(asyncio), "create_task": fail_second_create}),
    )
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(never_request)) as client:
            with pytest.raises(RuntimeError, match="synthetic worker startup failure"):
                await load.run_load(load.LoadConfig(users=2), client=client)
        joined_on_return = all(task.done() for task in created)
        closed_on_return = all(coro.cr_frame is None for coro in rejected)
    finally:
        # Cleanup also keeps the red test safe against the preserved broken source.
        for task in created:
            task.cancel()
        await asyncio.gather(*created, return_exceptions=True)
        for coro in rejected:
            coro.close()
    assert len(created) == len(rejected) == 1
    assert joined_on_return, "owned worker escaped the failed startup"
    assert closed_on_return, "unscheduled coroutine escaped the failed startup"


async def test_concurrent_runs_keep_terminal_counts_and_timings_independent() -> None:
    both_started = asyncio.Event()
    started = 0

    async def one_run(successful: bool) -> load.LoadReport:
        nonlocal started
        clock = ReviewClock()

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal started
            if request.url.path == "/retrieve":
                started += 1
                if started == 2:
                    both_started.set()
                await both_started.wait()
            clock.now += 0.25 if successful else 0.5
            if not successful:
                raise httpx.ReadError("synthetic failure details", request=request)
            return httpx.Response(200)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await load.run_load(
                load.LoadConfig(users=1, duration_s=0.5), client=client,
                clock=clock, sleep=clock.sleep,
            )

    successful, failed = await asyncio.wait_for(
        asyncio.gather(one_run(True), one_run(False)), timeout=1,
    )
    assert successful["status"] == "completed"
    assert failed["status"] == "failed"
    assert successful["actual_elapsed_s"] == 1.5
    assert failed["actual_elapsed_s"] == 2
    for endpoint in ("retrieve", "query"):
        assert successful[endpoint]["total"] == failed[endpoint]["total"] == 1
        assert successful[endpoint]["successful"] == 1
        assert failed[endpoint]["successful"] == 0
        assert successful[endpoint]["avg_ms"] == 250
        assert failed[endpoint]["avg_ms"] == 500
        assert successful[endpoint]["error_counts"] == {}
        assert failed[endpoint]["error_counts"] == {"transport_error": 1}


@pytest.mark.parametrize("failure", ["cancelled_query", "failed_think_time"])
async def test_failed_worker_joins_awaiting_sibling_cleanup_without_query(
    failure: str,
) -> None:
    sibling_started = asyncio.Event()
    sibling_cleaned = asyncio.Event()
    requests: list[tuple[str, str]] = []
    first_query, second_query = load.QUERIES[:2]
    clock = ReviewClock()

    async def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        requests.append((query, request.url.path))
        if query == second_query:
            sibling_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                sibling_cleaned.set()
        if request.url.path == "/retrieve":
            await sibling_started.wait()
        elif failure == "cancelled_query":
            raise asyncio.CancelledError
        return httpx.Response(200)

    async def fail_think_time(delay: float) -> None:
        raise RuntimeError("synthetic think-time failure")

    expected_error = asyncio.CancelledError if failure == "cancelled_query" else RuntimeError
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(expected_error):
            await asyncio.wait_for(
                load.run_load(
                    load.LoadConfig(users=2, duration_s=0.5), client=client,
                    clock=clock, sleep=fail_think_time,
                ), timeout=1,
            )
        assert not client.is_closed, "a borrowed client belongs to its caller"
    assert sibling_cleaned.is_set()
    assert requests.count((first_query, "/retrieve")) == 1
    assert requests.count((first_query, "/query")) == 1
    assert requests.count((second_query, "/retrieve")) == 1
    assert (second_query, "/query") not in requests
