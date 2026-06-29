#!/usr/bin/env python3
"""RAG Evaluation Runner — patched for /query + /retrieve endpoints."""
from __future__ import annotations
import argparse, asyncio, json, os, re, sys
from pathlib import Path
import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from packages.eval.ragas_evaluator import EvalCase, RagasEvaluator

HDR = {
    "Content-Type": "application/json",
    "X-User-ID": "admin", "X-Tenant-ID": "default",
    "X-Roles": "admin,platform_admin",
    "X-Permissions": "document:read,retrieval:query",
}

def load_dataset(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    name = data.get("dataset_name", Path(path).stem)
    version = data.get("dataset_version", "v1")
    cases = [EvalCase(
        case_id=item["case_id"], question=item["question"],
        reference_answer=item.get("reference_answer"),
        reference_contexts=tuple(item.get("reference_contexts", [])),
    ) for item in data.get("cases", [])]
    return cases, name, version

async def query_rag(api_url, question):
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{api_url}/query", json={"query": question, "top_k": 10}, headers=HDR)
        r.raise_for_status()
        inner = (r.json().get("data", {}) or {})
        answer = re.sub(r'<thinking>.*?</thinking>', '', inner.get("answer", "") or "", flags=re.DOTALL).strip()

        r2 = await c.post(f"{api_url}/retrieve", json={"query": question, "top_k": 10}, headers=HDR)
        r2.raise_for_status()
        inner2 = (r2.json().get("data", {}) or {})
        contexts = [c.get("chunk_id", "") for c in inner2.get("candidates", []) if c.get("chunk_id")]
    return answer, contexts

def _load_key():
    env_path = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / ".env"
    with open(env_path) as f:
        for line in f:
            if "DEEPSEEK_API_KEY" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""

async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--api-url", default="http://localhost:8000")
    p.add_argument("--dataset", default="evaluation/dataset/sample.json")
    p.add_argument("--output", default="evaluation/reports")
    p.add_argument("--pass-threshold", type=float, default=0.70)
    args = p.parse_args()

    cases, name, version = load_dataset(args.dataset)
    print(f"Loaded {len(cases)} cases from {args.dataset}")

    evaluator = RagasEvaluator(
        llm_model="deepseek-v4-flash",
        llm_base_url="https://api.deepseek.com/v1",
        llm_api_key=_load_key(),
        embedding_model="text-embedding-3-small",
        embedding_base_url="https://api.deepseek.com/v1",
        embedding_api_key=_load_key(),
        pass_threshold=args.pass_threshold,
        metrics=("faithfulness", "context_precision"),
    )

    async def run_fn(q):
        return await query_rag(args.api_url, q)

    report = await evaluator.evaluate(cases=cases, run_fn=run_fn, dataset_name=name, dataset_version=version)

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    jp = out / f"eval_{report.run_id}.json"
    jp.write_text(json.dumps(evaluator.to_evidence_report(report), indent=2, ensure_ascii=False))
    mp = out / f"eval_{report.run_id}.md"
    mp.write_text(evaluator.to_markdown(report))
    print(f"Reports: {jp}, {mp}")

    print(f"\n{'='*50}")
    print(f"Passed: {report.passed_count}/{report.case_count}")
    for n, s in sorted(report.aggregate_scores.items()):
        e = "OK" if s >= args.pass_threshold else "LO"
        print(f"  {n}: {e} {s:.4f}")
    print(f"{'='*50}")

if __name__ == "__main__":
    asyncio.run(main())
