from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from evaluation import load_test as load


@dataclass
class FakeClock:
    now: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay
        await asyncio.sleep(0)


async def measure(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    clock: FakeClock | None = None,
    config: load.LoadConfig | None = None,
) -> load.LoadReport:
    clock = clock or FakeClock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await load.run_load(
            config or load.LoadConfig(users=1, duration_s=0.5),
            client=client, clock=clock, sleep=clock.sleep,
        )


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200)


async def test_selected_origin_prefix_headers_and_pair_order_are_used() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    report = await measure(handler, config=load.LoadConfig(1, 0.5, "https://chosen.test/api/"))

    assert [str(request.url) for request in requests] == [
        "https://chosen.test/api/retrieve", "https://chosen.test/api/query",
    ]
    assert report["config"]["api_url"] == "https://chosen.test/api"
    for request in requests:
        for key, value in load.HDR.items():
            assert request.headers[key] == value
        assert json.loads(request.content) == {"query": load.QUERIES[0], "top_k": 10}
        assert request.extensions["timeout"] == {
            "connect": 120, "read": 120, "write": 120, "pool": 120,
        }


async def test_repeated_runs_have_independent_counts_and_outcomes() -> None:
    first = await measure(lambda request: httpx.Response(500))
    second = await measure(ok)

    assert first["retrieve"]["errors"] == first["query"]["errors"] == 1
    assert second["retrieve"]["total"] == second["query"]["total"] == 1
    assert second["retrieve"]["errors"] == second["query"]["errors"] == 0
    assert first["status"] == "failed"
    assert second["status"] == "completed"


@pytest.mark.parametrize("status", [200, 201, 204, 299, 199, 300, 302, 399, 400, 500])
async def test_only_2xx_is_successful_without_following_redirects(status: int) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, headers={"location": "https://redirect.test/secret"})

    clock = FakeClock()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True,
    ) as client:
        report = await load.run_load(
            load.LoadConfig(1, 0.5), client=client, clock=clock, sleep=clock.sleep,
        )

    successful = 200 <= status < 300
    assert len(requests) == 2
    assert report["status"] == ("completed" if successful else "failed")
    assert report["exit_code"] == (0 if successful else 1)
    assert report["retrieve"]["successful"] == int(successful)
    assert report["retrieve"]["status_counts"] == {str(status): 1}
    assert report["retrieve"]["error_counts"] == ({} if successful else {"http_status": 1})


async def test_retrieve_failure_still_dispatches_query_and_reports_partial() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/retrieve":
            raise httpx.ConnectError("private host and token", request=request)
        return httpx.Response(200)

    report = await measure(handler)

    assert calls == ["/retrieve", "/query"]
    assert report["status"] == "partial"
    assert report["exit_code"] == 1
    assert report["retrieve"]["status_counts"] == {}
    assert report["retrieve"]["error_counts"] == {"transport_error": 1}
    assert report["query"]["status_counts"] == {"200": 1}
    assert report["query"]["successful"] == 1


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (httpx.ReadTimeout("secret response"), "httpx_timeout"),
        (httpx.ConnectError("secret address"), "transport_error"),
        (TimeoutError("secret timeout"), "request_deadline"),
        (RuntimeError("secret internal details"), "unexpected_error"),
    ],
)
async def test_safe_error_categories_without_raw_details(error: Exception, category: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    report = await measure(handler)

    assert report["status"] == "failed"
    for endpoint in (report["retrieve"], report["query"]):
        assert endpoint["total"] == endpoint["errors"] == endpoint["latency_sample_count"] == 1
        assert endpoint["error_counts"] == {category: 1}
        assert endpoint["status_counts"] == {}
    assert "secret" not in json.dumps(report)


async def test_dispatch_deadline_drains_pair_and_full_think_time() -> None:
    clock = FakeClock()
    calls: list[tuple[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, clock.now))
        clock.now += 2
        return httpx.Response(200)

    report = await measure(handler, clock=clock, config=load.LoadConfig(1, 0.5))

    assert calls == [("/retrieve", 0), ("/query", 2)]
    assert clock.sleeps == [1]
    assert report["dispatch_window_s"] == 0.5
    assert report["actual_elapsed_s"] == 5
    assert report["drain_s"] == 4.5
    assert report["retrieve"]["throughput_qps"] == 0.2
    assert report["combined_throughput_qps"] == 0.4
    assert report["retrieve"]["avg_ms"] == 2000
    assert report["query"]["avg_ms"] == 2000


async def test_all_workers_receive_one_shared_dispatch_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deadlines: list[float] = []

    async def no_requests(
        index: int, deadline: float, client: httpx.AsyncClient, config: load.LoadConfig,
        stats: dict[str, load.EndpointStats], clock: Callable[[], float],
        sleep: Callable[[float], Any],
    ) -> None:
        deadlines.append(deadline)

    monkeypatch.setattr(load, "worker", no_requests)
    report = await measure(ok, clock=FakeClock(10), config=load.LoadConfig(3, 2.5))

    assert deadlines == [12.5, 12.5, 12.5]
    assert report["status"] == "empty"


async def test_empty_run_is_nonzero_and_has_null_latency() -> None:
    clock_calls = iter([0.0, 2.0, 2.0])
    async with httpx.AsyncClient(transport=httpx.MockTransport(ok)) as client:
        report = await load.run_load(
            load.LoadConfig(1, 1), client=client, clock=lambda: next(clock_calls),
        )

    assert report["status"] == "empty"
    assert report["exit_code"] == 1
    for endpoint in (report["retrieve"], report["query"]):
        assert endpoint["total"] == endpoint["successful"] == endpoint["errors"] == 0
        assert endpoint["p50_ms"] is endpoint["avg_ms"] is endpoint["min_ms"] is None
        assert endpoint["status_counts"] == endpoint["error_counts"] == {}
    assert report["combined_throughput_qps"] == 0


async def test_round_robin_queries_are_stable_for_each_worker_index() -> None:
    async def questions_for(index: int) -> list[str]:
        seen: list[str] = []
        clock = FakeClock()

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content)["query"])
            return httpx.Response(200)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await load.worker(
                index, 2, client, load.LoadConfig(1, 2),
                {"retrieve": load.EndpointStats(), "query": load.EndpointStats()},
                clock, clock.sleep,
            )
        return seen

    expected = [load.QUERIES[-1], load.QUERIES[-1], load.QUERIES[0], load.QUERIES[0]]
    assert await questions_for(len(load.QUERIES) - 1) == expected
    assert await questions_for(2 * len(load.QUERIES) - 1) == expected


def test_legacy_percentile_definition_and_empty_population() -> None:
    samples = [4.0, 1.0, 2.0, 3.0]
    assert load.pct(samples, 0) == 1
    assert load.pct(samples, 50) == 3
    assert load.pct(samples, 95) == load.pct(samples, 100) == 4
    assert samples == [4, 1, 2, 3]
    assert load.pct([], 50) is None
    assert load.pct([7], 50) == 7


@pytest.mark.parametrize("percentile", [-1, 101, float("nan"), float("inf")])
def test_invalid_percentiles_are_rejected(percentile: float) -> None:
    with pytest.raises(ValueError, match="percentile"):
        load.pct([1], percentile)


async def test_failed_attempts_are_in_latency_population() -> None:
    clock = FakeClock()

    def handler(request: httpx.Request) -> httpx.Response:
        # Both endpoint distributions contain a fast success and slower failure.
        first_pair = clock.now < 1
        clock.now += 0.1 if first_pair else 0.4
        return httpx.Response(200 if first_pair else 503)

    report = await measure(handler, clock=clock, config=load.LoadConfig(1, 2))

    for endpoint in (report["retrieve"], report["query"]):
        assert endpoint["total"] == endpoint["latency_sample_count"] == 2
        assert endpoint["successful"] == endpoint["errors"] == 1
        assert endpoint["latency_population"] == "all_terminal_attempts_including_failures"
        assert endpoint["avg_ms"] == pytest.approx(250)
        assert endpoint["p50_ms"] == pytest.approx(400)
        assert endpoint["successful_throughput_qps"] == pytest.approx(1 / 3)
    assert report["percentile_method"] == "sorted_zero_based_floor_n_p_over_100_clamped"
    assert report["combined_successful_throughput_qps"] == pytest.approx(2 / 3)


async def test_cooperative_whole_request_deadline_and_httpx_timeout_are_kept() -> None:
    seen_timeouts: list[dict[str, float]] = []

    async def blocked(request: httpx.Request) -> httpx.Response:
        seen_timeouts.append(request.extensions["timeout"])
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    clock = FakeClock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(blocked)) as client:
        report = await load.run_load(
            load.LoadConfig(1, 0.5, request_timeout_s=0.01),
            client=client, clock=clock, sleep=clock.sleep,
        )

    assert report["retrieve"]["error_counts"] == {"request_deadline": 1}
    assert report["query"]["error_counts"] == {"request_deadline": 1}
    assert seen_timeouts == [{"connect": 0.01, "read": 0.01, "write": 0.01, "pool": 0.01}] * 2


async def test_worker_cancellation_propagates_without_dispatching_query() -> None:
    calls: list[str] = []

    async def cancelled(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise asyncio.CancelledError

    clock = FakeClock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(cancelled)) as client:
        with pytest.raises(asyncio.CancelledError):
            await load.run_load(
                load.LoadConfig(1, 0.5), client=client, clock=clock, sleep=clock.sleep,
            )
    assert calls == ["/retrieve"]


async def test_caller_cancellation_cleans_up_sibling_workers() -> None:
    all_started = asyncio.Event()
    active = 0
    cleaned = 0

    async def blocked(request: httpx.Request) -> httpx.Response:
        nonlocal active, cleaned
        active += 1
        if active == 2:
            all_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned += 1
        raise AssertionError("unreachable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(blocked)) as client:
        task = asyncio.create_task(load.run_load(load.LoadConfig(2, 10), client=client))
        await asyncio.wait_for(all_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert cleaned == 2


@pytest.mark.parametrize("users", [0, -1, True, 1.5])
def test_invalid_user_count_is_rejected(users: int) -> None:
    with pytest.raises(ValueError, match="users"):
        load.LoadConfig(users=users)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), -float("inf"), True])
@pytest.mark.parametrize("field_name", ["duration_s", "request_timeout_s"])
def test_invalid_duration_or_timeout_is_rejected(field_name: str, value: float) -> None:
    values = {field_name: value}
    with pytest.raises(ValueError, match="finite and positive"):
        load.LoadConfig(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "url",
    ["localhost:8000", "ftp://host", "https://", "https://secret:password@host",
     "https://host?token=secret", "https://host#secret", "https://host:invalid",
     "https://host/api?", "https://host/api#"],
)
def test_invalid_origins_are_rejected_without_echoing_input(url: str) -> None:
    with pytest.raises(ValueError) as exc:
        load.LoadConfig(api_url=url)
    assert "secret" not in str(exc.value)
    assert url not in str(exc.value)


def test_positive_values_have_no_arbitrary_caps() -> None:
    config = load.LoadConfig(users=1_000_001, duration_s=1e10, request_timeout_s=1e10)
    assert config.users == 1_000_001
    assert config.duration_s == config.request_timeout_s == 1e10


async def test_report_contains_no_raw_content_and_files_are_unique(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, json={"secret": "body-secret"}, headers={"secret": "header-secret"},
        )

    report = await measure(handler)
    first = load.save_report(report, tmp_path)
    second = load.save_report(report, tmp_path)

    assert first != second
    assert first.name.startswith("loadtest_") and first.suffix == ".json"
    assert json.loads(first.read_text()) == report
    serialized = first.read_text()
    assert "secret" not in serialized
    assert "X-User-ID" not in serialized and "admin" not in serialized
    assert load.QUERIES[0] not in serialized


async def test_report_name_collision_does_not_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_datetime = SimpleNamespace(now=lambda tz: SimpleNamespace(strftime=lambda fmt: "fixed"))
    monkeypatch.setattr(load, "datetime", fixed_datetime)
    monkeypatch.setattr(load, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    report = await measure(ok)
    first = load.save_report(report, tmp_path)
    original = first.read_bytes()

    with pytest.raises(FileExistsError):
        load.save_report(report, tmp_path)
    assert first.read_bytes() == original


@pytest.mark.parametrize(("status", "exit_code"), [(200, 0), (302, 1), (503, 1)])
async def test_cli_uses_selected_origin_and_returns_outcome_exit_code(
    status: int, exit_code: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_run = load.run_load
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(status)

    async def offline_run(config: load.LoadConfig) -> load.LoadReport:
        clock = FakeClock()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await original_run(config, client=client, clock=clock, sleep=clock.sleep)

    monkeypatch.setattr(load, "run_load", offline_run)
    result = await load.main([
        "--users", "1", "--duration", "0.5", "--api-url", "https://selected.test",
        "--output-dir", str(tmp_path),
    ])

    assert result == exit_code
    assert seen == ["https://selected.test/retrieve", "https://selected.test/query"]
    [output] = list(tmp_path.glob("loadtest_*.json"))
    assert json.loads(output.read_text())["exit_code"] == exit_code


async def test_cli_invalid_config_does_not_start_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never_run(config: load.LoadConfig) -> load.LoadReport:
        raise AssertionError("configuration must be checked first")

    monkeypatch.setattr(load, "run_load", never_run)
    with pytest.raises(SystemExit) as exc:
        await load.main(["--users", "0"])
    assert exc.value.code == 2


async def test_report_write_failure_returns_nonzero_without_exception_details(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    report = await measure(ok)

    async def offline_run(config: load.LoadConfig) -> load.LoadReport:
        return report

    def fail_save(report: load.LoadReport, directory: Path) -> Path:
        raise OSError("secret filesystem detail")

    monkeypatch.setattr(load, "run_load", offline_run)
    monkeypatch.setattr(load, "save_report", fail_save)

    assert await load.main([]) == 1
    output = capsys.readouterr()
    assert "Report could not be saved" in output.err
    assert "secret" not in output.err


async def test_cli_cancellation_does_not_save_report(monkeypatch: pytest.MonkeyPatch) -> None:
    async def cancelled(config: load.LoadConfig) -> load.LoadReport:
        raise asyncio.CancelledError

    def never_save(report: load.LoadReport, directory: Path) -> Path:
        raise AssertionError("cancelled run must not be saved")

    monkeypatch.setattr(load, "run_load", cancelled)
    monkeypatch.setattr(load, "save_report", never_save)
    with pytest.raises(asyncio.CancelledError):
        await load.main([])
