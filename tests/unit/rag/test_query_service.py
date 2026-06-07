from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Never, cast

import pytest

from packages.auth.context import AuthContext
from packages.common.audit import AuditEvent, AuditPort, InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.data.dto import ChunkRecord
from packages.llm.adapters.fake import FailureMode, FakeLLMProvider
from packages.llm.dto import GenerateChunk, GenerateRequest
from packages.rag import (
    RAG_CONTEXT_UNAUTHORIZED_CHUNK,
    RAG_GENERATION_FAILED,
    RAG_QUERY_CLIENT_DISCONNECTED,
    RAG_QUERY_CONTEXT_UNAVAILABLE,
    RAG_QUERY_FORBIDDEN,
    CitationExtractor,
    ContextCandidate,
    ContextPacker,
    ContextPackingConfig,
    FinalEventPayload,
    PromptBuilder,
    PromptHistoryMessage,
    PromptMemoryContext,
    QueryCommand,
    RagContextPackingError,
    RagGenerationService,
    RagQueryApplicationService,
    RagQueryError,
    RagStreamEvent,
    RetrievalCandidateHydrator,
)
from packages.retrieval.dto import RetrievalCandidate, RetrievalRequest, RetrievalResult


@pytest.mark.asyncio
async def test_query_service_runs_non_streaming_rag_chain_with_safe_metadata() -> None:
    audit = InMemoryAuditPort()
    service = _service(audit=audit)

    response = await service.query(context=_context(), command=QueryCommand(query="私密问题"))

    assert response.answer == "基于上下文的回答。"
    assert response.no_answer is False
    assert response.citations[0].chunk_id == "chunk-1"
    retrieval_metadata = cast(Mapping[str, object], response.metadata["retrieval"])
    context_metadata = cast(Mapping[str, object], response.metadata["context"])
    generation_metadata = cast(Mapping[str, object], response.metadata["generation"])
    assert retrieval_metadata["result_count"] == 1
    assert context_metadata["citation_source_count"] == 1
    assert generation_metadata["provider"] == "fake"
    dumped = str(response.model_dump()).lower()
    for forbidden in ("私密问题", "授权正文", "raw_response", "api_key", "access_token"):
        assert forbidden.lower() not in dumped
    assert audit.events[-1].action == "rag.query"
    assert audit.events[-1].metadata["citation_count"] == 1
    assert audit.events[-1].metadata["context_source_count"] == 1
    assert audit.events[-1].metadata["provider"] == "fake"
    assert audit.events[-1].metadata["model"] == "fake-llm"
    token_usage = cast(Mapping[str, object], audit.events[-1].metadata["token_usage"])
    assert isinstance(token_usage["input_tokens"], int)
    assert isinstance(token_usage["output_tokens"], int)
    assert isinstance(token_usage["total_tokens"], int)
    assert token_usage["input_tokens"] >= 1
    assert token_usage["output_tokens"] >= 1
    assert token_usage["total_tokens"] >= 1
    assert audit.events[-1].metadata["tool_calls"] == 0


@pytest.mark.asyncio
async def test_query_service_accepts_memory_context_without_changing_retrieval_scope() -> None:
    audit = InMemoryAuditPort()
    service = _service(audit=audit)
    memory_context = PromptMemoryContext(
        session_id="session-1",
        messages=(
            PromptHistoryMessage(
                role="user",
                content="previous safe question",
                token_count=4,
                sequence_no=1,
            ),
        ),
        message_count=2,
        used_count=1,
        dropped_count=1,
        token_count=4,
    )

    response = await service.query(
        context=_context(session_id="session-1"),
        command=QueryCommand(query="继续问"),
        memory_context=memory_context,
    )

    assert response.metadata["memory"] == {
        "message_count": 2,
        "used_count": 1,
        "dropped_count": 1,
        "token_count": 4,
    }
    assert audit.events[-1].metadata["memory_message_count"] == 2
    assert audit.events[-1].metadata["memory_used_count"] == 1
    retrieval_service = service._retrieval_service  # noqa: SLF001
    assert isinstance(retrieval_service, FakeRetrievalService)
    assert retrieval_service.requests[-1].query == "继续问"


@pytest.mark.asyncio
async def test_query_service_requires_query_permission_before_retrieval() -> None:
    audit = InMemoryAuditPort()
    service = _service(audit=audit)

    with pytest.raises(RagQueryError) as exc_info:
        await service.query(
            context=_context(permissions=()),
            command=QueryCommand(query="问题"),
        )

    assert exc_info.value.code == RAG_QUERY_FORBIDDEN
    assert exc_info.value.status_code == 403
    assert audit.events[-1].status.value == "denied"
    assert audit.events[-1].error_code == RAG_QUERY_FORBIDDEN


@pytest.mark.asyncio
async def test_query_service_returns_no_answer_when_context_unavailable() -> None:
    service = _service(retrieval_candidates=())

    response = await service.query(context=_context(), command=QueryCommand(query="问题"))

    assert response.no_answer is True
    assert response.answer == "无法从给定上下文确认。"
    assert response.citations == ()
    assert response.metadata["error_code"] == RAG_QUERY_CONTEXT_UNAVAILABLE


@pytest.mark.asyncio
async def test_hydration_fails_closed_on_identity_mismatch_without_content_leak() -> None:
    service = _service(
        chunk=_chunk(chunk_id="different"),
    )

    with pytest.raises(RagQueryError) as exc_info:
        await service.query(context=_context(), command=QueryCommand(query="问题"))

    assert exc_info.value.code == RAG_QUERY_CONTEXT_UNAVAILABLE
    assert exc_info.value.details["stage"] == "hydration"
    assert "授权正文" not in str(exc_info.value.details)
    assert "document_id" not in exc_info.value.details
    assert "version_id" not in exc_info.value.details
    assert "chunk_id" not in exc_info.value.details
    assert "reason" not in exc_info.value.details


@pytest.mark.asyncio
async def test_hydration_denies_inactive_deleted_and_acl_denied_chunks() -> None:
    for chunk in (
        _chunk(status="archived"),
        _chunk(deleted_at=datetime.now(tz=UTC)),
        _chunk(acl={"visibility": "private", "allowed_user_ids": ["other"]}),
    ):
        service = _service(chunk=chunk)
        with pytest.raises(RagQueryError):
            await service.query(context=_context(), command=QueryCommand(query="问题"))


@pytest.mark.asyncio
async def test_hydration_normalizes_scores_to_context_candidate_range() -> None:
    service = _service(retrieval_candidates=(_candidate(score=7.5),))

    response = await service.query(context=_context(), command=QueryCommand(query="问题"))

    assert response.citations[0].score == 1.0


@pytest.mark.asyncio
async def test_stream_query_yields_citation_tokens_and_final_with_safe_audit() -> None:
    audit = InMemoryAuditPort()
    service = _service(audit=audit)

    events = await _collect_stream(
        service.stream_query(context=_context(), command=QueryCommand(query="私密问题"))
    )

    assert [event.event for event in events] == [
        "citation",
        "token",
        "final",
    ]
    citation_payload = events[0].payload
    assert citation_payload.event == "citation"
    assert citation_payload.citation.chunk_id == "chunk-1"
    token_payload = events[1].payload
    assert token_payload.event == "token"
    assert token_payload.request_id == "req-1"
    assert token_payload.delta
    final_payload = cast(FinalEventPayload, events[-1].payload)
    assert final_payload.event == "final"
    assert final_payload.status == "success"
    assert final_payload.answer == "基于上下文的回答。"
    assert final_payload.citations[0].chunk_id == "chunk-1"
    stream_metadata = cast(Mapping[str, object], final_payload.metadata["stream"])
    assert stream_metadata["event_counts"] == [
        {"event": "token", "count": 1},
        {"event": "citation", "count": 1},
        {"event": "error", "count": 0},
        {"event": "final", "count": 1},
    ]
    dumped = str(final_payload.model_dump()).lower()
    for forbidden in ("私密问题", "授权正文", "raw_response", "api_key", "access_token"):
        assert forbidden.lower() not in dumped
    assert audit.events[-1].action == "rag.query.stream"
    assert audit.events[-1].metadata["event_counts"] == [
        {"event": "token", "count": 1},
        {"event": "citation", "count": 1},
        {"event": "error", "count": 0},
        {"event": "final", "count": 1},
    ]
    assert audit.events[-1].metadata["citation_count"] == 1
    assert "授权正文" not in str(audit.events[-1].metadata)


@pytest.mark.asyncio
async def test_stream_query_returns_no_answer_final_without_llm_stream() -> None:
    audit = InMemoryAuditPort()
    service = _service(retrieval_candidates=(), audit=audit)

    events = await _collect_stream(
        service.stream_query(context=_context(), command=QueryCommand(query="问题"))
    )

    assert [event.event for event in events] == ["final"]
    final_payload = events[0].payload
    assert final_payload.event == "final"
    assert final_payload.no_answer is True
    assert final_payload.answer == "无法从给定上下文确认。"
    assert final_payload.citations == ()
    assert final_payload.metadata["error_code"] == RAG_QUERY_CONTEXT_UNAVAILABLE
    stream_metadata = cast(Mapping[str, object], final_payload.metadata["stream"])
    assert stream_metadata["event_counts"] == [
        {"event": "token", "count": 0},
        {"event": "citation", "count": 0},
        {"event": "error", "count": 0},
        {"event": "final", "count": 1},
    ]
    assert audit.events[-1].action == "rag.query.stream"
    assert audit.events[-1].metadata["provider"] is None


@pytest.mark.asyncio
async def test_stream_query_converts_provider_stream_failure_to_error_and_final() -> None:
    audit = InMemoryAuditPort()
    service = _service(audit=audit, provider_failure_mode="stream_failed")

    events = await _collect_stream(
        service.stream_query(context=_context(), command=QueryCommand(query="私密问题"))
    )

    assert [event.event for event in events] == ["citation", "error", "final"]
    error_payload = events[1].payload
    assert error_payload.event == "error"
    assert error_payload.code == "LLM_STREAM_FAILED"
    assert error_payload.terminal is True
    assert error_payload.details["stage"] == "generation_stream"
    final_payload = events[-1].payload
    assert final_payload.event == "final"
    assert final_payload.status == "error"
    assert final_payload.no_answer is True
    assert final_payload.metadata["error_code"] == "LLM_STREAM_FAILED"
    retrieval_metadata = cast(Mapping[str, object], final_payload.metadata["retrieval"])
    context_metadata = cast(Mapping[str, object], final_payload.metadata["context"])
    generation_metadata = cast(Mapping[str, object], final_payload.metadata["generation"])
    assert retrieval_metadata["result_count"] == 1
    assert context_metadata["item_count"] == 1
    assert context_metadata["citation_source_count"] == 1
    assert generation_metadata["provider"] == "fake"
    assert generation_metadata["model"] == "fake-llm"
    token_usage = cast(Mapping[str, object], generation_metadata["token_usage"])
    assert token_usage["total_tokens"] == 0
    stream_metadata = cast(Mapping[str, object], final_payload.metadata["stream"])
    assert stream_metadata["event_counts"] == [
        {"event": "token", "count": 0},
        {"event": "citation", "count": 1},
        {"event": "error", "count": 1},
        {"event": "final", "count": 1},
    ]
    assert audit.events[-1].status.value == "failure"
    assert audit.events[-1].error_code == "LLM_STREAM_FAILED"
    assert audit.events[-1].metadata["result_count"] == 1
    assert audit.events[-1].metadata["context_item_count"] == 1
    assert audit.events[-1].metadata["provider"] == "fake"
    assert audit.events[-1].metadata["model"] == "fake-llm"


@pytest.mark.asyncio
async def test_stream_query_hydration_failure_error_details_remain_safe() -> None:
    service = _service(chunk=_chunk(chunk_id="different"))

    events = await _collect_stream(
        service.stream_query(context=_context(), command=QueryCommand(query="私密问题"))
    )

    assert [event.event for event in events] == ["error", "final"]
    error_payload = events[0].payload
    assert error_payload.event == "error"
    assert error_payload.code == RAG_QUERY_CONTEXT_UNAVAILABLE
    assert error_payload.details["stage"] == "hydration"
    dumped = str(error_payload.model_dump()).lower()
    for forbidden in ("私密问题", "授权正文", "document_id", "version_id", "chunk_id"):
        assert forbidden.lower() not in dumped


@pytest.mark.asyncio
async def test_stream_query_context_error_details_use_safe_allowlist() -> None:
    service = _service(context_packer=LeakyContextPacker())

    events = await _collect_stream(
        service.stream_query(context=_context(), command=QueryCommand(query="私密问题"))
    )

    assert [event.event for event in events] == ["error", "final"]
    error_payload = events[0].payload
    assert error_payload.event == "error"
    assert error_payload.code == RAG_CONTEXT_UNAUTHORIZED_CHUNK
    assert error_payload.details == {
        "request_id": "req-1",
        "trace_id": "trace-1",
        "stage": "context_packing",
        "reason": "acl_denied",
        "error_code": RAG_CONTEXT_UNAUTHORIZED_CHUNK,
        "safe_counts": {"input_candidates": 1},
    }
    dumped = str(error_payload.model_dump()).lower()
    for forbidden in ("doc-secret", "version-secret", "chunk-secret", "tenant-secret"):
        assert forbidden not in dumped


@pytest.mark.asyncio
async def test_stream_query_rejects_provider_chunks_after_final() -> None:
    service = _service(provider=FinalThenTokenProvider(response_text="基于上下文的回答。"))

    events = await _collect_stream(
        service.stream_query(context=_context(), command=QueryCommand(query="私密问题"))
    )

    assert [event.event for event in events] == ["citation", "token", "error", "final"]
    error_payload = events[2].payload
    assert error_payload.event == "error"
    assert error_payload.code == RAG_GENERATION_FAILED
    assert error_payload.details["stage"] == "generation_stream"
    assert error_payload.details["reason"] == "provider_stream_chunk_after_final"
    final_payload = events[-1].payload
    assert final_payload.event == "final"
    assert final_payload.status == "error"


@pytest.mark.asyncio
async def test_stream_query_audits_partial_client_disconnect() -> None:
    audit = InMemoryAuditPort()
    service = _service(audit=audit)

    stream = cast(
        AsyncGenerator[RagStreamEvent, None],
        service.stream_query(context=_context(), command=QueryCommand(query="私密问题")),
    )
    first_event = await anext(stream)
    await stream.aclose()

    assert first_event.event == "citation"
    assert audit.events[-1].action == "rag.query.stream"
    assert audit.events[-1].status.value == "failure"
    assert audit.events[-1].error_code == RAG_QUERY_CLIENT_DISCONNECTED
    assert audit.events[-1].metadata["event_counts"] == [
        {"event": "token", "count": 0},
        {"event": "citation", "count": 1},
        {"event": "error", "count": 1},
        {"event": "final", "count": 0},
    ]


@pytest.mark.asyncio
async def test_stream_query_logs_audit_failure_without_blocking_final(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="packages.rag.query")
    service = _service(audit=FailingAuditPort())

    events = await _collect_stream(
        service.stream_query(context=_context(), command=QueryCommand(query="私密问题"))
    )

    assert events[-1].event == "final"
    final_payload = cast(FinalEventPayload, events[-1].payload)
    assert final_payload.status == "success"
    assert any(record.message == "rag.query.stream.audit_failed" for record in caplog.records)


class FakeRetrievalService:
    def __init__(self, candidates: tuple[RetrievalCandidate, ...]) -> None:
        self.candidates = candidates
        self.requests: list[RetrievalRequest] = []

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        auth: AuthContext | None,
    ) -> RetrievalResult:
        assert auth is not None
        self.requests.append(request)
        return RetrievalResult(
            request_id=request.request_id,
            trace_id=request.trace_id,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            top_k=request.top_k,
            query_summary={"length": len(request.query)},
            candidates=self.candidates,
            latency_ms=1.0,
        )


class FakeChunkRepository:
    def __init__(self, chunk: ChunkRecord | None) -> None:
        self.chunk = chunk

    async def get_chunk(
        self,
        *,
        tenant_id: str,
        chunk_id: str,
        document_id: str | None = None,
        version_id: str | None = None,
    ) -> ChunkRecord | None:
        return self.chunk


class FailingAuditPort:
    async def record(self, event: AuditEvent) -> None:
        _ = event
        raise RuntimeError("audit unavailable")


class LeakyContextPacker(ContextPacker):
    def pack(
        self,
        *,
        candidates: object,
        auth: AuthContext,
        config: ContextPackingConfig | None = None,
        related_chunks_by_id: Mapping[str, ContextCandidate] | None = None,
        request_id: str,
        trace_id: str,
    ) -> Never:
        _ = candidates, auth, config, related_chunks_by_id
        raise RagContextPackingError(
            code=RAG_CONTEXT_UNAUTHORIZED_CHUNK,
            message="Context candidate is not authorized for this request.",
            details={
                "request_id": request_id,
                "trace_id": trace_id,
                "stage": "context_packing",
                "reason": "acl_denied",
                "document_id": "doc-secret",
                "version_id": "version-secret",
                "chunk_id": "chunk-secret",
                "candidate_tenant_id": "tenant-secret",
                "error_code": RAG_CONTEXT_UNAUTHORIZED_CHUNK,
                "safe_counts": {"input_candidates": 1},
            },
            status_code=403,
        )


class FinalThenTokenProvider(FakeLLMProvider):
    async def stream(self, request: GenerateRequest) -> AsyncIterator[GenerateChunk]:
        final_index = 0
        final_metadata = None
        async for chunk in super().stream(request):
            final_index = chunk.index
            final_metadata = chunk.metadata
            yield chunk
        yield GenerateChunk(
            delta="late-token",
            index=final_index + 1,
            is_final=False,
            metadata=final_metadata,
        )


async def _collect_stream(stream: AsyncIterator[RagStreamEvent]) -> list[RagStreamEvent]:
    return [event async for event in stream]


def _service(
    *,
    retrieval_candidates: tuple[RetrievalCandidate, ...] | None = None,
    chunk: ChunkRecord | None = None,
    audit: AuditPort | None = None,
    provider_failure_mode: FailureMode | None = None,
    provider: FakeLLMProvider | None = None,
    context_packer: ContextPacker | None = None,
) -> RagQueryApplicationService:
    candidates = retrieval_candidates if retrieval_candidates is not None else (_candidate(),)
    hydrated_chunk = chunk if chunk is not None else _chunk()
    return RagQueryApplicationService(
        retrieval_service=FakeRetrievalService(candidates),
        hydrator=RetrievalCandidateHydrator(repository=FakeChunkRepository(hydrated_chunk)),
        context_packer=context_packer or ContextPacker(),
        prompt_builder=PromptBuilder(),
        generation_service=RagGenerationService(
            provider=provider
            or FakeLLMProvider(
                response_text="基于上下文的回答。",
                failure_mode=provider_failure_mode,
            )
        ),
        citation_extractor=CitationExtractor(),
        audit=audit or InMemoryAuditPort(),
    )


def _context(
    *,
    permissions: tuple[str, ...] = ("document:read", "retrieval:query"),
    session_id: str | None = None,
) -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-1",
        trace_id="trace-1",
        session_id=session_id,
        auth=AuthContext(
            tenant_id="tenant-1",
            user_id="user-1",
            roles=("knowledge_user",),
            permissions=permissions,
        ),
    )


def _candidate(
    *,
    score: float = 0.92,
    acl: Mapping[str, object] | None = None,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        tenant_id="tenant-1",
        document_id="doc-1",
        version_id="v1",
        chunk_id="chunk-1",
        source="policy.md",
        source_uri="kb://policy.md",
        source_type="markdown",
        page_start=1,
        page_end=1,
        title_path=("Policy",),
        score=score,
        retrieval_method="hybrid",
        acl=acl or {"visibility": "tenant"},
        metadata={"retrieval_provenance": {"fusion_reason": "dense_sparse_overlap"}},
    )


def _chunk(
    *,
    tenant_id: str = "tenant-1",
    document_id: str = "doc-1",
    version_id: str = "v1",
    chunk_id: str = "chunk-1",
    status: str = "active",
    deleted_at: datetime | None = None,
    acl: Mapping[str, object] | None = None,
) -> ChunkRecord:
    return ChunkRecord(
        tenant_id=tenant_id,
        document_id=document_id,
        version_id=version_id,
        chunk_id=chunk_id,
        created_by="user-1",
        status=status,
        source_type="markdown",
        source_uri="kb://policy.md",
        title_path=["Policy"],
        content="授权正文",
        page_start=1,
        page_end=1,
        token_count=20,
        acl=dict(acl or {"visibility": "tenant"}),
        checksum="checksum",
        section_ids=["section-1"],
        deleted_at=deleted_at,
    )
