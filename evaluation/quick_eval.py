#!/usr/bin/env python3
"""Quick RAGAS evaluation runner — dev-headers auth + DeepSeek judge."""
from __future__ import annotations

import asyncio, json, os, sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from packages.eval.ragas_evaluator import EvalCase, RagasEvaluator

DEV_HEADERS = {
    "Content-Type": "application/json",
    "X-User-ID": "admin",
    "X-Tenant-ID": "default",
    "X-Roles": "admin,platform_admin",
    "X-Permissions": "document:read,retrieval:query",
}

def _load_ds_key():
    env_path = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / ".env"
    with open(env_path) as f:
        for line in f:
            if "DEEPSEEK_API_KEY" in line and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""

DS_API_KEY = _load_ds_key()

def load_dataset(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    name = data.get("dataset_name", Path(path).stem)
    version = data.get("dataset_version", "v1")
    cases = []
    for item in data.get("cases", []):
        cases.append(EvalCase(
            case_id=item["case_id"],
            question=item["question"],
            reference_answer=item.get("reference_answer"),
            reference_contexts=tuple(item.get("reference_contexts", [])),
        ))
    return cases, name, version

async def query_rag(question, api_url="http://localhost:8000"):
    import httpx
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{api_url}/query",
            json={"query": question, "top_k": 10},
            headers=DEV_HEADERS,
        )
        resp.raise_for_status()
        data = resp.json()
        inner = data.get("data", {}) or {}
        answer = inner.get("answer", "") or ""
        contexts = [c.get("text", "") for c in inner.get("citations", [])]
        return answer, contexts

async def main():
    dataset = sys.argv[1] if len(sys.argv) > 1 else "evaluation/dataset/sample.json"
    cases, name, version = load_dataset(dataset)
    print(f"Dataset: {name} ({len(cases)} cases)")

    evaluator = RagasEvaluator(
        llm_model="deepseek-v4-flash",
        llm_base_url="https://api.deepseek.com/v1",
        llm_api_key=DS_API_KEY,
        pass_threshold=0.60,
        metrics=("faithfulness", "context_precision"),
    )

    async def run_fn(q):
        return await query_rag(q)

    print("Running evaluation...")
    report = evaluator.evaluate(
        cases=cases, run_fn=run_fn,
        dataset_name=name, dataset_version=version,
    )

    print(f"\n{'='*60}")
    print(f"Results: {report.passed_count}/{report.case_count} passed")
    for metric, score in sorted(report.aggregate_scores.items()):
        emoji = "OK" if score >= 0.60 else "LO"
        print(f"  {metric}: {emoji} {score:.4f}")
    print(f"  Avg latency: {report.average_latency_ms:.0f}ms")
    print(f"{'='*60}")

    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        print(f"\n[{status}] {r.case.case_id}: {r.case.question}")
        print(f"  Answer: {(r.answer or '')[:200]}")
        for s in r.scores:
            print(f"    {s.name}: {s.score:.4f}")
        print(f"  Latency: {r.latency_ms:.0f}ms")

    md = evaluator.to_markdown(report)
    out_path = PROJECT_ROOT / "evaluation" / "reports" / f"eval_{report.run_id}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"\nReport saved: {out_path}")

if __name__ == "__main__":
    asyncio.run(main())
