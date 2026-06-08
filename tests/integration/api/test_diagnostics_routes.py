from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.service_dependencies import get_diagnostics_service
from packages.common.context import AuthenticatedRequestContext
from packages.diagnostics.dto import (
    DiagnosticsLookupRequest,
    DiagnosticsResolveResponse,
    DiagnosticsStageSummary,
    DiagnosticsSummary,
    FailureStage,
)
from packages.diagnostics.exceptions import (
    DIAGNOSTICS_FORBIDDEN,
    DIAGNOSTICS_NOT_FOUND,
    DiagnosticsError,
)


class StubDiagnosticsService:
    def __init__(self, *, error: DiagnosticsError | None = None) -> None:
        self.calls: list[tuple[AuthenticatedRequestContext, DiagnosticsLookupRequest]] = []
        self._error = error

    async def resolve(
        self,
        *,
        context: AuthenticatedRequestContext,
        lookup: DiagnosticsLookupRequest,
    ) -> DiagnosticsResolveResponse:
        self.calls.append((context, lookup))
        if self._error is not None:
            raise self._error
        summary = DiagnosticsSummary(
            tenant_id=context.auth.tenant_id,
            user_id="user-1",
            request_id=lookup.request_id or "req-from-trace",
            trace_id=lookup.trace_id or "trace-1",
            action="rag.query",
            status="success",
            top_k=5,
            result_count=2,
            highest_rerank_score=0.82,
            citation_count=1,
            context_item_count=3,
            context_source_count=2,
            generation_provider="fake",
            generation_model="fake-model",
            generation_version="fake-v1",
            prompt_token_count=11,
            completion_token_count=7,
            total_token_count=18,
            event_count=4,
            latency_ms=45.0,
        )
        stages = (
            DiagnosticsStageSummary(
                name=FailureStage.RETRIEVAL,
                status="success",
                latency_ms=20.0,
                counts={"top_k": 5, "result_count": 2},
            ),
        )
        return DiagnosticsResolveResponse(
            lookup=lookup,
            summary=summary,
            stages=stages,
            next_steps=(
                ".venv\\Scripts\\python.exe -m pytest "
                "tests/integration/api/test_diagnostics_routes.py -q",
            ),
        )


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_diagnostics_resolve_route_returns_safe_envelope_and_calls_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubDiagnosticsService()
    app.dependency_overrides[get_diagnostics_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/diagnostics/resolve",
        headers=_auth_headers(),
        json={"request_id": "req-target", "include_report": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req-diagnostics"
    assert body["error"] is None
    assert body["data"]["summary"]["tenant_id"] == "tenant-1"
    assert body["data"]["summary"]["request_id"] == "req-target"
    assert body["data"]["summary"]["highest_rerank_score"] == 0.82
    assert body["data"]["stages"][0]["name"] == "retrieval"
    assert "query_text" not in str(body).lower()
    assert "answer text" not in str(body).lower()
    assert "source_uri" not in str(body).lower()
    assert len(service.calls) == 1
    context, lookup = service.calls[0]
    assert context.auth.tenant_id == "tenant-1"
    assert context.auth.permissions == ("audit:read",)
    assert lookup.request_id == "req-target"
    assert lookup.include_report is True


def test_diagnostics_resolve_route_supports_trace_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubDiagnosticsService()
    app.dependency_overrides[get_diagnostics_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/diagnostics/resolve",
        headers=_auth_headers(),
        json={"trace_id": "trace-target"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["lookup"]["trace_id"] == "trace-target"
    assert service.calls[0][1].trace_id == "trace-target"


def test_diagnostics_resolve_route_returns_forbidden_without_safe_data_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubDiagnosticsService(
        error=DiagnosticsError(
            code=DIAGNOSTICS_FORBIDDEN,
            message="Diagnostics permission is required.",
            details={"permission": "audit:read"},
            status_code=403,
        )
    )
    app.dependency_overrides[get_diagnostics_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/diagnostics/resolve",
        headers=_auth_headers(permissions="document:read"),
        json={"request_id": "req-target"},
    )

    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == DIAGNOSTICS_FORBIDDEN
    assert "req-target" not in str(body["error"]["details"])


def test_diagnostics_resolve_route_returns_safe_not_found_for_cross_tenant_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubDiagnosticsService(
        error=DiagnosticsError(
            code=DIAGNOSTICS_NOT_FOUND,
            message="Diagnostics records were not found.",
            details={"request_id": "req-target"},
            status_code=404,
        )
    )
    app.dependency_overrides[get_diagnostics_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/diagnostics/resolve",
        headers=_auth_headers(),
        json={"request_id": "req-target"},
    )

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == DIAGNOSTICS_NOT_FOUND
    assert "tenant-2" not in str(body)
    assert "query" not in str(body["error"]["details"]).lower()


def test_diagnostics_resolve_route_rejects_invalid_lookup_before_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubDiagnosticsService()
    app.dependency_overrides[get_diagnostics_service] = lambda: service
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/diagnostics/resolve", headers=_auth_headers(), json={})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DIAGNOSTICS_INVALID_LOOKUP"
    assert service.calls == []


def _auth_headers(permissions: str = "audit:read") -> dict[str, str]:
    return {
        "X-Request-ID": "req-diagnostics",
        "X-Trace-ID": "trace-diagnostics",
        "X-User-ID": "platform-user",
        "X-Tenant-ID": "tenant-1",
        "X-Roles": "platform_engineer",
        "X-Permissions": permissions,
    }
