from __future__ import annotations

# pattern: Imperative Shell
import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import uuid4

from rq import Retry
from rq.job import Job

from packages.common.config import AppSettings
from packages.data.dto import EnqueuedJob
from packages.data.exceptions import EmbeddingJobEnqueueError, IngestionJobEnqueueError
from packages.data.queue.contracts import QueuePayload
from packages.data.queue.rq_worker import WorkerSettings, create_queue

INGESTION_JOB_TARGET = "apps.worker.jobs.ingestion_jobs.process_document_ingestion"
EMBEDDING_JOB_TARGET = "apps.worker.jobs.embedding_jobs.process_document_embedding"


class _RQQueue(Protocol):
    def enqueue(
        self,
        f: str,
        *args: object,
        job_id: str,
        job_timeout: int,
        retry: Retry | None = None,
        result_ttl: int = 500,
        failure_ttl: int = 500,
    ) -> Job: ...

    def fetch_job(self, job_id: str) -> Job | None: ...


@dataclass(frozen=True)
class RQIngestionJobQueue:
    queue: object
    queue_name: str
    job_timeout_seconds: int = 900
    retry_max: int = 3
    idempotency_ttl_seconds: int = 86_400
    idempotency_lock_ttl_seconds: int = 300

    @classmethod
    def from_settings(cls, settings: AppSettings) -> RQIngestionJobQueue:
        if settings.redis_url is None:
            raise IngestionJobEnqueueError(details={"missing": ["REDIS_URL"]})
        queue_name = settings.ingestion_queue_name.strip()
        worker_settings = WorkerSettings(
            redis_url=settings.redis_url,
            queue_name=queue_name,
            redis_timeout_seconds=settings.readiness_timeout_seconds,
        )
        return cls(
            queue=create_queue(worker_settings),
            queue_name=queue_name,
            job_timeout_seconds=settings.queue_job_timeout_seconds,
            retry_max=settings.queue_retry_max,
            idempotency_ttl_seconds=settings.queue_idempotency_ttl_seconds,
            idempotency_lock_ttl_seconds=settings.queue_idempotency_lock_ttl_seconds,
        )

    async def enqueue_ingestion_job(self, payload: QueuePayload) -> EnqueuedJob:
        try:
            job = await asyncio.to_thread(
                self._enqueue,
                payload.model_dump(mode="json"),
            )
        except IngestionJobEnqueueError:
            raise
        except IdempotencyLeaseHeldError as exc:
            raise IngestionJobEnqueueError(
                details={
                    "queue_name": self.queue_name,
                    "retryable": True,
                    "retry_after_seconds": exc.retry_after_seconds,
                }
            ) from exc
        except Exception as exc:
            raise IngestionJobEnqueueError(details={"queue_name": self.queue_name}) from exc

        return EnqueuedJob(
            queue_job_id=str(job.id) if getattr(job, "id", None) is not None else None,
            queue_name=self.queue_name,
        )

    def _enqueue(self, payload: dict[str, object]) -> Job:
        queue = cast(_RQQueue, self.queue)
        return _enqueue_idempotently(
            queue=queue,
            target=INGESTION_JOB_TARGET,
            payload=payload,
            job_id=_idempotent_job_id("ingestion", payload),
            job_timeout=self.job_timeout_seconds,
            retry_max=self.retry_max,
            idempotency_ttl_seconds=self.idempotency_ttl_seconds,
            lock_ttl_seconds=self.idempotency_lock_ttl_seconds,
        )


@dataclass(frozen=True)
class RQEmbeddingJobQueue:
    queue: object
    queue_name: str
    job_timeout_seconds: int = 900
    retry_max: int = 3
    idempotency_ttl_seconds: int = 86_400
    idempotency_lock_ttl_seconds: int = 300

    @classmethod
    def from_settings(cls, settings: AppSettings) -> RQEmbeddingJobQueue:
        if settings.redis_url is None:
            raise EmbeddingJobEnqueueError(details={"missing": ["REDIS_URL"]})
        queue_name = settings.embedding_queue_name.strip()
        worker_settings = WorkerSettings(
            redis_url=settings.redis_url,
            queue_name=queue_name,
            redis_timeout_seconds=settings.readiness_timeout_seconds,
        )
        return cls(
            queue=create_queue(worker_settings),
            queue_name=queue_name,
            job_timeout_seconds=settings.queue_job_timeout_seconds,
            retry_max=settings.queue_retry_max,
            idempotency_ttl_seconds=settings.queue_idempotency_ttl_seconds,
            idempotency_lock_ttl_seconds=settings.queue_idempotency_lock_ttl_seconds,
        )

    async def enqueue_embedding_job(self, payload: QueuePayload) -> EnqueuedJob:
        try:
            job = await asyncio.to_thread(
                self._enqueue,
                payload.model_dump(mode="json"),
            )
        except EmbeddingJobEnqueueError:
            raise
        except IdempotencyLeaseHeldError as exc:
            raise EmbeddingJobEnqueueError(
                details={
                    "queue_name": self.queue_name,
                    "retryable": True,
                    "retry_after_seconds": exc.retry_after_seconds,
                }
            ) from exc
        except Exception as exc:
            raise EmbeddingJobEnqueueError(details={"queue_name": self.queue_name}) from exc

        return EnqueuedJob(
            queue_job_id=str(job.id) if getattr(job, "id", None) is not None else None,
            queue_name=self.queue_name,
        )

    def _enqueue(self, payload: dict[str, object]) -> Job:
        queue = cast(_RQQueue, self.queue)
        return _enqueue_idempotently(
            queue=queue,
            target=EMBEDDING_JOB_TARGET,
            payload=payload,
            job_id=_idempotent_job_id("embedding", payload),
            job_timeout=self.job_timeout_seconds,
            retry_max=self.retry_max,
            idempotency_ttl_seconds=self.idempotency_ttl_seconds,
            lock_ttl_seconds=self.idempotency_lock_ttl_seconds,
        )


def _idempotent_job_id(job_type: str, payload: dict[str, object]) -> str:
    resource_id = payload.get("resource_id")
    if not isinstance(resource_id, str) or not resource_id.strip():
        raise ValueError("queue payload resource_id is required")
    tenant_id = payload.get("tenant_id")
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise ValueError("queue payload tenant_id is required")
    # RQ >=2.9 only accepts letters, digits, underscores and dashes in IDs.
    # Hash an unambiguous tuple, rather than replacing separators (which can
    # collide between tenant/resource boundaries). Keep tenant/type isolation
    # and resource-level idempotence without leaking identifiers into key names.
    identity = json.dumps(
        [job_type, tenant_id.strip(), resource_id.strip()],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"aegisrag-v1-{hashlib.sha256(identity).hexdigest()}"


class IdempotencyLeaseHeldError(RuntimeError):
    def __init__(self, *, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("queue idempotency lease is held without a visible job")


def _enqueue_idempotently(
    *,
    queue: _RQQueue,
    target: str,
    payload: dict[str, object],
    job_id: str,
    job_timeout: int,
    retry_max: int,
    idempotency_ttl_seconds: int,
    lock_ttl_seconds: int = 300,
) -> Job:
    connection = getattr(queue, "connection", None)
    lock_key = f"{job_id}:lock"
    lock_acquired = True
    lock_token = uuid4().hex
    fetch_job = getattr(queue, "fetch_job", None)
    if callable(fetch_job):
        existing = cast(Job | None, fetch_job(job_id))
        if existing is not None:
            return existing
    if connection is not None and hasattr(connection, "set"):
        lock_acquired = bool(
            connection.set(
                lock_key,
                lock_token,
                nx=True,
                ex=min(max(lock_ttl_seconds, 1), idempotency_ttl_seconds),
            )
        )
        if not lock_acquired:
            existing = cast(Job | None, fetch_job(job_id)) if callable(fetch_job) else None
            if existing is not None:
                return existing
            raise IdempotencyLeaseHeldError(
                retry_after_seconds=min(max(lock_ttl_seconds, 1), idempotency_ttl_seconds)
            )
    try:
        kwargs: dict[str, object] = {
            "job_id": job_id,
            "job_timeout": job_timeout,
            "result_ttl": idempotency_ttl_seconds,
            "failure_ttl": idempotency_ttl_seconds,
        }
        if retry_max > 0:
            kwargs["retry"] = Retry(max=retry_max, interval=[5, 30, 120])
        return queue.enqueue(target, payload, **kwargs)  # type: ignore[arg-type]
    except Exception:
        if lock_acquired and connection is not None:
            _release_idempotency_lock(connection, lock_key, lock_token)
        raise


def _release_idempotency_lock(connection: object, lock_key: str, lock_token: str) -> None:
    eval_script = (
        "if redis.call('GET', KEYS[1]) == ARGV[1] then "
        "return redis.call('DEL', KEYS[1]) else return 0 end"
    )
    if hasattr(connection, "eval"):
        connection.eval(eval_script, 1, lock_key, lock_token)
