from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.service_dependencies import get_retrieve_application_service
from packages.common.context import AuthenticatedRequestContext
from packages.retrieval.application import (
    RetrieveCandidateResponse,
    RetrieveCommand,
    RetrieveResponse,
)
from packages.retrieval.exceptions import RETRIEVAL_BACKEND_FAILED, RetrievalError


class StubRetrieveApplicationService:
    def __init__(self, *, error: RetrievalError | None = None) -> None:
        self.calls: list[tuple[AuthenticatedRequestContext, RetrieveCommand]] = []
        self._error = error

    async def retrieve(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: RetrieveCommand,
    ) -> RetrieveResponse:
        self.calls.append((context, command))
        if self._error is not None:
            raise self._error
        return RetrieveResponse(
            request_id=context.request_id,
            trace_id=context.trace_id,
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            top_k=command.top_k,
            query_summary={"length": len(command.query)},
            latency_ms=3.5,
            candidates=(
                RetrieveCandidateResponse(
                    chunk_id="chunk-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    source="kb://policy.md",
                    source_uri="kb://policy.md",
                    source_type="markdown",
                    page_start=1,
                    page_end=1,
                    title_path=("Policy",),
                    score=0.91,
                    retrieval_method="hybrid",
                    tenant_id=context.auth.tenant_id,
                    acl={"visibility": "tenant"},
                    metadata={"retrieval_provenance": {"fusion_reason": "dense_sparse_overlap"}},
                ),
            ),
        )


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _auth_headers() -> dict[str, str]:
    return {
        "X-Request-ID": "req-retrieve",
        "X-Trace-ID": "trace-retrieve",
        "X-User-ID": "user-1",
        "X-Tenant-ID": "tenant-1",
        "X-Roles": "knowledge_user",
        "X-Permissions": "document:read",
    }


def test_retrieve_route_returns_success_envelope_and_calls_application_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubRetrieveApplicationService()
    app.dependency_overrides[get_retrieve_application_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/retrieve",
        headers=_auth_headers(),
        json={
            "query": "policy leave",
            "top_k": 3,
            "metadata_filter": {"department": "HR"},
            "score_threshold": 0.2,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req-retrieve"
    assert body["error"] is None
    assert body["metadata"] == {"latency_ms": None}
    assert body["data"]["request_id"] == "req-retrieve"
    assert body["data"]["trace_id"] == "trace-retrieve"
    assert body["data"]["tenant_id"] == "tenant-1"
    assert body["data"]["user_id"] == "user-1"
    assert body["data"]["candidates"][0]["chunk_id"] == "chunk-1"
    assert "content" not in body["data"]["candidates"][0]["metadata"]
    assert len(service.calls) == 1
    context, command = service.calls[0]
    assert context.auth.tenant_id == "tenant-1"
    assert context.auth.user_id == "user-1"
    assert command.query == "policy leave"
    assert command.top_k == 3
    assert command.metadata_filter == {"department": "HR"}
    assert command.score_threshold == 0.2


def test_retrieve_route_rejects_missing_auth_before_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENABLE_DEV_AUTH_HEADERS", raising=False)
    service = StubRetrieveApplicationService()
    app.dependency_overrides[get_retrieve_application_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/retrieve",
        headers={"X-Request-ID": "req-no-auth"},
        json={"query": "policy"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_CONTEXT_REQUIRED"
    assert service.calls == []


@pytest.mark.parametrize(
    "payload",
        [
            {"query": "   "},
            {"query": "policy", "top_k": 0},
            {"query": "policy", "score_threshold": 1.5},
            {"query": "policy", "metadata_filter": ["department", "HR"]},
            {"query": "policy", "metadata_filter": {"department": {"$ne": "HR"}}},
            {"query": "policy", "metadata_filter": {"$tenant_id": "tenant-2"}},
            {"query": "policy", "metadata_filter": {"bad key": "HR"}},
            {"query": "policy", "metadata_filter": {"department": ["HR"]}},
        ],
    )
def test_retrieve_route_invalid_body_returns_structured_error_without_service_call(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubRetrieveApplicationService()
    app.dependency_overrides[get_retrieve_application_service] = lambda: service
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/retrieve", headers=_auth_headers(), json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"
    assert service.calls == []


def test_retrieve_route_returns_stable_error_when_service_raises_retrieval_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubRetrieveApplicationService(
        error=RetrievalError(
            code=RETRIEVAL_BACKEND_FAILED,
            message="Retrieval backend failed.",
            details={
                "request_id": "req-retrieve",
                "trace_id": "trace-retrieve",
                "query": "secret full query",
                "sql": "select * from chunks where password='secret'",
                "vector": [0.1, 0.2, 0.3],
                "embedding": [0.4, 0.5],
                "provider_raw_response": "access_token=secret-token",
            },
            status_code=502,
        )
    )
    app.dependency_overrides[get_retrieve_application_service] = lambda: service
    client = TestClient(app)

    response = client.post("/retrieve", headers=_auth_headers(), json={"query": "policy"})

    assert response.status_code == 502
    body = response.json()
    assert body["request_id"] == "req-retrieve"
    assert body["error"]["code"] == RETRIEVAL_BACKEND_FAILED
    assert body["error"]["details"]["query"] == "[REDACTED]"
    assert body["error"]["details"]["sql"] == "[REDACTED]"
    assert body["error"]["details"]["vector"] == "[REDACTED]"
    assert body["error"]["details"]["embedding"] == "[REDACTED]"
    assert body["error"]["details"]["provider_raw_response"] == "[REDACTED]"
    assert len(service.calls) == 1
