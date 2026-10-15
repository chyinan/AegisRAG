"""Operator recovery uses persisted checkpoints; transport mocks are identified explicitly."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from rq.exceptions import NoSuchJobError
from rq.job import Job
from rq.serializers import JSONSerializer
from sqlalchemy import update

from packages.auth.context import AuthContext
from packages.common.audit import AuditStatus, InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError
from packages.data.queue.adapters import EMBEDDING_JOB_TARGET, RQEmbeddingJobQueue
from packages.data.storage.models import (
    DocumentModel,
    DocumentVersionModel,
    EmbeddingJobModel,
    IngestionJobModel,
)
from packages.data.storage.repositories import DocumentRepository
from packages.ingestion.recovery import HandoffRecoveryCommand, HandoffRecoveryService
from tests.integration.storage.test_embedding_handoff import (
    _checkpoint,
)
from tests.integration.storage.test_embedding_handoff import (
    committed_handoff as committed_handoff,
)
from tests.integration.storage.test_embedding_handoff import (
    database_url as database_url,
)
from tests.integration.storage.test_embedding_handoff import (
    factory as factory,
)


class InspectQueue:
    """In-memory RQ transport with status/membership and acknowledgement loss."""

    def __init__(self, *, fail=False):
        self.jobs = {}
        self.calls = 0
        self.fail = fail
        self.membership = True
        self.connection = self

    def fetch_job(self, job_id):
        job = self.jobs.get(job_id)
        # Match RQ's real queue-scoped lookup, which hides foreign origins.
        return job if job is not None and job.origin == "embedding" else None

    def set(self, *args, **kwargs):
        return True

    def eval(self, *args, **kwargs):
        return 1

    def enqueue(self, target, payload, *, job_id, **kwargs):
        self.calls += 1
        job = SimpleNamespace(
            id=job_id, origin="embedding", func_name=target,
            args=(deepcopy(payload),), kwargs={}, retries_left=kwargs["retry"].max,
            status="queued",
        )
        job.get_status = lambda refresh: job.status
        self.jobs[job_id] = job
        if self.fail:
            self.fail = False
            raise ConnectionError("secret://redis-password lost acknowledgement")
        return job

    def get_job_position(self, job_id):
        return 0 if self.membership and job_id in self.jobs else None


@pytest.fixture(autouse=True)
def global_rq_fetch(monkeypatch):
    """The transport mock preserves Job.fetch's global, cross-queue semantics."""
    original = Job.fetch

    def fetch(job_id, *, connection, serializer):
        if not isinstance(connection, InspectQueue):
            return original(job_id, connection=connection, serializer=serializer)
        assert serializer is JSONSerializer
        if job_id not in connection.jobs:
            raise NoSuchJobError(job_id)
        return connection.jobs[job_id]

    monkeypatch.setattr(Job, "fetch", fetch)


def operator(*, tenant="tenant-1", permissions=("document:manage",)):
    return AuthenticatedRequestContext(
        request_id="recover-request", trace_id="recover-trace",
        auth=AuthContext(user_id="operator-1", tenant_id=tenant, permissions=permissions),
    )


def recovery(repo, transport, audit):
    return HandoffRecoveryService(
        repository=repo,
        queue=RQEmbeddingJobQueue(queue=transport, queue_name="embedding"), audit=audit,
    )


async def recover(service, *, context=None, document="doc-1", version="ver-1", job="job-1"):
    return await service.recover(
        context or operator(), document_id=document, version_id=version,
        command=HandoffRecoveryCommand(ingestion_job_id=job),
    )


async def test_recover_after_ingestion_budget_exhaustion_is_idempotent_and_audited(
    factory, committed_handoff,
):
    transport = InspectQueue()
    audit = InMemoryAuditPort()
    async with factory() as session:
        # The original RQ ingestion job is no longer needed for an operator replay.
        await session.execute(update(IngestionJobModel).values(attempt_count=4))
        await session.commit()
        before = await _checkpoint(session)
        service = recovery(DocumentRepository(session), transport, audit)
        first = await recover(service)
        second = await recover(service)
        assert first == second
        assert first.status == "delivery_confirmed"
        assert first.embedding_job_id == "embed-job-1"
        assert first.ingestion_job_id == "job-1"
        assert first.queue_status == "queued"
        assert transport.calls == 1
        job = next(iter(transport.jobs.values()))
        assert job.func_name == EMBEDDING_JOB_TARGET
        assert job.args[0]["user_id"] == "user-1"  # Original task owner, not operator.
        assert job.retries_left == 3
        assert await _checkpoint(session) == before
    assert len(audit.events) == 2
    assert all(e.user_id == "operator-1" and e.status == AuditStatus.SUCCESS for e in audit.events)
    assert all(e.action == "document.embedding_handoff.recover" for e in audit.events)


async def test_lost_acknowledgement_is_safe_to_repeat(factory, committed_handoff):
    transport = InspectQueue(fail=True)
    audit = InMemoryAuditPort()
    async with factory() as session:
        service = recovery(DocumentRepository(session), transport, audit)
        before = await _checkpoint(session)
        with pytest.raises(DomainError) as error:
            await recover(service)
        assert error.value.code == "EMBEDDING_HANDOFF_RECOVERY_UNAVAILABLE"
        assert error.value.status_code == 503
        assert "secret" not in str(error.value)
        result = await recover(service)
        assert result.queue_status == "queued" and transport.calls == 1
        assert await _checkpoint(session) == before
    assert [e.status for e in audit.events] == [AuditStatus.FAILURE, AuditStatus.SUCCESS]


@pytest.mark.parametrize("context,document,version,job,code", [
    (operator(permissions=()), "doc-1", "ver-1", "job-1", "DOCUMENT_MANAGE_FORBIDDEN"),
    (operator(tenant="another-tenant"), "doc-1", "ver-1", "job-1", "DOCUMENT_VERSION_NOT_FOUND"),
    (operator(), "wrong-document", "ver-1", "job-1", "DOCUMENT_VERSION_NOT_FOUND"),
    (operator(), "doc-1", "wrong-version", "job-1", "DOCUMENT_VERSION_NOT_FOUND"),
    (operator(), "doc-1", "ver-1", "wrong-job", "DOCUMENT_VERSION_NOT_FOUND"),
])
async def test_recovery_denies_wrong_scope_and_permission(
    factory, committed_handoff, context, document, version, job, code,
):
    transport = InspectQueue()
    audit = InMemoryAuditPort()
    async with factory() as session:
        service = recovery(DocumentRepository(session), transport, audit)
        before = await _checkpoint(session)
        with pytest.raises(DomainError) as error:
            await recover(service, context=context, document=document, version=version, job=job)
        assert error.value.code == code and transport.calls == 0
        assert await _checkpoint(session) == before
    assert audit.events[-1].status == (
        AuditStatus.DENIED if code == "DOCUMENT_MANAGE_FORBIDDEN" else AuditStatus.FAILURE
    )


@pytest.mark.parametrize("model,changes,code", [
    (DocumentModel, {"status": "deleted"}, "DOCUMENT_VERSION_NOT_FOUND"),
    (DocumentVersionModel, {"status": "deleted"}, "DOCUMENT_VERSION_NOT_FOUND"),
    (DocumentVersionModel, {"status": "retrieval_ready"}, "EMBEDDING_HANDOFF_NOT_RECOVERABLE"),
    (IngestionJobModel, {"status": "failed_terminal"}, "EMBEDDING_HANDOFF_NOT_RECOVERABLE"),
    (EmbeddingJobModel, {"status": "embedding"}, "EMBEDDING_HANDOFF_NOT_RECOVERABLE"),
    (EmbeddingJobModel, {"status": "failed_retryable"}, "EMBEDDING_HANDOFF_NOT_RECOVERABLE"),
    (EmbeddingJobModel, {"status": "failed_terminal"}, "EMBEDDING_HANDOFF_NOT_RECOVERABLE"),
    (EmbeddingJobModel, {"attempt_count": 1}, "EMBEDDING_HANDOFF_NOT_RECOVERABLE"),
    (EmbeddingJobModel, {"metadata_": {}}, "EMBEDDING_HANDOFF_NOT_RECOVERABLE"),
])
async def test_recovery_never_resets_changed_or_inconsistent_checkpoints(
    factory, committed_handoff, model, changes, code,
):
    transport = InspectQueue()
    audit = InMemoryAuditPort()
    async with factory() as session:
        await session.execute(update(model).values(**changes))
        await session.commit()
        before = await _checkpoint(session)
        with pytest.raises(DomainError) as error:
            await recover(recovery(DocumentRepository(session), transport, audit))
        assert error.value.code == code and transport.calls == 0
        assert await _checkpoint(session) == before


@pytest.mark.parametrize("change", [
    {"status": "failed"}, {"status": "finished"}, {"status": "canceled"},
    {"status": "stopped"}, {"status": "scheduled"}, {"status": "deferred"},
    {"status": "started"}, {"origin": "obsolete-queue"}, {"func_name": "unexpected.target"},
    {"kwargs": {"override": True}}, {"args": ({"tenant_id": "wrong"},)},
])
async def test_existing_queue_conflicts_are_not_reported_as_recovered(
    factory, committed_handoff, change,
):
    transport = InspectQueue()
    audit = InMemoryAuditPort()
    async with factory() as session:
        service = recovery(DocumentRepository(session), transport, audit)
        await recover(service)
        job = next(iter(transport.jobs.values()))
        for key, value in change.items():
            setattr(job, key, value)
        before = await _checkpoint(session)
        with pytest.raises(DomainError) as error:
            await recover(service)
        assert error.value.code == "EMBEDDING_HANDOFF_QUEUE_CONFLICT"
        assert error.value.status_code == 409
        assert transport.calls == 1 and job.retries_left == 3
        assert await _checkpoint(session) == before


async def test_orphaned_queued_hash_is_a_conflict(factory, committed_handoff):
    transport = InspectQueue()
    transport.membership = False
    async with factory() as session:
        with pytest.raises(DomainError) as error:
            await recover(recovery(DocumentRepository(session), transport, InMemoryAuditPort()))
        assert error.value.code == "EMBEDDING_HANDOFF_QUEUE_CONFLICT"


async def test_audit_failure_is_not_reported_as_success(factory, committed_handoff):
    transport = InspectQueue()
    audit = AsyncMock()
    audit.record.side_effect = RuntimeError("secret://audit-password")
    async with factory() as session:
        with pytest.raises(DomainError) as error:
            await recover(recovery(DocumentRepository(session), transport, audit))
        assert error.value.code == "EMBEDDING_HANDOFF_RECOVERY_UNAVAILABLE"
        assert "secret" not in str(error.value)
        assert transport.calls == 1


@pytest.mark.parametrize("lease_acquired", [True, False])
async def test_cross_queue_entry_published_during_lease_is_never_overwritten(
    factory, committed_handoff, lease_acquired,
):
    transport = InspectQueue()
    audit = InMemoryAuditPort()
    async with factory() as session:
        service = recovery(DocumentRepository(session), transport, audit)
        result = await recover(service)
        old_job = transport.jobs.pop(result.queue_job_id)
        old_job.origin = "obsolete-queue"
        old_job.retries_left = 0

        def publish_during_lease(*args, **kwargs):
            transport.jobs[old_job.id] = old_job
            return lease_acquired

        transport.set = publish_during_lease
        with pytest.raises(DomainError) as error:
            await recover(service)
        assert error.value.code == "EMBEDDING_HANDOFF_QUEUE_CONFLICT"
        assert transport.calls == 1
        assert transport.fetch_job(old_job.id) is None  # Real RQ's queue-scoped semantics.
        assert transport.jobs[old_job.id] is old_job
        assert old_job.origin == "obsolete-queue" and old_job.retries_left == 0


async def test_missing_queue_connection_fails_closed_without_touching_checkpoint(
    factory, committed_handoff, monkeypatch,
):
    transport = InspectQueue()
    transport.connection = None
    audit = InMemoryAuditPort()
    monkeypatch.setattr(
        Job, "fetch", lambda *args, **kwargs: pytest.fail("must reject before reading Redis"),
    )
    async with factory() as session:
        before = await _checkpoint(session)
        with pytest.raises(DomainError) as error:
            await recover(recovery(DocumentRepository(session), transport, audit))
        assert error.value.code == "EMBEDDING_HANDOFF_RECOVERY_UNAVAILABLE"
        assert error.value.status_code == 503
        assert transport.calls == 0 and transport.jobs == {}
        assert await _checkpoint(session) == before
    assert len(audit.events) == 1
    assert audit.events[0].status == AuditStatus.FAILURE
    assert audit.events[0].error_code == error.value.code


@pytest.mark.parametrize("lease_acquired", [True, False])
async def test_matching_entry_published_during_lease_is_reused_without_budget_reset(
    factory, committed_handoff, lease_acquired,
):
    transport = InspectQueue()
    audit = InMemoryAuditPort()
    async with factory() as session:
        service = recovery(DocumentRepository(session), transport, audit)
        first = await recover(service)
        existing = transport.jobs.pop(first.queue_job_id)
        existing.retries_left = 0
        original_payload = deepcopy(existing.args)

        def publish_during_lease(*args, **kwargs):
            transport.jobs[existing.id] = existing
            return lease_acquired

        transport.set = publish_during_lease
        before = await _checkpoint(session)
        second = await recover(service)
        assert second == first
        assert transport.calls == 1
        assert transport.jobs == {existing.id: existing}
        assert existing.retries_left == 0 and existing.args == original_payload
        assert await _checkpoint(session) == before
    assert [event.status for event in audit.events] == [AuditStatus.SUCCESS, AuditStatus.SUCCESS]
