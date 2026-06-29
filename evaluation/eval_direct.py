#!/usr/bin/env python3
"""Direct RAGAS evaluation — queries chunks from DB for proper context evaluation."""
from __future__ import annotations
import asyncio, json, os, subprocess, sys, time
from dataclasses import dataclass
from pathlib import Path

import httpx
from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_URL = "http://localhost:8000"
DB_CONTAINER = "aegisrag-postgres-1"

DEV_HEADERS = {
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
LLM = OpenAI(base_url="https://api.deepseek.com/v1", api_key=DS_KEY)

@dataclass
class EvalCase:
    case_id: str
    question: str
    reference_answer: str | None = None

async def query_rag(question: str) -> tuple[str, list[str], list[str]]:
    """Returns (answer, chunk_ids, chunk_texts)."""
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{API_URL}/query", json={"query": question, "top_k": 10}, headers=DEV_HEADERS)
        r.raise_for_status()
        d = r.json().get("data", {}) or {}
        answer = d.get("answer", "") or ""
        chunk_ids = [cit.get("chunk_id", "") for cit in d.get("citations", []) if cit.get("chunk_id")]
    
    # Fetch chunk content from DB
    chunks = []
    if chunk_ids:
        ids_str = ", ".join(f"'{cid}'" for cid in chunk_ids)
        result = subprocess.run([
            "docker", "exec", DB_CONTAINER, "psql", "-U", "rag_app", "-d", "rag_app",
            "-t", "-c", f"SELECT content FROM chunks WHERE chunk_id IN ({ids_str});"
        ], capture_output=True, text=True)
        chunks = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
    
    return answer, chunk_ids, chunks

def judge(question: str, answer: str, contexts: list[str], metric: str) -> float:
    """LLM-as-judge for faithfulness or context_precision."""
    ctx_text = "\n---\n".join(contexts) if contexts else "(no contexts retrieved)"
    
    if metric == "faithfulness":
        prompt = f"""Rate how FACTUAL and GROUNDED this answer is in the retrieved contexts.

Question: {question}

Retrieved Contexts:
{ctx_text}

Answer:
{answer}

Score: 1.0 (fully grounded, no hallucinations) to 0.0 (entirely hallucinated).
Output ONLY a float between 0.0 and 1.0."""
    else:  # context_precision
        ctx_list = "\n".join(f"[{i+1}] {c[:300]}" for i, c in enumerate(contexts))
        prompt = f"""Rate what fraction of retrieved chunks are RELEVANT to the question.

Question: {question}

Retrieved chunks:
{ctx_list}

Score: 1.0 (all relevant) to 0.0 (none relevant).
Output ONLY a float between 0.0 and 1.0."""

    resp = LLM.chat.completions.create(
        model="deepseek-chat", temperature=0,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=10,
    )
    text = resp.choices[0].message.content.strip()
    try:
        return float(text)
    except ValueError:
        return 0.5

async def main():
    ds_path = sys.argv[1] if len(sys.argv) > 1 else "evaluation/dataset/sample.json"
    with open(ds_path) as f:
        ds = json.load(f)
    cases = [EvalCase(c["case_id"], c["question"], c.get("reference_answer")) for c in ds["cases"]]
    print(f"Evaluating {len(cases)} cases with DeepSeek judge...\n")
    
    results = []
    for case in cases:
        t0 = time.perf_counter()
        answer, chunk_ids, contexts = await query_rag(case.question)
        latency = (time.perf_counter() - t0) * 1000
        
        faith = judge(case.question, answer, contexts, "faithfulness") if answer else 0.0
        prec = judge(case.question, contexts, "context_precision") if contexts else 0.0
        
        results.append({
            "case_id": case.case_id, "question": case.question,
            "answer": answer[:300], "chunks_found": len(contexts),
            "faithfulness": faith, "context_precision": prec,
            "latency_ms": round(latency),
        })
        
        avg = (faith + prec) / 2 if contexts else faith
        status = "PASS" if avg >= 0.6 else "FAIL"
        print(f"[{status}] {case.case_id}: faith={faith:.2f} prec={prec:.2f} chunks={len(contexts)} latency={latency:.0f}ms")
        print(f"  Q: {case.question}")
        print(f"  A: {(answer or 'N/A')[:150]}")
        print()
    
    avg_f = sum(r["faithfulness"] for r in results) / len(results)
    avg_p = sum(r["context_precision"] for r in results) / len(results)
    avg_l = sum(r["latency_ms"] for r in results) / len(results)
    passed = sum(1 for r in results if (r["faithfulness"] + max(r["context_precision"], r.get("context_precision",0))) / 2 >= 0.6)
    
    print("=" * 60)
    print(f"AGGREGATE SCORES ({len(cases)} cases, {passed} passed)")
    print(f"  Faithfulness:       {avg_f:.4f}  {'OK' if avg_f >= 0.6 else 'LOW'}")
    print(f"  Context Precision:  {avg_p:.4f}  {'OK' if avg_p >= 0.6 else 'LOW'}")
    print(f"  Avg Latency:        {avg_l:.0f}ms")
    print("=" * 60)
    
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = PROJECT_ROOT / "evaluation" / "reports" / f"eval_{ts}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {"timestamp": ts, "aggregate": {"faithfulness": avg_f, "context_precision": avg_p, "avg_latency_ms": avg_l, "passed": passed, "total": len(cases)}, "cases": results}
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nReport: {out}")

if __name__ == "__main__":
    asyncio.run(main())
