from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO
from typing import Any, BinaryIO

import pytest

from packages.auth.context import AuthContext
from packages.common.audit import AuditStatus, InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError
from packages.data.dto import (
    DocumentRecord,
    DocumentVersionRecord,
    EnqueuedJob,
    IngestionJobRecord,
    StoredObject,
    UploadDocumentCommand,
)
from packages.data.exceptions import (
    DocumentUploadForbiddenError,
    DocumentUploadInvalidMetadataError,
    DocumentUploadTooLargeError,
    DocumentUploadUnsupportedTypeError,
    IngestionJobEnqueueError,
)
from packages.data.service import DocumentUploadService
from packages.data.storage.exceptions import StorageError


class FakeObjectStorage:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.deleted: list[dict[str, str]] = []

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
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "document_id": document_id,
                "version_id": version_id,
                "filename": filename,
                "content_type": content_type,
                "byte_size": byte_size,
                "checksum": checksum,
                "content": stream.read(),
            }
        )
        return StoredObject(
            bucket="documents",
            object_key=f"raw/{tenant_id}/{document_id}/{version_id}/{filename}",
            etag="etag-1",
            byte_size=byte_size,
            checksum=checksum,
        )

    async def delete_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        object_key: str,
    ) -> None:
        self.deleted.append(
            {
                "tenant_id": tenant_id,
                "document_id": document_id,
                "version_id": version_id,
                "object_key": object_key,
            }
        )


class FakeDocumentRepository:
    def __init__(
        self,
        *,
        fail_create: bool = False,
        existing_document: DocumentRecord | None = None,
    ) -> None:
        self.created: list[tuple[DocumentRecord, DocumentVersionRecord, IngestionJobRecord]] = []
        self.created_versions: list[tuple[DocumentVersionRecord, IngestionJobRecord]] = []
        self.queued_jobs: list[tuple[str, str | None]] = []
        self.failed_jobs: list[tuple[str, str]] = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_create = fail_create
        self.existing_document = existing_document

    async def create_upload_records(
        self,
        *,
        document: DocumentRecord,
        version: DocumentVersionRecord,
        job: IngestionJobRecord,
    ) -> tuple[DocumentRecord, DocumentVersionRecord, IngestionJobRecord]:
        if self.fail_create:
            raise StorageError(
                code="DOCUMENT_STORAGE_WRITE_FAILED",
                message="Document metadata storage write failed.",
            )
        self.created.append((document, version, job))
        return document, version, job

    async def get_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
    ) -> DocumentRecord | None:
        if (
            self.existing_document is None
            or self.existing_document.tenant_id != tenant_id
            or self.existing_document.id != document_id
        ):
            return None
        return self.existing_document

    async def create_document_version_records(
        self,
        *,
        version: DocumentVersionRecord,
        job: IngestionJobRecord,
    ) -> tuple[DocumentVersionRecord, IngestionJobRecord]:
        if self.fail_create:
            raise StorageError(
                code="DOCUMENT_STORAGE_WRITE_FAILED",
                message="Document metadata storage write failed.",
            )
        self.created_versions.append((version, job))
        return version, job

    async def mark_ingestion_job_queued(
        self,
        *,
        tenant_id: str,
        job_id: str,
        queue_job_id: str | None,
    ) -> IngestionJobRecord:
        self.queued_jobs.append((job_id, queue_job_id))
        job = self.created[-1][2] if self.created else self.created_versions[-1][1]
        return job.model_copy(update={"queue_job_id": queue_job_id})

    async def mark_ingestion_job_failed(
        self,
        *,
        tenant_id: str,
        job_id: str,
        error_code: str,
    ) -> IngestionJobRecord:
        self.failed_jobs.append((job_id, error_code))
        job = self.created[-1][2] if self.created else self.created_versions[-1][1]
        return job.model_copy(
            update={"status": "failed_retryable", "error_code": error_code}
        )

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def get_version(
        self,
        *,
        tenant_id: str,
        version_id: str,
    ) -> DocumentVersionRecord | None:
        return None


class FakeJobQueue:
    def __init__(
        self,
        *,
        fail: bool = False,
        repository: FakeDocumentRepository | None = None,
    ) -> None:
        self.fail = fail
        self.repository = repository
        self.payloads: list[dict[str, object]] = []

    async def enqueue_ingestion_job(self, payload: object) -> EnqueuedJob:
        if self.repository is not None:
            assert self.repository.commits >= 1
        dumped = payload.model_dump(mode="json")  # type: ignore[attr-defined]
        self.payloads.append(dumped)
        if self.fail:
            raise IngestionJobEnqueueError(details={"queue": "ingestion"})
        return EnqueuedJob(queue_job_id="rq-job-1", queue_name="ingestion")


def _context(*, permissions: tuple[str, ...]) -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-1",
        trace_id="trace-1",
        auth=AuthContext(
            user_id="user-1",
            tenant_id="tenant-1",
            roles=("knowledge_admin",),
            permissions=permissions,
        ),
    )


def _command(**overrides: object) -> UploadDocumentCommand:
    values: dict[str, Any] = {
        "filename": "policy.txt",
        "content_type": "text/plain",
        "source_type": "txt",
        "source_uri": "kb://policy.txt",
        "title": "Policy",
        "acl": {"visibility": "tenant"},
        "metadata": {"department": "HR"},
        "stream": BytesIO(b"hello policy"),
    }
    values.update(overrides)
    return UploadDocumentCommand(**values)


def _id_factory() -> Iterator[str]:
    yield from ("doc-1", "ver-1", "job-1", "ver-2", "job-2", "unused")


def _document(**overrides: object) -> DocumentRecord:
    values: dict[str, object] = {
        "id": "doc-existing",
        "tenant_id": "tenant-1",
        "created_by": "user-1",
        "status": "retrieval_ready",
        "source_type": "txt",
        "source_uri": "kb://policy.txt",
        "title": "Policy",
        "acl": {"visibility": "tenant"},
        "checksum": "old-checksum",
        "metadata": {},
    }
    values.update(overrides)
    return DocumentRecord.model_validate(values)


def _service(
    *,
    storage: FakeObjectStorage | None = None,
    repository: FakeDocumentRepository | None = None,
    queue: FakeJobQueue | None = None,
    audit: InMemoryAuditPort | None = None,
    max_upload_bytes: int = 1024,
) -> tuple[
    DocumentUploadService,
    FakeObjectStorage,
    FakeDocumentRepository,
    FakeJobQueue,
    InMemoryAuditPort,
]:
    ids = _id_factory()
    resolved_storage = storage or FakeObjectStorage()
    resolved_repository = repository or FakeDocumentRepository()
    resolved_queue = queue or FakeJobQueue()
    resolved_audit = audit or InMemoryAuditPort()
    return (
        DocumentUploadService(
            object_storage=resolved_storage,
            repository=resolved_repository,
            job_queue=resolved_queue,
            audit=resolved_audit,
            max_upload_bytes=max_upload_bytes,
            id_factory=lambda: next(ids),
        ),
        resolved_storage,
        resolved_repository,
        resolved_queue,
        resolved_audit,
    )


@pytest.mark.asyncio
async def test_upload_requires_document_permission_before_any_side_effect() -> None:
    service, storage, repository, queue, audit = _service()

    with pytest.raises(DocumentUploadForbiddenError) as exc_info:
        await service.upload(_context(permissions=("document:read",)), _command())

    assert exc_info.value.code == "DOCUMENT_UPLOAD_FORBIDDEN"
    assert storage.calls == []
    assert repository.created == []
    assert queue.payloads == []
    assert repository.commits == 1
    assert audit.events[-1].status is AuditStatus.DENIED
    assert audit.events[-1].error_code == "DOCUMENT_UPLOAD_FORBIDDEN"


@pytest.mark.asyncio
async def test_upload_success_stores_metadata_and_enqueues_id_only_payload() -> None:
    service, storage, repository, queue, audit = _service()

    result = await service.upload(_context(permissions=("document:upload",)), _command())

    assert result.document_id == "doc-1"
    assert result.version_id == "ver-1"
    assert result.job_id == "job-1"
    assert result.status == "uploaded"
    assert len(storage.calls) == 1
    assert storage.calls[0]["byte_size"] == len(b"hello policy")
    assert storage.calls[0]["content"] == b"hello policy"
    document, version, job = repository.created[0]
    assert document.tenant_id == "tenant-1"
    assert document.created_by == "user-1"
    assert document.source_type == "txt"
    assert document.acl == {"visibility": "tenant"}
    assert version.object_key == "raw/tenant-1/doc-1/ver-1/policy.txt"
    assert version.byte_size == len(b"hello policy")
    assert job.document_id == "doc-1"
    assert job.version_id == "ver-1"
    assert queue.payloads == [
        {
            "request_id": "req-1",
            "trace_id": "trace-1",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "job_type": "ingestion.process_document",
            "resource_id": "job-1",
            "parameters": {"document_id": "doc-1", "version_id": "ver-1"},
        }
    ]
    assert b"hello policy" not in repr(queue.payloads).encode()
    assert repository.queued_jobs == [("job-1", "rq-job-1")]
    assert repository.commits == 2
    assert audit.events[-1].status is AuditStatus.SUCCESS
    assert audit.events[-1].resource.id == "doc-1"


@pytest.mark.asyncio
async def test_upload_with_document_id_creates_new_version_without_overwriting_document() -> None:
    repository = FakeDocumentRepository(existing_document=_document())
    service, storage, _repository, queue, audit = _service(repository=repository)

    result = await service.upload(
        _context(permissions=("document:manage",)),
        _command(document_id="doc-existing", source_uri="kb://policy-v2.txt"),
    )

    assert result.document_id == "doc-existing"
    assert result.version_id == "doc-1"
    assert result.job_id == "ver-1"
    assert repository.created == []
    assert len(repository.created_versions) == 1
    version, job = repository.created_versions[0]
    assert version.document_id == "doc-existing"
    assert version.status == "uploaded"
    assert version.source_uri == "kb://policy-v2.txt"
    assert job.document_id == "doc-existing"
    assert job.version_id == "doc-1"
    assert storage.calls[0]["document_id"] == "doc-existing"
    assert storage.calls[0]["version_id"] == "doc-1"
    assert queue.payloads[0]["parameters"] == {
        "document_id": "doc-existing",
        "version_id": "doc-1",
    }
    assert audit.events[-1].status is AuditStatus.SUCCESS


@pytest.mark.asyncio
async def test_upload_with_new_document_id_uses_upload_permission_and_fixed_version_id() -> None:
    service, storage, repository, queue, audit = _service()

    result = await service.upload(
        _context(permissions=("document:upload",)),
        _command(document_id="doc-demo", version_id="ver-demo"),
    )

    assert result.document_id == "doc-demo"
    assert result.version_id == "ver-demo"
    assert result.job_id == "doc-1"
    document, version, job = repository.created[0]
    assert document.id == "doc-demo"
    assert version.id == "ver-demo"
    assert job.version_id == "ver-demo"
    assert storage.calls[0]["document_id"] == "doc-demo"
    assert storage.calls[0]["version_id"] == "ver-demo"
    assert queue.payloads[0]["parameters"] == {
        "document_id": "doc-demo",
        "version_id": "ver-demo",
    }
    assert audit.events[-1].status is AuditStatus.SUCCESS


@pytest.mark.asyncio
async def test_upload_with_document_id_requires_manage_permission() -> None:
    repository = FakeDocumentRepository(existing_document=_document())
    service, storage, _repository, queue, audit = _service(repository=repository)

    with pytest.raises(DomainError) as exc_info:
        await service.upload(
            _context(permissions=("document:upload",)),
            _command(document_id="doc-existing"),
        )

    assert exc_info.value.code == "DOCUMENT_MANAGE_FORBIDDEN"
    assert storage.calls == []
    assert repository.created_versions == []
    assert queue.payloads == []
    assert audit.events[-1].status is AuditStatus.FAILURE


@pytest.mark.asyncio
async def test_upload_with_document_id_rejects_deleted_document() -> None:
    repository = FakeDocumentRepository(
        existing_document=_document(id="doc-existing", status="deleted")
    )
    service, storage, _repository, queue, audit = _service(repository=repository)

    with pytest.raises(DomainError) as exc_info:
        await service.upload(
            _context(permissions=("document:manage",)),
            _command(document_id="doc-existing"),
        )

    assert exc_info.value.code == "DOCUMENT_NOT_FOUND"
    assert storage.calls == []
    assert repository.created_versions == []
    assert queue.payloads == []
    assert audit.events[-1].error_code == "DOCUMENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_type_before_storage_db_or_queue() -> None:
    service, storage, repository, queue, audit = _service()

    with pytest.raises(DocumentUploadUnsupportedTypeError) as exc_info:
        await service.upload(
            _context(permissions=("document:manage",)),
            _command(
                filename="policy.exe",
                content_type="application/x-msdownload",
                source_type="txt",
            ),
        )

    assert exc_info.value.code == "DOCUMENT_UPLOAD_UNSUPPORTED_TYPE"
    assert storage.calls == []
    assert repository.created == []
    assert queue.payloads == []
    assert audit.events[-1].status is AuditStatus.FAILURE


@pytest.mark.asyncio
async def test_upload_rejects_oversized_file_before_storage_db_or_queue() -> None:
    service, storage, repository, queue, audit = _service(max_upload_bytes=4)

    with pytest.raises(DocumentUploadTooLargeError) as exc_info:
        await service.upload(
            _context(permissions=("document:upload",)),
            _command(stream=BytesIO(b"12345")),
        )

    assert exc_info.value.code == "DOCUMENT_UPLOAD_TOO_LARGE"
    assert storage.calls == []
    assert repository.created == []
    assert queue.payloads == []
    assert audit.events[-1].status is AuditStatus.FAILURE


@pytest.mark.asyncio
async def test_queue_failure_marks_persisted_job_failed_and_returns_domain_error() -> None:
    service, storage, repository, queue, audit = _service(queue=FakeJobQueue(fail=True))

    with pytest.raises(IngestionJobEnqueueError) as exc_info:
        await service.upload(_context(permissions=("document:upload",)), _command())

    assert exc_info.value.code == "INGESTION_JOB_ENQUEUE_FAILED"
    assert len(storage.calls) == 1
    assert len(repository.created) == 1
    assert repository.failed_jobs == [("job-1", "INGESTION_JOB_ENQUEUE_FAILED")]
    assert repository.commits == 2
    assert queue.payloads[0]["parameters"] == {"document_id": "doc-1", "version_id": "ver-1"}
    assert audit.events[-1].status is AuditStatus.FAILURE
    assert audit.events[-1].error_code == "INGESTION_JOB_ENQUEUE_FAILED"


@pytest.mark.asyncio
async def test_upload_commits_initial_records_before_enqueue() -> None:
    repository = FakeDocumentRepository()
    queue = FakeJobQueue(repository=repository)
    service, _storage, _repository, _queue, _audit = _service(
        repository=repository,
        queue=queue,
    )

    await service.upload(_context(permissions=("document:upload",)), _command())

    assert repository.commits == 2
    assert queue.payloads[0]["resource_id"] == "job-1"


@pytest.mark.asyncio
async def test_db_failure_after_object_write_deletes_orphaned_object() -> None:
    storage = FakeObjectStorage()
    repository = FakeDocumentRepository(fail_create=True)
    service, _storage, _repository, queue, _audit = _service(
        storage=storage,
        repository=repository,
    )

    with pytest.raises(StorageError):
        await service.upload(_context(permissions=("document:upload",)), _command())

    assert len(storage.calls) == 1
    assert storage.deleted == [
        {
            "tenant_id": "tenant-1",
            "document_id": "doc-1",
            "version_id": "ver-1",
            "object_key": "raw/tenant-1/doc-1/ver-1/policy.txt",
        }
    ]
    assert queue.payloads == []


@pytest.mark.asyncio
async def test_upload_rejects_missing_content_type_before_side_effects() -> None:
    service, storage, repository, queue, audit = _service()

    with pytest.raises(DocumentUploadUnsupportedTypeError):
        await service.upload(
            _context(permissions=("document:upload",)),
            _command(content_type=None),
        )

    assert storage.calls == []
    assert repository.created == []
    assert queue.payloads == []
    assert audit.events[-1].status is AuditStatus.FAILURE


@pytest.mark.asyncio
async def test_upload_rejects_empty_file_before_side_effects() -> None:
    service, storage, repository, queue, audit = _service()

    with pytest.raises(DocumentUploadInvalidMetadataError) as exc_info:
        await service.upload(
            _context(permissions=("document:upload",)),
            _command(stream=BytesIO(b"")),
        )

    assert exc_info.value.code == "DOCUMENT_UPLOAD_INVALID_METADATA"
    assert storage.calls == []
    assert repository.created == []
    assert queue.payloads == []
    assert audit.events[-1].status is AuditStatus.FAILURE


@pytest.mark.asyncio
async def test_upload_rejects_acl_outside_uploader_authority_before_side_effects() -> None:
    service, storage, repository, queue, audit = _service()

    with pytest.raises(DocumentUploadInvalidMetadataError):
        await service.upload(
            _context(permissions=("document:upload",)),
            _command(acl={"visibility": "public"}),
        )

    assert storage.calls == []
    assert repository.created == []
    assert queue.payloads == []
    assert audit.events[-1].error_code == "DOCUMENT_UPLOAD_INVALID_METADATA"


@pytest.mark.asyncio
async def test_upload_rejects_values_that_exceed_storage_column_limits() -> None:
    service, storage, repository, queue, audit = _service()

    with pytest.raises(DocumentUploadInvalidMetadataError):
        await service.upload(
            _context(permissions=("document:upload",)),
            _command(filename=f"{'a' * 513}.txt"),
        )

    assert storage.calls == []
    assert repository.created == []
    assert queue.payloads == []
    assert audit.events[-1].status is AuditStatus.FAILURE
