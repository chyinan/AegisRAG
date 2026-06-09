from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.service_dependencies import get_eval_evidence_service
from packages.common.context import AuthenticatedRequestContext
from packages.eval import (
    EVAL_EVIDENCE_FORBIDDEN,
    EvalEvidenceError,
    EvalEvidenceFailureStage,
    EvalEvidenceReportListResponse,
    EvalEvidenceReportSummary,
    EvalEvidenceReportType,
    EvalEvidenceResolveResponse,
)


class StubEvalEvidenceService:
    def __init__(self, *, error: EvalEvidenceError | None = None) -> None:
        self.error = error
        self.list_calls: list[tuple[AuthenticatedRequestContext, int]] = []
        self.resolve_calls: list[tuple[AuthenticatedRequestContext, str]] = []

    async def list_reports(
        self,
        *,
        context: AuthenticatedRequestContext,
        limit: int = 20,
    ) -> EvalEvidenceReportListResponse:
        self.list_calls.append((context, limit))
        if self.error is not None:
            raise self.error
        return EvalEvidenceReportListResponse(
            items=(
                EvalEvidenceReportSummary(
                    report_filename="rag-smoke-20260609T100000Z-safe.json",
                    generated_at="2026-06-09T10:00:00+00:00",
                    report_type=EvalEvidenceReportType.RAG_QUALITY_RUNNER,
                    case_count=2,
                    passed_count=1,
                    failed_count=1,
                    retrieval_hit_rate=0.5,
                    citation_coverage=0.5,
                    no_answer_correctness=1.0,
                    acl_isolation=True,
                    prompt_injection=True,
                    average_latency_ms=12.5,
                    decision="failed",
                    failure_stages=(EvalEvidenceFailureStage.CITATION,),
                ),
            ),
            next_steps=(".venv\\Scripts\\python.exe -m pytest tests/eval -q",),
        )

    async def resolve_report(
        self,
        *,
        context: AuthenticatedRequestContext,
        report_filename: str,
    ) -> EvalEvidenceResolveResponse:
        self.resolve_calls.append((context, report_filename))
        if self.error is not None:
            raise self.error
        listing = await self.list_reports(context=context)
        return EvalEvidenceResolveResponse(
            summary=listing.items[0],
            failed_cases=(),
            gate_metrics=(),
            next_steps=listing.next_steps,
        )


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_eval_evidence_list_route_returns_safe_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubEvalEvidenceService()
    app.dependency_overrides[get_eval_evidence_service] = lambda: service
    client = TestClient(app)

    response = client.get("/eval/reports?limit=5", headers=_auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req-eval"
    assert body["error"] is None
    assert body["data"]["items"][0]["report_type"] == "rag_quality_runner"
    assert body["data"]["items"][0]["failed_count"] == 1
    assert "query" not in response.text.lower()
    assert '"answer"' not in response.text.lower()
    assert "source_uri" not in response.text.lower()
    assert service.list_calls[0][0].auth.tenant_id == "tenant-1"
    assert service.list_calls[0][0].auth.permissions == ("eval:read",)
    assert service.list_calls[0][1] == 5


def test_eval_evidence_detail_route_uses_backend_filename_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubEvalEvidenceService()
    app.dependency_overrides[get_eval_evidence_service] = lambda: service
    client = TestClient(app)

    response = client.get(
        "/eval/reports/rag-smoke-20260609T100000Z-safe.json",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["data"]["summary"]["report_filename"].endswith(".json")
    assert service.resolve_calls[0][1] == "rag-smoke-20260609T100000Z-safe.json"


def test_eval_evidence_permission_denial_has_uniform_safe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubEvalEvidenceService(
        error=EvalEvidenceError(
            code=EVAL_EVIDENCE_FORBIDDEN,
            message="Eval evidence permission is required.",
            details={"permission": "eval:read"},
            status_code=403,
        )
    )
    app.dependency_overrides[get_eval_evidence_service] = lambda: service
    client = TestClient(app)

    response = client.get(
        "/eval/reports/rag-smoke-20260609T100000Z-safe.json",
        headers=_auth_headers("document:read"),
    )

    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == EVAL_EVIDENCE_FORBIDDEN
    assert "rag-smoke" not in str(body["error"]["details"])
    assert "secret.json" not in str(body["error"]["details"])


def _auth_headers(permissions: str = "eval:read") -> dict[str, str]:
    return {
        "X-Request-ID": "req-eval",
        "X-Trace-ID": "trace-eval",
        "X-User-ID": "platform-user",
        "X-Tenant-ID": "tenant-1",
        "X-Roles": "platform_engineer",
        "X-Permissions": permissions,
    }
