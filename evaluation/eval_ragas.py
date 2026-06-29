#!/usr/bin/env python3
"""RAGAS 0.3.9 — DB contexts via COPY CSV (preserves chunk integrity)."""
from __future__ import annotations
import asyncio, csv, io, json, os, subprocess, sys, time
from pathlib import Path

import httpx
from langchain_openai import ChatOpenAI
from ragas import evaluate as ragas_evaluate, EvaluationDataset
from ragas.metrics import faithfulness, context_precision

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_URL = "http://localhost:8000"
DB_CONTAINER = "aegisrag-postgres-1"

DEV_HEADERS = {
    "Content-Type": "application/json",
        "X-User-ID": "admin", "X-Tenant-ID": "default",
    "X-Roles": "admin,platform_admin",
    "X-Permissions": "document:read,retrieval:query",
}

def _load_key():
    env_path = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / ".env"
    with open(env_path) as f:
        for line in f:
            if "DEEPSEEK_API_KEY" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""

DS_KEY = _load_key()

async def query_rag(question: str) -> tuple[str, list[str]]:
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{API_URL}/query", json={"query": question, "top_k": 10}, headers=DEV_HEADERS)
        r.raise_for_status()
        d = r.json().get("data", {}) or {}
        answer = d.get("answer", "") or ""
        chunk_ids = list(dict.fromkeys(
            cit.get("chunk_id", "") for cit in d.get("citations", []) if cit.get("chunk_id")
        ))

    contexts = []
    if chunk_ids:
        ids_str = ", ".join(f"'{cid}'" for cid in chunk_ids[:10])
        sql = f"COPY (SELECT content FROM chunks WHERE chunk_id IN ({ids_str})) TO STDOUT CSV"
        result = subprocess.run([
            "docker", "exec", DB_CONTAINER, "psql", "-U", "rag_app", "-d", "rag_app",
            "-A", "-t", "-c", sql
        ], capture_output=True, text=True)
        reader = csv.reader(io.StringIO(result.stdout))
        contexts = [row[0] for row in reader if row and row[0].strip()]
    return answer, contexts

async def main():
    ds_path = sys.argv[1] if len(sys.argv) > 1 else "evaluation/dataset/sample.json"
    with open(ds_path) as f:
        ds = json.load(f)
    print(f"Dataset: {ds['dataset_name']} ({len(ds['cases'])} cases)\n")

    llm = ChatOpenAI(model="deepseek-v4-flash", base_url="https://api.deepseek.com/v1", api_key=DS_KEY, temperature=0)

    samples = []
    latencies = []
    answers = []
    for case in ds["cases"]:
        t0 = time.perf_counter()
        answer, contexts = await query_rag(case["question"])
        lt = (time.perf_counter() - t0) * 1000
        latencies.append(lt)
        answers.append(answer)
        samples.append({
            "user_input": case["question"],
            "response": answer,
            "retrieved_contexts": contexts if contexts else [""],
            "reference": case.get("reference_answer", "") or "",
        })
        print(f"  {case['case_id']}: {len(contexts)} contexts, {lt:.0f}ms")

    print("\nComputing RAGAS metrics (DeepSeek judge)...")
    ds_ragas = EvaluationDataset.from_dict(samples)
    result = ragas_evaluate(ds_ragas, metrics=[faithfulness, context_precision], llm=llm, raise_exceptions=False)
    df = result.to_pandas()

    print("\n" + "=" * 60)
    print("RAGAS EVALUATION RESULTS")
    print("=" * 60)
    avg_lat = sum(latencies) / len(latencies)
    for col in ("faithfulness", "context_precision"):
        if col in df.columns:
            val = df[col].mean()
            emoji = "OK" if val >= 0.6 else "LO"
            print(f"  {col}: {emoji} {val:.4f}")
    print(f"  avg_latency_ms: {avg_lat:.0f}")

    metric_cols = [c for c in ("faithfulness", "context_precision") if c in df.columns]
    print(f"\n{'='*60}")
    for i, case in enumerate(ds["cases"]):
        scores = {}
        for col in metric_cols:
            v = df[col].iloc[i]
            if not (isinstance(v, float) and (v != v)):
                scores[col] = float(v)
        avg = sum(scores.values()) / max(len(scores), 1) if scores else 0
        status = "PASS" if avg >= 0.6 else "FAIL"
        print(f"[{status}] {case['case_id']}: {case['question']}")
        print(f"  A: {(answers[i] or 'N/A')[:150]}")
        for k, v in scores.items():
            print(f"  {k}: {v:.4f}")
        print(f"  latency: {latencies[i]:.0f}ms")

    import pandas as pd
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = PROJECT_ROOT / "evaluation" / "reports" / f"ragas_{ts}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\nCSV: {out}")

if __name__ == "__main__":
    asyncio.run(main())
