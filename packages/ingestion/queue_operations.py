"""Bounded operator controls for ingestion and embedding queue deliveries.

Domain attempts belong to worker claims. Operators may consume a remaining RQ
retry, but cannot replenish it or turn a semantic terminal failure into a retry.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, NoReturn, Protocol

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
from packages.data.exceptions import DocumentManageForbiddenError
from packages.data.queue.contracts import QueuePayload

JobKind = Literal["ingestion", "embedding"]
AttentionState = Literal["failed", "stale", "stuck", "terminated"]
JobRecord = IngestionJobRecord | EmbeddingJobRecord


class QueueOperationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_attempt_count: int = Field(ge=0)
    expected_queue_status: Literal[
        "queued", "scheduled", "started", "failed", "stopped", "canceled"
    ]


class QueueJobItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_kind: JobKind
    job_id: str
    document_id: str
    version_id: str
    queue_job_id: str
    queue_name: str
    queue_status: str
    state: AttentionState
    domain_status: str
    attempt_count: int
    retries_remaining: int
    age_seconds: int
    last_heartbeat_at: datetime | None = None
    error_code: str | None = None
    retry_allowed: bool
    terminate_allowed: bool


class QueueJobListResult(BaseModel):
    items: list[QueueJobItem]
    next_cursor: str | None
    scanned_count: int
    request_id: str
    trace_id: str


class QueueOperationResult(BaseModel):
    status: Literal["queued", "terminated", "stop_requested"]
    job_kind: JobKind
    job_id: str
    queue_job_id: str
    queue_name: str
    attempt_count: int
    retries_remaining: int
    request_id: str
    trace_id: str


@dataclass(frozen=True)
class QueueDelivery:
    job_kind: JobKind
    queue_job_id: str
    queue_name: str
    queue_status: str
    payload: QueuePayload
    state: AttentionState | None
    retries_remaining: int
    age_seconds: int
    last_heartbeat_at: datetime | None = None


@dataclass(frozen=True)
class QueueScan:
    deliveries: list[QueueDelivery]
    next_cursor: str | None
    scanned_count: int


class QueueOperationsPort(Protocol):
    async def scan(
        self,
        *,
        tenant_id: str,
        cursor: str | None,
        limit: int,
        stale_after_seconds: int,
    ) -> QueueScan: ...

    async def get(
        self, *, tenant_id: str, job_kind: JobKind, queue_job_id: str
    ) -> QueueDelivery: ...

    async def retry(self, delivery: QueueDelivery) -> QueueDelivery: ...

    async def terminate(
        self, delivery: QueueDelivery
    ) -> Literal["terminated", "stop_requested"]: ...


class QueueOperationsRepository(Protocol):
    async def get_document(self, *, tenant_id: str, document_id: str) -> DocumentRecord | None: ...

    async def get_version(
        self,
        *,
        tenant_id: str,
        version_id: str,
    ) -> DocumentVersionRecord | None: ...

    async def get_ingestion_job(
        self, *, tenant_id: str, job_id: str
    ) -> IngestionJobRecord | None: ...

    async def get_embedding_job(
        self, *, tenant_id: str, job_id: str
    ) -> EmbeddingJobRecord | None: ...

    async def mark_ingestion_job_failed(
        self,
        *,
        tenant_id: str,
        job_id: str,
        error_code: str,
        status: str,
        expected_attempt_count: int | None = None,
    ) -> IngestionJobRecord: ...

    async def mark_embedding_job_failed(
        self,
        *,
        tenant_id: str,
        job_id: str,
        error_code: str,
        status: str,
        expected_attempt_count: int | None = None,
    ) -> EmbeddingJobRecord: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class QueueOperationsService:
    def __init__(
        self,
        *,
        repository: QueueOperationsRepository,
        queue: QueueOperationsPort,
        audit: AuditPort,
        stale_after_seconds: int = 900,
    ) -> None:
        self._repository = repository
        self._queue = queue
        self._audit = audit
        self._stale_after_seconds = max(stale_after_seconds, 1)

    async def list_jobs(
        self,
        context: AuthenticatedRequestContext,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> QueueJobListResult:
        started = time.perf_counter()
        try:
            self._require_permission(context)
            if not 1 <= limit <= 100:
                raise queue_error("QUEUE_OPERATIONS_INVALID_REQUEST", 422)
            scan = await self._queue.scan(
                tenant_id=context.auth.tenant_id,
                cursor=cursor,
                limit=limit,
                stale_after_seconds=self._stale_after_seconds,
            )
            items = []
            for delivery in scan.deliveries:
                job = await self._load_job(context, delivery, missing_ok=True)
                if job is not None and delivery.state is not None:
                    items.append(_item(delivery, job))
            result = QueueJobListResult(
                items=items,
                next_cursor=scan.next_cursor,
                scanned_count=scan.scanned_count,
                request_id=context.request_id,
                trace_id=context.trace_id,
            )
            await self._record(context, "list", "queues", started, metadata={"count": len(items)})
            return result
        except Exception as exc:
            await self._failure(context, "list", "queues", started, exc)

    async def retry_job(
        self,
        context: AuthenticatedRequestContext,
        *,
        job_kind: JobKind,
        queue_job_id: str,
        command: QueueOperationCommand,
    ) -> QueueOperationResult:
        return await self._operate(context, job_kind, queue_job_id, command, retry=True)

    async def terminate_job(
        self,
        context: AuthenticatedRequestContext,
        *,
        job_kind: JobKind,
        queue_job_id: str,
        command: QueueOperationCommand,
    ) -> QueueOperationResult:
        return await self._operate(context, job_kind, queue_job_id, command, retry=False)

    async def _operate(
        self,
        context: AuthenticatedRequestContext,
        job_kind: JobKind,
        queue_job_id: str,
        command: QueueOperationCommand,
        *,
        retry: bool,
    ) -> QueueOperationResult:
        started = time.perf_counter()
        action = "retry" if retry else "terminate"
        try:
            self._require_permission(context)
            delivery = await self._queue.get(
                tenant_id=context.auth.tenant_id,
                job_kind=job_kind,
                queue_job_id=queue_job_id,
            )
            job = await self._load_job(context, delivery)
            if job is None:
                raise queue_error("QUEUE_JOB_NOT_FOUND", 404)
            if (
                job.attempt_count != command.expected_attempt_count
                or delivery.queue_status != command.expected_queue_status
            ):
                raise queue_error("QUEUE_JOB_CHANGED", 409)
            if retry:
                if not _retry_allowed(delivery, job):
                    raise queue_error("QUEUE_JOB_NOT_RETRYABLE", 409)
                delivery = await self._queue.retry(delivery)
                status: Literal["queued", "terminated", "stop_requested"] = "queued"
            else:
                if not _terminate_allowed(delivery, job):
                    raise queue_error("QUEUE_JOB_NOT_TERMINABLE", 409)
                status = await self._queue.terminate(delivery)
                if status == "terminated":
                    marker = (
                        self._repository.mark_ingestion_job_failed
                        if job_kind == "ingestion"
                        else self._repository.mark_embedding_job_failed
                    )
                    updated = await marker(
                        tenant_id=context.auth.tenant_id,
                        job_id=job.id,
                        error_code="QUEUE_OPERATOR_TERMINATED",
                        status="failed_terminal",
                        expected_attempt_count=job.attempt_count,
                    )
                    if updated.status != "failed_terminal":
                        raise queue_error("QUEUE_JOB_CHANGED", 409)
            await self._record(
                context,
                action,
                job.id,
                started,
                metadata={"job_kind": job_kind, "queue_job_id": queue_job_id, "result": status},
            )
            return QueueOperationResult(
                status=status,
                job_kind=job_kind,
                job_id=job.id,
                queue_job_id=queue_job_id,
                queue_name=delivery.queue_name,
                attempt_count=job.attempt_count,
                retries_remaining=delivery.retries_remaining,
                request_id=context.request_id,
                trace_id=context.trace_id,
            )
        except Exception as exc:
            await self._failure(context, action, queue_job_id, started, exc)

    async def _load_job(
        self,
        context: AuthenticatedRequestContext,
        delivery: QueueDelivery,
        *,
        missing_ok: bool = False,
    ) -> JobRecord | None:
        payload = delivery.payload
        tenant_id = context.auth.tenant_id
        reader = (
            self._repository.get_ingestion_job
            if delivery.job_kind == "ingestion"
            else self._repository.get_embedding_job
        )
        job = await reader(tenant_id=tenant_id, job_id=payload.resource_id)
        document_id = payload.parameters.get("document_id")
        version_id = payload.parameters.get("version_id")
        if (
            job is None
            or job.tenant_id != tenant_id
            or payload.tenant_id != tenant_id
            or job.document_id != document_id
            or job.version_id != version_id
            or job.created_by != payload.user_id
        ):
            if missing_ok:
                return None
            raise queue_error("QUEUE_JOB_NOT_FOUND", 404)
        document = await self._repository.get_document(
            tenant_id=tenant_id, document_id=job.document_id
        )
        version = await self._repository.get_version(tenant_id=tenant_id, version_id=job.version_id)
        if (
            document is None
            or version is None
            or version.document_id != job.document_id
            or document.deleted_at is not None
            or version.deleted_at is not None
            or document.status == "deleted"
            or version.status == "deleted"
        ):
            if missing_ok:
                return None
            raise queue_error("QUEUE_JOB_NOT_FOUND", 404)
        return job

    @staticmethod
    def _require_permission(context: AuthenticatedRequestContext) -> None:
        if not has_document_manage_permission(context.auth):
            raise DocumentManageForbiddenError()

    async def _record(
        self,
        context: AuthenticatedRequestContext,
        action: str,
        resource_id: str,
        started: float,
        *,
        metadata: dict[str, object] | None = None,
        error: DomainError | None = None,
    ) -> None:
        await self._audit.record(
            AuditEvent(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                action=f"queue.job.{action}",
                resource=AuditResource(type="queue_job", id=resource_id),
                status=(
                    AuditStatus.DENIED
                    if error and error.status_code == 403
                    else AuditStatus.FAILURE
                    if error
                    else AuditStatus.SUCCESS
                ),
                latency_ms=(time.perf_counter() - started) * 1000,
                error_code=error.code if error else None,
                metadata=metadata or {},
            )
        )
        await self._repository.commit()

    async def _failure(
        self,
        context: AuthenticatedRequestContext,
        action: str,
        resource_id: str,
        started: float,
        exc: Exception,
    ) -> NoReturn:
        await self._repository.rollback()
        error = (
            exc
            if isinstance(exc, DomainError)
            else queue_error("QUEUE_OPERATIONS_UNAVAILABLE", 503)
        )
        await self._record(context, action, resource_id, started, error=error)
        raise error from exc


def _retry_allowed(delivery: QueueDelivery, job: JobRecord) -> bool:
    due = job.next_retry_at is None or _utc(job.next_retry_at) <= datetime.now(tz=UTC)
    return (
        delivery.queue_status in {"failed", "stopped"}
        and delivery.retries_remaining > 0
        and job.status == "failed_retryable"
        and due
    )


def _terminate_allowed(delivery: QueueDelivery, job: JobRecord) -> bool:
    return delivery.queue_status in {
        "queued",
        "scheduled",
        "started",
        "failed",
        "stopped",
        "canceled",
    } and job.status not in {"chunked", "embedded", "retrieval_ready", "deleted"}


def _item(delivery: QueueDelivery, job: JobRecord) -> QueueJobItem:
    assert delivery.state is not None
    return QueueJobItem(
        job_kind=delivery.job_kind,
        job_id=job.id,
        document_id=job.document_id,
        version_id=job.version_id,
        queue_job_id=delivery.queue_job_id,
        queue_name=delivery.queue_name,
        queue_status=delivery.queue_status,
        state=delivery.state,
        domain_status=job.status,
        attempt_count=job.attempt_count,
        retries_remaining=delivery.retries_remaining,
        age_seconds=delivery.age_seconds,
        last_heartbeat_at=delivery.last_heartbeat_at,
        error_code=job.error_code,
        retry_allowed=_retry_allowed(delivery, job),
        terminate_allowed=_terminate_allowed(delivery, job),
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def queue_error(code: str, status_code: int) -> DomainError:
    messages = {
        "QUEUE_JOB_NOT_FOUND": "Queue delivery was not found.",
        "QUEUE_JOB_CHANGED": "Job state changed. Refresh before trying again.",
        "QUEUE_JOB_NOT_RETRYABLE": "This delivery has no eligible remaining retry.",
        "QUEUE_JOB_NOT_TERMINABLE": "This delivery cannot be terminated in its current state.",
        "QUEUE_OPERATIONS_INVALID_REQUEST": "Queue operation parameters are invalid.",
        "QUEUE_OPERATIONS_UNAVAILABLE": "Queue operation was not confirmed. Refresh job status.",
    }
    return DomainError(code=code, message=messages[code], status_code=status_code)
