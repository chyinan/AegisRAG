from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.service_dependencies import get_audit_explorer_service
from packages.audit import (
    AUDIT_EXPLORER_FORBIDDEN,
    AuditExplorerError,
    AuditExplorerListResponse,
    AuditExportPayload,
    AuditExportRequest,
    AuditLogQueryRequest,
    AuditLogSummary,
)
from packages.common.context import AuthenticatedRequestContext


class StubAuditExplorerService:
    def __init__(self, *, error: AuditExplorerError | None = None) -> None:
        self.error = error
        self.list_calls: list[tuple[AuthenticatedRequestContext, AuditLogQueryRequest]] = []
        self.export_calls: list[tuple[AuthenticatedRequestContext, AuditExportRequest]] = []

    async def list_logs(
        self,
        *,
        context: AuthenticatedRequestContext,
        query: AuditLogQueryRequest,
    ) -> AuditExplorerListResponse:
        self.list_calls.append((context, query))
        if self.error is not None:
            raise self.error
        return AuditExplorerListResponse(items=(_summary(context),))

    async def export_logs(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: AuditExportRequest,
    ) -> AuditExportPayload:
        self.export_calls.append((context, request))
        if self.error is not None:
            raise self.error
        return AuditExportPayload(
            export_id="audit-export-1",
            generated_at="2026-06-09T10:00:00+00:00",
            filter_summary={"request_id": request.request_id or "req-target"},
            fields=("id", "tenant_id", "request_id", "trace_id", "safe_summary"),
            item_count=1,
            request_ids=("req-target",),
            trace_ids=("trace-target",),
            items=(_summary(context),),
        )


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_audit_logs_route_returns_safe_envelope_and_does_not_accept_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubAuditExplorerService()
    app.dependency_overrides[get_audit_explorer_service] = lambda: service
    client = TestClient(app)

    response = client.get(
        "/audit/logs?request_id=req-target&tenant_id=tenant-evil&limit=5",
        headers=_auth_headers(),
    )

    assert response.status_code == 422
    assert service.list_calls == []

    response = client.get("/audit/logs?request_id=req-target&limit=5", headers=_auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req-audit"
    assert body["error"] is None
    assert body["data"]["items"][0]["tenant_id"] == "tenant-1"
    assert "source_uri" not in response.text.lower()
    context, query = service.list_calls[0]
    assert context.auth.tenant_id == "tenant-1"
    assert query.request_id == "req-target"
    assert query.limit == 5


def test_audit_export_route_returns_backend_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubAuditExplorerService()
    app.dependency_overrides[get_audit_explorer_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/audit/export",
        headers=_auth_headers(),
        json={"request_id": "req-target", "limit": 50},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["export_id"] == "audit-export-1"
    assert body["data"]["request_ids"] == ["req-target"]
    assert "prompt" not in response.text.lower()
    assert service.export_calls[0][1].request_id == "req-target"


def test_audit_route_permission_denial_has_uniform_safe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubAuditExplorerService(
        error=AuditExplorerError(
            code=AUDIT_EXPLORER_FORBIDDEN,
            message="Audit explorer permission is required.",
            details={"request_id": "req-audit", "trace_id": "trace-audit", "stage": "permission"},
            status_code=403,
        )
    )
    app.dependency_overrides[get_audit_explorer_service] = lambda: service
    client = TestClient(app)

    response = client.get(
        "/audit/logs?request_id=req-target",
        headers=_auth_headers("document:read"),
    )

    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == AUDIT_EXPLORER_FORBIDDEN
    assert "req-target" not in str(body["error"]["details"])
    assert "audit_logs" not in response.text.lower()


def _summary(context: AuthenticatedRequestContext) -> AuditLogSummary:
    now = datetime(2026, 6, 9, 10, 0, tzinfo=UTC)
    return AuditLogSummary(
        id="audit-1",
        tenant_id=context.auth.tenant_id,
        user_id="user-1",
        request_id="req-target",
        trace_id="trace-target",
        action="rag.query",
        resource_type="rag_query",
        resource_id="req-target",
        status="success",
        latency_ms=12.5,
        error_code=None,
        created_at=now,
        safe_summary={"citation_count": 1},
        safe_counts={"citation_count": 1},
    )


def _auth_headers(permissions: str = "audit:read") -> dict[str, str]:
    return {
        "X-Request-ID": "req-audit",
        "X-Trace-ID": "trace-audit",
        "X-User-ID": "platform-user",
        "X-Tenant-ID": "tenant-1",
        "X-Roles": "security_auditor",
        "X-Permissions": permissions,
    }
