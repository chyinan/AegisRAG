#!/usr/bin/env python3
"""
RAG Evaluation Runner — computes RAGAS metrics against the AegisRAG API.

Usage:
    python evaluation/evaluate.py \
      --api-url http://localhost:8000 \
      --dataset evaluation/dataset/sample.json \
      --output evaluation/reports/
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from packages.eval.ragas_evaluator import EvalCase, RagasEvaluator


def load_dataset(path: str) -> tuple[list[EvalCase], str, str]:
    """Load evaluation dataset from JSON file.

    Expected format:
    {
        "dataset_name": "...",
        "dataset_version": "v1",
        "cases": [
            {
                "case_id": "001",
                "question": "...",
                "reference_answer": "...",       // optional
                "reference_contexts": ["..."]    // optional
            }
        ]
    }
    """
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
                metadata=item.get("metadata"),
            )
        )

    return cases, dataset_name, dataset_version


async def query_rag(api_url: str, question: str, api_key: str | None = None) -> tuple[str, list[str]]:
    """Send a query to the AegisRAG API and return (answer, contexts)."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{api_url}/rag/query",
            json={"query": question, "top_k": 10},
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

        answer = data.get("answer", "")
        contexts = [
            c.get("content", "")
            for c in data.get("metadata", {}).get("context", [])
        ]
        return answer, contexts


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run RAGAS evaluation against AegisRAG")
    parser.add_argument("--api-url", default="http://localhost:8000", help="AegisRAG API base URL")
    parser.add_argument("--api-key", help="API key (if auth is enabled)")
    parser.add_argument("--dataset", required=True, help="Path to evaluation dataset JSON")
    parser.add_argument("--output", default="evaluation/reports", help="Output directory for reports")
    parser.add_argument("--llm-model", default=os.getenv("RAGAS_LLM_MODEL", "gpt-4o-mini"))
    parser.add_argument("--llm-base-url", default=os.getenv("RAGAS_LLM_BASE_URL"))
    parser.add_argument("--llm-api-key", default=os.getenv("RAGAS_LLM_API_KEY"))
    parser.add_argument("--embedding-model", default=os.getenv("RAGAS_EMBEDDING_MODEL", "text-embedding-3-small"))
    parser.add_argument("--pass-threshold", type=float, default=0.70)
    parser.add_argument("--metrics", default="context_precision,context_recall,faithfulness,answer_relevancy")
    args = parser.parse_args()

    # Load dataset
    cases, dataset_name, dataset_version = load_dataset(args.dataset)
    print(f"Loaded {len(cases)} cases from {args.dataset}")

    # Create evaluator
    evaluator = RagasEvaluator(
        llm_model=args.llm_model,
        llm_base_url=args.llm_base_url,
        llm_api_key=args.llm_api_key,
        embedding_model=args.embedding_model,
        pass_threshold=args.pass_threshold,
        metrics=tuple(args.metrics.split(",")),
    )

    # Run evaluation
    print(f"Evaluating against {args.api_url} ...")

    async def run_case(question: str) -> tuple[str, list[str]]:
        return await query_rag(args.api_url, question, args.api_key)

    report = evaluator.evaluate(
        cases=cases,
        run_fn=run_case,
        dataset_name=dataset_name,
        dataset_version=dataset_version,
    )

    # Save reports
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # JSON report (compatible with EvalEvidenceService)
    json_path = output_dir / f"eval_{report.run_id}.json"
    evidence_report = evaluator.to_evidence_report(report)
    json_path.write_text(json.dumps(evidence_report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"JSON report: {json_path}")

    # Markdown report
    md_path = output_dir / f"eval_{report.run_id}.md"
    md_path.write_text(evaluator.to_markdown(report), encoding="utf-8")
    print(f"Markdown report: {md_path}")

    # Summary
    print()
    print("=" * 50)
    print(f"Passed: {report.passed_count}/{report.case_count}")
    for name, score in sorted(report.aggregate_scores.items()):
        emoji = "✅" if score >= args.pass_threshold else "❌"
        print(f"  {name}: {emoji} {score:.4f}")
    print("=" * 50)

    if report.failed_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
