"""Authorized, bounded recovery of an already committed embedding handoff.

This operation never reparses a document, creates a replacement embedding job,
or changes stored attempts or an existing delivery's retry budget. It is
deliberately not a general job retry.
"""
from __future__ import annotations

import time
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from packages.auth.policies import has_document_manage_permission
from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError
from packages.data.dto import (
    DocumentRecord,
    DocumentVersionRecord,
    EmbeddingJobRecord,
    IngestionJobRecord,
)
from packages.data.exceptions import DocumentManageForbiddenError, DocumentVersionNotFoundError
from packages.data.queue.contracts import QueuePayload
from packages.data.queue.embedding import EMBEDDING_JOB_TYPE


class HandoffRecoveryCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ingestion_job_id: str = Field(min_length=1, max_length=128)


class HandoffDelivery(BaseModel):
    model_config = ConfigDict(frozen=True)

    queue_job_id: str
    queue_name: str
    queue_status: Literal["queued"]


class HandoffRecoveryResult(HandoffDelivery):
    status: Literal["delivery_confirmed"] = "delivery_confirmed"
    document_id: str
    version_id: str
    ingestion_job_id: str
    embedding_job_id: str
    request_id: str
    trace_id: str


class HandoffRecoveryRepository(Protocol):
    async def get_document(self, *, tenant_id: str, document_id: str) -> DocumentRecord | None: ...

    async def get_version(
        self, *, tenant_id: str, version_id: str,
    ) -> DocumentVersionRecord | None: ...

    async def get_ingestion_job(
        self, *, tenant_id: str, job_id: str,
    ) -> IngestionJobRecord | None: ...

    async def get_ingestion_embedding_handoff(
        self, *, tenant_id: str, job_id: str, document_id: str, version_id: str,
    ) -> EmbeddingJobRecord | None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class HandoffRecoveryQueue(Protocol):
    async def recover_embedding_handoff(self, payload: QueuePayload) -> HandoffDelivery: ...


class HandoffRecoveryService:
    def __init__(
        self, *, repository: HandoffRecoveryRepository, queue: HandoffRecoveryQueue,
        audit: AuditPort,
    ) -> None:
        self._repository = repository
        self._queue = queue
        self._audit = audit

    async def recover(
        self, context: AuthenticatedRequestContext, *, document_id: str,
        version_id: str, command: HandoffRecoveryCommand,
    ) -> HandoffRecoveryResult:
        started = time.perf_counter()
        try:
            if not has_document_manage_permission(context.auth):
                raise DocumentManageForbiddenError()
            tenant_id = context.auth.tenant_id
            document = await self._repository.get_document(
                tenant_id=tenant_id, document_id=document_id,
            )
            version = await self._repository.get_version(
                tenant_id=tenant_id, version_id=version_id,
            )
            job = await self._repository.get_ingestion_job(
                tenant_id=tenant_id, job_id=command.ingestion_job_id,
            )
            if (
                document is None or version is None or job is None
                or version.document_id != document_id
                or job.document_id != document_id or job.version_id != version_id
                or document.deleted_at is not None or document.status == "deleted"
                or version.deleted_at is not None or version.status == "deleted"
            ):
                raise DocumentVersionNotFoundError()
            if job.status != "chunked" or version.status != "chunked":
                raise _not_recoverable()
            handoff = await self._repository.get_ingestion_embedding_handoff(
                tenant_id=tenant_id, job_id=job.id,
                document_id=document_id, version_id=version_id,
            )
            if handoff is None or handoff.status != "queued" or handoff.attempt_count != 0:
                raise _not_recoverable()
            # Preserve the persisted task owner. The authenticated operator is
            # recorded separately in audit; roles/permissions are never forged.
            payload = QueuePayload(
                request_id=context.request_id, trace_id=context.trace_id,
                tenant_id=tenant_id, user_id=handoff.created_by,
                job_type=EMBEDDING_JOB_TYPE, resource_id=handoff.id,
                parameters={"document_id": document_id, "version_id": version_id},
            )
            delivery = await self._queue.recover_embedding_handoff(payload)
            result = HandoffRecoveryResult(
                **delivery.model_dump(), document_id=document_id, version_id=version_id,
                ingestion_job_id=job.id, embedding_job_id=handoff.id,
                request_id=context.request_id, trace_id=context.trace_id,
            )
        except Exception as exc:
            error = _safe_recovery_error(exc)
            await self._repository.rollback()
            await self._record(
                context, document_id=document_id, version_id=version_id,
                ingestion_job_id=command.ingestion_job_id, started=started,
                status=(AuditStatus.DENIED if error.status_code == 403 else AuditStatus.FAILURE),
                error_code=error.code,
            )
            raise error from exc
        await self._record(
            context, document_id=document_id, version_id=version_id,
            ingestion_job_id=command.ingestion_job_id, started=started,
            status=AuditStatus.SUCCESS, error_code=None,
        )
        return result

    async def _record(
        self, context: AuthenticatedRequestContext, *, document_id: str,
        version_id: str, ingestion_job_id: str, started: float, status: AuditStatus,
        error_code: str | None,
    ) -> None:
        try:
            await self._audit.record(AuditEvent(
                request_id=context.request_id, trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id, user_id=context.auth.user_id,
                action="document.embedding_handoff.recover",
                resource=AuditResource(type="document", id=document_id),
                status=status, latency_ms=(time.perf_counter() - started) * 1000,
                error_code=error_code,
                metadata={"version_id": version_id, "ingestion_job_id": ingestion_job_id},
            ))
            await self._repository.commit()
        except Exception as exc:
            await self._repository.rollback()
            raise _safe_recovery_error(exc) from exc


def _not_recoverable() -> DomainError:
    return DomainError(
        code="EMBEDDING_HANDOFF_NOT_RECOVERABLE",
        message="Only an unstarted, chunked embedding handoff can be recovered. Check status.",
        status_code=409,
    )


def _safe_recovery_error(exc: Exception) -> DomainError:
    if isinstance(exc, DomainError) and exc.code in {
        "DOCUMENT_MANAGE_FORBIDDEN", "DOCUMENT_VERSION_NOT_FOUND",
        "EMBEDDING_HANDOFF_NOT_RECOVERABLE", "EMBEDDING_HANDOFF_QUEUE_CONFLICT",
    }:
        return exc
    if isinstance(exc, DomainError) and exc.code in {
        "EMBEDDING_HANDOFF_INVALID_STATE", "EMBEDDING_HANDOFF_NOT_FOUND",
    }:
        return _not_recoverable()
    return DomainError(
        code="EMBEDDING_HANDOFF_RECOVERY_UNAVAILABLE",
        message="Handoff delivery could not be confirmed. Check status before retrying.",
        status_code=503,
    )
