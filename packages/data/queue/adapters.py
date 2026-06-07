from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol, cast

from rq.job import Job

from packages.common.config import AppSettings
from packages.data.dto import EnqueuedJob
from packages.data.exceptions import EmbeddingJobEnqueueError, IngestionJobEnqueueError
from packages.data.queue.contracts import QueuePayload
from packages.data.queue.rq_worker import WorkerSettings, create_queue

INGESTION_JOB_TARGET = "apps.worker.jobs.ingestion_jobs.process_document_ingestion"
EMBEDDING_JOB_TARGET = "apps.worker.jobs.embedding_jobs.process_document_embedding"


class _RQQueue(Protocol):
    def enqueue(self, f: str, *args: object) -> Job: ...


@dataclass(frozen=True)
class RQIngestionJobQueue:
    queue: object
    queue_name: str

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
        return cls(queue=create_queue(worker_settings), queue_name=queue_name)

    async def enqueue_ingestion_job(self, payload: QueuePayload) -> EnqueuedJob:
        try:
            job = await asyncio.to_thread(
                self._enqueue,
                payload.model_dump(mode="json"),
            )
        except IngestionJobEnqueueError:
            raise
        except Exception as exc:
            raise IngestionJobEnqueueError(details={"queue_name": self.queue_name}) from exc

        return EnqueuedJob(
            queue_job_id=str(job.id) if getattr(job, "id", None) is not None else None,
            queue_name=self.queue_name,
        )

    def _enqueue(self, payload: dict[str, object]) -> Job:
        queue = cast(_RQQueue, self.queue)
        return queue.enqueue(INGESTION_JOB_TARGET, payload)


@dataclass(frozen=True)
class RQEmbeddingJobQueue:
    queue: object
    queue_name: str

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
        return cls(queue=create_queue(worker_settings), queue_name=queue_name)

    async def enqueue_embedding_job(self, payload: QueuePayload) -> EnqueuedJob:
        try:
            job = await asyncio.to_thread(
                self._enqueue,
                payload.model_dump(mode="json"),
            )
        except EmbeddingJobEnqueueError:
            raise
        except Exception as exc:
            raise EmbeddingJobEnqueueError(details={"queue_name": self.queue_name}) from exc

        return EnqueuedJob(
            queue_job_id=str(job.id) if getattr(job, "id", None) is not None else None,
            queue_name=self.queue_name,
        )

    def _enqueue(self, payload: dict[str, object]) -> Job:
        queue = cast(_RQQueue, self.queue)
        return queue.enqueue(EMBEDDING_JOB_TARGET, payload)
