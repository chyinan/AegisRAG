#!/usr/bin/env python3
"""A/B Retrieval Experiment Framework — compare different retrieval configurations.

Usage:
    python evaluation/ab_retrieval.py                              # default: built-in 12 queries, 1 repeat
    python evaluation/ab_retrieval.py --repeat 3                   # repeat each query 3 times
    python evaluation/ab_retrieval.py --queries my_queries.txt     # custom query list
    python evaluation/ab_retrieval.py --api-url http://localhost:8000
    python evaluation/ab_retrieval.py --configs A B                # only run config A and B
    python evaluation/ab_retrieval.py --top-k 10                   # override default top_k

Configurations (3+ built-in):
    Config A:  dense-only — pure vector retrieval       (POST /retrieve, score_threshold=0.0)
    Config B:  hybrid    — dense + sparse + RRF          (POST /retrieve, default params)
    Config C:  full      — dense + sparse + RRF + rerank (POST /query, full RAG pipeline)

Metrics collected:
    - latency_ms:        end-to-end response time per query
    - result_count:      number of retrieved candidates
    - rerank_scores:     max/min/mean scores from candidates
    - hit_rate:          optional, if ground-truth chunk_ids are provided
    - status_code:       HTTP status for each request

Output:
    - Markdown comparison table (stdout + .md file)
    - JSON report saved to evaluation/reports/ab_YYYYMMDD_HHMMSS.json
    - Wilcoxon signed-rank test for statistical significance (if scipy available, n>=20)

Requirements:
    pip install httpx
    pip install scipy   # optional, for significance test
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# ── Configuration ─────────────────────────────────────────────────────────────

DEFAULT_API = "http://localhost:8000"

DEFAULT_HEADERS: dict[str, str] = {
    "Content-Type": "application/json",
    "X-User-ID": "admin",
    "X-Tenant-ID": "default",
    "X-Roles": "admin,platform_admin",
    "X-Permissions": "document:read,retrieval:query",
}

# 12 default test queries (from load_test.py + extras)
DEFAULT_QUERIES: list[str] = [
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


# ── Retrieval Config Definitions ──────────────────────────────────────────────

@dataclass
class RetrievalConfig:
    """One retrieval configuration to benchmark."""

    name: str
    description: str
    endpoint: str  # "/retrieve" or "/query"
    params: dict[str, Any] = field(default_factory=dict)
    # params can override: top_k, score_threshold, metadata_filter, answer_style, etc.

    @classmethod
    def default_configs(cls) -> list[RetrievalConfig]:
        """Return the built-in 3+ configs for A/B comparison."""
        return [
            cls(
                name="A_dense_only",
                description="Dense-only: pure vector retrieval (score_threshold=0.0)",
                endpoint="/retrieve",
                params={"score_threshold": 0.0},
            ),
            cls(
                name="B_hybrid",
                description="Hybrid: dense + sparse + RRF (default /retrieve)",
                endpoint="/retrieve",
                params={},
            ),
            cls(
                name="C_full_pipeline",
                description="Full: dense + sparse + RRF + rerank (full RAG /query)",
                endpoint="/query",
                params={},
            ),
        ]


# ── Data Models ────────────────────────────────────────────────────────────────


@dataclass
class QueryResult:
    """Single query execution result."""

    query: str
    latency_ms: float
    status_code: int
    result_count: int
    scores: list[float]  # candidate scores
    retrieval_methods: list[str]  # per-candidate retrieval method
    candidates: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    @property
    def max_score(self) -> float | None:
        return max(self.scores) if self.scores else None

    @property
    def mean_score(self) -> float:
        return statistics.mean(self.scores) if self.scores else 0.0

    @property
    def min_score(self) -> float | None:
        return min(self.scores) if self.scores else None

    @property
    def dominant_method(self) -> str:
        """Most common retrieval method among candidates."""
        if not self.retrieval_methods:
            return "none"
        from collections import Counter

        return Counter(self.retrieval_methods).most_common(1)[0][0]


@dataclass
class ConfigRunResult:
    """Aggregated results for one config across all queries."""

    config: RetrievalConfig
    results: list[QueryResult] = field(default_factory=list)

    @property
    def num_queries(self) -> int:
        return len(self.results)

    @property
    def num_success(self) -> int:
        return sum(1 for r in self.results if r.error is None)

    @property
    def num_errors(self) -> int:
        return sum(1 for r in self.results if r.error is not None)

    @property
    def latencies(self) -> list[float]:
        return [r.latency_ms for r in self.results if r.error is None]

    @property
    def result_counts(self) -> list[int]:
        return [r.result_count for r in self.results if r.error is None]

    @property
    def mean_scores(self) -> list[float]:
        return [r.mean_score for r in self.results if r.error is None]

    @property
    def max_scores(self) -> list[float]:
        return [s for r in self.results if (s := r.max_score) is not None]

    # ── Aggregate stats ──

    def pct(self, data: list[float], p: float) -> float:
        if not data:
            return 0.0
        s = sorted(data)
        idx = int(len(s) * p / 100)
        return s[min(idx, len(s) - 1)]

    @property
    def avg_latency_ms(self) -> float:
        lats = self.latencies
        return statistics.mean(lats) if lats else 0.0

    @property
    def p50_latency_ms(self) -> float:
        return self.pct(self.latencies, 50)

    @property
    def p95_latency_ms(self) -> float:
        return self.pct(self.latencies, 95)

    @property
    def p99_latency_ms(self) -> float:
        return self.pct(self.latencies, 99)

    @property
    def min_latency_ms(self) -> float:
        lats = self.latencies
        return min(lats) if lats else 0.0

    @property
    def max_latency_ms(self) -> float:
        lats = self.latencies
        return max(lats) if lats else 0.0

    @property
    def avg_result_count(self) -> float:
        counts = self.result_counts
        return statistics.mean(counts) if counts else 0.0

    @property
    def avg_score(self) -> float:
        scores = self.mean_scores
        return statistics.mean(scores) if scores else 0.0

    @property
    def avg_max_score(self) -> float:
        scores = self.max_scores
        return statistics.mean(scores) if scores else 0.0

    @property
    def error_rate(self) -> float:
        if self.num_queries == 0:
            return 0.0
        return self.num_errors / self.num_queries

    @property
    def dominant_method_summary(self) -> str:
        """Most common retrieval method across all queries."""
        if not self.results:
            return "none"
        from collections import Counter

        all_methods: list[str] = []
        for r in self.results:
            all_methods.extend(r.retrieval_methods)
        if not all_methods:
            return "none"
        return Counter(all_methods).most_common(1)[0][0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": self.config.name,
            "description": self.config.description,
            "num_queries": self.num_queries,
            "num_success": self.num_success,
            "num_errors": self.num_errors,
            "error_rate": round(self.error_rate, 4),
            "latency_ms": {
                "avg": round(self.avg_latency_ms, 1),
                "p50": round(self.p50_latency_ms, 1),
                "p95": round(self.p95_latency_ms, 1),
                "p99": round(self.p99_latency_ms, 1),
                "min": round(self.min_latency_ms, 1),
                "max": round(self.max_latency_ms, 1),
            },
            "result_count": {
                "avg": round(self.avg_result_count, 1),
                "min": min(self.result_counts) if self.result_counts else 0,
                "max": max(self.result_counts) if self.result_counts else 0,
            },
            "scores": {
                "avg_mean_score": round(self.avg_score, 4),
                "avg_max_score": round(self.avg_max_score, 4),
            },
            "dominant_retrieval_method": self.dominant_method_summary,
            "per_query": [
                {
                    "query": r.query[:100],
                    "latency_ms": round(r.latency_ms, 1),
                    "status_code": r.status_code,
                    "result_count": r.result_count,
                    "max_score": round(r.max_score, 4) if r.max_score is not None else None,
                    "mean_score": round(r.mean_score, 4),
                    "dominant_method": r.dominant_method,
                    "error": r.error,
                }
                for r in self.results
            ],
        }


# ── API Client ────────────────────────────────────────────────────────────────


async def run_single_query(
    client: httpx.AsyncClient,
    query: str,
    config: RetrievalConfig,
    top_k: int,
    api_url: str,
    headers: dict[str, str],
) -> QueryResult:
    """Execute one query against one config and return results."""
    t0 = time.perf_counter()
    params = {**config.params}
    if "top_k" not in params:
        params["top_k"] = top_k
    if "query" not in params:
        params["query"] = query

    try:
        resp = await client.post(
            f"{api_url}{config.endpoint}",
            json=params,
            headers=headers,
            timeout=120,
        )
        latency = (time.perf_counter() - t0) * 1000
        body = resp.json()
        inner = body.get("data", body)

        if config.endpoint == "/query":
            # /query returns answer + citations
            candidates_raw = inner.get("citations", [])
        else:
            # /retrieve returns candidates
            candidates_raw = inner.get("candidates", [])

        result_count = len(candidates_raw)
        scores: list[float] = [
            float(c.get("score", 0)) for c in candidates_raw if c.get("score") is not None
        ]
        methods: list[str] = [
            str(c.get("retrieval_method", "unknown")) for c in candidates_raw
        ]

        return QueryResult(
            query=query,
            latency_ms=latency,
            status_code=resp.status_code,
            result_count=result_count,
            scores=scores,
            retrieval_methods=methods,
            candidates=candidates_raw[:20],  # keep first 20 for report
        )
    except httpx.HTTPStatusError as exc:
        latency = (time.perf_counter() - t0) * 1000
        return QueryResult(
            query=query,
            latency_ms=latency,
            status_code=exc.response.status_code,
            result_count=0,
            scores=[],
            retrieval_methods=[],
            error=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
        )
    except Exception as exc:
        latency = (time.perf_counter() - t0) * 1000
        return QueryResult(
            query=query,
            latency_ms=latency,
            status_code=0,
            result_count=0,
            scores=[],
            retrieval_methods=[],
            error=str(exc)[:500],
        )


async def run_config(
    client: httpx.AsyncClient,
    config: RetrievalConfig,
    queries: list[str],
    top_k: int,
    api_url: str,
    headers: dict[str, str],
) -> ConfigRunResult:
    """Run all queries (with repeats) through one config."""
    result = ConfigRunResult(config=config)

    for i, query in enumerate(queries):
        qr = await run_single_query(client, query, config, top_k, api_url, headers)
        result.results.append(qr)

        if qr.error:
            print(f"  [{config.name}] q{i + 1:02d} ERROR: {qr.error}")
        else:
            print(
                f"  [{config.name}] q{i + 1:02d} "
                f"latency={qr.latency_ms:.0f}ms "
                f"count={qr.result_count} "
                f"max_score={qr.max_score or 0:.3f} "
                f"method={qr.dominant_method}"
            )

    return result


# ── Statistical Significance ──────────────────────────────────────────────────


def wilcoxon_test(
    results_a: list[float],
    results_b: list[float],
    label_a: str,
    label_b: str,
) -> dict[str, Any]:
    """Run Wilcoxon signed-rank test if scipy is available and n>=20."""
    try:
        from scipy.stats import wilcoxon  # type: ignore[import-untyped]
    except ImportError:
        return {
            "test": "Wilcoxon signed-rank",
            "status": "scipy_not_available",
            "note": "scipy not available, skip significance test",
        }

    # Trim to equal lengths (pairwise comparison)
    min_len = min(len(results_a), len(results_b))
    if min_len < 20:
        return {
            "test": "Wilcoxon signed-rank",
            "status": "insufficient_samples",
            "note": f"Need >=20 paired samples for reliable test, got {min_len}",
            "n": min_len,
        }

    a_paired = results_a[:min_len]
    b_paired = results_b[:min_len]

    try:
        stat, p_value = wilcoxon(a_paired, b_paired, zero_method="zsplit")
    except Exception as exc:
        return {
            "test": "Wilcoxon signed-rank",
            "status": "error",
            "note": str(exc),
        }

    significant = bool(p_value < 0.05)
    p_val = float(p_value)
    w_stat = float(stat)
    return {
        "test": "Wilcoxon signed-rank",
        "comparison": f"{label_a} vs {label_b}",
        "n": min_len,
        "statistic": round(w_stat, 4),
        "p_value": round(p_val, 6),
        "significant_at_0.05": significant,
        "interpretation": (
            f"Statistically significant difference (p={p_val:.4f})"
            if significant
            else f"No statistically significant difference (p={p_val:.4f})"
        ),
    }


# ── Report Generation ─────────────────────────────────────────────────────────


def generate_markdown_table(results: list[ConfigRunResult], comparisons: list[dict] | None = None) -> str:
    """Generate Markdown comparison table."""
    lines: list[str] = []
    lines.append("# A/B Retrieval Experiment Results")
    lines.append("")
    lines.append(f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Total queries per config:** {results[0].num_queries if results else 0}")
    lines.append("")

    # ── Summary Table ──
    lines.append("## Summary Comparison")
    lines.append("")
    header = (
        "| Config | Description | Avg Lat | P50 | P95 | P99 | "
        "Avg Count | Avg Score | Max Score | Errors | Method |"
    )
    sep = (
        "|--------|-------------|---------|-----|-----|-----|"
        "----------|-----------|-----------|--------|--------|"
    )
    lines.append(header)
    lines.append(sep)

    for r in results:
        lines.append(
            f"| **{r.config.name}** | {r.config.description[:50]} | "
            f"{r.avg_latency_ms:.0f}ms | {r.p50_latency_ms:.0f}ms | "
            f"{r.p95_latency_ms:.0f}ms | {r.p99_latency_ms:.0f}ms | "
            f"{r.avg_result_count:.1f} | {r.avg_score:.4f} | "
            f"{r.avg_max_score:.4f} | {r.num_errors} | "
            f"{r.dominant_method_summary} |"
        )

    # ── Latency Comparison ──
    lines.append("")
    lines.append("## Latency Distribution")
    lines.append("")
    lines.append(
        "| Config | Min | P50 | Avg | P95 | P99 | Max |"
    )
    lines.append(
        "|--------|-----|-----|-----|-----|-----|-----|"
    )
    for r in results:
        lines.append(
            f"| **{r.config.name}** | {r.min_latency_ms:.0f}ms | "
            f"{r.p50_latency_ms:.0f}ms | {r.avg_latency_ms:.0f}ms | "
            f"{r.p95_latency_ms:.0f}ms | {r.p99_latency_ms:.0f}ms | "
            f"{r.max_latency_ms:.0f}ms |"
        )

    # ── Per-Query Detail ──
    lines.append("")
    lines.append("## Per-Query Detail")
    lines.append("")
    all_queries: list[str] = []
    if results:
        all_queries = [r.query for r in results[0].results]

    for qi, query in enumerate(all_queries):
        lines.append(f"### Query {qi + 1}: {query[:80]}")
        lines.append("")
        lines.append(
            "| Config | Latency | Count | Max Score | Mean Score | Method | Status |"
        )
        lines.append(
            "|--------|---------|-------|-----------|------------|--------|--------|"
        )
        for r in results:
            qr = r.results[qi] if qi < len(r.results) else None
            if qr is None:
                continue
            if qr.error:
                lines.append(
                    f"| **{r.config.name}** | {qr.latency_ms:.0f}ms | — | — | — | — | "
                    f"❌ {qr.error[:40]} |"
                )
            else:
                lines.append(
                    f"| **{r.config.name}** | {qr.latency_ms:.0f}ms | {qr.result_count} | "
                    f"{qr.max_score or 0:.3f} | {qr.mean_score:.3f} | "
                    f"{qr.dominant_method} | ✅ |"
                )
        lines.append("")

    # ── Statistical Significance ──
    if comparisons:
        lines.append("## Statistical Significance (Wilcoxon Signed-Rank Test)")
        lines.append("")
        for comp in comparisons:
            status = comp.get("status", "unknown")
            if status == "scipy_not_available":
                lines.append("- ⚠️ **scipy not available, skip significance test**")
            elif status == "insufficient_samples":
                lines.append(
                    f"- ⚠️ {comp.get('comparison', '?')}: insufficient samples "
                    f"(n={comp.get('n', '?')}), need >=20"
                )
            elif status == "error":
                lines.append(f"- ❌ {comp.get('comparison', '?')}: error — {comp.get('note', '')}")
            else:
                sig = "✅ Significant" if comp.get("significant_at_0.05") else "❌ Not significant"
                lines.append(
                    f"- **{comp.get('comparison', '?')}**: {sig} "
                    f"(W={comp.get('statistic', '?')}, p={comp.get('p_value', '?')})"
                )
        lines.append("")

    lines.append("---")
    lines.append(f"*Report generated by ab_retrieval.py at {time.strftime('%Y-%m-%d %H:%M:%S')}*")
    lines.append("")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="A/B Retrieval Experiment Framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--queries",
        type=str,
        default=None,
        help="Path to a text file with one query per line (default: built-in 12 queries)",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Number of times to repeat each query (default: 1). Repeats appear as separate samples.",
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default=DEFAULT_API,
        help=f"Base URL of the RAG API (default: {DEFAULT_API})",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of results to retrieve per query (default: 5)",
    )
    parser.add_argument(
        "--configs",
        type=str,
        nargs="*",
        default=None,
        help="Configs to run (e.g., 'A B C'). Default: all built-in configs.",
    )
    parser.add_argument(
        "--no-significance",
        action="store_true",
        help="Skip statistical significance testing",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="HTTP request timeout in seconds (default: 120)",
    )

    args = parser.parse_args()

    # ── Load queries ──
    if args.queries:
        queries_path = Path(args.queries)
        if not queries_path.exists():
            print(f"ERROR: Query file not found: {queries_path}")
            sys.exit(1)
        queries = [
            line.strip()
            for line in queries_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        print(f"Loaded {len(queries)} queries from {queries_path}")
    else:
        queries = list(DEFAULT_QUERIES)
        print(f"Using built-in {len(queries)} default queries")

    # Apply repeats
    if args.repeat > 1:
        original_count = len(queries)
        queries = queries * args.repeat
        print(f"Repeated {original_count} queries x {args.repeat} = {len(queries)} total runs")

    # ── Select configs ──
    all_configs = RetrievalConfig.default_configs()
    if args.configs:
        selected_map = {c.name.split("_")[0]: c for c in all_configs}
        configs = []
        for label in args.configs:
            if label in selected_map:
                configs.append(selected_map[label])
            else:
                print(f"WARNING: Unknown config '{label}', skipping. Available: {list(selected_map.keys())}")
        if not configs:
            print("ERROR: No valid configs selected. Available:", list(selected_map.keys()))
            sys.exit(1)
    else:
        configs = list(all_configs)

    print(f"Running {len(configs)} configs: {[c.name for c in configs]}")
    print(f"API: {args.api_url}")
    print(f"Top-K: {args.top_k}")
    print()

    # ── Health check ──
    try:
        async with httpx.AsyncClient(timeout=10) as hc:
            r = await hc.get(f"{args.api_url}/health")
            r.raise_for_status()
            print(f"API healthy: {args.api_url}")
    except Exception as exc:
        print(f"ERROR: API UNREACHABLE ({exc}) — is the server running?")
        sys.exit(1)

    # ── Run all configs ──
    all_results: list[ConfigRunResult] = []

    async with httpx.AsyncClient(timeout=args.timeout) as client:
        for config in configs:
            print(f"\n{'=' * 60}")
            print(f"Config: {config.name} — {config.description}")
            print(f"{'=' * 60}")

            t0 = time.perf_counter()
            result = await run_config(
                client, config, queries, args.top_k, args.api_url, DEFAULT_HEADERS
            )
            elapsed = time.perf_counter() - t0
            all_results.append(result)

            print(
                f"  Done: {result.num_success}/{result.num_queries} success, "
                f"avg latency={result.avg_latency_ms:.0f}ms, "
                f"total time={elapsed:.1f}s"
            )

    # ── Pairwise significance tests ──
    significance_results: list[dict[str, Any]] = []
    if not args.no_significance and len(all_results) >= 2:
        for i in range(len(all_results)):
            for j in range(i + 1, len(all_results)):
                comp = wilcoxon_test(
                    all_results[i].latencies,
                    all_results[j].latencies,
                    all_results[i].config.name,
                    all_results[j].config.name,
                )
                significance_results.append(comp)

    # ── Generate report ──
    md_report = generate_markdown_table(all_results, significance_results)
    print("\n" + md_report)

    # ── Save reports ──
    report_dir = Path(__file__).resolve().parent / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    json_path = report_dir / f"ab_{ts}.json"
    md_path = report_dir / f"ab_{ts}.md"

    json_report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": {
            "api_url": args.api_url,
            "top_k": args.top_k,
            "repeat": args.repeat,
            "total_queries": len(queries),
            "unique_queries": len(queries) // args.repeat if args.repeat else 0,
        },
        "configs": [r.to_dict() for r in all_results],
        "significance": significance_results if significance_results else None,
    }
    json_path.write_text(json.dumps(json_report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(md_report, encoding="utf-8")

    print(f"\nJSON report: {json_path}")
    print(f"Markdown report: {md_path}")

    # ── Quick summary ──
    print(f"\n{'=' * 60}")
    print("QUICK SUMMARY")
    print(f"{'=' * 60}")
    for r in all_results:
        print(
            f"  {r.config.name:<20} "
            f"avg={r.avg_latency_ms:7.0f}ms "
            f"p50={r.p50_latency_ms:7.0f}ms "
            f"p95={r.p95_latency_ms:7.0f}ms "
            f"count={r.avg_result_count:5.1f} "
            f"score={r.avg_max_score:.3f} "
            f"errors={r.num_errors}"
        )


if __name__ == "__main__":
    asyncio.run(main())
