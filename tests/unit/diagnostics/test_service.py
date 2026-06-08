from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import pytest

from packages.auth.context import AuthContext
from packages.common.context import AuthenticatedRequestContext
from packages.data.storage.audit_repositories import AuditLogRecord
from packages.data.storage.exceptions import StorageError
from packages.diagnostics.dto import DiagnosticsLookupRequest, FailureStage
from packages.diagnostics.exceptions import (
    DIAGNOSTICS_FORBIDDEN,
    DIAGNOSTICS_NOT_FOUND,
    DIAGNOSTICS_STORAGE_READ_FAILED,
    DiagnosticsError,
)
from packages.diagnostics.service import DiagnosticsService
from packages.retrieval.dto import RetrievalLogRecord


class FakeRetrievalLogReader:
    def __init__(self, records: list[RetrievalLogRecord]) -> None:
        self.records = records
        self.request_calls: list[tuple[str, str]] = []
        self.trace_calls: list[tuple[str, str]] = []

    async def list_by_request_id(
        self,
        *,
        tenant_id: str,
        request_id: str,
    ) -> list[RetrievalLogRecord]:
        self.request_calls.append((tenant_id, request_id))
        return [
            record
            for record in self.records
            if record.tenant_id == tenant_id and record.request_id == request_id
        ]

    async def list_by_trace_id(
        self,
        *,
        tenant_id: str,
        trace_id: str,
        limit: int = 100,
    ) -> list[RetrievalLogRecord]:
        self.trace_calls.append((tenant_id, trace_id))
        return [
            record
            for record in self.records
            if record.tenant_id == tenant_id and record.trace_id == trace_id
        ][:limit]


class FakeAuditLogReader:
    def __init__(self, records: list[AuditLogRecord]) -> None:
        self.records = records
        self.request_calls: list[tuple[str, str]] = []
        self.trace_calls: list[tuple[str, str]] = []

    async def list_by_request_id(
        self,
        *,
        tenant_id: str,
        request_id: str,
        limit: int = 100,
    ) -> list[AuditLogRecord]:
        self.request_calls.append((tenant_id, request_id))
        return [
            record
            for record in self.records
            if record.tenant_id == tenant_id and record.request_id == request_id
        ][:limit]

    async def list_by_trace_id(
        self,
        *,
        tenant_id: str,
        trace_id: str,
        limit: int = 100,
    ) -> list[AuditLogRecord]:
        self.trace_calls.append((tenant_id, trace_id))
        return [
            record
            for record in self.records
            if record.tenant_id == tenant_id and record.trace_id == trace_id
        ][:limit]


class FailingRetrievalLogReader(FakeRetrievalLogReader):
    async def list_by_request_id(
        self,
        *,
        tenant_id: str,
        request_id: str,
    ) -> list[RetrievalLogRecord]:
        raise StorageError(
            code="RETRIEVAL_LOG_STORAGE_READ_FAILED",
            message="read failed",
            details={"sql": "select * from chunks where token='secret'"},
        )


def _context(
    *,
    tenant_id: str = "tenant-1",
    permissions: tuple[str, ...] = ("audit:read",),
) -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-current",
        trace_id="trace-current",
        session_id=None,
        auth=AuthContext(
            tenant_id=tenant_id,
            user_id="user-platform",
            roles=("platform_engineer",),
            permissions=permissions,
        ),
    )


def _retrieval_record(
    *,
    tenant_id: str = "tenant-1",
    request_id: str = "req-1",
    trace_id: str = "trace-1",
    status: Literal["success", "failure"] = "success",
) -> RetrievalLogRecord:
    return RetrievalLogRecord(
        id="log-1",
        created_at=datetime(2026, 6, 9, tzinfo=UTC),
        updated_at=datetime(2026, 6, 9, tzinfo=UTC),
        request_id=request_id,
        trace_id=trace_id,
        tenant_id=tenant_id,
        user_id="user-1",
        created_by="user-1",
        status=status,
        latency_ms=20.0,
        top_k=5,
        result_count=2,
        rerank_score=0.82,
        error_code=None,
        query_summary={"length": 120},
        metadata={
            "dense_top_k": 8,
            "sparse_top_k": 6,
            "rrf": {"deduped_count": 4, "filtered_count": 2},
            "rerank": {"candidate_count": 2, "highest_score": 0.91},
            "query": "raw question must not leak",
            "source_uri": "file:///C:/secret/policy.md",
        },
    )


def _audit_record(
    *,
    tenant_id: str = "tenant-1",
    request_id: str = "req-1",
    trace_id: str = "trace-1",
    status: str = "success",
    action: str = "rag.query",
    error_code: str | None = None,
    metadata: dict[str, object] | None = None,
) -> AuditLogRecord:
    return AuditLogRecord(
        id="audit-1",
        tenant_id=tenant_id,
        user_id="user-1",
        created_by="user-1",
        status=status,
        request_id=request_id,
        trace_id=trace_id,
        action=action,
        resource_type="rag_query",
        resource_id=request_id,
        resource_metadata={},
        latency_ms=45.0,
        error_code=error_code,
        metadata=metadata
        or {
            "context": {"item_count": 3, "source_count": 2},
            "generation": {
                "provider": "fake",
                "model": "fake-model",
                "version": "fake-v1",
                "token_usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                },
            },
            "citation": {"citation_count": 1},
            "event_count": 4,
            "answer": "must not leak",
            "prompt": "must not leak",
        },
        created_at=datetime(2026, 6, 9, tzinfo=UTC),
        updated_at=datetime(2026, 6, 9, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_diagnostics_service_aggregates_safe_request_summary() -> None:
    retrieval_reader = FakeRetrievalLogReader([_retrieval_record()])
    audit_reader = FakeAuditLogReader([_audit_record()])
    service = DiagnosticsService(
        retrieval_logs=retrieval_reader,
        audit_logs=audit_reader,
        generated_at=lambda: "2026-06-09T00:00:00+08:00",
    )

    result = await service.resolve(
        context=_context(),
        lookup=DiagnosticsLookupRequest(request_id="req-1", include_report=True),
    )

    assert retrieval_reader.request_calls == [("tenant-1", "req-1")]
    assert audit_reader.request_calls == [("tenant-1", "req-1")]
    assert result.summary.tenant_id == "tenant-1"
    assert result.summary.user_id == "user-1"
    assert result.summary.top_k == 5
    assert result.summary.result_count == 2
    assert result.summary.highest_rerank_score == 0.82
    assert result.summary.citation_count == 1
    assert result.summary.context_item_count == 3
    assert result.summary.context_source_count == 2
    assert result.summary.generation_provider == "fake"
    assert result.summary.prompt_token_count == 11
    assert result.summary.total_token_count == 18
    assert result.report is not None
    assert "raw question" not in str(result.model_dump(mode="json")).lower()
    assert "full query" not in str(result.model_dump(mode="json")).lower()
    assert "answer" not in str(result.model_dump(mode="json")).lower()
    assert "source_uri" not in str(result.model_dump(mode="json")).lower()
    assert "C:/secret" not in str(result.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_diagnostics_service_supports_trace_lookup_and_tenant_isolation() -> None:
    service = DiagnosticsService(
        retrieval_logs=FakeRetrievalLogReader(
            [
                _retrieval_record(tenant_id="tenant-1"),
                _retrieval_record(tenant_id="tenant-2"),
            ]
        ),
        audit_logs=FakeAuditLogReader(
            [
                _audit_record(tenant_id="tenant-1"),
                _audit_record(tenant_id="tenant-2"),
            ]
        ),
    )

    result = await service.resolve(
        context=_context(tenant_id="tenant-1"),
        lookup=DiagnosticsLookupRequest(trace_id="trace-1"),
    )

    assert result.summary.tenant_id == "tenant-1"
    assert result.summary.request_id == "req-1"


@pytest.mark.asyncio
async def test_diagnostics_service_requires_diagnostics_permission() -> None:
    service = DiagnosticsService(
        retrieval_logs=FakeRetrievalLogReader([]),
        audit_logs=FakeAuditLogReader([]),
    )

    with pytest.raises(DiagnosticsError) as exc_info:
        await service.resolve(
            context=_context(permissions=("document:read",)),
            lookup=DiagnosticsLookupRequest(request_id="req-1"),
        )

    assert exc_info.value.code == DIAGNOSTICS_FORBIDDEN
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_diagnostics_service_not_found_does_not_disclose_other_tenants() -> None:
    service = DiagnosticsService(
        retrieval_logs=FakeRetrievalLogReader([_retrieval_record(tenant_id="tenant-2")]),
        audit_logs=FakeAuditLogReader([_audit_record(tenant_id="tenant-2")]),
    )

    with pytest.raises(DiagnosticsError) as exc_info:
        await service.resolve(
            context=_context(tenant_id="tenant-1"),
            lookup=DiagnosticsLookupRequest(request_id="req-1"),
        )

    assert exc_info.value.code == DIAGNOSTICS_NOT_FOUND
    assert "tenant-2" not in str(exc_info.value.details)


@pytest.mark.asyncio
async def test_diagnostics_service_maps_failure_stage_from_safe_audit_metadata() -> None:
    service = DiagnosticsService(
        retrieval_logs=FakeRetrievalLogReader(
            [_retrieval_record(status="failure")]
        ),
        audit_logs=FakeAuditLogReader(
            [
                _audit_record(
                    status="failure",
                    error_code="LLM_PROVIDER_FAILED",
                    metadata={"error_details": {"stage": "generation", "exception": "raw"}},
                )
            ]
        ),
    )

    result = await service.resolve(
        context=_context(),
        lookup=DiagnosticsLookupRequest(request_id="req-1"),
    )

    assert result.summary.failure_stage == FailureStage.GENERATION
    assert result.stages[-1].name == FailureStage.GENERATION
    assert result.stages[-1].error_code == "LLM_PROVIDER_FAILED"
    assert "exception" not in str(result.model_dump(mode="json")).lower()


@pytest.mark.asyncio
async def test_diagnostics_service_storage_failures_are_stable() -> None:
    service = DiagnosticsService(
        retrieval_logs=FailingRetrievalLogReader([]),
        audit_logs=FakeAuditLogReader([]),
    )

    with pytest.raises(DiagnosticsError) as exc_info:
        await service.resolve(
            context=_context(),
            lookup=DiagnosticsLookupRequest(request_id="req-1"),
        )

    assert exc_info.value.code == DIAGNOSTICS_STORAGE_READ_FAILED
    assert "select *" not in str(exc_info.value.details)
    assert "secret" not in str(exc_info.value.details)
