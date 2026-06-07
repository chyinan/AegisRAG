from __future__ import annotations

from math import isclose
from typing import Any, cast

import pytest

from packages.auth.context import AuthContext
from packages.retrieval.dto import RetrievalCandidate, RetrievalFilterSet, RetrievalRequest
from packages.retrieval.exceptions import (
    RETRIEVAL_HYBRID_BRANCH_FAILED,
    RetrievalError,
)
from packages.retrieval.filters import build_retrieval_filter_set
from packages.retrieval.rrf import HybridMergeConfig, HybridRetriever, RRFMerger
from packages.retrieval.service import RetrievalService


@pytest.mark.asyncio
async def test_rrf_merger_deduplicates_overlap_and_records_safe_provenance() -> None:
    request = _request()
    filters = _filters(request)
    merger = RRFMerger(config=HybridMergeConfig())

    candidates = merger.merge(
        request=request,
        filters=filters,
        dense_candidates=[
            _candidate(chunk_id="overlap", score=0.70, method="dense"),
            _candidate(chunk_id="dense-only", score=0.40, method="dense"),
        ],
        sparse_candidates=[
            _candidate(chunk_id="sparse-only", score=0.90, method="sparse"),
            _candidate(chunk_id="overlap", score=0.80, method="sparse"),
        ],
    )

    assert [candidate.chunk_id for candidate in candidates] == [
        "overlap",
        "sparse-only",
        "dense-only",
    ]
    overlap = candidates[0]
    assert overlap.retrieval_method == "hybrid"
    assert overlap.document_id == "doc-overlap"
    assert overlap.version_id == "ver-overlap"
    assert overlap.source == "kb://overlap.md"
    assert overlap.source_uri == "kb://overlap.md"
    assert overlap.source_type == "markdown"
    assert overlap.page_start == 1
    assert overlap.page_end == 2
    assert overlap.title_path == ("Policy", "overlap")
    assert overlap.tenant_id == "tenant-a"
    assert overlap.acl == {"visibility": "tenant", "allowed_roles": ["hr"]}

    provenance = _provenance(overlap)
    assert provenance["retrieval_methods"] == ("dense", "sparse")
    assert provenance["fusion_reason"] == "dense_sparse_overlap"
    assert isclose(
        provenance["raw_rrf_score"],
        (1.0 / 61.0) + (1.0 / 62.0),
    )
    assert isclose(overlap.score, provenance["normalized_fusion_score"])
    assert 0.0 <= overlap.score <= 1.0
    assert provenance["sources"] == (
        {
            "retrieval_method": "dense",
            "rank": 1,
            "score": 0.70,
            "weight": 1.0,
            "contribution": 1.0 / 61.0,
        },
        {
            "retrieval_method": "sparse",
            "rank": 2,
            "score": 0.80,
            "weight": 1.0,
            "contribution": 1.0 / 62.0,
        },
    )
    assert "department" in overlap.metadata
    assert "secret full query" not in str(overlap.model_dump())
    assert "chunk_content" not in str(overlap.model_dump()).lower()
    assert "sql" not in str(overlap.model_dump()).lower()
    assert "vector" not in str(overlap.model_dump()).lower()

    assert merger.last_trace is not None
    assert merger.last_trace.input_counts == {"dense": 2, "sparse": 2}
    assert merger.last_trace.deduped_count == 3
    assert merger.last_trace.filtered_count == 0
    assert merger.last_trace.threshold is None
    assert merger.last_trace.rank_constant == 60.0
    assert merger.last_trace.weights == {"dense": 1.0, "sparse": 1.0}


def test_rrf_merger_uses_weighted_rank_formula_and_normalized_scores() -> None:
    request = _request()
    filters = _filters(request)
    config = HybridMergeConfig(rank_constant=10.0, dense_weight=2.0, sparse_weight=1.0)
    merger = RRFMerger(config=config)

    candidates = merger.merge(
        request=request,
        filters=filters,
        dense_candidates=[_candidate(chunk_id="shared", score=0.20, method="dense")],
        sparse_candidates=[_candidate(chunk_id="shared", score=0.99, method="sparse")],
    )

    expected_raw = (2.0 / 11.0) + (1.0 / 11.0)
    expected_max = (2.0 + 1.0) / 11.0
    assert isclose(_provenance(candidates[0])["raw_rrf_score"], expected_raw)
    assert isclose(candidates[0].score, expected_raw / expected_max)


def test_rrf_merger_ignores_duplicate_chunk_from_same_branch() -> None:
    request = _request()
    filters = _filters(request)
    merger = RRFMerger(config=HybridMergeConfig())

    candidates = merger.merge(
        request=request,
        filters=filters,
        dense_candidates=[
            _candidate(chunk_id="shared", score=0.90, method="dense"),
            _candidate(chunk_id="shared", score=0.10, method="dense"),
        ],
        sparse_candidates=[],
    )

    provenance = _provenance(candidates[0])
    assert provenance["retrieval_methods"] == ("dense",)
    assert provenance["sources"] == (
        {
            "retrieval_method": "dense",
            "rank": 1,
            "score": 0.90,
            "weight": 1.0,
            "contribution": 1.0 / 61.0,
        },
    )
    assert isclose(provenance["raw_rrf_score"], 1.0 / 61.0)


def test_rrf_merger_filters_threshold_after_fusion_and_handles_empty_branches() -> None:
    request = _request(score_threshold=0.50)
    filters = _filters(request)
    merger = RRFMerger(config=HybridMergeConfig(min_fusion_score=0.75))

    candidates = merger.merge(
        request=request,
        filters=filters,
        dense_candidates=[
            _candidate(chunk_id="dense-only", score=0.99, method="dense"),
            _candidate(chunk_id="shared", score=0.10, method="dense"),
        ],
        sparse_candidates=[_candidate(chunk_id="shared", score=0.10, method="sparse")],
    )

    assert [candidate.chunk_id for candidate in candidates] == ["shared"]
    assert _provenance(candidates[0])["fusion_reason"] == "dense_sparse_overlap"
    assert merger.last_trace is not None
    assert merger.last_trace.filtered_count == 1
    assert merger.last_trace.threshold == 0.75

    empty = merger.merge(
        request=_request(),
        filters=filters,
        dense_candidates=[],
        sparse_candidates=[],
    )
    assert empty == []
    assert merger.last_trace is not None
    assert merger.last_trace.input_counts == {"dense": 0, "sparse": 0}

    no_threshold_merger = RRFMerger(config=HybridMergeConfig())
    dense_only = no_threshold_merger.merge(
        request=_request(),
        filters=filters,
        dense_candidates=[_candidate(chunk_id="dense-only", method="dense")],
        sparse_candidates=[],
    )
    assert [candidate.chunk_id for candidate in dense_only] == ["dense-only"]

    sparse_only = no_threshold_merger.merge(
        request=_request(),
        filters=filters,
        dense_candidates=[],
        sparse_candidates=[_candidate(chunk_id="sparse-only", method="sparse")],
    )
    assert [candidate.chunk_id for candidate in sparse_only] == ["sparse-only"]


def test_rrf_merger_sorting_is_deterministic_for_ties() -> None:
    request = _request()
    filters = _filters(request)
    merger = RRFMerger(config=HybridMergeConfig())

    candidates = merger.merge(
        request=request,
        filters=filters,
        dense_candidates=[_candidate(chunk_id="chunk-b", score=0.5, method="dense")],
        sparse_candidates=[_candidate(chunk_id="chunk-a", score=0.5, method="sparse")],
    )

    assert [candidate.chunk_id for candidate in candidates] == ["chunk-a", "chunk-b"]


def test_rrf_merger_tiebreaks_identical_chunk_ids_by_full_identity() -> None:
    request = _request()
    filters = _filters(request)
    merger = RRFMerger(config=HybridMergeConfig())

    candidates = merger.merge(
        request=request,
        filters=filters,
        dense_candidates=[
            _candidate(
                chunk_id="same",
                method="dense",
                document_id="doc-b",
                version_id="ver-b",
            )
        ],
        sparse_candidates=[
            _candidate(
                chunk_id="same",
                method="sparse",
                document_id="doc-a",
                version_id="ver-a",
            )
        ],
    )

    assert [(candidate.document_id, candidate.chunk_id) for candidate in candidates] == [
        ("doc-a", "same"),
        ("doc-b", "same"),
    ]


def test_rrf_merger_redacts_sensitive_metadata_aliases() -> None:
    request = _request()
    filters = _filters(request)
    merger = RRFMerger(config=HybridMergeConfig())

    candidates = merger.merge(
        request=request,
        filters=filters,
        dense_candidates=[
            _candidate(
                chunk_id="alias",
                method="dense",
                metadata_extra={
                    "chunk-content": "must not survive",
                    "documentContent": "must not survive",
                    "query_text": "must not survive",
                    "api-key": "sk-test-secret",
                    "safe_label": "allowed",
                },
            )
        ],
        sparse_candidates=[],
    )

    dumped = str(candidates[0].model_dump())
    assert "must not survive" not in dumped
    assert "sk-test-secret" not in dumped
    assert candidates[0].metadata["safe_label"] == "allowed"


@pytest.mark.asyncio
async def test_hybrid_retriever_clears_branch_threshold_and_uses_same_filters() -> None:
    dense = RecordingRetriever([_candidate(chunk_id="dense-only", method="dense")])
    sparse = RecordingRetriever([_candidate(chunk_id="sparse-only", method="sparse")])
    config = HybridMergeConfig(max_candidates_per_branch=5)
    retriever = HybridRetriever(
        dense_retriever=dense,
        sparse_retriever=sparse,
        merger=RRFMerger(config=config),
        config=config,
    )
    request = _request(top_k=1, score_threshold=0.40)
    filters = _filters(request)

    candidates = await retriever.retrieve(request=request, filters=filters)

    assert dense.last_request is not None
    assert sparse.last_request is not None
    assert dense.last_request.score_threshold is None
    assert sparse.last_request.score_threshold is None
    assert dense.last_request.top_k == 5
    assert sparse.last_request.top_k == 5
    assert dense.last_filters == filters
    assert sparse.last_filters == filters
    assert len(candidates) == 1


@pytest.mark.asyncio
async def test_hybrid_retriever_branch_failure_maps_to_safe_error() -> None:
    retriever = HybridRetriever(
        dense_retriever=FailingRetriever(),
        sparse_retriever=RecordingRetriever([]),
        merger=RRFMerger(config=HybridMergeConfig()),
        config=HybridMergeConfig(),
    )
    request = _request(query="secret full query text")

    with pytest.raises(RetrievalError) as exc_info:
        await retriever.retrieve(request=request, filters=_filters(request))

    assert exc_info.value.code == RETRIEVAL_HYBRID_BRANCH_FAILED
    assert exc_info.value.details == {
        "request_id": "req-1",
        "trace_id": "trace-1",
        "tenant_id": "tenant-a",
        "user_id": "user-1",
        "top_k": 10,
        "retrieval_method": "hybrid",
        "hybrid_stage": "branch",
        "safe_counts": {"returned_candidates": 0},
        "branch": "dense",
        "error_code": RETRIEVAL_HYBRID_BRANCH_FAILED,
    }
    assert "secret full query text" not in str(exc_info.value.details)
    assert "password" not in str(exc_info.value.details).lower()
    assert "C:\\" not in str(exc_info.value.details)


@pytest.mark.asyncio
async def test_hybrid_retriever_merge_failure_maps_to_safe_counts() -> None:
    retriever = HybridRetriever(
        dense_retriever=RecordingRetriever([_candidate(chunk_id="dense-only", method="dense")]),
        sparse_retriever=RecordingRetriever([_candidate(chunk_id="sparse-only", method="sparse")]),
        merger=cast(RRFMerger, FailingMerger()),
        config=HybridMergeConfig(),
    )
    request = _request(query="secret full query text")

    with pytest.raises(RetrievalError) as exc_info:
        await retriever.retrieve(request=request, filters=_filters(request))

    assert exc_info.value.details == {
        "request_id": "req-1",
        "trace_id": "trace-1",
        "tenant_id": "tenant-a",
        "user_id": "user-1",
        "top_k": 10,
        "retrieval_method": "hybrid",
        "hybrid_stage": "merge",
        "safe_counts": {"dense_candidates": 1, "sparse_candidates": 1},
        "error_code": "RETRIEVAL_HYBRID_MERGE_FAILED",
    }
    assert "secret full query text" not in str(exc_info.value.details)


@pytest.mark.asyncio
async def test_hybrid_retriever_and_service_keep_security_guard() -> None:
    retriever = HybridRetriever(
        dense_retriever=RecordingRetriever(
            [
                _candidate(chunk_id="allowed", method="dense"),
                _candidate(chunk_id="wrong-tenant", method="dense", tenant_id="tenant-b"),
                _candidate(chunk_id="wrong-metadata", method="dense", department="finance"),
                _candidate(
                    chunk_id="private",
                    method="dense",
                    acl={"visibility": "private"},
                ),
                _candidate(
                    chunk_id="denied",
                    method="dense",
                    acl={"visibility": "tenant", "denied_users": ["user-1"]},
                ),
            ]
        ),
        sparse_retriever=RecordingRetriever([_candidate(chunk_id="allowed", method="sparse")]),
        merger=RRFMerger(config=HybridMergeConfig()),
        config=HybridMergeConfig(),
    )
    service = RetrievalService(retriever=retriever)

    result = await service.retrieve(
        request=_request(metadata_filter={"department": "people"}),
        auth=_auth(),
    )

    assert [candidate.chunk_id for candidate in result.candidates] == ["allowed"]
    assert result.candidates[0].retrieval_method == "hybrid"


def test_hybrid_merge_config_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="rank_constant"):
        HybridMergeConfig(rank_constant=0)
    with pytest.raises(ValueError, match="dense_weight"):
        HybridMergeConfig(dense_weight=float("inf"))
    with pytest.raises(ValueError, match="sparse_weight"):
        HybridMergeConfig(sparse_weight=-1)
    with pytest.raises(ValueError, match="min_fusion_score"):
        HybridMergeConfig(min_fusion_score=1.5)
    with pytest.raises(ValueError, match="max_candidates_per_branch"):
        HybridMergeConfig(max_candidates_per_branch=0)


class RecordingRetriever:
    def __init__(self, candidates: list[RetrievalCandidate]) -> None:
        self._candidates = candidates
        self.last_request: RetrievalRequest | None = None
        self.last_filters: RetrievalFilterSet | None = None

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        self.last_request = request
        self.last_filters = filters
        return self._candidates


class FailingRetriever:
    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        raise RuntimeError("raw password at C:\\secret\\backend.sql")


class FailingMerger:
    def merge(self, **_: object) -> list[RetrievalCandidate]:
        raise RuntimeError("raw password at C:\\secret\\merge.sql")


def _request(
    *,
    query: str = "policy",
    top_k: int = 10,
    score_threshold: float | None = None,
    metadata_filter: dict[str, object] | None = None,
) -> RetrievalRequest:
    return RetrievalRequest(
        query=query,
        top_k=top_k,
        score_threshold=score_threshold,
        metadata_filter=metadata_filter or {},
        request_id="req-1",
        trace_id="trace-1",
    )


def _filters(request: RetrievalRequest) -> RetrievalFilterSet:
    return build_retrieval_filter_set(auth=_auth(), request=request)


def _auth() -> AuthContext:
    return AuthContext(
        user_id="user-1",
        tenant_id="tenant-a",
        roles=("hr",),
        department="people",
        permissions=("document:read",),
    )


def _candidate(
    *,
    chunk_id: str,
    method: str,
    score: float = 0.80,
    tenant_id: str = "tenant-a",
    department: str = "people",
    acl: dict[str, object] | None = None,
    document_id: str | None = None,
    version_id: str | None = None,
    metadata_extra: dict[str, object] | None = None,
) -> RetrievalCandidate:
    metadata: dict[str, object] = {
        "department": department,
        "chunk_content": "must not be copied",
        "sql": "select secret",
        "vector": [0.1, 0.2],
    }
    if metadata_extra is not None:
        metadata.update(metadata_extra)
    return RetrievalCandidate(
        document_id=document_id or f"doc-{chunk_id}",
        version_id=version_id or f"ver-{chunk_id}",
        chunk_id=chunk_id,
        source=f"kb://{chunk_id}.md",
        source_type="markdown",
        source_uri=f"kb://{chunk_id}.md",
        page_start=1,
        page_end=2,
        title_path=("Policy", chunk_id),
        score=score,
        retrieval_method=method,
        tenant_id=tenant_id,
        acl=acl or {"visibility": "tenant", "allowed_roles": ["hr"]},
        metadata=metadata,
    )


def _provenance(candidate: RetrievalCandidate) -> dict[str, Any]:
    return cast("dict[str, Any]", candidate.metadata["retrieval_provenance"])
