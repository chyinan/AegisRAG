#!/usr/bin/env python3
"""
Benchmark Runner — compares different RAG configurations and outputs a comparison report.

Usage:
    python evaluation/benchmark.py \
      --api-url http://localhost:8000 \
      --dataset evaluation/dataset/sample.json \
      --configs default,hybrid_only,rerank_only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from packages.eval.ragas_evaluator import EvalCase, RagasEvaluator


CONFIGS: dict[str, dict[str, object]] = {
    "default": {"top_k": 10},
    "high_recall": {"top_k": 20},
    "strict": {"top_k": 5, "score_threshold": 0.7},
}


def load_dataset(path: str) -> tuple[list[EvalCase], str, str]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    dataset_name = data.get("dataset_name", Path(path).stem)
    dataset_version = data.get("dataset_version", "v1")
    cases = []
    for item in data.get("cases", []):
        cases.append(
            EvalCase(
                case_id=item["case_id"],
                question=item["question"],
                reference_answer=item.get("reference_answer"),
                reference_contexts=tuple(item.get("reference_contexts", [])),
            )
        )
    return cases, dataset_name, dataset_version


async def query_rag(
    api_url: str, question: str, config: dict[str, object], api_key: str | None = None
) -> tuple[str, list[str]]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body = {"query": question}
    body.update(config)

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{api_url}/rag/query", json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        answer = data.get("answer", "")
        contexts = [
            c.get("content", "") for c in data.get("metadata", {}).get("context", [])
        ]
        return answer, contexts


async def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark RAG configurations")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--api-key", help="API key")
    parser.add_argument("--dataset", required=True, help="Evaluation dataset JSON")
    parser.add_argument("--output", default="evaluation/reports", help="Output directory")
    parser.add_argument("--configs", default="default", help="Comma-separated config names to test")
    parser.add_argument("--custom-config", action="append", help="Custom config as JSON: '{\"name\":\"x\",\"top_k\":5}'")
    parser.add_argument("--llm-model", default=os.getenv("RAGAS_LLM_MODEL", "gpt-4o-mini"))
    parser.add_argument("--llm-base-url", default=os.getenv("RAGAS_LLM_BASE_URL"))
    parser.add_argument("--llm-api-key", default=os.getenv("RAGAS_LLM_API_KEY"))
    parser.add_argument("--pass-threshold", type=float, default=0.70)
    parser.add_argument("--repeat", type=int, default=1, help="Repeat each case N times (for stability)")
    args = parser.parse_args()

    # Load dataset
    cases, dataset_name, dataset_version = load_dataset(args.dataset)
    print(f"Dataset: {dataset_name} ({len(cases)} cases)")

    # Build config list
    config_names = [c.strip() for c in args.configs.split(",") if c.strip()]
    configs_to_test: dict[str, dict[str, object]] = {}
    for name in config_names:
        if name in CONFIGS:
            configs_to_test[name] = CONFIGS[name]
        else:
            print(f"⚠ Unknown config '{name}', skipping")
    if args.custom_config:
        for item in args.custom_config:
            cfg = json.loads(item)
            configs_to_test[cfg["name"]] = {k: v for k, v in cfg.items() if k != "name"}

    # Evaluate each config
    evaluator = RagasEvaluator(
        llm_model=args.llm_model,
        llm_base_url=args.llm_base_url,
        llm_api_key=args.llm_api_key,
        pass_threshold=args.pass_threshold,
    )

    all_reports: dict[str, object] = {}
    comparison_rows: list[dict[str, object]] = []

    for config_name, config in configs_to_test.items():
        print(f"\n{'=' * 50}")
        print(f"Testing config: {config_name} — {json.dumps(config)}")
        print("=" * 50)

        start = time.perf_counter()
        repeated_cases = cases * args.repeat if args.repeat > 1 else cases

        async def run_case(question: str) -> tuple[str, list[str]]:
            return await query_rag(args.api_url, question, config, args.api_key)

        report = evaluator.evaluate(
            cases=repeated_cases,
            run_fn=run_case,
            dataset_name=f"{dataset_name}__{config_name}",
            dataset_version=dataset_version,
        )

        elapsed = time.perf_counter() - start
        print(f"  Time: {elapsed:.1f}s")
        for name, score in sorted(report.aggregate_scores.items()):
            emoji = "✅" if score >= args.pass_threshold else "❌"
            print(f"  {name}: {emoji} {score:.4f}")

        all_reports[config_name] = evaluator.to_evidence_report(report)
        comparison_rows.append({
            "config": config_name,
            **{k: round(v, 4) for k, v in report.aggregate_scores.items()},
            "passed": report.passed_count,
            "failed": report.failed_count,
            "avg_latency_ms": round(report.average_latency_ms, 1),
            "elapsed_s": round(elapsed, 1),
        })

    # Save comparison
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    comparison_path = output_dir / f"benchmark_{timestamp}.json"
    comparison_data = {
        "dataset": dataset_name,
        "dataset_version": dataset_version,
        "timestamp": timestamp,
        "configs_tested": list(configs_to_test.keys()),
        "pass_threshold": args.pass_threshold,
        "comparison": comparison_rows,
        "reports": all_reports,
    }
    comparison_path.write_text(json.dumps(comparison_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nComparison saved: {comparison_path}")

    # Markdown comparison table
    md_path = output_dir / f"benchmark_{timestamp}.md"
    md_lines = [
        "# RAG Benchmark Comparison",
        f"**Dataset:** {dataset_name} ({dataset_version})",
        f"**Pass Threshold:** {args.pass_threshold:.0%}",
        f"**Timestamp:** {timestamp}",
        "",
        "## Results",
        "",
        "| Config | Context Precision | Context Recall | Faithfulness | Answer Relevancy | Passed | Avg Latency |",
        "|--------|-------------------|----------------|--------------|------------------|--------|-------------|",
    ]
    for row in comparison_rows:
        md_lines.append(
            f"| {row['config']} "
            f"| {row.get('context_precision', '—')} "
            f"| {row.get('context_recall', '—')} "
            f"| {row.get('faithfulness', '—')} "
            f"| {row.get('answer_relevancy', '—')} "
            f"| {row['passed']}/{int(row['passed']) + int(row['failed'])} "
            f"| {row['avg_latency_ms']:.0f}ms |"
        )
    md_lines += ["", "## Best Config"]
    best = max(comparison_rows, key=lambda r: sum(
        float(r.get(m, 0)) for m in ["context_precision", "context_recall", "faithfulness", "answer_relevancy"]
        if isinstance(r.get(m), (int, float))
    ))
    md_lines.append(f"**{best['config']}** — highest combined score")
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"Markdown report: {md_path}")


if __name__ == "__main__":
    asyncio.run(main())
