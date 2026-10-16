"""RQ 2.9 operator adapter; inspection never runs registry cleanup.

Mutations keep the original JSON payload, queue identity, and remaining retry
budget. WATCH makes operator writes conditional on the current delivery state.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal, cast

from redis.exceptions import WatchError
from rq import Queue
from rq.command import send_stop_job_command
from rq.exceptions import NoSuchJobError
from rq.job import Job
from rq.registry import (
    CanceledJobRegistry,
    FailedJobRegistry,
    ScheduledJobRegistry,
    StartedJobRegistry,
)
from rq.serializers import JSONSerializer

from packages.common.config import AppSettings
from packages.data.queue.adapters import (
    EMBEDDING_JOB_TARGET,
    INGESTION_JOB_TARGET,
    _idempotent_job_id,
)
from packages.data.queue.contracts import QueuePayload
from packages.data.queue.embedding import EMBEDDING_JOB_TYPE
from packages.data.queue.ingestion import INGESTION_JOB_TYPE
from packages.data.queue.rq_worker import WorkerSettings, create_queue
from packages.ingestion.queue_operations import (
    AttentionState,
    JobKind,
    QueueDelivery,
    QueueScan,
    queue_error,
)

_REGISTRIES = ("failed", "started", "queued", "scheduled", "canceled")
_MAX_CURSOR_OFFSET = 1_000_000


@dataclass(frozen=True)
class RQQueueOperations:
    ingestion: Queue
    embedding: Queue

    @classmethod
    def from_settings(cls, settings: AppSettings) -> RQQueueOperations:
        if not settings.redis_url:
            raise queue_error("QUEUE_OPERATIONS_UNAVAILABLE", 503)
        return cls(
            **{
                kind: create_queue(
                    WorkerSettings(
                        redis_url=settings.redis_url,
                        queue_name=getattr(settings, f"{kind}_queue_name").strip(),
                        redis_timeout_seconds=settings.readiness_timeout_seconds,
                    )
                )
                for kind in ("ingestion", "embedding")
            }
        )

    async def scan(
        self,
        *,
        tenant_id: str,
        cursor: str | None,
        limit: int,
        stale_after_seconds: int,
    ) -> QueueScan:
        return await asyncio.to_thread(
            self._scan,
            tenant_id,
            cursor,
            limit,
            stale_after_seconds,
        )

    def _scan(
        self,
        tenant_id: str,
        cursor: str | None,
        limit: int,
        stale_after_seconds: int,
    ) -> QueueScan:
        source, offset = _decode_cursor(cursor)
        deliveries: list[QueueDelivery] = []
        scanned = 0
        own_scanned = 0
        now = datetime.now(tz=UTC)
        while source < 2 * len(_REGISTRIES) and scanned < limit:
            kind: JobKind = "ingestion" if source < len(_REGISTRIES) else "embedding"
            queue = self._queue(kind)
            registry = _REGISTRIES[source % len(_REGISTRIES)]
            remaining = limit - scanned
            if registry == "queued":
                ids = queue.get_job_ids(offset=offset, length=remaining + 1)
            else:
                ids = _registry(queue, registry).get_job_ids(
                    start=offset,
                    end=offset + remaining,
                    cleanup=False,
                )
            for job_id in ids[:remaining]:
                scanned += 1
                try:
                    delivery = self._read(kind, job_id, tenant_id, now, stale_after_seconds)
                except NoSuchJobError:
                    continue
                if delivery is not None:
                    own_scanned += 1
                    if delivery.state is not None:
                        deliveries.append(delivery)
            if len(ids) > remaining:
                offset += remaining
                break
            source += 1
            offset = 0
        next_cursor = f"{source}:{offset}" if source < 2 * len(_REGISTRIES) else None
        return QueueScan(deliveries, next_cursor, own_scanned)

    async def get(self, *, tenant_id: str, job_kind: JobKind, queue_job_id: str) -> QueueDelivery:
        try:
            delivery = await asyncio.to_thread(
                self._read,
                job_kind,
                queue_job_id,
                tenant_id,
                datetime.now(tz=UTC),
                900,
            )
        except NoSuchJobError as exc:
            raise queue_error("QUEUE_JOB_NOT_FOUND", 404) from exc
        if delivery is None:
            raise queue_error("QUEUE_JOB_NOT_FOUND", 404)
        return delivery

    def _read(
        self,
        kind: JobKind,
        job_id: str,
        tenant_id: str,
        now: datetime,
        stale_after_seconds: int,
    ) -> QueueDelivery | None:
        queue = self._queue(kind)
        job = Job.fetch(job_id, connection=queue.connection, serializer=JSONSerializer)
        payload = _validated_payload(job, kind, queue.name, tenant_id)
        if payload is None:
            return None
        return _delivery(job, kind, payload, now, stale_after_seconds, queue)

    async def retry(self, delivery: QueueDelivery) -> QueueDelivery:
        return await asyncio.to_thread(self._retry, delivery)

    def _retry(self, delivery: QueueDelivery) -> QueueDelivery:
        queue = self._queue(delivery.job_kind)
        registry = FailedJobRegistry(queue=queue)
        try:
            with queue.connection.pipeline() as pipeline:
                pipeline.watch(  # type: ignore[no-untyped-call]
                    Job.key_for(delivery.queue_job_id), registry.key
                )
                job = Job.fetch(
                    delivery.queue_job_id,
                    connection=queue.connection,
                    serializer=JSONSerializer,
                )
                _confirm_unchanged(job, delivery, queue)
                if (
                    delivery.queue_status not in {"failed", "stopped"}
                    or (job.retries_left or 0) <= 0
                    or pipeline.zscore(registry.key, job.id) is None
                ):
                    raise queue_error("QUEUE_JOB_NOT_RETRYABLE", 409)
                # Manual retry is one use of the existing budget, never a reset.
                job.retries_left = (job.retries_left or 0) - 1
                job.started_at = None
                job.ended_at = None
                pipeline.multi()
                pipeline.zrem(registry.key, job.id)
                job.save(pipeline=pipeline)
                queue.enqueue_job(job, pipeline=pipeline)
                pipeline.execute()
        except (WatchError, NoSuchJobError) as exc:
            raise queue_error("QUEUE_JOB_CHANGED", 409) from exc
        return replace(
            delivery,
            queue_status="queued",
            state=None,
            retries_remaining=job.retries_left or 0,
            age_seconds=0,
        )

    async def terminate(
        self,
        delivery: QueueDelivery,
    ) -> Literal["terminated", "stop_requested"]:
        return await asyncio.to_thread(self._terminate, delivery)

    def _terminate(self, delivery: QueueDelivery) -> Literal["terminated", "stop_requested"]:
        queue = self._queue(delivery.job_kind)
        try:
            with queue.connection.pipeline() as pipeline:
                pipeline.watch(Job.key_for(delivery.queue_job_id))  # type: ignore[no-untyped-call]
                job = Job.fetch(
                    delivery.queue_job_id,
                    connection=queue.connection,
                    serializer=JSONSerializer,
                )
                _confirm_unchanged(job, delivery, queue)
                if delivery.queue_status == "started":
                    # Pubsub is a request, not proof of termination. Keep the DB
                    # attempt active until the worker reports stopped/failed.
                    send_stop_job_command(
                        queue.connection,
                        job.id,
                        serializer=JSONSerializer,
                    )
                    return "stop_requested"
                if delivery.queue_status == "canceled":
                    return "terminated"
                if delivery.queue_status not in {"queued", "scheduled", "failed", "stopped"}:
                    raise queue_error("QUEUE_JOB_NOT_TERMINABLE", 409)
                pipeline.multi()
                job.cancel(pipeline=pipeline)
                pipeline.execute()
        except (WatchError, NoSuchJobError) as exc:
            raise queue_error("QUEUE_JOB_CHANGED", 409) from exc
        return "terminated"

    def _queue(self, kind: JobKind) -> Queue:
        return self.ingestion if kind == "ingestion" else self.embedding


def _validated_payload(
    job: Job, kind: JobKind, queue_name: str, tenant_id: str
) -> QueuePayload | None:
    target = INGESTION_JOB_TARGET if kind == "ingestion" else EMBEDDING_JOB_TARGET
    job_type = INGESTION_JOB_TYPE if kind == "ingestion" else EMBEDDING_JOB_TYPE
    if job.origin != queue_name or job.func_name != target or job.kwargs or len(job.args) != 1:
        return None
    try:
        payload = QueuePayload.model_validate(job.args[0])
    except ValueError:
        return None
    if (
        payload.tenant_id != tenant_id
        or payload.job_type != job_type
        or set(payload.parameters) != {"document_id", "version_id"}
        or any(
            not isinstance(value, str) or not value.strip() for value in payload.parameters.values()
        )
        or str(job.id) != _idempotent_job_id(kind, payload.model_dump(mode="json"))
    ):
        return None
    return payload


def _confirm_unchanged(job: Job, delivery: QueueDelivery, queue: Queue) -> None:
    payload = _validated_payload(job, delivery.job_kind, queue.name, delivery.payload.tenant_id)
    status = job.get_status(refresh=True)
    if (
        payload != delivery.payload
        or getattr(status, "value", status) != delivery.queue_status
        or (job.retries_left or 0) != delivery.retries_remaining
    ):
        raise queue_error("QUEUE_JOB_CHANGED", 409)


def _delivery(
    job: Job,
    kind: JobKind,
    payload: QueuePayload,
    now: datetime,
    stale_after_seconds: int,
    queue: Queue,
) -> QueueDelivery:
    status = job.get_status(refresh=True)
    status_value = str(getattr(status, "value", status))
    heartbeat = _utc(job.last_heartbeat) if job.last_heartbeat else None
    timestamp = job.enqueued_at or job.created_at
    if status_value == "started":
        timestamp = job.last_heartbeat or job.started_at or timestamp
    elif status_value == "scheduled":
        score = queue.connection.zscore(_registry(queue, "scheduled").key, job.id)
        if score is not None:
            timestamp = datetime.fromtimestamp(cast(float, score), tz=UTC)
    age_seconds = max(0, int((now - _utc(timestamp)).total_seconds()))
    state: AttentionState | None = None
    if status_value in {"failed", "stopped"}:
        state = "failed"
    elif status_value == "canceled":
        state = "terminated"
    elif status_value == "started" and age_seconds >= stale_after_seconds:
        state = "stuck"
    elif status_value in {"queued", "scheduled"} and age_seconds >= stale_after_seconds:
        state = "stale"
    return QueueDelivery(
        job_kind=kind,
        queue_job_id=str(job.id),
        queue_name=queue.name,
        queue_status=status_value,
        payload=payload,
        state=state,
        retries_remaining=max(job.retries_left or 0, 0),
        age_seconds=age_seconds,
        last_heartbeat_at=heartbeat,
    )


def _registry(queue: Queue, name: str) -> FailedJobRegistry:
    registry_type = {
        "failed": FailedJobRegistry,
        "started": StartedJobRegistry,
        "scheduled": ScheduledJobRegistry,
        "canceled": CanceledJobRegistry,
    }[name]
    return cast(FailedJobRegistry, registry_type(queue=queue))


def _decode_cursor(cursor: str | None) -> tuple[int, int]:
    if cursor is None:
        return 0, 0
    try:
        source, offset = (int(value) for value in cursor.split(":"))
    except (ValueError, TypeError) as exc:
        raise queue_error("QUEUE_OPERATIONS_INVALID_REQUEST", 422) from exc
    if not 0 <= source < 2 * len(_REGISTRIES) or not 0 <= offset <= _MAX_CURSOR_OFFSET:
        raise queue_error("QUEUE_OPERATIONS_INVALID_REQUEST", 422)
    return source, offset


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
