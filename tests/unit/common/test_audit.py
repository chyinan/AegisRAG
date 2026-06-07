from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from packages.common.audit import AuditEvent, AuditResource, AuditStatus, InMemoryAuditPort


def test_audit_event_requires_structured_resource_and_core_fields() -> None:
    created_at = datetime(2026, 5, 27, tzinfo=UTC)
    event = AuditEvent(
        request_id="req-123",
        trace_id="trace-123",
        tenant_id="tenant-abc",
        user_id="user-123",
        action="document.upload.requested",
        resource=AuditResource(type="document", id="doc-123"),
        status=AuditStatus.SUCCESS,
        latency_ms=12.5,
        error_code=None,
        created_at=created_at,
    )

    assert event.model_dump() == {
        "request_id": "req-123",
        "trace_id": "trace-123",
        "tenant_id": "tenant-abc",
        "user_id": "user-123",
        "action": "document.upload.requested",
        "resource": {"type": "document", "id": "doc-123", "metadata": {}},
        "status": AuditStatus.SUCCESS,
        "latency_ms": 12.5,
        "error_code": None,
        "created_at": created_at,
        "metadata": {},
    }


def test_audit_event_rejects_natural_language_resource() -> None:
    with pytest.raises(ValidationError):
        AuditEvent.model_validate(
            {
                "request_id": "req-123",
                "trace_id": "trace-123",
                "tenant_id": "tenant-abc",
                "user_id": "user-123",
                "action": "document.upload.requested",
                "resource": "document doc-123",
                "status": "success",
                "latency_ms": 12.5,
            }
        )


async def test_in_memory_audit_port_records_events_and_redacts_metadata() -> None:
    audit = InMemoryAuditPort()
    event = AuditEvent(
        request_id="req-123",
        trace_id="trace-123",
        tenant_id="tenant-abc",
        user_id="user-123",
        action="document.upload.requested",
        resource=AuditResource(
            type="document",
            id="doc-123",
            metadata={"api_key": "secret-key", "document_content": "full text", "safe": "metadata"},
        ),
        status=AuditStatus.SUCCESS,
        latency_ms=12.5,
        metadata={
            "Authorization": "Bearer secret-token",
            "note": "Bearer secret-token",
            "safe": "metadata",
        },
    )

    await audit.record(event)

    assert audit.events == [
        event.model_copy(
            update={
                "resource": AuditResource(
                    type="document",
                    id="doc-123",
                    metadata={
                        "api_key": "[REDACTED]",
                        "document_content": "[REDACTED]",
                        "safe": "metadata",
                    },
                ),
                "metadata": {
                    "Authorization": "[REDACTED]",
                    "note": "[REDACTED]",
                    "safe": "metadata",
                },
            }
        )
    ]
