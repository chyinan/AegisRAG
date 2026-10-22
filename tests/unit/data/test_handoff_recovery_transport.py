"""Use real RQ Job/Queue serialization and lookup, without opening a Redis socket."""
from unittest.mock import MagicMock

import pytest
from redis import Redis
from rq import Queue
from rq.job import Job, JobStatus
from rq.serializers import JSONSerializer

from packages.common.errors import DomainError
from packages.data.queue.adapters import (
    EMBEDDING_JOB_TARGET,
    RQEmbeddingJobQueue,
    _idempotent_job_id,
)
from packages.data.queue.contracts import QueuePayload
from packages.data.queue.embedding import EMBEDDING_JOB_TYPE


@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.FAILED, JobStatus.STARTED])
async def test_real_rq_foreign_origin_is_visible_to_recovery_without_enqueue(status):
    payload = QueuePayload(
        request_id="request", trace_id="trace", tenant_id="tenant", user_id="owner",
        job_type=EMBEDDING_JOB_TYPE, resource_id="original-embedding-job",
        parameters={"document_id": "document", "version_id": "version"},
    )
    body = payload.model_dump(mode="json")
    connection = MagicMock(spec=Redis)
    original = Job.create(
        EMBEDDING_JOB_TARGET, args=(body,), connection=connection, serializer=JSONSerializer,
        id=_idempotent_job_id("embedding", body), origin="old-embedding", status=status,
    )
    original.retries_left = 0
    stored = {
        key.encode(): (value if isinstance(value, bytes) else
                       value.encode() if isinstance(value, str) else str(value).encode())
        for key, value in original.to_dict().items()
    }
    connection.hgetall.return_value = stored
    connection.hget.return_value = status.value.encode()
    queue = Queue("new-embedding", connection=connection, serializer=JSONSerializer)
    # Actual RQ 2.9 hides this same-ID hash from a differently named queue.
    assert queue.fetch_job(original.id) is None
    globally_visible = Job.fetch(original.id, connection=connection, serializer=JSONSerializer)
    assert globally_visible.origin == "old-embedding" and globally_visible.retries_left == 0
    queue.enqueue = MagicMock(side_effect=AssertionError("must never replace old queue work"))
    adapter = RQEmbeddingJobQueue(queue=queue, queue_name=queue.name)
    with pytest.raises(DomainError) as error:
        await adapter.recover_embedding_handoff(payload)
    assert error.value.code == "EMBEDDING_HANDOFF_QUEUE_CONFLICT"
    queue.enqueue.assert_not_called()
    connection.set.assert_not_called()
    connection.hset.assert_not_called()
    assert connection.hgetall.return_value == stored


@pytest.mark.parametrize("status,expected", [
    (JobStatus.QUEUED, "queued"), (JobStatus.FAILED, None), (JobStatus.STARTED, None),
])
async def test_real_rq_matching_origin_preserves_existing_payload_and_budget(status, expected):
    payload = QueuePayload(
        request_id="request", trace_id="trace", tenant_id="tenant", user_id="owner",
        job_type=EMBEDDING_JOB_TYPE, resource_id="original-embedding-job",
        parameters={"document_id": "document", "version_id": "version"},
    )
    body = payload.model_dump(mode="json")
    connection = MagicMock(spec=Redis)
    original = Job.create(
        EMBEDDING_JOB_TARGET, args=(body,), connection=connection, serializer=JSONSerializer,
        id=_idempotent_job_id("embedding", body), origin="embedding", status=status,
    )
    original.retries_left = 0
    stored = {
        key.encode(): (value if isinstance(value, bytes) else
                       value.encode() if isinstance(value, str) else str(value).encode())
        for key, value in original.to_dict().items()
    }
    connection.hgetall.return_value = stored
    connection.hget.return_value = status.value.encode()
    queue = Queue("embedding", connection=connection, serializer=JSONSerializer)
    queue.get_job_position = MagicMock(return_value=0)
    queue.enqueue = MagicMock(side_effect=AssertionError("must preserve existing job"))
    adapter = RQEmbeddingJobQueue(queue=queue, queue_name=queue.name)
    if expected is not None:
        result = await adapter.recover_embedding_handoff(payload)
        assert result.queue_status == expected and result.queue_job_id == original.id
    else:
        with pytest.raises(DomainError) as error:
            await adapter.recover_embedding_handoff(payload)
        assert error.value.code == "EMBEDDING_HANDOFF_QUEUE_CONFLICT"
    queue.enqueue.assert_not_called()
    connection.set.assert_not_called()
    connection.hset.assert_not_called()
