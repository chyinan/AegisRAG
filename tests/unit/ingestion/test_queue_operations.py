"""Positive operator workflows using a mock transport and persisted-style DTOs."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from packages.auth.context import AuthContext
from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.data.dto import (
    DocumentRecord,
    DocumentVersionRecord,
    EmbeddingJobRecord,
    IngestionJobRecord,
)
from packages.data.queue.contracts import QueuePayload
from packages.data.queue.embedding import EMBEDDING_JOB_TYPE
from packages.data.queue.ingestion import INGESTION_JOB_TYPE
from packages.ingestion.queue_operations import (
    QueueDelivery,
    QueueOperationCommand,
    QueueOperationsService,
    QueueScan,
)


def operator():
    return AuthenticatedRequestContext(
        request_id="request-1",
        trace_id="trace-1",
        auth=AuthContext(
            user_id="operator-1", tenant_id="tenant-1", permissions=("document:manage",)
        ),
    )


def workflow(kind="ingestion", *, queue_status="failed", domain_status="failed_retryable"):
    common = dict(
        id="job-1",
        tenant_id="tenant-1",
        created_by="user-1",
        document_id="doc-1",
        version_id="ver-1",
        status=domain_status,
        attempt_count=2,
        next_retry_at=datetime.now(UTC) - timedelta(seconds=30),
    )
    job = (
        IngestionJobRecord(**common, queue_name="ingestion")
        if kind == "ingestion"
        else EmbeddingJobRecord(**common, provider="fake", model="fake-model")
    )
    document = DocumentRecord(
        id="doc-1",
        tenant_id="tenant-1",
        created_by="user-1",
        status=domain_status,
        source_type="txt",
        source_uri=None,
        title="Policy",
        checksum="checksum",
    )
    version = DocumentVersionRecord(
        id="ver-1",
        document_id="doc-1",
        tenant_id="tenant-1",
        created_by="user-1",
        status="chunked" if kind == "embedding" else domain_status,
        source_type="txt",
        source_uri=None,
        object_key="raw/policy.txt",
        filename="policy.txt",
        content_type="text/plain",
        byte_size=100,
        checksum="checksum",
    )
    payload = QueuePayload(
        request_id="original-request",
        trace_id="original-trace",
        tenant_id="tenant-1",
        user_id="user-1",
        job_type=(INGESTION_JOB_TYPE if kind == "ingestion" else EMBEDDING_JOB_TYPE),
        resource_id=job.id,
        parameters={"document_id": "doc-1", "version_id": "ver-1"},
    )
    delivery = QueueDelivery(
        job_kind=kind,
        queue_job_id="delivery-1",
        queue_name=kind,
        queue_status=queue_status,
        payload=payload,
        state="failed",
        retries_remaining=2,
        age_seconds=1200,
    )
    repo = AsyncMock()
    repo.get_ingestion_job.return_value = job
    repo.get_embedding_job.return_value = job
    repo.get_document.return_value = document
    repo.get_version.return_value = version
    repo.mark_ingestion_job_failed.return_value = job.model_copy(
        update={"status": "failed_terminal"}
    )
    repo.mark_embedding_job_failed.return_value = job.model_copy(
        update={"status": "failed_terminal"}
    )
    queue = AsyncMock()
    queue.get.return_value = delivery
    queue.retry.return_value = replace(delivery, queue_status="queued", retries_remaining=1)
    queue.terminate.return_value = "terminated"
    queue.scan.return_value = QueueScan([delivery], "1:0", 1)
    audit = InMemoryAuditPort()
    service = QueueOperationsService(repository=repo, queue=queue, audit=audit)
    return service, repo, queue, audit, job, delivery


@pytest.mark.parametrize("kind", ["ingestion", "embedding"])
async def test_list_failed_delivery_includes_domain_attempts_and_controls(kind):
    service, repo, queue, audit, job, delivery = workflow(kind)
    result = await service.list_jobs(operator(), limit=10)
    assert result.items[0].job_kind == kind
    assert result.items[0].attempt_count == 2
    assert result.items[0].retry_allowed and result.items[0].terminate_allowed
    assert result.items[0].retries_remaining == 2
    assert result.next_cursor == "1:0"
    assert result.items[0].error_code is None
    queue.scan.assert_awaited_once_with(
        tenant_id="tenant-1",
        cursor=None,
        limit=10,
        stale_after_seconds=900,
    )
    assert audit.events[0].action == "queue.job.list"
    repo.commit.assert_awaited_once()


@pytest.mark.parametrize("kind", ["ingestion", "embedding"])
async def test_retry_consumes_existing_delivery_retry_and_preserves_domain_attempt(kind):
    service, repo, queue, audit, job, delivery = workflow(kind)
    result = await service.retry_job(
        operator(),
        job_kind=kind,
        queue_job_id="delivery-1",
        command=QueueOperationCommand(expected_attempt_count=2, expected_queue_status="failed"),
    )
    assert result.status == "queued" and result.retries_remaining == 1
    assert result.attempt_count == 2 and job.attempt_count == 2
    queue.retry.assert_awaited_once_with(delivery)
    repo.mark_ingestion_job_failed.assert_not_awaited()
    repo.mark_embedding_job_failed.assert_not_awaited()
    assert audit.events[0].user_id == "operator-1"
    assert delivery.payload.user_id == "user-1"


@pytest.mark.parametrize("kind", ["ingestion", "embedding"])
@pytest.mark.parametrize("status", ["queued", "scheduled", "stopped"])
async def test_terminate_confirmed_delivery_records_terminal_domain_state(kind, status):
    service, repo, queue, audit, job, delivery = workflow(kind, queue_status=status)
    result = await service.terminate_job(
        operator(),
        job_kind=kind,
        queue_job_id="delivery-1",
        command=QueueOperationCommand(expected_attempt_count=2, expected_queue_status=status),
    )
    assert result.status == "terminated" and result.attempt_count == 2
    marker = (
        repo.mark_ingestion_job_failed if kind == "ingestion" else repo.mark_embedding_job_failed
    )
    marker.assert_awaited_once_with(
        tenant_id="tenant-1",
        job_id="job-1",
        error_code="QUEUE_OPERATOR_TERMINATED",
        status="failed_terminal",
        expected_attempt_count=2,
    )
    assert audit.events[0].metadata["result"] == "terminated"


@pytest.mark.parametrize("kind", ["ingestion", "embedding"])
async def test_running_delivery_stop_is_reported_as_requested(kind):
    service, repo, queue, audit, job, delivery = workflow(
        kind,
        queue_status="started",
        domain_status="parsing" if kind == "ingestion" else "embedding",
    )
    queue.terminate.return_value = "stop_requested"
    result = await service.terminate_job(
        operator(),
        job_kind=kind,
        queue_job_id="delivery-1",
        command=QueueOperationCommand(expected_attempt_count=2, expected_queue_status="started"),
    )
    assert result.status == "stop_requested"
    repo.mark_ingestion_job_failed.assert_not_awaited()
    repo.mark_embedding_job_failed.assert_not_awaited()
    assert audit.events[0].metadata["result"] == "stop_requested"
