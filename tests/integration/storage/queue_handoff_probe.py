"""Live PostgreSQL + Redis/RQ handoff probe; source storage and model are synthetic."""

import asyncio
import hashlib
from uuid import uuid4

import pytest
from redis import Redis
from rq import Queue, SimpleWorker
from rq.job import JobStatus
from rq.serializers import JSONSerializer
from sqlalchemy import delete, func, select

from packages.auth.context import AuthContext
from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError
from packages.common.retry import QueueRetryPolicy
from packages.common.tenant_context import tenant_scope
from packages.data.exceptions import EmbeddingJobEnqueueError
from packages.data.queue.adapters import RQEmbeddingJobQueue
from packages.data.storage.models import DocumentModel, VectorRecordModel
from packages.data.storage.repositories import DocumentRepository
from packages.data.storage.session import create_async_db_engine, create_session_factory
from packages.ingestion.recovery import HandoffRecoveryCommand, HandoffRecoveryService
from packages.ingestion.service import IngestionParseService
from tests.integration.storage.test_document_repositories import _document, _job, _version
from tests.integration.storage.test_embedding_handoff import MustNotParse, _unexpected_id
from tests.unit.ingestion.test_parse_service import FakeStorage


class _FaultingRedisQueue:
    """Inject at the real enqueue boundary, retaining the server's accepted job."""

    def __init__(self, queue: Queue, failure: str) -> None:
        self.queue = queue
        self.connection = queue.connection
        self.failure = failure
        self.calls = 0

    def fetch_job(self, job_id: str):
        return self.queue.fetch_job(job_id)

    def get_job_position(self, job_id: str):
        return self.queue.get_job_position(job_id)

    def enqueue(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1 and self.failure == "before_accept":
            raise ConnectionError("synthetic transport failure before Redis accepts the job")
        job = self.queue.enqueue(*args, **kwargs)
        if self.calls == 1 and self.failure == "after_accept":
            raise ConnectionError("synthetic lost acknowledgement after Redis accepted the job")
        return job


def exercise_real_queue_handoff(
    database_url: str,
    redis_url: str,
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    operator_recovery: bool = False,
) -> None:
    """A fresh service/session replays one committed job, then the real worker runs it."""
    probe_id = uuid4().hex
    tenant_id, document_id = str(uuid4()), str(uuid4())
    version_id, ingestion_id, embedding_id = str(uuid4()), str(uuid4()), str(uuid4())
    content = b"Synthetic handoff policy."
    checksum = hashlib.sha256(content).hexdigest()
    context = AuthenticatedRequestContext(
        request_id=f"handoff-request-{probe_id}", trace_id=f"handoff-trace-{probe_id}",
        auth=AuthContext(tenant_id=tenant_id, user_id="synthetic-user"),
    )
    connection = Redis.from_url(redis_url)
    assert connection.ping()
    queue = Queue(f"aegis-handoff-{probe_id}", connection=connection, serializer=JSONSerializer)
    transport = _FaultingRedisQueue(queue, failure)
    adapter = RQEmbeddingJobQueue(queue=transport, queue_name=queue.name)
    queue_job_id: str | None = None
    seeded = False

    async def recover(repo):
        service = HandoffRecoveryService(
            repository=repo, queue=adapter, audit=InMemoryAuditPort(),
        )
        return await service.recover(
            context.model_copy(update={"auth": AuthContext(
                user_id="synthetic-operator", tenant_id=tenant_id,
                permissions=("document:manage",),
            )}),
            document_id=document_id, version_id=version_id,
            command=HandoffRecoveryCommand(ingestion_job_id=ingestion_id),
        )

    async def parse(repo, *, replay=False, remaining=3):
        never = MustNotParse()
        service = IngestionParseService(
            repository=repo,
            object_storage=never if replay else FakeStorage(
                content=content, byte_size=len(content), checksum=checksum,
            ),
            parser_registry=never if replay else None,
            chunker=never if replay else None,
            audit=InMemoryAuditPort(), embedding_queue=adapter,
            embedding_provider="fake", embedding_model="fake-embedding", embedding_dim=4,
            id_factory=_unexpected_id if replay else lambda: embedding_id,
            queue_retry_policy=QueueRetryPolicy(remaining, 5),
        )
        return await service.parse_job(
            context, job_id=ingestion_id, document_id=document_id, version_id=version_id,
        )

    async def seed(factory) -> None:
        async with factory() as session:
            repo = DocumentRepository(session)
            await repo.create_upload_records(
                document=_document(
                    tenant_id=tenant_id, document_id=document_id, status="uploaded",
                ),
                version=_version(
                    tenant_id=tenant_id, document_id=document_id,
                    version_id=version_id, status="uploaded",
                ).model_copy(update={"checksum": checksum, "byte_size": len(content)}),
                job=_job(
                    tenant_id=tenant_id, document_id=document_id, version_id=version_id,
                    job_id=ingestion_id, status="queued",
                ),
            )
            await repo.commit()

    async def prepare() -> str:
        nonlocal seeded
        engine = create_async_db_engine(database_url)
        factory = create_session_factory(engine)
        try:
            with tenant_scope(tenant_id):
                async with factory() as session:
                    # Never overwrite any pre-existing row, even in the disposable gate.
                    assert await DocumentRepository(session).get_document(
                        tenant_id=tenant_id, document_id=document_id,
                    ) is None
                await seed(factory)
                seeded = True
                async with factory() as session:
                    with pytest.raises(EmbeddingJobEnqueueError):
                        await parse(
                            DocumentRepository(session), remaining=0 if operator_recovery else 3,
                        )
                assert queue.count == (1 if failure == "after_accept" else 0)

                # Both calls are fresh worker deliveries, including the last retry budget.
                for remaining in (1, 0):
                    async with factory() as session:
                        repo = DocumentRepository(session)
                        result = (
                            await recover(repo) if operator_recovery
                            else await parse(repo, replay=True, remaining=remaining)
                        )
                        assert result.embedding_job_id == embedding_id
                        if not operator_recovery:
                            assert result.chunk_count == 1
                        jobs = await repo.list_embedding_jobs(
                            tenant_id=tenant_id, version_id=version_id,
                        )
                        assert len(jobs) == 1 and jobs[0].attempt_count == 0
                        assert jobs[0].status == "queued"
                        ingestion = await repo.get_ingestion_job(
                            tenant_id=tenant_id, job_id=ingestion_id,
                        )
                        assert ingestion.status == "chunked" and ingestion.attempt_count == 1
                assert queue.count == 1
                assert transport.calls == (2 if failure == "before_accept" else 1)
                job_id = queue.job_ids[0]
                job = queue.fetch_job(job_id)
                assert job is not None
                assert job.func_name == "apps.worker.jobs.embedding_jobs.process_document_embedding"
                assert job.args[0]["resource_id"] == embedding_id
                return job_id
        finally:
            await engine.dispose()

    async def verify_ready() -> None:
        engine = create_async_db_engine(database_url)
        try:
            with tenant_scope(tenant_id):
                async with create_session_factory(engine)() as session:
                    repo = DocumentRepository(session)
                    status = await repo.get_document_version_status(
                        tenant_id=tenant_id, document_id=document_id, version_id=version_id,
                    )
                    assert status.status == "retrieval_ready" and status.attempt_count == 1
                    jobs = await repo.list_embedding_jobs(
                        tenant_id=tenant_id, version_id=version_id,
                    )
                    assert len(jobs) == 1 and jobs[0].id == embedding_id
                    assert jobs[0].status == "embedded" and jobs[0].attempt_count == 1
                    assert await session.scalar(select(func.count()).select_from(
                        VectorRecordModel,
                    ).where(
                        VectorRecordModel.tenant_id == tenant_id,
                        VectorRecordModel.document_id == document_id,
                    )) == 1
                    calls_before = transport.calls
                    if operator_recovery:
                        with pytest.raises(DomainError) as completed_error:
                            await recover(repo)
                        assert completed_error.value.code == "EMBEDDING_HANDOFF_NOT_RECOVERABLE"
                    else:
                        await parse(repo, replay=True)
                    assert transport.calls == calls_before and queue.count == 0
        finally:
            await engine.dispose()

    async def cleanup_rows() -> None:
        engine = create_async_db_engine(database_url)
        try:
            with tenant_scope(tenant_id):
                async with create_session_factory(engine)() as session:
                    await session.execute(delete(DocumentModel).where(
                        DocumentModel.tenant_id == tenant_id, DocumentModel.id == document_id,
                    ))
                    await session.commit()
        finally:
            await engine.dispose()

    try:
        queue_job_id = asyncio.run(prepare())
        with monkeypatch.context() as environment:
            environment.setenv("DATABASE_URL", database_url)
            environment.setenv("APP_ENV", "test")
            environment.setenv("EMBEDDING_PROVIDER", "fake")
            environment.setenv("EMBEDDING_ADAPTER", "native")
            environment.setenv("EMBEDDING_MODEL", "fake-embedding")
            environment.setenv("EMBEDDING_DIM", "4")
            environment.setenv("VECTOR_STORE_TYPE", "pgvector")
            environment.setenv("VECTOR_INDEX_DIM", "4")
            # A genuine RQ worker imports the production job target and creates
            # its own PostgreSQL session, provider, audit port and vector store.
            worker = SimpleWorker([queue], connection=connection, serializer=JSONSerializer)
            assert worker.work(burst=True, with_scheduler=False)
        completed = queue.fetch_job(queue_job_id)
        assert completed is not None and completed.get_status(refresh=True) == JobStatus.FINISHED
        assert completed.return_value()["status"] == "embedded"
        asyncio.run(verify_ready())
    finally:
        if seeded:
            asyncio.run(cleanup_rows())
        # Only this synthetic queue/job is touched; never FLUSHDB shared Redis.
        if queue_job_id is not None:
            job = queue.fetch_job(queue_job_id)
            if job is not None:
                job.delete()
            connection.delete(f"{queue_job_id}:lock")
        queue.delete(delete_jobs=True)
        connection.close()
