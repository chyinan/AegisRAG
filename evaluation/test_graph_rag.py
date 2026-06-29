#!/usr/bin/env python3
"""End-to-end Graph RAG smoke test.

1. Reads real document chunks from the PostgreSQL knowledge base.
2. Builds a knowledge graph using the LLM (DeepSeek) for entity extraction.
3. Runs test queries and shows graph-traversal results.

Usage:
    python evaluation/test_graph_rag.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

# Add project root to path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg
import httpx
import networkx as nx

from packages.retrieval.graph_rag import GraphRAGPipeline

# ── Config ──────────────────────────────────────────────────────────

def _load_env(path: Path) -> dict[str, str]:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip('"').strip("'")
    return env

PROJECT_ENV = _load_env(Path(__file__).resolve().parent.parent / ".env")
HERMES_ENV = _load_env(Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / ".env")

DS_KEY = HERMES_ENV.get("DEEPSEEK_API_KEY", "")
DS_URL = "https://api.deepseek.com/v1/chat/completions"

DB_URL_RAW = PROJECT_ENV.get("DATABASE_URL", "postgresql://rag_app:***@localhost:5432/rag_app")
DB_URL = DB_URL_RAW.replace("+asyncpg", "").replace("postgres:5432", "localhost:5432")


# ── LLM Callback ────────────────────────────────────────────────────

async def deepseek_chat(system: str, user: str) -> str:
    """Call DeepSeek API — GraphRAG pipeline callback."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            DS_URL,
            headers={
                "Authorization": f"Bearer {DS_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-v4-flash",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.1,
                "max_tokens": 512,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


# ── Main ────────────────────────────────────────────────────────────

async def main() -> None:
    print("=" * 68)
    print("  AegisRAG — Graph RAG End-to-End Smoke Test")
    print("=" * 68)

    if not DS_KEY:
        print("✗ DEEPSEEK_API_KEY not found — skipping test.")
        return

    # 1. Read chunks from PostgreSQL.
    print("\n[1/4] Reading chunks from PostgreSQL...")
    conn = await asyncpg.connect(DB_URL)
    try:
        rows = await conn.fetch("""
            SELECT chunk_id, document_id, content, title_path, source_type,
                   token_count, status
            FROM chunks
            WHERE status = 'active' AND deleted_at IS NULL AND content IS NOT NULL
            ORDER BY token_count DESC
            LIMIT 12
        """)
    finally:
        await conn.close()

    print(f"      Found {len(rows)} active chunks.")
    if not rows:
        print("✗ No chunks in database — run ingestion first.")
        return

    # 2. Build knowledge graph.
    print("\n[2/4] Building knowledge graph (LLM entity extraction)...")
    pipeline = GraphRAGPipeline(llm=deepseek_chat, max_neighbour_hops=2)

    chunks_data = [
        {
            "chunk_id": row["chunk_id"],
            "text": row["content"][:3000],  # Cap at 3000 chars per chunk for LLM.
        }
        for row in rows
    ]

    t0 = time.perf_counter()
    g = nx.DiGraph()
    for chunk in chunks_data:
        try:
            g = await pipeline.build_graph_inline(
                tenant_id="default",
                chunks=[chunk],
                graph=g,
            )
        except Exception as exc:
            print(f"      ⚠ Chunk {chunk['chunk_id']}: {exc}")

    build_time = time.perf_counter() - t0
    print(f"      Graph built in {build_time:.1f}s")
    print(f"      Nodes: {g.number_of_nodes()}  |  Edges: {g.number_of_edges()}")

    if g.number_of_nodes() == 0:
        print("✗ No entities extracted — check LLM connectivity.")
        return

    # 3. Show graph sample.
    print("\n[3/4] Knowledge graph sample (top edges):")
    edges = list(g.edges(data=True))
    for _, (src, dst, data) in enumerate(edges[:10]):
        rel = data.get("relation", "related_to")
        print(f"      {src} → {rel} → {dst}")

    # 4. Run test queries.
    print("\n[4/4] Running test queries...")
    test_queries = [
        "How does pgvector compare to Milvus?",
        "What security features does AegisRAG have?",
        "How does the RAG retrieval pipeline work?",
    ]

    for query in test_queries:
        print(f"\n    Query: \"{query}\"")
        try:
            results = await pipeline.retrieve(
                query=query,
                graph=g,
                top_k=3,
            )
            if results:
                for j, r in enumerate(results):
                    relations = r.get("relations", [])[:3]
                    print(f"      [{j+1}] chunk={r['chunk_id']}")
                    for rel in relations:
                        print(f"          {rel}")
            else:
                print("      (no graph matches)")
        except Exception as exc:
            print(f"      ✗ Error: {exc}")

    print("\n" + "=" * 68)
    print("  ✓ Graph RAG smoke test complete")
    print(f"    Nodes: {g.number_of_nodes()} | Edges: {g.number_of_edges()}")
    print(f"    Build time: {build_time:.1f}s")
    print("=" * 68)


if __name__ == "__main__":
    asyncio.run(main())
