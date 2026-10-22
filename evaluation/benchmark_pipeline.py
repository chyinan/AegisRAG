#!/usr/bin/env python3
"""Measure sequential client-observed latency on an approved synthetic target.

This legacy operator tool uses development identity headers. It is not an
answer-quality evaluator, authenticated deployment acceptance, or a load test.
No retry is performed. A retrieval failure skips that pair's generation request.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

API = "http://localhost:8000"
# Retained invocation contract: only use an explicitly approved development
# deployment. This client does not enable development authentication on a server.
HDR = {
    "Content-Type": "application/json",
    "X-User-ID": "admin", "X-Tenant-ID": "default",
    "X-Roles": "admin,platform_admin",
    "X-Permissions": "document:read,retrieval:query",
}
MAX_ROUNDS = 100
REQUEST_TIMEOUT_SECONDS = 120.0
BENCHMARK_QUERIES = [
    "What is Retrieval-Augmented Generation?",
    "How does multi-tenant access control work?",
    "Explain chunking strategies for RAG systems.",
    "Describe a hybrid retrieval pipeline.",
    "How to ensure answer faithfulness in RAG?",
    "What is the role of vector databases in RAG?",
    "Explain the difference between dense and sparse retrieval.",
    "What is Reciprocal Rank Fusion?",
    "How does HyDE query rewriting improve retrieval?",
    "What are RAGAS evaluation metrics?",
]
Clock = Callable[[], float]


@dataclass(frozen=True)
class BenchmarkConfig:
    api_url: str = API
    rounds: int = 3
    queries: int = 5
    request_timeout_seconds: float = REQUEST_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if type(self.rounds) is not int or not 1 <= self.rounds <= MAX_ROUNDS:
            raise ValueError("rounds must be an integer between 1 and 100")
        if type(self.queries) is not int or not 1 <= self.queries <= len(BENCHMARK_QUERIES):
            raise ValueError("queries must be an integer between 1 and 10")
        if not math.isfinite(self.request_timeout_seconds) or not (
            0 < self.request_timeout_seconds <= REQUEST_TIMEOUT_SECONDS
        ):
            raise ValueError("request timeout must be finite and in (0, 120]")
        try:
            url = httpx.URL(self.api_url)
        except (TypeError, httpx.InvalidURL):
            raise ValueError("api-url must be a direct HTTP(S) API origin") from None
        normalized_url = str(url)
        if (
            url.scheme not in {"http", "https"} or not url.host
            or url.userinfo or "?" in normalized_url or "#" in normalized_url
            or url.path not in {"", "/"}
        ):
            raise ValueError("api-url must be a direct HTTP(S) API origin")
        object.__setattr__(self, "api_url", normalized_url.rstrip("/"))


@dataclass
class EndpointSamples:
    planned: int
    attempts: int = 0
    skipped: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    errors: Counter[str] = field(default_factory=Counter)

    def summary(self) -> dict[str, Any]:
        ordered = sorted(self.latencies_ms)
        count = len(ordered)

        def percentile(value: int) -> float | None:
            # Nearest rank, explicitly defined for small synthetic sample sets.
            return round(ordered[math.ceil(count * value / 100) - 1], 3) if count else None

        return {
            "planned": self.planned,
            "attempts": self.attempts,
            "successes": count,
            "failures": self.attempts - count,
            "skipped": self.skipped,
            "errors": dict(sorted(self.errors.items())),
            "p50": percentile(50), "p95": percentile(95), "p99": percentile(99),
            "avg": round(sum(ordered) / count, 3) if count else None,
            "min": round(ordered[0], 3) if count else None,
            "max": round(ordered[-1], 3) if count else None,
        }


async def _post(
    client: httpx.AsyncClient, query: str, *, endpoint: str, api_url: str,
    request_timeout_seconds: float, clock: Clock,
) -> tuple[dict[str, Any], float, int]:
    started = clock()
    # HTTPX's per-I/O timeouts alone do not bound an entire response read.
    async with asyncio.timeout(request_timeout_seconds):
        response = await client.post(
            api_url.rstrip("/") + endpoint,
            json={"query": query, "top_k": 10}, headers=HDR,
        )
        response.raise_for_status()
        envelope = response.json()
        if not isinstance(envelope, dict) or not isinstance(envelope.get("data"), dict):
            raise ValueError("invalid response envelope")
        data = envelope["data"]
    return data, (clock() - started) * 1000, response.status_code


async def benchmark_retrieve(
    client: httpx.AsyncClient, query: str, *, api_url: str = API,
    request_timeout_seconds: float = REQUEST_TIMEOUT_SECONDS, clock: Clock | None = None,
) -> dict[str, Any]:
    data, latency, status = await _post(
        client, query, endpoint="/retrieve", api_url=api_url,
        request_timeout_seconds=request_timeout_seconds, clock=clock or time.perf_counter,
    )
    if not isinstance(data.get("candidates"), list):
        raise ValueError("invalid retrieval response")
    return {"latency_ms": latency, "candidates": len(data["candidates"]), "status": status}


async def benchmark_query(
    client: httpx.AsyncClient, query: str, *, api_url: str = API,
    request_timeout_seconds: float = REQUEST_TIMEOUT_SECONDS, clock: Clock | None = None,
) -> dict[str, Any]:
    data, latency, status = await _post(
        client, query, endpoint="/query", api_url=api_url,
        request_timeout_seconds=request_timeout_seconds, clock=clock or time.perf_counter,
    )
    if not isinstance(data.get("answer"), str):
        raise ValueError("invalid query response")
    return {"latency_ms": latency, "answer_chars": len(data["answer"]), "status": status}


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return "http_status_" + str(exc.response.status_code)
    if isinstance(exc, httpx.RequestError):
        return "transport_error"
    if isinstance(exc, (ValueError, TypeError)):
        return "invalid_response"
    return "unexpected_error"


async def run_benchmark(
    client: httpx.AsyncClient, config: BenchmarkConfig, *, clock: Clock | None = None,
) -> dict[str, Any]:
    now = clock or time.perf_counter
    started = now()
    planned = config.rounds * config.queries
    retrieval, generation = EndpointSamples(planned), EndpointSamples(planned)
    completed_pairs = 0
    for _round in range(config.rounds):
        for query in BENCHMARK_QUERIES[:config.queries]:
            retrieval.attempts += 1
            try:
                retrieved = await benchmark_retrieve(
                    client, query, api_url=config.api_url,
                    request_timeout_seconds=config.request_timeout_seconds, clock=now,
                )
            except Exception as exc:
                retrieval.errors[_failure_code(exc)] += 1
                # Preserve the legacy request sequence: a failed retrieval never
                # silently adds a generation call or retry for that query pair.
                generation.skipped += 1
                continue
            retrieval.latencies_ms.append(retrieved["latency_ms"])
            generation.attempts += 1
            try:
                generated = await benchmark_query(
                    client, query, api_url=config.api_url,
                    request_timeout_seconds=config.request_timeout_seconds, clock=now,
                )
            except Exception as exc:
                generation.errors[_failure_code(exc)] += 1
                continue
            generation.latencies_ms.append(generated["latency_ms"])
            completed_pairs += 1
    elapsed = max(0.0, now() - started)
    return {
        "schema_version": 2,
        "status": "complete" if completed_pairs == planned else "incomplete",
        "config": {
            "api_url": config.api_url, "rounds": config.rounds, "queries": config.queries,
            "request_timeout_seconds": config.request_timeout_seconds,
            "execution_mode": "sequential", "authentication_mode": "legacy_development_headers",
        },
        "latency_unit": "milliseconds",
        "percentile_method": "nearest_rank",
        "latency_scope": "successful client requests only, including response decoding",
        "retrieve": retrieval.summary(), "query": generation.summary(),
        "completed_pairs": completed_pairs,
        "elapsed_seconds": round(elapsed, 6),
        "observed_sequential_pairs_per_second": (
            round(completed_pairs / elapsed, 6) if elapsed > 0 else None
        ),
        "limitations": [
            "Sequential completion rate is not concurrent service throughput or capacity.",
            "HTTP/response-shape success does not establish answer quality or groundedness.",
            "Successful-request latencies exclude failed and skipped requests; inspect counts.",
            "This legacy tool uses development identity headers, not production login.",
            "No provider configuration, warmup state, load, or deployment SLO is established.",
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--queries", type=int, default=5)
    parser.add_argument("--api-url", default=API)
    parser.add_argument("--request-timeout-seconds", type=float, default=REQUEST_TIMEOUT_SECONDS)
    return parser


async def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        config = BenchmarkConfig(
            api_url=args.api_url, rounds=args.rounds, queries=args.queries,
            request_timeout_seconds=args.request_timeout_seconds,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print("Sequential synthetic benchmark; development identity headers are in use.")
    print("Only run against a target and server-side providers you are authorized to exercise.")
    async with httpx.AsyncClient(timeout=config.request_timeout_seconds) as client:
        result = await run_benchmark(client, config)
    output_dir = Path("evaluation/reports")
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")
    output_path = output_dir / f"benchmark_{timestamp}.json"
    with output_path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    for endpoint in ("retrieve", "query"):
        values = result[endpoint]
        print(f"/{endpoint}: {values['successes']}/{values['planned']} successful, "
              f"{values['failures']} failed, {values['skipped']} skipped; "
              f"mean={values['avg']} ms, p95={values['p95']} ms")
    print(f"Status: {result['status']}; report: {output_path}")
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
