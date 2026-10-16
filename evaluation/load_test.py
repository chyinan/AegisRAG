#!/usr/bin/env python3
"""Closed-loop HTTP measurements; see docs/operations/LOAD_MEASUREMENT.md.

``--duration`` is a pair-dispatch window, not a hard runtime limit. Existing
retrieve/query pairs and their one-second think time drain after that window.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypedDict
from uuid import uuid4

import httpx

API = "http://localhost:8000"
HDR = {
    "Content-Type": "application/json",
    "X-User-ID": "admin", "X-Tenant-ID": "default",
    "X-Roles": "admin,platform_admin",
    "X-Permissions": "document:read,retrieval:query",
}

QUERIES = (
    "What is Retrieval-Augmented Generation?",
    "How does multi-tenant access control work?",
    "Explain chunking strategies for RAG.",
    "Describe a hybrid retrieval pipeline.",
    "How to ensure answer faithfulness?",
    "What is the role of vector databases?",
    "Explain dense vs sparse retrieval.",
    "What is Reciprocal Rank Fusion?",
    "How does HyDE query rewriting work?",
    "What are RAGAS evaluation metrics?",
    "Explain prompt injection defenses in RAG.",
    "How does citation extraction work?",
)
THINK_TIME_S = 1.0
PERCENTILE_METHOD = "sorted_zero_based_floor_n_p_over_100_clamped"
RunStatus = Literal["completed", "partial", "failed", "empty"]


@dataclass(frozen=True)
class LoadConfig:
    users: int = 10
    duration_s: float = 30.0
    api_url: str = API
    request_timeout_s: float = 120.0

    def __post_init__(self) -> None:
        if isinstance(self.users, bool) or not isinstance(self.users, int) or self.users <= 0:
            raise ValueError("users must be a positive integer")
        for name, value in (
            ("duration", self.duration_s), ("request timeout", self.request_timeout_s),
        ):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        try:
            url = httpx.URL(self.api_url)
        except (httpx.InvalidURL, TypeError, ValueError):
            raise ValueError("api-url must be an absolute HTTP(S) base URL") from None
        if (
            url.scheme not in {"http", "https"} or not url.host
            or url.userinfo or "?" in str(url) or "#" in str(url)
        ):
            raise ValueError(
                "api-url requires HTTP(S), a host, and no credentials, query or fragment"
            )
        object.__setattr__(self, "api_url", str(url).rstrip("/"))


@dataclass
class EndpointStats:
    """One run's terminal outcomes; never response content or exception text."""

    times_ms: list[float] = field(default_factory=list)
    successes: int = 0
    status_counts: Counter[str] = field(default_factory=Counter)
    error_counts: Counter[str] = field(default_factory=Counter)

    def record(self, elapsed_ms: float, status_code: int | None, error: str | None) -> None:
        self.times_ms.append(elapsed_ms)
        if status_code is not None:
            self.status_counts[str(status_code)] += 1
        if error is None:
            self.successes += 1
        else:
            self.error_counts[error] += 1


class EndpointReport(TypedDict):
    total: int
    successful: int
    errors: int
    throughput_qps: float
    successful_throughput_qps: float
    status_counts: dict[str, int]
    error_counts: dict[str, int]
    latency_population: str
    latency_sample_count: int
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    avg_ms: float | None
    min_ms: float | None
    max_ms: float | None


class ConfigReport(TypedDict):
    users: int
    duration_s: float
    api_url: str
    request_timeout_s: float
    think_time_s: float
    workload_selection: str


class LoadReport(TypedDict):
    schema_version: int
    status: RunStatus
    exit_code: int
    config: ConfigReport
    dispatch_window_s: float
    actual_elapsed_s: float
    drain_s: float
    percentile_method: str
    retrieve: EndpointReport
    query: EndpointReport
    combined_throughput_qps: float
    combined_successful_throughput_qps: float


def pct(data: list[float], p: float) -> float | None:
    """Legacy index floor(n*p/100), clamped to n-1; no interpolation."""
    if not math.isfinite(p) or not 0 <= p <= 100:
        raise ValueError("percentile must be finite and between 0 and 100")
    if not data:
        return None
    ordered = sorted(data)
    return ordered[min(int(len(ordered) * p / 100), len(ordered) - 1)]


def _endpoint_report(stats: EndpointStats, elapsed: float) -> EndpointReport:
    total = len(stats.times_ms)
    return {
        "total": total,
        "successful": stats.successes,
        "errors": sum(stats.error_counts.values()),
        "throughput_qps": total / elapsed if elapsed > 0 else 0.0,
        "successful_throughput_qps": stats.successes / elapsed if elapsed > 0 else 0.0,
        "status_counts": dict(sorted(stats.status_counts.items())),
        "error_counts": dict(sorted(stats.error_counts.items())),
        "latency_population": "all_terminal_attempts_including_failures",
        "latency_sample_count": total,
        "p50_ms": pct(stats.times_ms, 50),
        "p95_ms": pct(stats.times_ms, 95),
        "p99_ms": pct(stats.times_ms, 99),
        "avg_ms": sum(stats.times_ms) / total if total else None,
        "min_ms": min(stats.times_ms) if total else None,
        "max_ms": max(stats.times_ms) if total else None,
    }


async def _request(
    client: httpx.AsyncClient,
    config: LoadConfig,
    endpoint: str,
    query: str,
    stats: EndpointStats,
    clock: Callable[[], float],
) -> None:
    started = clock()
    status_code = None
    error = None
    try:
        # HTTPX retains per-phase limits; this additionally bounds cooperative
        # elapsed request time. Neither can terminate blocking/resistant code.
        async with asyncio.timeout(config.request_timeout_s):
            response = await client.post(
                f"{config.api_url}/{endpoint}",
                json={"query": query, "top_k": 10},
                headers=HDR,
                timeout=config.request_timeout_s,
                follow_redirects=False,
            )
        status_code = response.status_code
        if not 200 <= status_code < 300:
            error = "http_status"
    except TimeoutError:
        error = "request_deadline"
    except httpx.TimeoutException:
        error = "httpx_timeout"
    except httpx.HTTPError:
        error = "transport_error"
    except Exception:
        # Account for a terminal unexpected client failure, without logging its
        # potentially sensitive message. Cancellation (BaseException) propagates.
        error = "unexpected_error"
    stats.record((clock() - started) * 1000, status_code, error)


async def worker(
    index: int,
    deadline: float,
    client: httpx.AsyncClient,
    config: LoadConfig,
    stats: dict[str, EndpointStats],
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
) -> None:
    """Finish each started pair even if retrieve fails or the window expires."""
    query_index = index % len(QUERIES)
    while clock() < deadline:
        query = QUERIES[query_index % len(QUERIES)]
        query_index += 1
        await _request(client, config, "retrieve", query, stats["retrieve"], clock)
        await _request(client, config, "query", query, stats["query"], clock)
        await sleep(THINK_TIME_S)


async def _measure(
    config: LoadConfig,
    client: httpx.AsyncClient,
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
) -> LoadReport:
    stats = {"retrieve": EndpointStats(), "query": EndpointStats()}
    started = clock()
    deadline = started + config.duration_s
    tasks: list[asyncio.Task[None]] = []
    try:
        for index in range(config.users):
            pending = worker(index, deadline, client, config, stats, clock, sleep)
            try:
                task = asyncio.create_task(pending)
            except BaseException:
                pending.close()
                raise
            tasks.append(task)
        await asyncio.gather(*tasks)
    except BaseException:
        # Also propagate a worker's cancellation, rather than treating its
        # missing outcomes as a complete run. Always drain sibling cleanup.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    elapsed = max(0.0, clock() - started)
    retrieve = _endpoint_report(stats["retrieve"], elapsed)
    query = _endpoint_report(stats["query"], elapsed)
    total = retrieve["total"] + query["total"]
    successful = retrieve["successful"] + query["successful"]
    status: RunStatus = "completed"
    if total == 0:
        status = "empty"
    elif successful == 0:
        status = "failed"
    elif successful < total:
        status = "partial"
    return {
        "schema_version": 2,
        "status": status,
        "exit_code": 0 if status == "completed" else 1,
        "config": {
            "users": config.users,
            "duration_s": config.duration_s,
            "api_url": config.api_url,
            "request_timeout_s": config.request_timeout_s,
            "think_time_s": THINK_TIME_S,
            "workload_selection": "worker_index_round_robin_v1",
        },
        "dispatch_window_s": config.duration_s,
        "actual_elapsed_s": elapsed,
        "drain_s": max(0.0, elapsed - config.duration_s),
        "percentile_method": PERCENTILE_METHOD,
        "retrieve": retrieve,
        "query": query,
        "combined_throughput_qps": total / elapsed if elapsed > 0 else 0.0,
        "combined_successful_throughput_qps": successful / elapsed if elapsed > 0 else 0.0,
    }


async def run_load(
    config: LoadConfig,
    *,
    client: httpx.AsyncClient | None = None,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> LoadReport:
    """Run once; injectable client/clock permit offline contract tests."""
    clock = clock or time.perf_counter
    sleep = sleep or asyncio.sleep
    if client is not None:
        return await _measure(config, client, clock, sleep)
    async with httpx.AsyncClient(
        timeout=config.request_timeout_s,
        limits=httpx.Limits(max_connections=config.users * 2),
        follow_redirects=False,
    ) as owned_client:
        return await _measure(config, owned_client, clock, sleep)


def save_report(report: LoadReport, directory: Path) -> Path:
    """Exclusive creation prevents even simultaneous runs from overwriting."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")
    path = directory / f"loadtest_{stamp}_{uuid4().hex}.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    return path


def _print_report(report: LoadReport) -> None:
    print(f"Measurement status: {report['status']}")
    print(f"API: {report['config']['api_url']}")
    print(f"Pair dispatch window: {report['dispatch_window_s']:.3f}s")
    print(f"Actual elapsed (including drain/think time): {report['actual_elapsed_s']:.3f}s")
    print(f"Drain beyond dispatch window: {report['drain_s']:.3f}s")
    print(f"Latency: all terminal attempts; percentile method: {report['percentile_method']}")
    for endpoint, result in (("retrieve", report["retrieve"]), ("query", report["query"])):
        print(
            f"{endpoint}: total={result['total']} successful={result['successful']} "
            f"errors={result['errors']} throughput={result['throughput_qps']:.3f} req/s"
        )
        print(f"  HTTP statuses: {result['status_counts']}; errors: {result['error_counts']}")
        print(
            f"  Latency ms: p50={result['p50_ms']} p95={result['p95_ms']} "
            f"p99={result['p99_ms']} avg={result['avg_ms']}"
        )


async def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=10)
    parser.add_argument("--duration", type=float, default=30)
    parser.add_argument("--api-url", default=API)
    parser.add_argument("--request-timeout", type=float, default=120)
    parser.add_argument("--output-dir", type=Path, default=Path("evaluation/reports"))
    args = parser.parse_args(argv)
    try:
        config = LoadConfig(args.users, args.duration, args.api_url, args.request_timeout)
    except ValueError as exc:
        parser.error(str(exc))
    report = await run_load(config)
    _print_report(report)
    try:
        path = save_report(report, args.output_dir)
    except (OSError, ValueError):
        print("Report could not be saved", file=sys.stderr)
        return 1
    print(f"Report: {path}")
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
