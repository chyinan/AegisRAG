"""Queue adapter idempotency tests."""

import pytest

from packages.data.queue.adapters import (
    RQEmbeddingJobQueue,
    RQIngestionJobQueue,
    _enqueue_idempotently,
    _idempotent_job_id,
)
from packages.data.queue.contracts import QueuePayload


class _Job:
    id = "rq-id"


class _Queue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def enqueue(
        self,
        target: str,
        *args: object,
        job_id: str,
        job_timeout: int,
        retry: object | None = None,
        result_ttl: int = 500,
        failure_ttl: int = 500,
    ) -> _Job:
        self.calls.append(
            (
                target,
                args,
                {
                    "job_id": job_id,
                    "job_timeout": job_timeout,
                    "retry": retry,
                    "result_ttl": result_ttl,
                    "failure_ttl": failure_ttl,
                },
            )
        )
        return _Job()


@pytest.mark.asyncio
async def test_queue_adapters_use_deterministic_resource_job_id() -> None:
    payload = QueuePayload(
        request_id="req-a",
        trace_id="trace-a",
        tenant_id="tenant-a",
        user_id="user-a",
        job_type="embedding",
        resource_id="job-a",
        parameters={"document_id": "doc-a", "version_id": "ver-a"},
    )
    queue = _Queue()

    result = await RQEmbeddingJobQueue(queue=queue, queue_name="embedding").enqueue_embedding_job(
        payload
    )

    assert result.queue_job_id == "rq-id"
    assert queue.calls[0][2]["job_id"] == (
        "aegisrag-v1-4627cf699b5542aaf2cac56bce11ea3efcf8c08c5dfbe31f921bc61d9a29b47f"
    )
    assert queue.calls[0][2]["job_id"] == _idempotent_job_id(
        "embedding", payload.model_dump(mode="json")
    )
    assert queue.calls[0][2]["job_timeout"] == 900


@pytest.mark.asyncio
async def test_ingestion_queue_uses_its_own_idempotency_namespace() -> None:
    payload = QueuePayload(
        request_id="req-a",
        trace_id="trace-a",
        tenant_id="tenant-a",
        user_id="user-a",
        job_type="ingestion",
        resource_id="job-a",
        parameters={"document_id": "doc-a", "version_id": "ver-a"},
    )
    queue = _Queue()

    await RQIngestionJobQueue(queue=queue, queue_name="ingestion").enqueue_ingestion_job(payload)

    assert queue.calls[0][2]["job_id"] == _idempotent_job_id(
        "ingestion", payload.model_dump(mode="json")
    )


@pytest.mark.asyncio
async def test_queue_retry_zero_omits_invalid_rq_retry_object() -> None:
    payload = QueuePayload(
        request_id="req-a",
        trace_id="trace-a",
        tenant_id="tenant-a",
        user_id="user-a",
        job_type="embedding",
        resource_id="job-a",
        parameters={"document_id": "doc-a", "version_id": "ver-a"},
    )
    queue = _Queue()

    await RQEmbeddingJobQueue(
        queue=queue,
        queue_name="embedding",
        retry_max=0,
    ).enqueue_embedding_job(payload)

    assert queue.calls[0][2]["retry"] is None


def test_enqueue_idempotency_lock_does_not_replace_another_owner() -> None:
    class Connection:
        def __init__(self) -> None:
            self.lock: str | None = None

        def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
            del key, ex
            if nx and self.lock is not None:
                return False
            self.lock = value
            return True

        def eval(self, script: str, numkeys: int, key: str, token: str) -> int:
            del script, numkeys, key
            if self.lock == token:
                self.lock = None
                return 1
            return 0

    class QueueWithConnection(_Queue):
        def __init__(self) -> None:
            super().__init__()
            self.connection = Connection()
            self.existing: _Job | None = None

        def fetch_job(self, job_id: str) -> _Job | None:
            del job_id
            return self.existing

    queue = QueueWithConnection()
    payload = {
        "tenant_id": "tenant-a",
        "resource_id": "job-a",
    }
    first = _enqueue_idempotently(
        queue=queue,
        target="target",
        payload=payload,
        job_id="aegisrag:embedding:tenant-a:job-a",
        job_timeout=10,
        retry_max=0,
        idempotency_ttl_seconds=60,
    )
    queue.existing = first
    second = _enqueue_idempotently(
        queue=queue,
        target="target",
        payload=payload,
        job_id="aegisrag:embedding:tenant-a:job-a",
        job_timeout=10,
        retry_max=0,
        idempotency_ttl_seconds=60,
    )

    assert second is first
    assert len(queue.calls) == 1


def test_enqueue_checks_existing_job_before_acquiring_short_lease() -> None:
    class Connection:
        def __init__(self) -> None:
            self.expires: int | None = None

        def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
            del key, value, nx
            self.expires = ex
            return True

    class QueueWithExisting(_Queue):
        def __init__(self) -> None:
            super().__init__()
            self.connection = Connection()
            self.existing = _Job()

        def fetch_job(self, job_id: str) -> _Job | None:
            del job_id
            return self.existing

    queue = QueueWithExisting()
    result = _enqueue_idempotently(
        queue=queue,
        target="target",
        payload={"tenant_id": "tenant-a", "resource_id": "job-a"},
        job_id="aegisrag:embedding:tenant-a:job-a",
        job_timeout=10,
        retry_max=0,
        idempotency_ttl_seconds=86_400,
        lock_ttl_seconds=300,
    )

    assert result is queue.existing
    assert queue.calls == []
    assert queue.connection.expires is None

    queue.existing = None
    _enqueue_idempotently(
        queue=queue,
        target="target",
        payload={"tenant_id": "tenant-a", "resource_id": "job-b"},
        job_id="aegisrag:embedding:tenant-a:job-b",
        job_timeout=10,
        retry_max=0,
        idempotency_ttl_seconds=86_400,
        lock_ttl_seconds=300,
    )
    assert queue.connection.expires == 300


def test_deterministic_ids_pass_real_rq_validation_without_a_server() -> None:
    from redis import Redis
    from rq.job import Job, validate_job_id
    from rq.serializers import JSONSerializer

    connection = Redis(host="127.0.0.1", port=1)
    for kind in ("ingestion", "embedding"):
        job_id = _idempotent_job_id(kind, {"tenant_id": "tenant:a", "resource_id": "文档:b"})
        validate_job_id(job_id)
        job = Job.create(
            "builtins.len",
            args=([1, 2],),
            id=job_id,
            connection=connection,
            serializer=JSONSerializer,
        )
        assert job.id == job_id
        assert len(job_id) == 76
        assert ":" not in job_id


def test_idempotent_ids_isolate_type_tenant_and_ambiguous_boundaries() -> None:
    base = {"tenant_id": "a", "resource_id": "b-c"}
    job_id = _idempotent_job_id("ingestion", base)
    assert job_id == _idempotent_job_id("ingestion", {**base, "request_id": "new-request"})
    assert job_id == _idempotent_job_id("ingestion", {"tenant_id": " a ", "resource_id": " b-c "})
    assert job_id != _idempotent_job_id("embedding", base)
    assert job_id != _idempotent_job_id("ingestion", {"tenant_id": "other", "resource_id": "b-c"})
    assert job_id != _idempotent_job_id("ingestion", {"tenant_id": "a-b", "resource_id": "c"})
    assert job_id != _idempotent_job_id("ingestion", {"tenant_id": "a", "resource_id": "b:c"})
