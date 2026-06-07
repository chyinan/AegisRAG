from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

import pytest

from packages.auth.context import AuthContext
from packages.retrieval import (
    FakeReranker,
    RerankConfig,
    RerankingRetriever,
    RerankResult,
    RerankTrace,
)
from packages.retrieval.dto import RetrievalCandidate, RetrievalFilterSet, RetrievalRequest
from packages.retrieval.exceptions import (
    RETRIEVAL_RERANK_DEGRADED,
    RETRIEVAL_RERANK_FAILED,
    RETRIEVAL_RERANK_INVALID_CANDIDATE,
    RETRIEVAL_RERANK_INVALID_SCORE,
    RetrievalError,
)
from packages.retrieval.filters import build_retrieval_filter_set
from packages.retrieval.rerank import RERANK_PROVENANCE_METADATA_KEY
from packages.retrieval.service import RetrievalService


@pytest.mark.asyncio
async def test_fake_reranker_scores_deterministically_and_keeps_citations() -> None:
    request = _request()
    filters = _filters(request)
    reranker = FakeReranker(score_by_chunk_id={"low": 0.25, "high": 0.91})

    result = await reranker.rerank(
        request=request,
        filters=filters,
        candidates=[
            _candidate(chunk_id="low", score=0.80),
            _candidate(chunk_id="high", score=0.20),
        ],
    )

    assert [candidate.chunk_id for candidate in result.candidates] == ["high", "low"]
    assert [candidate.score for candidate in result.candidates] == [0.91, 0.25]
    assert result.trace.provider == "fake"
    assert result.trace.model == "fake-reranker-v1"
    assert result.trace.input_count == 2
    assert result.trace.output_count == 2
    assert result.trace.degraded is False
    high = result.candidates[0]
    assert high.document_id == "doc-high"
    assert high.version_id == "ver-high"
    assert high.source == "kb://high.md"
    assert high.source_uri == "kb://high.md"
    assert high.source_type == "markdown"
    assert high.page_start == 1
    assert high.page_end == 2
    assert high.title_path == ("Policy", "high")
    assert high.tenant_id == "tenant-a"
    assert high.acl == {"visibility": "tenant", "allowed_roles": ["hr"]}
    provenance = _rerank_provenance(high)
    assert provenance["provider"] == "fake"
    assert provenance["model"] == "fake-reranker-v1"
    assert provenance["status"] == "success"
    assert provenance["input_rank"] == 2
    assert provenance["output_rank"] == 1
    assert provenance["pre_score"] == 0.20
    assert provenance["rerank_score"] == 0.91
    assert provenance["score_source"] == "rerank"
    assert "chunk_content" not in str(high.model_dump()).lower()
    assert "secret full query" not in str(high.model_dump())
    assert "vector" not in str(high.model_dump()).lower()

    repeat = await reranker.rerank(
        request=request,
        filters=filters,
        candidates=list(result.candidates),
    )
    assert [candidate.chunk_id for candidate in repeat.candidates] == ["high", "low"]


@pytest.mark.asyncio
async def test_fake_reranker_defaults_to_existing_score_with_stable_tie_breaker() -> None:
    request = _request()
    filters = _filters(request)
    reranker = FakeReranker()

    result = await reranker.rerank(
        request=request,
        filters=filters,
        candidates=[
            _candidate(chunk_id="b", score=0.50),
            _candidate(chunk_id="a", score=0.50),
        ],
    )

    assert [candidate.chunk_id for candidate in result.candidates] == ["a", "b"]
    assert [candidate.score for candidate in result.candidates] == [0.50, 0.50]


@pytest.mark.asyncio
async def test_reranking_retriever_handles_empty_candidates_and_top_k_limit() -> None:
    request = _request(top_k=1)
    filters = _filters(request)
    empty = RerankingRetriever(
        upstream_retriever=RecordingRetriever([]),
        reranker=FakeReranker(),
        config=RerankConfig(),
    )

    assert await empty.retrieve(request=request, filters=filters) == []
    assert empty.last_trace is not None
    assert empty.last_trace.input_count == 0
    assert empty.last_trace.output_count == 0

    retriever = RerankingRetriever(
        upstream_retriever=RecordingRetriever(
            [
                _candidate(chunk_id="one", score=0.10),
                _candidate(chunk_id="two", score=0.90),
            ]
        ),
        reranker=FakeReranker(),
        config=RerankConfig(max_candidates=2),
    )

    result = await retriever.retrieve(request=request, filters=filters)

    assert [candidate.chunk_id for candidate in result] == ["two"]


@pytest.mark.asyncio
async def test_reranking_retriever_fallback_keeps_order_and_safe_trace() -> None:
    request = _request(query="secret full query text")
    filters = _filters(request)
    retriever = RerankingRetriever(
        upstream_retriever=RecordingRetriever(
            [
                _candidate(chunk_id="first", score=0.80),
                _candidate(chunk_id="second", score=0.70),
            ]
        ),
        reranker=FakeReranker(failure_mode="raise_domain"),
        config=RerankConfig(failure_policy="fallback", provider="fake", model="fake-reranker-v1"),
    )

    result = await retriever.retrieve(request=request, filters=filters)

    assert [candidate.chunk_id for candidate in result] == ["first", "second"]
    assert [candidate.score for candidate in result] == [0.80, 0.70]
    assert retriever.last_trace is not None
    assert retriever.last_trace.degraded is True
    assert retriever.last_trace.error_code == RETRIEVAL_RERANK_DEGRADED
    first_provenance = _rerank_provenance(result[0])
    assert first_provenance["status"] == "degraded"
    assert first_provenance["score_source"] == "fallback_upstream"
    assert first_provenance["error_code"] == RETRIEVAL_RERANK_DEGRADED
    dumped = str(retriever.last_trace.model_dump()) + str(result[0].model_dump())
    assert "secret full query text" not in dumped
    assert "password" not in dumped.lower()
    assert "C:\\" not in dumped
    assert "provider raw response" not in dumped.lower()


@pytest.mark.asyncio
async def test_reranking_retriever_timeout_uses_fallback_policy() -> None:
    request = _request()
    filters = _filters(request)
    retriever = RerankingRetriever(
        upstream_retriever=RecordingRetriever([_candidate(chunk_id="first", score=0.80)]),
        reranker=FakeReranker(failure_mode="timeout"),
        config=RerankConfig(failure_policy="fallback"),
    )

    result = await retriever.retrieve(request=request, filters=filters)

    assert [candidate.chunk_id for candidate in result] == ["first"]
    assert retriever.last_trace is not None
    assert retriever.last_trace.error_code == RETRIEVAL_RERANK_DEGRADED


@pytest.mark.asyncio
async def test_reranking_retriever_fail_closed_maps_failures_to_safe_retrieval_error() -> None:
    request = _request(query="secret full query text")
    filters = _filters(request)
    retriever = RerankingRetriever(
        upstream_retriever=RecordingRetriever([_candidate(chunk_id="first", score=0.80)]),
        reranker=FakeReranker(failure_mode="raise_unexpected"),
        config=RerankConfig(failure_policy="fail_closed"),
    )

    with pytest.raises(RetrievalError) as exc_info:
        await retriever.retrieve(request=request, filters=filters)

    assert exc_info.value.code == RETRIEVAL_RERANK_FAILED
    assert exc_info.value.details == {
        "request_id": "req-1",
        "trace_id": "trace-1",
        "tenant_id": "tenant-a",
        "user_id": "user-1",
        "top_k": 10,
        "retrieval_method": "hybrid",
        "rerank_stage": "rerank",
        "provider": "fake",
        "model": "fake-reranker-v1",
        "safe_counts": {"input_candidates": 1, "output_candidates": 0},
        "error_code": RETRIEVAL_RERANK_FAILED,
    }
    assert "secret full query text" not in str(exc_info.value.details)
    assert "password" not in str(exc_info.value.details).lower()


@pytest.mark.asyncio
async def test_reranking_retriever_rejects_invalid_scores() -> None:
    request = _request()
    filters = _filters(request)
    retriever = RerankingRetriever(
        upstream_retriever=RecordingRetriever([_candidate(chunk_id="bad", score=0.80)]),
        reranker=FakeReranker(failure_mode="invalid_score"),
        config=RerankConfig(failure_policy="fail_closed"),
    )

    with pytest.raises(RetrievalError) as exc_info:
        await retriever.retrieve(request=request, filters=filters)

    assert exc_info.value.code == RETRIEVAL_RERANK_INVALID_SCORE


@pytest.mark.asyncio
async def test_reranking_retriever_restores_safe_identity_from_provider_mutation() -> None:
    request = _request()
    filters = _filters(request)
    retriever = RerankingRetriever(
        upstream_retriever=RecordingRetriever([_candidate(chunk_id="safe", score=0.80)]),
        reranker=MutatingReranker(),
        config=RerankConfig(),
    )

    result = await retriever.retrieve(request=request, filters=filters)

    assert len(result) == 1
    candidate = result[0]
    assert candidate.score == 0.80
    assert candidate.tenant_id == "tenant-a"
    assert candidate.document_id == "doc-safe"
    assert candidate.version_id == "ver-safe"
    assert candidate.chunk_id == "safe"
    assert candidate.acl == {"visibility": "tenant", "allowed_roles": ["hr"]}
    assert candidate.source == "kb://safe.md"
    assert candidate.page_start == 1
    assert candidate.title_path == ("Policy", "safe")
    dumped = str(candidate.model_dump())
    assert "wrong-tenant" not in dumped
    assert "evil" not in dumped
    assert "must not survive" not in dumped


@pytest.mark.asyncio
async def test_reranking_retriever_blocks_out_of_scope_candidates_before_provider() -> None:
    request = _request()
    filters = _filters(request)
    reranker = SpyReranker()
    retriever = RerankingRetriever(
        upstream_retriever=RecordingRetriever(
            [_candidate(chunk_id="other", score=0.80, tenant_id="tenant-b")]
        ),
        reranker=reranker,
        config=RerankConfig(),
    )

    with pytest.raises(RetrievalError) as exc_info:
        await retriever.retrieve(request=request, filters=filters)

    assert exc_info.value.code == RETRIEVAL_RERANK_INVALID_CANDIDATE
    assert reranker.called is False
    assert retriever.last_trace is not None
    assert retriever.last_trace.error_code == RETRIEVAL_RERANK_INVALID_CANDIDATE


@pytest.mark.asyncio
async def test_reranking_retriever_sanitizes_candidates_before_provider() -> None:
    request = _request()
    filters = _filters(request)
    reranker = SpyReranker()
    retriever = RerankingRetriever(
        upstream_retriever=RecordingRetriever([_candidate(chunk_id="safe", score=0.80)]),
        reranker=reranker,
        config=RerankConfig(),
    )

    await retriever.retrieve(request=request, filters=filters)

    assert reranker.seen_candidates
    dumped = str(reranker.seen_candidates[0].metadata)
    assert "chunk_content" not in dumped
    assert "secret full query" not in dumped
    assert "select * from secrets" not in dumped
    assert "vector" not in dumped.lower()


@pytest.mark.asyncio
async def test_reranking_retriever_degrades_unknown_partial_and_duplicate_outputs() -> None:
    request = _request()
    filters = _filters(request)

    for mode in ("unknown", "partial", "duplicate"):
        retriever = RerankingRetriever(
            upstream_retriever=RecordingRetriever(
                [
                    _candidate(chunk_id="first", score=0.80),
                    _candidate(chunk_id="second", score=0.70),
                ]
            ),
            reranker=MalformedReranker(mode=mode),
            config=RerankConfig(failure_policy="fallback"),
        )

        result = await retriever.retrieve(request=request, filters=filters)

        assert [candidate.chunk_id for candidate in result] == ["first", "second"]
        assert retriever.last_trace is not None
        assert retriever.last_trace.error_code == RETRIEVAL_RERANK_DEGRADED
        assert _rerank_provenance(result[0])["score_source"] == "fallback_upstream"


@pytest.mark.asyncio
async def test_reranking_retriever_fail_closed_rejects_unknown_provider_output() -> None:
    request = _request()
    filters = _filters(request)
    retriever = RerankingRetriever(
        upstream_retriever=RecordingRetriever([_candidate(chunk_id="first", score=0.80)]),
        reranker=MalformedReranker(mode="unknown"),
        config=RerankConfig(failure_policy="fail_closed"),
    )

    with pytest.raises(RetrievalError) as exc_info:
        await retriever.retrieve(request=request, filters=filters)

    assert exc_info.value.code == RETRIEVAL_RERANK_INVALID_CANDIDATE
    assert retriever.last_trace is not None
    assert retriever.last_trace.error_code == RETRIEVAL_RERANK_INVALID_CANDIDATE


@pytest.mark.asyncio
async def test_reranking_retriever_rejects_invalid_upstream_score_before_fallback() -> None:
    request = _request()
    filters = _filters(request)
    reranker = SpyReranker()
    retriever = RerankingRetriever(
        upstream_retriever=RecordingRetriever([_candidate(chunk_id="bad", score=1.20)]),
        reranker=reranker,
        config=RerankConfig(failure_policy="fallback"),
    )

    with pytest.raises(RetrievalError) as exc_info:
        await retriever.retrieve(request=request, filters=filters)

    assert exc_info.value.code == RETRIEVAL_RERANK_INVALID_SCORE
    assert reranker.called is False
    assert retriever.last_trace is not None
    assert retriever.last_trace.error_code == RETRIEVAL_RERANK_INVALID_SCORE


@pytest.mark.asyncio
async def test_reranking_retriever_updates_trace_on_fail_closed_error() -> None:
    retriever = RerankingRetriever(
        upstream_retriever=RecordingRetriever([_candidate(chunk_id="first", score=0.80)]),
        reranker=FakeReranker(),
        config=RerankConfig(failure_policy="fail_closed"),
    )

    await retriever.retrieve(
        request=_request(request_id="req-ok", trace_id="trace-ok"),
        filters=_filters(_request(request_id="req-ok", trace_id="trace-ok")),
    )
    retriever._reranker = FakeReranker(failure_mode="raise_unexpected")  # noqa: SLF001

    with pytest.raises(RetrievalError):
        await retriever.retrieve(
            request=_request(request_id="req-fail", trace_id="trace-fail"),
            filters=_filters(_request(request_id="req-fail", trace_id="trace-fail")),
        )

    assert retriever.last_trace is not None
    assert retriever.last_trace.request_id == "req-fail"
    assert retriever.last_trace.trace_id == "trace-fail"
    assert retriever.last_trace.error_code == RETRIEVAL_RERANK_FAILED


@pytest.mark.asyncio
async def test_reranking_retriever_marks_disabled_candidates_with_safe_provenance() -> None:
    request = _request(top_k=1)
    filters = _filters(request)
    retriever = RerankingRetriever(
        upstream_retriever=RecordingRetriever(
            [
                _candidate(chunk_id="first", score=0.80),
                _candidate(chunk_id="second", score=0.70),
            ]
        ),
        reranker=FakeReranker(),
        config=RerankConfig(enabled=False),
    )

    result = await retriever.retrieve(request=request, filters=filters)

    assert [candidate.chunk_id for candidate in result] == ["first"]
    provenance = _rerank_provenance(result[0])
    assert provenance["status"] == "disabled"
    assert provenance["score_source"] == "disabled_upstream"
    assert retriever.last_trace is not None
    assert retriever.last_trace.input_count == 2
    assert retriever.last_trace.output_count == 1


@pytest.mark.asyncio
async def test_retrieval_service_accepts_reranking_retriever_and_keeps_result_guard() -> None:
    retriever = RerankingRetriever(
        upstream_retriever=RecordingRetriever(
            [
                _candidate(chunk_id="low", score=0.30),
                _candidate(chunk_id="high", score=0.80),
            ]
        ),
        reranker=FakeReranker(),
        config=RerankConfig(),
    )
    service = RetrievalService(retriever=retriever)

    result = await service.retrieve(
        request=_request(metadata_filter={"department": "people"}, score_threshold=0.5),
        auth=_auth(),
    )

    assert [candidate.chunk_id for candidate in result.candidates] == ["high"]
    provenance = cast(
        "Mapping[str, object]",
        result.candidates[0].metadata[RERANK_PROVENANCE_METADATA_KEY],
    )
    assert provenance["score_source"] == "rerank"


def test_rerank_config_rejects_invalid_values() -> None:
    invalid_policy = cast(Any, "open")
    with pytest.raises(ValueError, match="failure_policy"):
        RerankConfig(failure_policy=invalid_policy)
    with pytest.raises(ValueError, match="timeout_seconds"):
        RerankConfig(timeout_seconds=0)
    with pytest.raises(ValueError, match="provider"):
        RerankConfig(provider=" ")
    with pytest.raises(ValueError, match="model"):
        RerankConfig(model="")
    with pytest.raises(ValueError, match="max_candidates"):
        RerankConfig(max_candidates=0)


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


class MutatingReranker:
    async def rerank(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
        candidates: Sequence[RetrievalCandidate],
    ) -> RerankResult:
        safe = candidates[0]
        mutated = safe.model_copy(
            update={
                "tenant_id": "wrong-tenant",
                "document_id": "evil-doc",
                "version_id": "evil-version",
                "chunk_id": "evil-chunk",
                "source": "file:///C:/secret.md",
                "page_start": 99,
                "page_end": 100,
                "title_path": ("Evil",),
                "acl": {"visibility": "public"},
                "metadata": {
                    "department": "people",
                    "chunk_content": "must not survive",
                },
                "score": 0.99,
            }
        )
        return RerankResult(
            candidates=(mutated,),
            trace=RerankTrace(
                request_id=request.request_id,
                trace_id=request.trace_id,
                tenant_id=filters.tenant_id,
                user_id=filters.user_id,
                provider="fake",
                model="fake-reranker-v1",
                latency_ms=0.0,
                input_count=len(candidates),
                output_count=1,
                safe_counts={"input_candidates": len(candidates), "output_candidates": 1},
            ),
        )


class MalformedReranker:
    def __init__(self, *, mode: str) -> None:
        self._mode = mode

    async def rerank(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
        candidates: Sequence[RetrievalCandidate],
    ) -> RerankResult:
        output: tuple[RetrievalCandidate, ...]
        if self._mode == "unknown":
            output = (
                candidates[0].model_copy(
                    update={
                        "tenant_id": "tenant-b",
                        "document_id": "evil-doc",
                        "version_id": "evil-version",
                        "chunk_id": "evil",
                        "score": 0.99,
                    }
                ),
                *candidates[1:],
            )
        elif self._mode == "partial":
            output = tuple(candidates[:1])
        elif self._mode == "duplicate":
            output = (candidates[0], candidates[0])
        else:
            output = tuple(candidates)
        return RerankResult(
            candidates=tuple(output),
            trace=RerankTrace(
                request_id=request.request_id,
                trace_id=request.trace_id,
                tenant_id=filters.tenant_id,
                user_id=filters.user_id,
                provider="fake",
                model="fake-reranker-v1",
                latency_ms=0.0,
                input_count=len(candidates),
                output_count=len(output),
                safe_counts={
                    "input_candidates": len(candidates),
                    "output_candidates": len(output),
                },
            ),
        )


class SpyReranker:
    def __init__(self) -> None:
        self.called = False
        self.seen_candidates: tuple[RetrievalCandidate, ...] = ()

    async def rerank(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
        candidates: Sequence[RetrievalCandidate],
    ) -> RerankResult:
        self.called = True
        self.seen_candidates = tuple(candidates)
        reranker = FakeReranker()
        return await reranker.rerank(request=request, filters=filters, candidates=candidates)


def _request(
    *,
    query: str = "policy",
    top_k: int = 10,
    request_id: str = "req-1",
    trace_id: str = "trace-1",
    score_threshold: float | None = None,
    metadata_filter: dict[str, object] | None = None,
) -> RetrievalRequest:
    return RetrievalRequest(
        query=query,
        top_k=top_k,
        score_threshold=score_threshold,
        metadata_filter=metadata_filter or {},
        request_id=request_id,
        trace_id=trace_id,
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
    score: float,
    tenant_id: str = "tenant-a",
    department: str = "people",
) -> RetrievalCandidate:
    return RetrievalCandidate(
        document_id=f"doc-{chunk_id}",
        version_id=f"ver-{chunk_id}",
        chunk_id=chunk_id,
        source=f"kb://{chunk_id}.md",
        source_type="markdown",
        source_uri=f"kb://{chunk_id}.md",
        page_start=1,
        page_end=2,
        title_path=("Policy", chunk_id),
        score=score,
        retrieval_method="hybrid",
        tenant_id=tenant_id,
        acl={"visibility": "tenant", "allowed_roles": ["hr"]},
        metadata={
            "department": department,
            "chunk_content": "must not be copied",
            "query": "secret full query",
            "sql": "select * from secrets",
            "vector": [0.1, 0.2],
        },
    )


def _rerank_provenance(candidate: RetrievalCandidate) -> dict[str, Any]:
    return cast("dict[str, Any]", candidate.metadata[RERANK_PROVENANCE_METADATA_KEY])
