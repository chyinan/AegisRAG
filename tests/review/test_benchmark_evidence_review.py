"""Independent ordinary benchmark regressions; synthetic transport, no services."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from evaluation import benchmark_pipeline as benchmark


class ManualClock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def advance(self, milliseconds: float) -> None:
        self.seconds += milliseconds / 1000.0


def test_nearest_rank_at_exact_percentile_boundaries() -> None:
    samples = benchmark.EndpointSamples(planned=100, attempts=100)
    samples.latencies_ms.extend(float(value) for value in range(100, 0, -1))
    summary = samples.summary()
    assert (summary["p50"], summary["p95"], summary["p99"]) == (50.0, 95.0, 99.0)
    assert summary["avg"] == 50.5
    assert summary["min"] == 1.0
    assert summary["max"] == 100.0


@pytest.mark.asyncio
async def test_mixed_failures_across_rounds_preserve_independent_accounting() -> None:
    clock = ManualClock()
    outcomes: Iterator[tuple[str, float, str]] = iter([
        ("/retrieve", 10, "success"), ("/query", 20, "success"),
        ("/retrieve", 40, "http_failure"),
        ("/retrieve", 10, "success"), ("/query", 30, "invalid_response"),
        ("/retrieve", 5, "timeout"),
        ("/retrieve", 10, "success"), ("/query", 20, "success"),
        ("/retrieve", 10, "success"), ("/query", 50, "http_failure"),
    ])
    sent_queries: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        expected_path, duration, outcome = next(outcomes)
        assert request.url.path == expected_path
        payload = json.loads(request.content)
        sent_queries.append((expected_path, payload["query"]))
        assert payload["top_k"] == 10
        clock.advance(duration)
        if outcome == "http_failure":
            return httpx.Response(503, text="Synthetic response text omitted from report")
        if outcome == "timeout":
            raise httpx.ReadTimeout("Synthetic read failure", request=request)
        if outcome == "invalid_response":
            return httpx.Response(200, json={"data": {"answer": None}})
        data = {"candidates": []} if expected_path == "/retrieve" else {
            "answer": "Synthetic answer text omitted from report",
        }
        return httpx.Response(200, json={"data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await benchmark.run_benchmark(
            client, benchmark.BenchmarkConfig(rounds=2, queries=3), clock=clock,
        )

    assert next(outcomes, None) is None
    assert [query for path, query in sent_queries if path == "/retrieve"] == (
        benchmark.BENCHMARK_QUERIES[:3] * 2
    )
    assert result["status"] == "incomplete"
    assert result["completed_pairs"] == 2
    assert result["elapsed_seconds"] == 0.205
    assert result["observed_sequential_pairs_per_second"] == 9.756098
    for endpoint, expected in {
        "retrieve": (6, 6, 4, 2, 0, 10.0),
        "query": (6, 4, 2, 2, 2, 20.0),
    }.items():
        values = result[endpoint]
        assert tuple(values[field] for field in (
            "planned", "attempts", "successes", "failures", "skipped", "avg",
        )) == expected
        assert values["attempts"] + values["skipped"] == values["planned"]
        assert values["successes"] + values["failures"] == values["attempts"]
        assert sum(values["errors"].values()) == values["failures"]
    assert result["retrieve"]["errors"] == {"http_status_503": 1, "timeout": 1}
    assert result["query"]["errors"] == {"http_status_503": 1, "invalid_response": 1}
    serialized = json.dumps(result, allow_nan=False)
    assert "Synthetic answer text" not in serialized
    assert "Synthetic response text" not in serialized
    assert "Synthetic read failure" not in serialized


@pytest.mark.parametrize("query_response", [
    httpx.Response(200, text="not-json"),
    httpx.Response(200, json={"data": {"answer": 12}}),
    httpx.Response(200, json={"data": {}}),
])
@pytest.mark.asyncio
async def test_bad_query_response_does_not_erase_successful_retrieval(
    query_response: httpx.Response,
) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/retrieve":
            return httpx.Response(200, json={"data": {"candidates": []}})
        return query_response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await benchmark.run_benchmark(
            client, benchmark.BenchmarkConfig(rounds=1, queries=1), clock=ManualClock(),
        )
    assert paths == ["/retrieve", "/query"]
    assert result["status"] == "incomplete"
    assert result["retrieve"]["successes"] == 1
    assert result["retrieve"]["failures"] == 0
    assert result["query"]["attempts"] == result["query"]["failures"] == 1
    assert result["query"]["skipped"] == 0
    assert result["query"]["errors"] == {"invalid_response": 1}
    assert result["query"]["avg"] is None


@pytest.mark.parametrize("failure_type,category", [
    (httpx.ConnectError, "transport_error"),
    (httpx.ReadTimeout, "timeout"),
])
@pytest.mark.asyncio
async def test_transport_failure_is_counted_without_retries(
    failure_type: type[httpx.RequestError], category: str,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise failure_type("Synthetic transport failure", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await benchmark.run_benchmark(
            client, benchmark.BenchmarkConfig(rounds=1, queries=1), clock=ManualClock(),
        )
    assert calls == 1
    assert result["retrieve"]["errors"] == {category: 1}
    assert result["retrieve"]["failures"] == 1
    assert result["query"]["attempts"] == 0
    assert result["query"]["skipped"] == 1


@pytest.mark.parametrize("api_url", [
    "https://benchmark.example.invalid/",
    "http://benchmark.example.invalid:8123",
])
@pytest.mark.asyncio
async def test_normalized_origin_is_used_for_both_endpoints(api_url: str) -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        data = {"candidates": []} if request.url.path == "/retrieve" else {"answer": ""}
        return httpx.Response(200, json={"data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await benchmark.run_benchmark(
            client, benchmark.BenchmarkConfig(api_url=api_url, rounds=1, queries=1),
            clock=ManualClock(),
        )
    assert urls == [api_url.rstrip("/") + path for path in ("/retrieve", "/query")]
    assert result["config"]["api_url"] == api_url.rstrip("/")
    assert result["status"] == "complete"
    assert result["completed_pairs"] == 1
    assert result["elapsed_seconds"] == 0.0
    assert result["observed_sequential_pairs_per_second"] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("arguments", [
    ["--api-url", "benchmark.example.invalid"],
    ["--api-url", "https://benchmark.example.invalid/api"],
    ["--rounds", "0"],
    ["--queries", "11"],
    ["--request-timeout-seconds", "nan"],
    ["--request-timeout-seconds", "inf"],
])
@pytest.mark.asyncio
async def test_invalid_cli_arguments_precede_client_creation_and_report_io(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    def forbidden_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        raise AssertionError("No HTTP client should be constructed")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(benchmark.httpx, "AsyncClient", forbidden_client)
    with pytest.raises(SystemExit) as raised:
        await benchmark.main(arguments)
    assert raised.value.code == 2
    assert not (tmp_path / "evaluation").exists()


@pytest.mark.parametrize("delimiter", ["?", "#", "?#"], ids=[
    "empty-query", "empty-fragment", "empty-query-and-fragment",
])
@pytest.mark.asyncio
async def test_empty_url_delimiters_are_rejected_before_cli_io(
    delimiter: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    api_url = "https://chosen.test" + delimiter
    with pytest.raises(ValueError, match="direct HTTP\\(S\\) API origin"):
        benchmark.BenchmarkConfig(api_url=api_url)

    def forbidden_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        raise AssertionError("No HTTP client should be constructed")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(benchmark.httpx, "AsyncClient", forbidden_client)
    with pytest.raises(SystemExit) as raised:
        await benchmark.main(["--api-url", api_url, "--rounds", "1", "--queries", "1"])
    assert raised.value.code == 2
    assert not (tmp_path / "evaluation").exists()


@pytest.mark.asyncio
async def test_complete_cli_returns_zero_and_writes_schema_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    client_type = httpx.AsyncClient
    clock = ManualClock()

    def handler(request: httpx.Request) -> httpx.Response:
        clock.advance(10)
        data = {"candidates": []} if request.url.path == "/retrieve" else {"answer": ""}
        return httpx.Response(200, json={"data": data})

    def make_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return client_type(*args, **kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(benchmark.httpx, "AsyncClient", make_client)
    monkeypatch.setattr(benchmark.time, "perf_counter", clock)
    assert await benchmark.main([
        "--api-url", "https://benchmark.example.invalid", "--rounds", "1", "--queries", "1",
    ]) == 0
    reports = list((tmp_path / "evaluation/reports").glob("benchmark_*.json"))
    assert len(reports) == 1
    result = json.loads(reports[0].read_text(encoding="utf-8"))
    assert result["schema_version"] == 2
    assert result["status"] == "complete"
    assert result["completed_pairs"] == 1
    assert result["elapsed_seconds"] == 0.02
    assert result["observed_sequential_pairs_per_second"] == 50.0
    assert result["retrieve"]["successes"] == result["query"]["successes"] == 1
    assert "throughput_qps" not in result
    json.dumps(result, allow_nan=False)
