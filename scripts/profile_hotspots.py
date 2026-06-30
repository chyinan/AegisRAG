#!/usr/bin/env python3
"""cProfile-based hotspot analysis for AegisRAG retrieval modules.

Profiles:
  1. RRF merge (rrf.py)
  2. Sparse retrieval (sparse.py query parsing, filtering)
  3. Dense retrieval (dense.py candidate construction)
  4. Rerank validation (rerank/__init__.py)

Output: docs/operations/cprofile_results.txt
"""

from __future__ import annotations

import cProfile
import io
import pstats
import sys
import time
from pathlib import Path

PROJECT = Path("D:/Programs/RAG-Local-System")
sys.path.insert(0, str(PROJECT))

OUTPUT = PROJECT / "docs" / "operations" / "cprofile_results.txt"


def build_test_candidates(n: int, method: str = "dense") -> list:
    """Build synthetic RetrievalCandidate objects for profiling."""
    from packages.retrieval.dto import RetrievalCandidate

    candidates = []
    for i in range(n):
        c = RetrievalCandidate(
            document_id=f"doc-{i:04d}",
            version_id=f"ver-{i:04d}",
            chunk_id=f"chunk-{i:04d}",
            source=f"test-source-{i}",
            source_type="markdown",
            source_uri=None,
            page_start=None,
            page_end=None,
            title_path=("Test Document", f"Section {i}"),
            score=0.9 - (i * 0.01),
            retrieval_method=method,
            tenant_id="default",
            acl={"visibility": "tenant"},
            metadata={"title_paths": [["Test", f"Section {i}"]]},
        )
        candidates.append(c)
    return candidates


def profile_rrf_merge() -> str:
    """Profile RRFMerger.merge() with 20 from each branch."""
    from packages.retrieval.dto import RetrievalRequest
    from packages.retrieval.rrf import HybridMergeConfig, RRFMerger

    config = HybridMergeConfig(
        rank_constant=60.0,
        dense_weight=1.0,
        sparse_weight=1.0,
        max_candidates_per_branch=20,
    )
    merger = RRFMerger(config=config)
    request = RetrievalRequest(
        query="test query for profiling",
        top_k=10,
        metadata_filter={},
        score_threshold=None,
        request_id="test-rrf",
        trace_id="trace-rrf",
    )

    from packages.retrieval.dto import RetrievalFilterSet
    filters = RetrievalFilterSet(
        tenant_id="default",
        user_id="admin",
        metadata_filter={},
    )

    dense = build_test_candidates(20, "dense")
    sparse = build_test_candidates(20, "sparse")

    pr = cProfile.Profile()
    pr.enable()
    for _ in range(1000):
        merger.merge(
            request=request,
            filters=filters,
            dense_candidates=dense,
            sparse_candidates=sparse,
        )
    pr.disable()

    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(30)
    return s.getvalue()


def profile_sparse_parsing() -> str:
    """Profile parse_sparse_query_terms."""
    from packages.retrieval.sparse import parse_sparse_query_terms

    queries = [
        "How does dense retrieval compare to sparse retrieval?",
        "What is the optimal chunk size for RAG documents with embedding models?",
        "Explain the difference between BM25 and TF-IDF scoring algorithms.",
        "How do hybrid retrieval pipelines combine multiple ranking signals?",
    ]

    pr = cProfile.Profile()
    pr.enable()
    for _ in range(10000):
        for q in queries:
            parse_sparse_query_terms(q, max_terms=32, max_term_length=128)
    pr.disable()

    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(20)
    return s.getvalue()


def profile_candidate_construction() -> str:
    """Profile candidate_from_vector_result and related functions."""
    from packages.retrieval.dense import _candidate_from_vector_result
    from packages.vectorstores.dto import VectorSearchResult

    pr = cProfile.Profile()
    pr.enable()

    for i in range(2000):
        result = VectorSearchResult(
            document_id=f"doc-{i:04d}",
            version_id=f"ver-{i:04d}",
            chunk_id=f"chk-{i:04d}",
            source=f"test-source-{i}",
            source_type="markdown",
            source_uri=None,
            page_start=(i % 50) + 1,
            page_end=(i % 50) + 3,
            title_path=[f"Title {i}", f"Section {i % 10}"],
            score=0.85 - (i * 0.001),
            tenant_id="default",
            acl={"visibility": "tenant", "groups": ["engineering"]},
            metadata={"chunk_index": i, "char_count": 512, "token_count": 128},
        )
        _candidate_from_vector_result(result)

    pr.disable()

    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(25)
    return s.getvalue()


def profile_rerank_validation() -> str:
    """Profile rerank candidate validation and scoring."""
    from packages.retrieval.dto import RetrievalFilterSet, RetrievalRequest
    from packages.retrieval.rerank import (
        FakeReranker,
        RerankConfig,
    )

    _ = RerankConfig(
        enabled=True,
        failure_policy="fallback",
        timeout_seconds=2.0,
        provider="fake",
        model="fake-reranker-v1",
        max_candidates=20,
    )
    score_map = {f"chunk-{i:04d}": 0.7 + (i % 10) * 0.02 for i in range(20)}
    reranker = FakeReranker(score_by_chunk_id=score_map)

    request = RetrievalRequest(
        query="test rerank profiling query with multiple terms",
        top_k=10,
        metadata_filter={},
        score_threshold=None,
        request_id="test-rerank",
        trace_id="trace-rerank",
    )
    filters = RetrievalFilterSet(
        tenant_id="default",
        user_id="admin",
        metadata_filter={},
    )

    candidates = build_test_candidates(20, "hybrid")

    import asyncio

    async def run_rerank():
        pr = cProfile.Profile()
        pr.enable()
        for _ in range(500):
            await reranker.rerank(
                request=request,
                filters=filters,
                candidates=candidates,
            )
        pr.disable()
        return pr

    pr = asyncio.run(run_rerank())

    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(25)
    return s.getvalue()


def main():
    print("=" * 70)
    print("AegisRAG cProfile Hotspot Analysis")
    print(f"Output: {OUTPUT}")
    print("=" * 70)

    results = {}

    print("\n[1/4] Profiling RRF merge (RRFMerger.merge)...")
    t0 = time.perf_counter()
    results["rrf_merge"] = profile_rrf_merge()
    print(f"  Done in {time.perf_counter() - t0:.2f}s")

    print("\n[2/4] Profiling sparse query parsing...")
    t0 = time.perf_counter()
    results["sparse_parsing"] = profile_sparse_parsing()
    print(f"  Done in {time.perf_counter() - t0:.2f}s")

    print("\n[3/4] Profiling candidate construction...")
    t0 = time.perf_counter()
    results["candidate_construction"] = profile_candidate_construction()
    print(f"  Done in {time.perf_counter() - t0:.2f}s")

    print("\n[4/4] Profiling rerank validation...")
    t0 = time.perf_counter()
    results["rerank_validation"] = profile_rerank_validation()
    print(f"  Done in {time.perf_counter() - t0:.2f}s")

    # Write results
    with open(OUTPUT, "w", encoding="utf-8") as f:
        for name, output in results.items():
            f.write(f"\n{'=' * 70}\n")
            f.write(f"PROFILE: {name}\n")
            f.write(f"{'=' * 70}\n\n")
            f.write(output)
            f.write("\n\n")

    print(f"\nResults written to: {OUTPUT}")


if __name__ == "__main__":
    main()
