from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from packages.agent.dto import ToolCallQuery, ToolCallRecord
from packages.audit import (
    AUDIT_EXPLORER_FORBIDDEN,
    AuditExplorerError,
    AuditExplorerService,
    AuditExportRequest,
    AuditLogQueryRequest,
)
from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.data.storage.audit_repositories import AuditLogRecord


def test_query_request_rejects_tenant_and_invalid_time_window() -> None:
    with pytest.raises(ValidationError):
        AuditLogQueryRequest.model_validate(
            {
                "tenant_id": "tenant-evil",
                "request_id": "req-1",
            }
        )

    with pytest.raises(ValidationError):
        AuditLogQueryRequest(
            created_at_from=datetime(2026, 6, 9, 12, 0, tzinfo=UTC),
            created_at_to=datetime(2026, 6, 9, 11, 0, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_service_requires_audit_read_permission_without_leaking_query() -> None:
    service = AuditExplorerService(audit_logs=StubAuditReader([]))

    with pytest.raises(AuditExplorerError) as exc_info:
        await service.list_logs(
            context=_context(permissions=("document:read",)),
            query=AuditLogQueryRequest(request_id="req-secret"),
        )

    assert exc_info.value.code == AUDIT_EXPLORER_FORBIDDEN
    assert "req-secret" not in str(exc_info.value.details)


@pytest.mark.asyncio
async def test_service_returns_allowlisted_summary_and_agent_association() -> None:
    record = _audit_record(
        action="agent.tool.execute",
        resource_metadata={
            "agent_run_id": "run-1",
            "tool_name": "rag_search",
            "permission": "agent:tool:rag_search",
            "source_uri": "file:///secret",
        },
        metadata={
            "citation_count": 2,
            "token_usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            "prompt": "must not return",
            "raw_exception": "select * from secrets",
        },
    )
    service = AuditExplorerService(
        audit_logs=StubAuditReader([record]),
        tool_calls=StubToolCallReader(),
    )

    result = await service.list_logs(
        context=_context(),
        query=AuditLogQueryRequest(action="agent.tool.execute"),
    )

    assert len(result.items) == 1
    item = result.items[0]
    assert item.tenant_id == "tenant-1"
    assert item.safe_summary["citation_count"] == 2
    assert item.safe_counts["input_token_count"] == 10
    assert item.association is not None
    assert item.association.agent_run_id == "run-1"
    assert item.association.tool_name == "rag_search"
    dumped = result.model_dump(mode="json")
    assert "prompt" not in str(dumped).lower()
    assert "source_uri" not in str(dumped).lower()
    assert "select *" not in str(dumped).lower()
    assert "file:///" not in str(dumped).lower()


@pytest.mark.asyncio
async def test_export_uses_backend_payload_and_records_safe_audit_event() -> None:
    audit = InMemoryAuditPort()
    service = AuditExplorerService(
        audit_logs=StubAuditReader(
            [_audit_record(request_id="req-export", trace_id="trace-export")]
        ),
        audit=audit,
        generated_at=lambda: "2026-06-09T10:00:00+00:00",
        export_id_factory=lambda: "audit-export-test",
    )

    payload = await service.export_logs(
        context=_context(request_id="req-current", trace_id="trace-current"),
        request=AuditExportRequest(request_id="req-export", limit=10),
    )

    assert payload.export_id == "audit-export-test"
    assert payload.item_count == 1
    assert payload.request_ids == ("req-export",)
    assert payload.trace_ids == ("trace-export",)
    assert "tenant_id" not in payload.filter_summary
    assert audit.events[0].action == "audit_explorer.export"
    assert audit.events[0].metadata["item_count"] == 1
    assert "items" not in str(audit.events[0].metadata).lower()


class StubAuditReader:
    def __init__(self, records: list[AuditLogRecord]) -> None:
        self.records = records
        self.calls: list[tuple[str, object]] = []

    async def list_records(self, *, tenant_id: str, query: object) -> list[AuditLogRecord]:
        self.calls.append((tenant_id, query))
        return self.records


class StubToolCallReader:
    async def list_tool_calls(self, query: ToolCallQuery) -> list[ToolCallRecord]:
        _ = query
        return []


def _context(
    *,
    request_id: str = "req-current",
    trace_id: str = "trace-current",
    permissions: tuple[str, ...] = ("audit:read",),
) -> AuthenticatedRequestContext:
    from packages.auth.context import AuthContext

    return AuthenticatedRequestContext(
        request_id=request_id,
        trace_id=trace_id,
        auth_method="dev_headers",
        auth=AuthContext(
            user_id="auditor-1",
            tenant_id="tenant-1",
            roles=("security_auditor",),
            department="Security",
            permissions=permissions,
        ),
    )


def _audit_record(
    *,
    request_id: str = "req-1",
    trace_id: str = "trace-1",
    action: str = "rag.query",
    resource_metadata: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
) -> AuditLogRecord:
    now = datetime(2026, 6, 9, 10, 0, tzinfo=UTC)
    return AuditLogRecord(
        id="audit-1",
        tenant_id="tenant-1",
        user_id="user-1",
        created_by="user-1",
        status="success",
        request_id=request_id,
        trace_id=trace_id,
        action=action,
        resource_type="agent_run",
        resource_id="run-1",
        resource_metadata=resource_metadata or {},
        latency_ms=12.5,
        error_code=None,
        metadata=metadata or {},
        created_at=now,
        updated_at=now,
    )
