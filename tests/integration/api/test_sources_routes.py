from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.service_dependencies import get_source_resolve_service
from packages.common.context import AuthenticatedRequestContext
from packages.rag.source_resolver import (
    SOURCE_ACCESS_DENIED,
    SourceResolveCommand,
    SourceResolveError,
    SourceResolveResponse,
)


class StubSourceResolveService:
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.fail = fail
        self.calls: list[tuple[AuthenticatedRequestContext, SourceResolveCommand]] = []

    async def resolve(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: SourceResolveCommand,
    ) -> SourceResolveResponse:
        if self.fail is not None:
            raise self.fail
        self.calls.append((context, command))
        return SourceResolveResponse(
            request_id=context.request_id,
            trace_id=context.trace_id,
            document_id=command.document_id,
            version_id=command.version_id,
            chunk_id=command.chunk_id,
            source="Policy",
            source_uri="kb://policy.md",
            source_type="markdown",
            page_start=1,
            page_end=1,
            title_path=("Policy",),
            text_excerpt="Authorized source excerpt.",
            excerpt_char_count=26,
            token_count=6,
            retrieval_method="hybrid",
            score=0.9,
        )


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_sources_resolve_route_returns_envelope_and_calls_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubSourceResolveService()
    app.dependency_overrides[get_source_resolve_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/sources/resolve",
        headers=_auth_headers(),
        json={"document_id": "doc-1", "version_id": "v1", "chunk_id": "chunk-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req-source"
    assert body["data"]["text_excerpt"] == "Authorized source excerpt."
    assert body["data"]["document_id"] == "doc-1"
    assert len(service.calls) == 1
    context, command = service.calls[0]
    assert context.auth.tenant_id == "tenant-1"
    assert command.chunk_id == "chunk-1"


def test_sources_resolve_route_rejects_missing_permission_before_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubSourceResolveService()
    app.dependency_overrides[get_source_resolve_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/sources/resolve",
        headers=_auth_headers(permissions="document:read"),
        json={"document_id": "doc-1", "version_id": "v1", "chunk_id": "chunk-1"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "RAG_QUERY_FORBIDDEN"
    assert service.calls == []


def test_sources_resolve_route_returns_safe_denial_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubSourceResolveService(
        fail=SourceResolveError(
            code=SOURCE_ACCESS_DENIED,
            details={"request_id": "req-source", "trace_id": "trace-source"},
        )
    )
    app.dependency_overrides[get_source_resolve_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/sources/resolve",
        headers=_auth_headers(),
        json={"document_id": "doc-secret", "version_id": "v1", "chunk_id": "chunk-secret"},
    )

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == SOURCE_ACCESS_DENIED
    assert "doc-secret" not in str(body["error"]["details"])


def _auth_headers(permissions: str = "document:read,retrieval:query") -> dict[str, str]:
    return {
        "X-Request-ID": "req-source",
        "X-Trace-ID": "trace-source",
        "X-User-ID": "user-1",
        "X-Tenant-ID": "tenant-1",
        "X-Roles": "knowledge_user",
        "X-Permissions": permissions,
    }
