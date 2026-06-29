#!/usr/bin/env python3
"""RAG Pipeline Benchmark — measure latency, throughput, and quality across configurations.

Usage:
    python evaluation/benchmark_pipeline.py              # Quick benchmark (5 queries, 3 rounds)
    python evaluation/benchmark_pipeline.py --rounds 10  # Extended benchmark
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

API = "http://localhost:8000"
HDR = {
    "Content-Type": "application/json",
    "X-User-ID": "admin", "X-Tenant-ID": "default",
    "X-Roles": "admin,platform_admin",
    "X-Permissions": "document:read,retrieval:query",
}

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


async def benchmark_retrieve(client: httpx.AsyncClient, query: str) -> dict:
    """Measure /retrieve latency."""
    t0 = time.perf_counter()
    r = await client.post(f"{API}/retrieve", json={"query": query, "top_k": 10}, headers=HDR)
    r.raise_for_status()
    lat = (time.perf_counter() - t0) * 1000
    data = (r.json().get("data", {}) or {})
    return {"latency_ms": round(lat, 1), "candidates": len(data.get("candidates", [])), "status": r.status_code}


async def benchmark_query(client: httpx.AsyncClient, query: str) -> dict:
    """Measure /query end-to-end latency."""
    t0 = time.perf_counter()
    r = await client.post(f"{API}/query", json={"query": query, "top_k": 10}, headers=HDR)
    r.raise_for_status()
    lat = (time.perf_counter() - t0) * 1000
    inner = (r.json().get("data", {}) or {})
    answer_len = len(inner.get("answer", "") or "")
    return {"latency_ms": round(lat, 1), "answer_chars": answer_len, "status": r.status_code}


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--queries", type=int, default=5)
    p.add_argument("--api-url", default=API)
    args = p.parse_args()

    queries = BENCHMARK_QUERIES[: args.queries]
    print(f"Benchmarking {len(queries)} queries × {args.rounds} rounds...")
    print(f"API: {args.api_url}\n")

    retrieve_times: list[float] = []
    query_times: list[float] = []

    async with httpx.AsyncClient(timeout=120) as client:
        for rnd in range(args.rounds):
            print(f"--- Round {rnd + 1}/{args.rounds} ---")
            for i, q in enumerate(queries):
                try:
                    ret = await benchmark_retrieve(client, q)
                    retrieve_times.append(ret["latency_ms"])

                    qry = await benchmark_query(client, q)
                    query_times.append(qry["latency_ms"])

                    print(f"  [{i+1}] retrieve={ret['latency_ms']:6.0f}ms  query={qry['latency_ms']:6.0f}ms  "
                          f"({ret['candidates']} chunks, {qry['answer_chars']} chars)  {q[:50]}...")
                except Exception as exc:
                    print(f"  [{i+1}] ERROR: {exc}")

    retrieve_times.sort()
    query_times.sort()
    n = len(retrieve_times)

    def pct(data, p):
        idx = int(len(data) * p / 100)
        return data[min(idx, len(data) - 1)]

    print(f"\n{'='*60}")
    print("RESULTS")
    print(f"{'='*60}")
    print(f"{'Metric':<20} {'p50':>8} {'p95':>8} {'p99':>8} {'avg':>8} {'min':>8} {'max':>8}")
    print("-" * 68)
    print(f"{'/retrieve (ms)':<20} {pct(retrieve_times, 50):8.0f} {pct(retrieve_times, 95):8.0f} "
          f"{pct(retrieve_times, 99):8.0f} {sum(retrieve_times)/n:8.0f} {min(retrieve_times):8.0f} {max(retrieve_times):8.0f}")
    print(f"{'/query (ms)':<20} {pct(query_times, 50):8.0f} {pct(query_times, 95):8.0f} "
          f"{pct(query_times, 99):8.0f} {sum(query_times)/n:8.0f} {min(query_times):8.0f} {max(query_times):8.0f}")
    print(f"\nTotal samples: {n} (per endpoint)")
    print(f"Throughput (sequential): {n / (sum(query_times) / 1000):.1f} queries/sec")

    report = {
        "config": {"rounds": args.rounds, "queries": len(queries)},
        "retrieve": {
            "p50": round(pct(retrieve_times, 50), 1),
            "p95": round(pct(retrieve_times, 95), 1),
            "p99": round(pct(retrieve_times, 99), 1),
            "avg": round(sum(retrieve_times) / n, 1),
            "min": round(min(retrieve_times), 1),
            "max": round(max(retrieve_times), 1),
        },
        "query": {
            "p50": round(pct(query_times, 50), 1),
            "p95": round(pct(query_times, 95), 1),
            "p99": round(pct(query_times, 99), 1),
            "avg": round(sum(query_times) / n, 1),
            "min": round(min(query_times), 1),
            "max": round(max(query_times), 1),
        },
        "throughput_qps": round(n / (sum(query_times) / 1000), 1),
    }
    out = Path("evaluation/reports")
    out.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    (out / f"benchmark_{ts}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nReport: evaluation/reports/benchmark_{ts}.json")


if __name__ == "__main__":
    asyncio.run(main())
