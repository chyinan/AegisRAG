#!/usr/bin/env python3
"""Performance profiling script for AegisRAG retrieval pipeline.

Measures real end-to-end latency for /retrieve and /query endpoints,
plus cProfile-based hotspot analysis of key retrieval modules.

Usage:
    python scripts/profile_retrieval.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pstats
import statistics
import sys
import time
from pathlib import Path

import httpx

# ── Configuration ──────────────────────────────────────────────────────────
API = "http://localhost:8000"
N_REQUESTS = 20
TIMEOUT = 60.0

HDR = {
    "Content-Type": "application/json",
    "X-User-ID": "admin",
    "X-Tenant-ID": "default",
    "X-Roles": "admin,platform_admin",
    "X-Permissions": "document:read,retrieval:query",
}

QUERIES = [
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
    "What is semantic chunking?",
    "Explain the FAISS index structure.",
    "How does re-ranking improve retrieval?",
    "What is Graph RAG?",
    "Explain context window management.",
    "How does embedding caching work?",
    "What is the role of metadata filters?",
    "Explain ACL-based access control in RAG.",
]


def stats_summary(name: str, times: list[float]) -> dict:
    if not times:
        return {"name": name, "count": 0}
    sorted_t = sorted(times)
    return {
        "name": name,
        "count": len(times),
        "mean_ms": round(statistics.mean(times), 2),
        "median_ms": round(statistics.median(times), 2),
        "min_ms": round(sorted_t[0], 2),
        "max_ms": round(sorted_t[-1], 2),
        "p95_ms": round(sorted_t[int(len(sorted_t) * 0.95) - 1 if len(sorted_t) > 1 else 0], 2),
        "p99_ms": round(sorted_t[int(len(sorted_t) * 0.99) - 1 if len(sorted_t) > 1 else 0], 2),
        "stddev_ms": round(statistics.stdev(times) if len(times) > 1 else 0, 2),
    }


async def profile_endpoint(
    endpoint: str,
    client: httpx.AsyncClient,
    times: list[float],
    statuses: list[int],
    result_sizes: list[int],
    error_details: list[str],
    use_payload: bool = False,
) -> None:
    """Send N_REQUESTS requests to an endpoint and record timing data."""
    for i in range(N_REQUESTS):
        query = QUERIES[i % len(QUERIES)]
        payload: dict = {"query": query, "top_k": 10}
        if use_payload:
            payload["stream"] = False

        t0 = time.perf_counter()
        try:
            r = await client.post(
                f"{API}{endpoint}",
                json=payload,
                headers=HDR,
                timeout=TIMEOUT,
            )
            lat = (time.perf_counter() - t0) * 1000
            times.append(lat)
            statuses.append(r.status_code)

            if r.status_code < 400:
                body = r.json()
                data = body.get("data", {})
                candidates = data.get("candidates", [])
                result_sizes.append(len(candidates))
            else:
                result_sizes.append(0)
                error_details.append(f"{endpoint} #{i}: HTTP {r.status_code} - {r.text[:200]}")
        except Exception as exc:
            lat = (time.perf_counter() - t0) * 1000
            times.append(lat)
            statuses.append(0)
            result_sizes.append(0)
            error_details.append(f"{endpoint} #{i}: {exc}")

        # Small gap between requests
        await asyncio.sleep(0.05)


async def main() -> None:
    print("=" * 70)
    print("AegisRAG Retrieval Performance Profiling")
    print(f"Target API: {API}")
    print(f"Requests per endpoint: {N_REQUESTS}")
    print("=" * 70)

    retrieve_times: list[float] = []
    retrieve_statuses: list[int] = []
    retrieve_sizes: list[int] = []
    query_times: list[float] = []
    query_statuses: list[int] = []
    query_sizes: list[int] = []
    error_details: list[str] = []

    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
    async with httpx.AsyncClient(limits=limits) as client:
        # Phase 1: Profile /retrieve
        print("\n[Phase 1] Profiling /retrieve endpoint...")
        await profile_endpoint(
            "/retrieve", client,
            retrieve_times, retrieve_statuses, retrieve_sizes, error_details,
        )
        retrieve_stats = stats_summary("/retrieve", retrieve_times)
        print(f"  Completed {retrieve_stats['count']} requests")
        print(f"  Mean: {retrieve_stats['mean_ms']:.1f}ms, Median: {retrieve_stats['median_ms']:.1f}ms")
        print(f"  P95: {retrieve_stats['p95_ms']:.1f}ms, P99: {retrieve_stats['p99_ms']:.1f}ms")
        print(f"  Errors: {sum(1 for s in retrieve_statuses if s >= 400)}")
        print(f"  Avg result count: {statistics.mean(retrieve_sizes) if retrieve_sizes else 0:.1f}")

        # Phase 2: Profile /query
        print("\n[Phase 2] Profiling /query endpoint...")
        await profile_endpoint(
            "/query", client,
            query_times, query_statuses, query_sizes, error_details,
            use_payload=True,
        )
        query_stats = stats_summary("/query", query_times)
        print(f"  Completed {query_stats['count']} requests")
        print(f"  Mean: {query_stats['mean_ms']:.1f}ms, Median: {query_stats['median_ms']:.1f}ms")
        print(f"  P95: {query_stats['p95_ms']:.1f}ms, P99: {query_stats['p99_ms']:.1f}ms")
        print(f"  Errors: {sum(1 for s in query_statuses if s >= 400)}")
        print(f"  Avg result count: {statistics.mean(query_sizes) if query_sizes else 0:.1f}")

    # ── Print Summary Report ─────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("PROFILING SUMMARY")
    print("=" * 70)

    report = {
        "metadata": {
            "target": API,
            "requests_per_endpoint": N_REQUESTS,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "endpoints": {
            "/retrieve": retrieve_stats,
            "/query": query_stats,
        },
        "analysis": {
            "retrieve_result_sizes": retrieve_sizes,
            "query_result_sizes": query_sizes,
            "retrieve_statuses": retrieve_statuses,
            "query_statuses": query_statuses,
        },
        "errors": error_details,
    }

    print(json.dumps(report["endpoints"], indent=2, ensure_ascii=False))

    if retrieve_times and query_times:
        overhead_pct = (
            (query_stats["median_ms"] - retrieve_stats["median_ms"])
            / retrieve_stats["median_ms"] * 100
        )
        print(f"\nLLM generation overhead: {overhead_pct:.1f}% of retrieval time")

    # Save report
    report_path = Path("D:/Programs/RAG-Local-System/docs/operations/profiling_results.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nReport saved to: {report_path}")

    # Also dump as simple format for the case study
    stats_path = Path("D:/Programs/RAG-Local-System/docs/operations/profiling_stats.txt")
    with open(stats_path, "w", encoding="utf-8") as f:
        f.write("AegisRAG Profiling Statistics\n")
        f.write("=" * 60 + "\n\n")
        for ep_name, ep_stats in [("/retrieve", retrieve_stats), ("/query", query_stats)]:
            f.write(f"Endpoint: {ep_name}\n")
            for k, v in ep_stats.items():
                f.write(f"  {k}: {v}\n")
            f.write("\n")
        f.write(f"Time series (retrieve): {retrieve_times}\n")
        f.write(f"Time series (query): {query_times}\n")
    print(f"Stats saved to: {stats_path}")


if __name__ == "__main__":
    asyncio.run(main())
