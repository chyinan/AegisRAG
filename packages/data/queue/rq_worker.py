from __future__ import annotations

import os
from dataclasses import dataclass

from redis import Redis
from rq import Queue, Worker
from rq.serializers import JSONSerializer

from packages.common.config import load_settings


@dataclass(frozen=True)
class WorkerSettings:
    redis_url: str
    queue_name: str
    redis_timeout_seconds: float = 5.0
    burst: bool = False

    @classmethod
    def from_environment(cls) -> WorkerSettings:
        settings = load_settings()
        if not settings.redis_url:
            raise ValueError("REDIS_URL must be configured before starting a worker.")

        queue_name = settings.worker_queue_name.strip()
        if not queue_name:
            raise ValueError("WORKER_QUEUE_NAME must not be empty.")

        return cls(
            redis_url=settings.redis_url,
            queue_name=queue_name,
            redis_timeout_seconds=settings.readiness_timeout_seconds,
            burst=_env_flag("WORKER_BURST"),
        )


def create_queue(settings: WorkerSettings) -> Queue:
    connection: Redis = _redis_connection(settings)
    return Queue(
        settings.queue_name,
        connection=connection,
        serializer=JSONSerializer,
    )


def create_worker(settings: WorkerSettings) -> Worker:
    connection: Redis = _redis_connection(settings)
    queue = Queue(
        settings.queue_name,
        connection=connection,
        serializer=JSONSerializer,
    )
    return Worker(
        [queue],
        connection=connection,
        serializer=JSONSerializer,
    )


def run_worker(settings: WorkerSettings | None = None) -> bool:
    resolved_settings = settings or WorkerSettings.from_environment()
    worker = create_worker(resolved_settings)
    return bool(worker.work(burst=resolved_settings.burst))


def _redis_connection(settings: WorkerSettings) -> Redis:
    return Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.redis_timeout_seconds,
        socket_timeout=settings.redis_timeout_seconds,
    )


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}
