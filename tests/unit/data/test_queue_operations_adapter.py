"""Positive RQ workflows using the real Job/Queue classes and mocked Redis I/O."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from redis import Redis
from rq import Queue
from rq.job import Job
from rq.serializers import JSONSerializer

from packages.data.queue.adapters import (
    EMBEDDING_JOB_TARGET,
    INGESTION_JOB_TARGET,
    _idempotent_job_id,
)
from packages.data.queue.contracts import QueuePayload
from packages.data.queue.embedding import EMBEDDING_JOB_TYPE
from packages.data.queue.ingestion import INGESTION_JOB_TYPE
from packages.data.queue.operations import RQQueueOperations, _delivery


def transport(kind="ingestion", *, status="failed", age=1200):
    connection = MagicMock(spec=Redis)
    connection.hget.return_value = status.encode()
    pipeline = MagicMock()
    connection.pipeline.return_value.__enter__.return_value = pipeline
    pipeline.zscore.return_value = 1
    connection.zscore.return_value = (datetime.now(UTC) - timedelta(seconds=age)).timestamp()
    queue = Queue(kind, connection=connection, serializer=JSONSerializer)
    queue.redis_server_version = (7, 0, 0)
    payload = QueuePayload(
        request_id="request-1",
        trace_id="trace-1",
        tenant_id="tenant-1",
        user_id="user-1",
        job_type=INGESTION_JOB_TYPE if kind == "ingestion" else EMBEDDING_JOB_TYPE,
        resource_id="job-1",
        parameters={"document_id": "doc-1", "version_id": "ver-1"},
    )
    job = Job.create(
        INGESTION_JOB_TARGET if kind == "ingestion" else EMBEDDING_JOB_TARGET,
        args=(payload.model_dump(mode="json"),),
        connection=connection,
        serializer=JSONSerializer,
        origin=kind,
        id=_idempotent_job_id(kind, payload.model_dump(mode="json")),
    )
    job.retries_left = 2
    job.enqueued_at = datetime.now(UTC) - timedelta(seconds=age)
    job.started_at = job.enqueued_at
    job.last_heartbeat = job.enqueued_at
    return queue, job, payload, pipeline


@pytest.mark.parametrize("kind", ["ingestion", "embedding"])
@pytest.mark.parametrize(
    "status,state",
    [
        ("failed", "failed"),
        ("stopped", "failed"),
        ("queued", "stale"),
        ("scheduled", "stale"),
        ("started", "stuck"),
        ("canceled", "terminated"),
    ],
)
def test_attention_classification_uses_delivery_timestamps(kind, status, state):
    queue, job, payload, pipeline = transport(kind, status=status)
    snapshot = _delivery(job, kind, payload, datetime.now(UTC), 900, queue)
    assert snapshot.state == state
    assert snapshot.age_seconds >= 1200
    assert snapshot.last_heartbeat_at == job.last_heartbeat


@pytest.mark.parametrize("kind", ["ingestion", "embedding"])
def test_retry_reuses_real_rq_job_and_decrements_retry_budget(monkeypatch, kind):
    queue, job, payload, pipeline = transport(kind)
    monkeypatch.setattr(Job, "fetch", lambda *args, **kwargs: job)
    delivery = _delivery(job, kind, payload, datetime.now(UTC), 900, queue)
    result = RQQueueOperations(queue, queue)._retry(delivery)
    assert result.queue_job_id == job.id and result.retries_remaining == 1
    assert result.queue_status == "queued"
    assert job.args == (payload.model_dump(mode="json"),)
    assert job.retries_left == 1
    pipeline.watch.assert_called_once()
    pipeline.zrem.assert_called_once_with(f"rq:failed:{kind}", job.id)
    pipeline.rpush.assert_called_once_with(queue.key, job.id)
    pipeline.execute.assert_called_once()
    saved = [
        call.kwargs["mapping"] for call in pipeline.hset.call_args_list if "mapping" in call.kwargs
    ]
    assert any(values.get("retries_left") == 1 for values in saved)


@pytest.mark.parametrize("status", ["queued", "scheduled", "failed", "stopped"])
def test_termination_uses_real_rq_cancellation_without_deleting_job(monkeypatch, status):
    queue, job, payload, pipeline = transport(status=status)
    monkeypatch.setattr(Job, "fetch", lambda *args, **kwargs: job)
    delivery = _delivery(job, "ingestion", payload, datetime.now(UTC), 900, queue)
    result = RQQueueOperations(queue, queue)._terminate(delivery)
    assert result == "terminated"
    pipeline.lrem.assert_called_once_with(queue.key, 1, job.id)
    pipeline.delete.assert_not_called()
    pipeline.execute.assert_called_once()
    assert job.retries_left == 2


def test_running_stop_uses_rq_worker_command_and_waits_for_confirmation(monkeypatch):
    queue, job, payload, pipeline = transport(status="started")
    monkeypatch.setattr(Job, "fetch", lambda *args, **kwargs: job)
    stop = MagicMock()
    monkeypatch.setattr("packages.data.queue.operations.send_stop_job_command", stop)
    delivery = _delivery(job, "ingestion", payload, datetime.now(UTC), 900, queue)
    assert RQQueueOperations(queue, queue)._terminate(delivery) == "stop_requested"
    stop.assert_called_once_with(queue.connection, job.id, serializer=JSONSerializer)
    pipeline.execute.assert_not_called()


def test_registry_listing_is_bounded_and_does_not_run_cleanup(monkeypatch):
    queue, job, payload, pipeline = transport()
    monkeypatch.setattr(Job, "fetch", lambda *args, **kwargs: job)
    registry = MagicMock()
    registry.get_job_ids.return_value = [job.id, "next-delivery"]
    monkeypatch.setattr("packages.data.queue.operations._registry", lambda *args: registry)
    result = RQQueueOperations(queue, queue)._scan("tenant-1", None, 1, 900)
    assert len(result.deliveries) == 1 and result.next_cursor == "0:1"
    registry.get_job_ids.assert_called_once_with(start=0, end=1, cleanup=False)
    assert result.deliveries[0].queue_job_id == job.id
