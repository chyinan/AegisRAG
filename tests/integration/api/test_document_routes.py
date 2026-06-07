from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.service_dependencies import get_document_lifecycle_service
from packages.common.context import AuthenticatedRequestContext
from packages.data.dto import (
    DocumentDeleteCommand,
    DocumentDeleteResult,
    DocumentVersionStatusResult,
)
from packages.data.exceptions import DocumentManageForbiddenError, DocumentNotFoundError


class StubLifecycleService:
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.fail = fail
        self.status_calls: list[tuple[AuthenticatedRequestContext, str, str]] = []
        self.delete_calls: list[tuple[AuthenticatedRequestContext, DocumentDeleteCommand]] = []

    async def get_version_status(
        self,
        context: AuthenticatedRequestContext,
        *,
        document_id: str,
        version_id: str,
    ) -> DocumentVersionStatusResult:
        if self.fail is not None:
            raise self.fail
        self.status_calls.append((context, document_id, version_id))
        return DocumentVersionStatusResult(
            document_id=document_id,
            version_id=version_id,
            status="retrieval_ready",
            chunk_count=2,
            embedding_provider="fake",
            embedding_model="fake-embedding",
            embedding_version="fake-v1",
            embedding_dim=4,
            vector_count=2,
            index_status="indexed",
            job_id="embed-job-1",
            attempt_count=1,
            last_attempt_at=None,
            next_retry_at=None,
            error_summary=None,
            request_id=context.request_id,
            trace_id=context.trace_id,
        )

    async def delete(
        self,
        context: AuthenticatedRequestContext,
        command: DocumentDeleteCommand,
    ) -> DocumentDeleteResult:
        if self.fail is not None:
            raise self.fail
        self.delete_calls.append((context, command))
        return DocumentDeleteResult(
            document_id=command.document_id,
            version_id=command.version_id,
            status="deleted",
            deleted_versions=1,
            deleted_chunks=2,
            deleted_vectors=2,
            request_id=context.request_id,
            trace_id=context.trace_id,
        )


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_status_route_returns_envelope_and_calls_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubLifecycleService()
    app.dependency_overrides[get_document_lifecycle_service] = lambda: service
    client = TestClient(app)

    response = client.get(
        "/documents/doc-1/versions/ver-1/status",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req-doc"
    assert body["data"]["status"] == "retrieval_ready"
    assert body["data"]["vector_count"] == 2
    assert body["data"]["job_id"] == "embed-job-1"
    assert body["data"]["attempt_count"] == 1
    assert body["data"]["last_attempt_at"] is None
    assert body["data"]["next_retry_at"] is None
    assert body["data"]["error_summary"] is None
    assert service.status_calls[0][1:] == ("doc-1", "ver-1")


def test_delete_version_route_returns_safe_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubLifecycleService()
    app.dependency_overrides[get_document_lifecycle_service] = lambda: service
    client = TestClient(app)

    response = client.delete(
        "/documents/doc-1/versions/ver-1",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "document_id": "doc-1",
        "version_id": "ver-1",
        "status": "deleted",
        "deleted_versions": 1,
        "deleted_chunks": 2,
        "deleted_vectors": 2,
        "request_id": "req-doc",
        "trace_id": "trace-doc",
    }
    assert service.delete_calls[0][1] == DocumentDeleteCommand(
        document_id="doc-1",
        version_id="ver-1",
    )


def test_document_routes_require_auth_before_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENABLE_DEV_AUTH_HEADERS", raising=False)
    service = StubLifecycleService()
    app.dependency_overrides[get_document_lifecycle_service] = lambda: service
    client = TestClient(app)

    response = client.delete("/documents/doc-1", headers={"X-Request-ID": "req-no-auth"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_CONTEXT_REQUIRED"
    assert service.delete_calls == []


def test_document_routes_return_stable_permission_and_not_found_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    forbidden = StubLifecycleService(fail=DocumentManageForbiddenError())
    app.dependency_overrides[get_document_lifecycle_service] = lambda: forbidden
    client = TestClient(app)

    denied = client.delete("/documents/doc-1", headers=_auth_headers("document:read"))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "DOCUMENT_MANAGE_FORBIDDEN"

    not_found_service = StubLifecycleService(fail=DocumentNotFoundError())
    app.dependency_overrides[get_document_lifecycle_service] = lambda: not_found_service
    missing = client.get("/documents/doc-2/versions/ver-1/status", headers=_auth_headers())
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"


def _auth_headers(permissions: str = "document:manage") -> dict[str, str]:
    return {
        "X-Request-ID": "req-doc",
        "X-Trace-ID": "trace-doc",
        "X-User-ID": "user-1",
        "X-Tenant-ID": "tenant-1",
        "X-Roles": "knowledge_admin",
        "X-Permissions": permissions,
    }
