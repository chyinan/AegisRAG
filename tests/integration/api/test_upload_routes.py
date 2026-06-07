from __future__ import annotations

from collections.abc import Iterator
from typing import BinaryIO

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.service_dependencies import get_document_upload_service
from packages.auth.context import AuthContext
from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.data.dto import (
    DocumentRecord,
    DocumentVersionRecord,
    EnqueuedJob,
    IngestionJobRecord,
    StoredObject,
    UploadDocumentCommand,
    UploadDocumentResult,
)
from packages.data.queue.contracts import QueuePayload
from packages.data.service import DocumentUploadService


class StubUploadService:
    def __init__(self) -> None:
        self.calls: list[tuple[AuthenticatedRequestContext, UploadDocumentCommand]] = []

    async def upload(
        self,
        context: AuthenticatedRequestContext,
        command: UploadDocumentCommand,
    ) -> UploadDocumentResult:
        self.calls.append((context, command))
        return UploadDocumentResult(
            document_id="doc-1",
            version_id="ver-1",
            job_id="job-1",
            status="uploaded",
        )


class NoSideEffectStorage:
    calls = 0

    async def put_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        filename: str,
        content_type: str | None,
        stream: BinaryIO,
        byte_size: int,
        checksum: str,
    ) -> StoredObject:
        self.calls += 1
        raise AssertionError("storage must not be called")

    async def delete_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        object_key: str,
    ) -> None:
        self.calls += 1
        raise AssertionError("storage must not be called")


class NoSideEffectRepository:
    created = 0
    commits = 0

    async def create_upload_records(
        self,
        *,
        document: DocumentRecord,
        version: DocumentVersionRecord,
        job: IngestionJobRecord,
    ) -> tuple[DocumentRecord, DocumentVersionRecord, IngestionJobRecord]:
        self.created += 1
        raise AssertionError("repository must not be called")

    async def get_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
    ) -> DocumentRecord | None:
        raise AssertionError("repository must not be called")

    async def create_document_version_records(
        self,
        *,
        version: DocumentVersionRecord,
        job: IngestionJobRecord,
    ) -> tuple[DocumentVersionRecord, IngestionJobRecord]:
        raise AssertionError("repository must not be called")

    async def mark_ingestion_job_queued(
        self,
        *,
        tenant_id: str,
        job_id: str,
        queue_job_id: str | None,
    ) -> IngestionJobRecord:
        raise AssertionError("repository must not be called")

    async def mark_ingestion_job_failed(
        self,
        *,
        tenant_id: str,
        job_id: str,
        error_code: str,
    ) -> IngestionJobRecord:
        raise AssertionError("repository must not be called")

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None

    async def get_version(
        self,
        *,
        tenant_id: str,
        version_id: str,
    ) -> DocumentVersionRecord | None:
        return None


class NoSideEffectQueue:
    calls = 0

    async def enqueue_ingestion_job(self, payload: QueuePayload) -> EnqueuedJob:
        self.calls += 1
        raise AssertionError("queue must not be called")


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _auth_headers(permissions: str = "document:upload") -> dict[str, str]:
    return {
        "X-Request-ID": "req-upload",
        "X-Trace-ID": "trace-upload",
        "X-User-ID": "user-1",
        "X-Tenant-ID": "tenant-1",
        "X-Roles": "knowledge_admin",
        "X-Permissions": permissions,
    }


def test_upload_route_returns_success_envelope_and_calls_application_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubUploadService()
    app.dependency_overrides[get_document_upload_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/upload",
        headers=_auth_headers(),
        data={
            "source_type": "txt",
            "source_uri": "kb://policy.txt",
            "title": "Policy",
            "acl": '{"visibility":"tenant"}',
            "metadata": '{"department":"HR"}',
        },
        files={"file": ("policy.txt", b"hello policy", "text/plain")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "request_id": "req-upload",
        "data": {
            "document_id": "doc-1",
            "version_id": "ver-1",
            "job_id": "job-1",
            "status": "uploaded",
        },
        "error": None,
        "metadata": {"latency_ms": None},
    }
    assert len(service.calls) == 1
    context, command = service.calls[0]
    assert context.auth == AuthContext(
        user_id="user-1",
        tenant_id="tenant-1",
        roles=("knowledge_admin",),
        permissions=("document:upload",),
    )
    assert command.filename == "policy.txt"
    assert command.metadata == {"department": "HR"}


def test_upload_route_passes_optional_document_id_to_application_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubUploadService()
    app.dependency_overrides[get_document_upload_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/upload",
        headers=_auth_headers("document:manage"),
        data={
            "document_id": "doc-existing",
            "source_type": "txt",
        },
        files={"file": ("policy.txt", b"hello policy", "text/plain")},
    )

    assert response.status_code == 200
    assert len(service.calls) == 1
    _context, command = service.calls[0]
    assert command.document_id == "doc-existing"


def test_upload_route_rejects_missing_auth_before_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENABLE_DEV_AUTH_HEADERS", raising=False)
    service = StubUploadService()
    app.dependency_overrides[get_document_upload_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/upload",
        headers={"X-Request-ID": "req-no-auth"},
        data={"source_type": "txt"},
        files={"file": ("policy.txt", b"hello policy", "text/plain")},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_CONTEXT_REQUIRED"
    assert service.calls == []


def test_upload_route_invalid_metadata_returns_structured_error_without_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubUploadService()
    app.dependency_overrides[get_document_upload_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/upload",
        headers=_auth_headers(),
        data={"source_type": "txt", "metadata": "{not-json"},
        files={"file": ("policy.txt", b"hello policy", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DOCUMENT_UPLOAD_INVALID_METADATA"
    assert service.calls == []


def test_upload_route_blank_source_type_returns_structured_error_without_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubUploadService()
    app.dependency_overrides[get_document_upload_service] = lambda: service
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/upload",
        headers=_auth_headers(),
        data={"source_type": "   "},
        files={"file": ("policy.txt", b"hello policy", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DOCUMENT_UPLOAD_INVALID_METADATA"
    assert service.calls == []


def test_upload_route_permission_denied_returns_403_and_no_storage_db_or_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    storage = NoSideEffectStorage()
    repository = NoSideEffectRepository()
    queue = NoSideEffectQueue()
    service = DocumentUploadService(
        object_storage=storage,
        repository=repository,
        job_queue=queue,
        audit=InMemoryAuditPort(),
    )
    app.dependency_overrides[get_document_upload_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/upload",
        headers=_auth_headers("document:read"),
        data={"source_type": "txt"},
        files={"file": ("policy.txt", b"hello policy", "text/plain")},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "DOCUMENT_UPLOAD_FORBIDDEN"
    assert storage.calls == 0
    assert repository.created == 0
    assert queue.calls == 0
