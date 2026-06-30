from __future__ import annotations

import hashlib
import re
import tempfile
import time
from collections.abc import Callable
from pathlib import PurePath
from typing import BinaryIO, Protocol, cast
from uuid import uuid4

from packages.auth.policies import has_document_manage_permission, has_document_upload_permission
from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError
from packages.data.dto import (
    DocumentRecord,
    DocumentVersionRecord,
    IngestionJobRecord,
    StoredObject,
    UploadDocumentCommand,
    UploadDocumentResult,
)
from packages.data.exceptions import (
    DocumentManageForbiddenError,
    DocumentNotFoundError,
    DocumentStorageWriteError,
    DocumentUploadForbiddenError,
    DocumentUploadInvalidMetadataError,
    DocumentUploadTooLargeError,
    DocumentUploadUnsupportedTypeError,
    IngestionJobEnqueueError,
)
from packages.data.ports import JobQueue
from packages.data.queue.ingestion import build_ingestion_queue_payload

DEFAULT_UPLOAD_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_UPLOAD_CHUNK_BYTES = 1024 * 1024
INITIAL_UPLOAD_STATUS = "uploaded"
FAILED_RETRYABLE_STATUS = "failed_retryable"
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_ALLOWED_ACL_KEYS = {"visibility", "roles", "users", "departments"}
_ALLOWED_ACL_VISIBILITY = {"tenant", "private", "restricted"}
_MAX_FILENAME_LENGTH = 512
_MAX_SOURCE_TYPE_LENGTH = 64
_MAX_SOURCE_URI_LENGTH = 2048
_MAX_TITLE_LENGTH = 512
_MAX_CONTENT_TYPE_LENGTH = 255
_ALLOWED_UPLOAD_TYPES: dict[str, dict[str, object]] = {
    "pdf": {
        "extensions": {".pdf"},
        "content_types": {"application/pdf"},
    },
    "docx": {
        "extensions": {".docx"},
        "content_types": {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        },
    },
    "txt": {
        "extensions": {".txt"},
        "content_types": {"text/plain"},
    },
    "markdown": {
        "extensions": {".md", ".markdown"},
        "content_types": {
            "text/markdown",
            "text/x-markdown",
            "text/plain",
        },
    },
    "image": {
        "extensions": {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"},
        "content_types": {
            "image/jpeg",
            "image/png",
            "image/bmp",
            "image/tiff",
        },
    },
    "scanned_pdf": {
        "extensions": {".pdf"},
        "content_types": {"application/pdf"},
    },
}


class UploadObjectStorage(Protocol):
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
    ) -> StoredObject: ...

    async def delete_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        object_key: str,
    ) -> None: ...


class UploadDocumentRepository(Protocol):
    async def create_upload_records(
        self,
        *,
        document: DocumentRecord,
        version: DocumentVersionRecord,
        job: IngestionJobRecord,
    ) -> tuple[DocumentRecord, DocumentVersionRecord, IngestionJobRecord]: ...

    async def get_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
    ) -> DocumentRecord | None: ...

    async def create_document_version_records(
        self,
        *,
        version: DocumentVersionRecord,
        job: IngestionJobRecord,
    ) -> tuple[DocumentVersionRecord, IngestionJobRecord]: ...

    async def mark_ingestion_job_queued(
        self,
        *,
        tenant_id: str,
        job_id: str,
        queue_job_id: str | None,
    ) -> IngestionJobRecord: ...

    async def mark_ingestion_job_failed(
        self,
        *,
        tenant_id: str,
        job_id: str,
        error_code: str,
    ) -> IngestionJobRecord: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class DocumentUploadService:
    def __init__(
        self,
        *,
        object_storage: UploadObjectStorage,
        repository: UploadDocumentRepository,
        job_queue: JobQueue,
        audit: AuditPort,
        max_upload_bytes: int = DEFAULT_UPLOAD_MAX_BYTES,
        id_factory: Callable[[], str] | None = None,
        queue_name: str = "ingestion",
    ) -> None:
        if max_upload_bytes <= 0:
            raise ValueError("max_upload_bytes must be positive")
        self._object_storage = object_storage
        self._repository = repository
        self._job_queue = job_queue
        self._audit = audit
        self._max_upload_bytes = max_upload_bytes
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._queue_name = queue_name

    async def upload(
        self,
        context: AuthenticatedRequestContext,
        command: UploadDocumentCommand,
    ) -> UploadDocumentResult:
        started = time.perf_counter()
        document_id: str | None = None
        version_id: str | None = None
        job_id: str | None = None
        byte_size: int | None = None
        checksum: str | None = None

        try:
            if not has_document_upload_permission(context.auth):
                raise DocumentUploadForbiddenError(
                    details={"required_permissions": ["document:upload", "document:manage"]}
                )
            _validate_command_bounds(command)
            _validate_acl(command.acl)
            _validate_source_uri(command.source_uri)
            _validate_upload_type(command)
            spooled_stream, byte_size, checksum = _copy_stream_with_checksum(
                command.stream,
                max_upload_bytes=self._max_upload_bytes,
            )
            if byte_size == 0:
                raise DocumentUploadInvalidMetadataError(
                    details={"field": "file", "reason": "empty"}
                )
            try:
                source_type = _normalized_source_type(command.source_type)
                existing_document: DocumentRecord | None = None
                if command.document_id is None:
                    document_id = self._id_factory()
                else:
                    document_id = command.document_id
                    existing_document = await self._repository.get_document(
                        tenant_id=context.auth.tenant_id,
                        document_id=document_id,
                    )
                    if existing_document is not None and (
                        existing_document.deleted_at is not None
                        or existing_document.status == "deleted"
                    ):
                        raise DocumentNotFoundError(details={"document_id": document_id})
                    if existing_document is not None and not has_document_manage_permission(
                        context.auth
                    ):
                        raise DocumentManageForbiddenError(
                            details={"required_permissions": ["document:manage"]}
                        )

                version_id = command.version_id or self._id_factory()
                job_id = self._id_factory()

                stored_object = await self._put_object(
                    context=context,
                    command=command,
                    document_id=document_id,
                    version_id=version_id,
                    stream=spooled_stream,
                    byte_size=byte_size,
                    checksum=checksum,
                )

                document = existing_document or DocumentRecord(
                    id=document_id,
                    tenant_id=context.auth.tenant_id,
                    created_by=context.auth.user_id,
                    status=INITIAL_UPLOAD_STATUS,
                    source_type=source_type,
                    source_uri=command.source_uri,
                    title=command.title,
                    acl=command.acl,
                    checksum=checksum,
                    metadata=command.metadata,
                )
                version = DocumentVersionRecord(
                    id=version_id,
                    document_id=document_id,
                    tenant_id=context.auth.tenant_id,
                    created_by=context.auth.user_id,
                    status=INITIAL_UPLOAD_STATUS,
                    source_type=source_type,
                    source_uri=command.source_uri,
                    object_key=stored_object.object_key,
                    filename=command.filename,
                    content_type=command.content_type,
                    byte_size=byte_size,
                    acl=command.acl,
                    checksum=checksum,
                    metadata={
                        **command.metadata,
                        "object_etag": stored_object.etag,
                        "object_bucket": stored_object.bucket,
                    },
                )
                job = IngestionJobRecord(
                    id=job_id,
                    tenant_id=context.auth.tenant_id,
                    created_by=context.auth.user_id,
                    status=INITIAL_UPLOAD_STATUS,
                    document_id=document_id,
                    version_id=version_id,
                    queue_name=self._queue_name,
                    queue_job_id=None,
                    attempt_count=0,
                    error_code=None,
                )
                try:
                    if existing_document is None:
                        await self._repository.create_upload_records(
                            document=document,
                            version=version,
                            job=job,
                        )
                    else:
                        await self._repository.create_document_version_records(
                            version=version,
                            job=job,
                        )
                    await self._repository.commit()
                except DomainError:
                    await self._delete_object(
                        context=context,
                        document_id=document_id,
                        version_id=version_id,
                        object_key=stored_object.object_key,
                    )
                    raise

                payload = build_ingestion_queue_payload(
                    context=context,
                    job_id=job_id,
                    document_id=document_id,
                    version_id=version_id,
                )
                try:
                    enqueued = await self._job_queue.enqueue_ingestion_job(payload)
                except IngestionJobEnqueueError as exc:
                    await self._repository.mark_ingestion_job_failed(
                        tenant_id=context.auth.tenant_id,
                        job_id=job_id,
                        error_code=exc.code,
                    )
                    await self._record_audit(
                        context=context,
                        status=AuditStatus.FAILURE,
                        started=started,
                        error_code=exc.code,
                        document_id=document_id,
                        version_id=version_id,
                        job_id=job_id,
                        source_type=source_type,
                        content_type=command.content_type,
                        byte_size=byte_size,
                        checksum=checksum,
                    )
                    await self._repository.commit()
                    raise

                await self._repository.mark_ingestion_job_queued(
                    tenant_id=context.auth.tenant_id,
                    job_id=job_id,
                    queue_job_id=enqueued.queue_job_id,
                )
                await self._record_audit(
                    context=context,
                    status=AuditStatus.SUCCESS,
                    started=started,
                    document_id=document_id,
                    version_id=version_id,
                    job_id=job_id,
                    source_type=source_type,
                    content_type=command.content_type,
                    byte_size=byte_size,
                    checksum=checksum,
                )
                await self._repository.commit()
                return UploadDocumentResult(
                    document_id=document_id,
                    version_id=version_id,
                    job_id=job_id,
                    status=INITIAL_UPLOAD_STATUS,
                )
            finally:
                spooled_stream.close()
        except DomainError as exc:
            await self._handle_domain_failure(
                context=context,
                started=started,
                error=exc,
                document_id=document_id,
                version_id=version_id,
                job_id=job_id,
                source_type=command.source_type,
                content_type=command.content_type,
                byte_size=byte_size,
                checksum=checksum,
            )
            raise

    async def _put_object(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: UploadDocumentCommand,
        document_id: str,
        version_id: str,
        stream: BinaryIO,
        byte_size: int,
        checksum: str,
    ) -> StoredObject:
        try:
            stream.seek(0)
            return await self._object_storage.put_document(
                tenant_id=context.auth.tenant_id,
                document_id=document_id,
                version_id=version_id,
                filename=command.filename,
                content_type=command.content_type,
                stream=stream,
                byte_size=byte_size,
                checksum=checksum,
            )
        except DomainError:
            raise
        except Exception as exc:
            raise DocumentStorageWriteError(
                details={"source_type": _normalized_source_type(command.source_type)}
            ) from exc

    async def _delete_object(
        self,
        *,
        context: AuthenticatedRequestContext,
        document_id: str,
        version_id: str,
        object_key: str,
    ) -> None:
        await self._object_storage.delete_document(
            tenant_id=context.auth.tenant_id,
            document_id=document_id,
            version_id=version_id,
            object_key=object_key,
        )

    async def _handle_domain_failure(
        self,
        *,
        context: AuthenticatedRequestContext,
        started: float,
        error: DomainError,
        document_id: str | None,
        version_id: str | None,
        job_id: str | None,
        source_type: str,
        content_type: str | None,
        byte_size: int | None,
        checksum: str | None,
    ) -> None:
        if error.code == "INGESTION_JOB_ENQUEUE_FAILED":
            return
        if document_id is not None:
            await self._repository.rollback()
        status = (
            AuditStatus.DENIED if error.code == "DOCUMENT_UPLOAD_FORBIDDEN" else AuditStatus.FAILURE
        )
        await self._record_audit(
            context=context,
            status=status,
            started=started,
            error_code=error.code,
            document_id=document_id,
            version_id=version_id,
            job_id=job_id,
            source_type=_normalized_source_type(source_type),
            content_type=content_type,
            byte_size=byte_size,
            checksum=checksum,
        )
        await self._repository.commit()

    async def _record_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        status: AuditStatus,
        started: float,
        error_code: str | None = None,
        document_id: str | None = None,
        version_id: str | None = None,
        job_id: str | None = None,
        source_type: str | None = None,
        content_type: str | None = None,
        byte_size: int | None = None,
        checksum: str | None = None,
    ) -> None:
        resource_id = document_id or "upload"
        metadata: dict[str, object] = {}
        if source_type is not None:
            metadata["source_type"] = source_type
        if content_type is not None:
            metadata["content_type"] = content_type
        if byte_size is not None:
            metadata["byte_size"] = byte_size
        if checksum is not None:
            metadata["checksum"] = checksum
        if document_id is not None:
            metadata["document_id"] = document_id
        if version_id is not None:
            metadata["version_id"] = version_id
        if job_id is not None:
            metadata["job_id"] = job_id

        await self._audit.record(
            AuditEvent(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                action="document.upload",
                resource=AuditResource(
                    type="document",
                    id=resource_id,
                    metadata=metadata,
                ),
                status=status,
                latency_ms=(time.perf_counter() - started) * 1000,
                error_code=error_code,
                metadata=metadata,
            )
        )


def _copy_stream_with_checksum(
    stream: BinaryIO,
    *,
    max_upload_bytes: int,
) -> tuple[BinaryIO, int, str]:
    checksum = hashlib.sha256()
    total = 0
    spooled = tempfile.SpooledTemporaryFile(
        max_size=min(max_upload_bytes, DEFAULT_UPLOAD_CHUNK_BYTES)
    )
    try:
        while True:
            chunk = stream.read(DEFAULT_UPLOAD_CHUNK_BYTES)
            if chunk == b"":
                break
            if not isinstance(chunk, bytes):
                raise DocumentUploadInvalidMetadataError(details={"field": "file"})
            total += len(chunk)
            if total > max_upload_bytes:
                raise DocumentUploadTooLargeError(details={"max_bytes": max_upload_bytes})
            checksum.update(chunk)
            spooled.write(chunk)
        spooled.seek(0)
        return cast(BinaryIO, spooled), total, checksum.hexdigest()
    except Exception:
        spooled.close()
        raise


def _validate_upload_type(command: UploadDocumentCommand) -> None:
    source_type = _normalized_source_type(command.source_type)
    expected = _ALLOWED_UPLOAD_TYPES.get(source_type)
    if expected is None:
        raise DocumentUploadUnsupportedTypeError(details={"source_type": source_type})

    extension = PurePath(command.filename).suffix.lower()
    extensions = expected["extensions"]
    if not isinstance(extensions, set) or extension not in extensions:
        raise DocumentUploadUnsupportedTypeError(
            details={"source_type": source_type, "extension": extension or None}
        )

    content_type = _normalized_content_type(command.content_type)
    if content_type is None:
        raise DocumentUploadUnsupportedTypeError(
            details={"source_type": source_type, "content_type": None}
        )

    content_types = expected["content_types"]
    if not isinstance(content_types, set) or content_type not in content_types:
        raise DocumentUploadUnsupportedTypeError(
            details={"source_type": source_type, "content_type": content_type}
        )


def _validate_command_bounds(command: UploadDocumentCommand) -> None:
    limits = (
        ("filename", command.filename, _MAX_FILENAME_LENGTH),
        ("source_type", command.source_type, _MAX_SOURCE_TYPE_LENGTH),
        ("source_uri", command.source_uri, _MAX_SOURCE_URI_LENGTH),
        ("title", command.title, _MAX_TITLE_LENGTH),
        ("content_type", command.content_type, _MAX_CONTENT_TYPE_LENGTH),
    )
    for field_name, value, max_length in limits:
        if value is not None and len(value) > max_length:
            raise DocumentUploadInvalidMetadataError(
                details={"field": field_name, "max_length": max_length}
            )


def _validate_acl(acl: dict[str, object]) -> None:
    extra_keys = set(acl) - _ALLOWED_ACL_KEYS
    if extra_keys:
        raise DocumentUploadInvalidMetadataError(
            details={"field": "acl", "invalid_keys": sorted(extra_keys)}
        )
    visibility = acl.get("visibility", "tenant")
    if visibility not in _ALLOWED_ACL_VISIBILITY:
        raise DocumentUploadInvalidMetadataError(details={"field": "acl.visibility"})
    for field_name in ("roles", "users", "departments"):
        if field_name not in acl:
            continue
        value = acl[field_name]
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise DocumentUploadInvalidMetadataError(details={"field": f"acl.{field_name}"})


def _validate_source_uri(source_uri: str | None) -> None:
    if source_uri is None:
        return
    normalized = source_uri.strip()
    if (
        normalized.startswith("/")
        or normalized.startswith("\\\\")
        or _WINDOWS_ABSOLUTE_PATH.match(normalized) is not None
    ):
        raise DocumentUploadInvalidMetadataError(details={"field": "source_uri"})


def _normalized_source_type(source_type: str) -> str:
    normalized = source_type.strip().lower()
    if normalized in {"md", "markdown"}:
        return "markdown"
    return normalized


def _normalized_content_type(content_type: str | None) -> str | None:
    if content_type is None:
        return None
    return content_type.split(";", maxsplit=1)[0].strip().lower() or None
