"""Quick sanity-check for BGE Local Reranker with real model."""
import asyncio, time, uuid
from packages.retrieval.dto import RetrievalRequest, RetrievalCandidate, RetrievalFilterSet
from packages.retrieval.rerank.adapters.bge_local import BGELocalReranker


def _candidate(cid: str, text: str) -> RetrievalCandidate:
    return RetrievalCandidate(
        document_id=f"doc-{cid}",
        version_id="v1",
        chunk_id=cid,
        source_type="markdown",
        title_path=("Test",),
        retrieval_method="dense",
        tenant_id="default",
        score=0.5,
        metadata={"chunk_text": text},
    )


async def main() -> None:
    rid = str(uuid.uuid4())
    candidates = [
        _candidate("c1", "PostgreSQL supports HNSW indexing for vector search with pgvector extension."),
        _candidate("c2", "The sky is blue and the sun is shining brightly today."),
        _candidate("c3", "Redis is an in-memory data structure store used as a cache and message broker."),
        _candidate("c4", "To optimize vector search, use HNSW index with appropriate m and ef_construction parameters."),
        _candidate("c5", "Docker containers can be orchestrated with Kubernetes for production deployments."),
    ]
    request = RetrievalRequest(
        request_id=rid, trace_id=rid,
        query="How to optimize vector search in PostgreSQL?",
        top_k=5,
    )
    filters = RetrievalFilterSet(tenant_id="default", user_id="test")

    print("Loading BGE model (first run downloads ~1.5GB, ~30s)...")
    t0 = time.perf_counter()
    reranker = BGELocalReranker(model_name="BAAI/bge-reranker-v2-m3")
    result = await reranker.rerank(request=request, filters=filters, candidates=candidates)
    elapsed = time.perf_counter() - t0
    print(f"\nLoad + first rerank: {elapsed:.1f}s")
    print(f"Ranked results:")
    for c in result.candidates:
        prov = c.metadata.get("rerank_provenance", {})
        score = prov.get("rerank_score", 0)
        text = c.metadata.get("chunk_text", "")[:70]
        print(f"  {score:.4f} | {text}")

    # Second call — model cached
    print("\nSecond call (model cached, no download)...")
    t0 = time.perf_counter()
    result2 = await reranker.rerank(request=request, filters=filters, candidates=candidates)
    print(f"Rerank latency: {result2.trace.latency_ms:.0f}ms")
    print(f"Total wall: {time.perf_counter() - t0:.2f}s")

    # Compare: how much faster than LLM Reranker?
    llm_latency_ms = 500  # typical DeepSeek API call
    bge_latency_ms = result2.trace.latency_ms
    speedup = llm_latency_ms / max(bge_latency_ms, 1)
    print(f"\n🆚 LLM Reranker (~{llm_latency_ms}ms) vs BGE Local (~{bge_latency_ms:.0f}ms) = {speedup:.0f}x faster")


if __name__ == "__main__":
    asyncio.run(main())
