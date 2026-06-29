#!/usr/bin/env python3
"""RAG Pipeline Load Test — concurrent query benchmarking.

Usage:
    python evaluation/load_test.py                    # Default: 10 users, 30s
    python evaluation/load_test.py --users 50 --duration 60
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
]

stats: dict = {
    "retrieve": {"count": 0, "errors": 0, "times": [], "statuses": []},
    "query": {"count": 0, "errors": 0, "times": [], "statuses": []},
}


async def worker(name: str, duration: float, client: httpx.AsyncClient):
    """Simulate a single user sending queries in a loop."""
    end_time = time.perf_counter() + duration
    q_idx = hash(name) % len(QUERIES)
    while time.perf_counter() < end_time:
        query = QUERIES[q_idx % len(QUERIES)]
        q_idx += 1

        # /retrieve
        t0 = time.perf_counter()
        try:
            r = await client.post(f"{API}/retrieve", json={"query": query, "top_k": 10}, headers=HDR)
            lat = (time.perf_counter() - t0) * 1000
            stats["retrieve"]["times"].append(lat)
            stats["retrieve"]["statuses"].append(r.status_code)
            stats["retrieve"]["count"] += 1
            if r.status_code >= 400:
                stats["retrieve"]["errors"] += 1
        except Exception:
            stats["retrieve"]["errors"] += 1
            stats["retrieve"]["count"] += 1
            stats["retrieve"]["times"].append((time.perf_counter() - t0) * 1000)
            stats["retrieve"]["statuses"].append(0)

        # /query
        t0 = time.perf_counter()
        try:
            r = await client.post(f"{API}/query", json={"query": query, "top_k": 10}, headers=HDR)
            lat = (time.perf_counter() - t0) * 1000
            stats["query"]["times"].append(lat)
            stats["query"]["statuses"].append(r.status_code)
            stats["query"]["count"] += 1
            if r.status_code >= 400:
                stats["query"]["errors"] += 1
        except Exception:
            stats["query"]["errors"] += 1
            stats["query"]["count"] += 1
            stats["query"]["times"].append((time.perf_counter() - t0) * 1000)
            stats["query"]["statuses"].append(0)

        # Small delay between queries
        await asyncio.sleep(1.0)


def pct(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    idx = int(len(s) * p / 100)
    return s[min(idx, len(s) - 1)]


def percentile_text(data: list[float]) -> str:
    return f"p50={pct(data,50):.0f}ms  p95={pct(data,95):.0f}ms  p99={pct(data,99):.0f}ms  avg={sum(data)/max(len(data),1):.0f}ms"


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--users", type=int, default=10)
    p.add_argument("--duration", type=int, default=30)
    p.add_argument("--api-url", default=API)
    args = p.parse_args()

    print(f"🚀 Load Test: {args.users} concurrent users × {args.duration}s")
    print(f"   API: {args.api_url}")
    print(f"   Query pool: {len(QUERIES)} unique questions\n")

    t0 = time.perf_counter()

    async with httpx.AsyncClient(timeout=120, limits=httpx.Limits(max_connections=args.users * 2)) as client:
        workers = [
            worker(f"user-{i}", args.duration, client)
            for i in range(args.users)
        ]
        await asyncio.gather(*workers)

    elapsed = time.perf_counter() - t0

    print(f"\n{'='*60}")
    print(f"LOAD TEST RESULTS ({args.users} users × {args.duration}s)")
    print(f"{'='*60}")

    for ep in ["retrieve", "query"]:
        s = stats[ep]
        total = s["count"]
        errors = s["errors"]
        success = total - errors
        rate = total / elapsed if elapsed > 0 else 0
        err_rate = (errors / total * 100) if total > 0 else 0

        print(f"\n── {ep.upper()} ──")
        print(f"  Total requests:    {total}")
        print(f"  Successful:        {success} ({100 - err_rate:.1f}%)")
        print(f"  Failed:            {errors} ({err_rate:.1f}%)")
        print(f"  Throughput:        {rate:.1f} req/s")
        if s["times"]:
            print(f"  Latency:           {percentile_text(s['times'])}")
            print(f"  Min/Max:           {min(s['times']):.0f}ms / {max(s['times']):.0f}ms")
        # Status code breakdown
        if s["statuses"]:
            from collections import Counter
            sc = Counter(s["statuses"])
            print(f"  Status codes:      {dict(sc)}")

    print(f"\n  Total duration:    {elapsed:.1f}s")
    total_all = stats["retrieve"]["count"] + stats["query"]["count"]
    print(f"  Combined throughput: {total_all / elapsed:.1f} req/s")

    # Save report
    report = {
        "config": {"users": args.users, "duration_s": args.duration},
        "retrieve": {
            "total": stats["retrieve"]["count"],
            "errors": stats["retrieve"]["errors"],
            "throughput_qps": round(stats["retrieve"]["count"] / elapsed, 1) if elapsed else 0,
            "p50_ms": round(pct(stats["retrieve"]["times"], 50), 1),
            "p95_ms": round(pct(stats["retrieve"]["times"], 95), 1),
            "p99_ms": round(pct(stats["retrieve"]["times"], 99), 1),
            "avg_ms": round(sum(stats["retrieve"]["times"]) / max(len(stats["retrieve"]["times"]), 1), 1),
        },
        "query": {
            "total": stats["query"]["count"],
            "errors": stats["query"]["errors"],
            "throughput_qps": round(stats["query"]["count"] / elapsed, 1) if elapsed else 0,
            "p50_ms": round(pct(stats["query"]["times"], 50), 1),
            "p95_ms": round(pct(stats["query"]["times"], 95), 1),
            "p99_ms": round(pct(stats["query"]["times"], 99), 1),
            "avg_ms": round(sum(stats["query"]["times"]) / max(len(stats["query"]["times"]), 1), 1),
        },
        "combined_throughput_qps": round(total_all / elapsed, 1) if elapsed else 0,
    }
    out = Path("evaluation/reports")
    out.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    (out / f"loadtest_{ts}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\n  Report: evaluation/reports/loadtest_{ts}.json")


if __name__ == "__main__":
    asyncio.run(main())
