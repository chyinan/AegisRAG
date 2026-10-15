"""Committed handoff recovery with SQLite and the real queue adapter.

The RQ transport below is an in-memory fault injector, not a Redis integration.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.common.audit import InMemoryAuditPort
from packages.common.retry import QueueRetryPolicy
from packages.data.dto import EnqueuedJob
from packages.data.exceptions import EmbeddingJobEnqueueError
from packages.data.queue.adapters import RQEmbeddingJobQueue
from packages.data.queue.contracts import QueuePayload
from packages.data.storage.exceptions import StorageError
from packages.data.storage.models import DocumentVersionModel, EmbeddingJobModel
from packages.data.storage.repositories import DocumentRepository
from packages.embeddings.adapters.fake import FakeEmbeddingProvider
from packages.embeddings.exceptions import EmbeddingJobError
from packages.embeddings.service import EmbeddingJobService
from packages.ingestion.service import IngestionParseService
from packages.vectorstores.adapters.fake import FakeVectorStore
from tests.integration.storage.test_document_repositories import (
    _document,
    _job,
    _run_migrations,
    _sqlite_async_url,
    _version,
)
from tests.unit.ingestion.test_parse_service import FakeStorage, _context


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = _sqlite_async_url(tmp_path / "embedding-handoff.db")
    _run_migrations(url, monkeypatch)
    return url


@pytest.fixture
async def factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


class FaultQueue:
    """Accept before losing the reply, or fail before accepting anything."""

    def __init__(self, failure: str | None) -> None:
        self.failure = failure
        self.calls = 0
        self.jobs: dict[str, object] = {}
        self.payloads: list[dict[str, object]] = []

    def fetch_job(self, job_id: str) -> object | None:
        return self.jobs.get(job_id)

    def enqueue(self, target: str, payload: dict[str, object], *, job_id: str, **kwargs):
        self.calls += 1
        if self.calls == 1 and self.failure == "before_accept":
            raise ConnectionError("synthetic failure before acceptance")
        job = SimpleNamespace(id=job_id)
        self.jobs[job_id] = job
        self.payloads.append(payload)
        if self.calls == 1 and self.failure == "after_accept":
            raise ConnectionError("synthetic lost acknowledgement")
        return job


class MustNotParse:
    def get(self, source_type: str):
        return self

    async def parse(self, request):
        raise AssertionError("handoff replay must not parse the source")

    def split(self, parsed):
        raise AssertionError("handoff replay must not rechunk")

    async def get_document(self, **kwargs):
        raise AssertionError("handoff replay must not read the source")


def _unexpected_id() -> str:
    raise AssertionError("handoff replay must not create an embedding job")


async def _seed(factory) -> None:
    async with factory() as session:
        repo = DocumentRepository(session)
        await repo.create_upload_records(
            document=_document(status="uploaded"),
            version=_version(status="uploaded"),
            job=_job(status="queued"),
        )
        await repo.commit()


def _service(repo, queue, *, replay=False, remaining=3):
    never = MustNotParse()
    return IngestionParseService(
        repository=repo,
        object_storage=(
            never if replay else FakeStorage(
                content=b"Policy bytes", byte_size=12, checksum="checksum-ver-1",
            )
        ),
        audit=InMemoryAuditPort(),
        parser_registry=never if replay else None,
        chunker=never if replay else None,
        embedding_queue=queue,
        embedding_provider="fake",
        embedding_model="fake-embedding",
        embedding_dim=4,
        id_factory=_unexpected_id if replay else lambda: "embed-job-1",
        queue_retry_policy=QueueRetryPolicy(remaining_retries=remaining, delay_seconds=5),
    )


async def _parse(service):
    return await service.parse_job(
        _context(), job_id="job-1", document_id="doc-1", version_id="ver-1",
    )


def _queue(transport):
    return RQEmbeddingJobQueue(queue=transport, queue_name="embedding")


@pytest.mark.parametrize("failure", ["before_accept", "after_accept"])
async def test_committed_handoff_reentry_reuses_job_and_eventually_becomes_ready(
    factory, failure,
) -> None:
    await _seed(factory)
    transport = FaultQueue(failure)
    async with factory() as session:
        repo = DocumentRepository(session)
        with pytest.raises(EmbeddingJobEnqueueError):
            await _parse(_service(repo, _queue(transport)))

    # New session and service model a new worker delivery after the failure.
    async with factory() as session:
        repo = DocumentRepository(session)
        assert (await repo.get_ingestion_job(tenant_id="tenant-1", job_id="job-1")).status == (
            "chunked"
        )
        assert (await repo.get_version(tenant_id="tenant-1", version_id="ver-1")).status == (
            "chunked"
        )
        jobs = await repo.list_embedding_jobs(tenant_id="tenant-1", version_id="ver-1")
        assert len(jobs) == 1 and jobs[0].id == "embed-job-1" and jobs[0].status == "queued"
        chunks_before = await repo.list_chunks_for_version(
            tenant_id="tenant-1", document_id="doc-1", version_id="ver-1",
        )
        # A final allowed delivery still gets a chance to complete the handoff.
        for remaining in (1, 0):
            result = await _parse(
                _service(repo, _queue(transport), replay=True, remaining=remaining)
            )
            assert result.embedding_job_id == "embed-job-1"
            assert result.chunk_count == len(chunks_before)
        assert len(transport.jobs) == len(transport.payloads) == 1
        assert transport.calls == (2 if failure == "before_accept" else 1)
        assert transport.payloads[0]["resource_id"] == "embed-job-1"
        assert transport.payloads[0]["parameters"] == {
            "document_id": "doc-1", "version_id": "ver-1",
        }
        assert await repo.list_chunks_for_version(
            tenant_id="tenant-1", document_id="doc-1", version_id="ver-1",
        ) == chunks_before
        ingestion = await repo.get_ingestion_job(tenant_id="tenant-1", job_id="job-1")
        assert ingestion.attempt_count == 1 and ingestion.error_code is None
        embedding = await repo.get_embedding_job(tenant_id="tenant-1", job_id="embed-job-1")
        assert embedding.attempt_count == 0

        embedder = EmbeddingJobService(
            repository=repo,
            provider=FakeEmbeddingProvider(dim=4),
            audit=InMemoryAuditPort(),
            vector_store=FakeVectorStore(index_dim=4),
        )
        await embedder.embed_job(
            _context(), job_id="embed-job-1", document_id="doc-1", version_id="ver-1",
        )
        calls_before = transport.calls
        await _parse(_service(repo, _queue(transport), replay=True))
        assert transport.calls == calls_before
        status = await repo.get_document_version_status(
            tenant_id="tenant-1", document_id="doc-1", version_id="ver-1",
        )
        assert status.status == "retrieval_ready" and status.attempt_count == 1
        assert len(await repo.list_embedding_jobs(tenant_id="tenant-1", version_id="ver-1")) == 1


@pytest.mark.parametrize("status", ["embedding", "failed_retryable", "failed_terminal"])
async def test_reentry_never_resets_downstream_attempt_or_retry_budget(factory, status) -> None:
    await _seed(factory)
    transport = FaultQueue(None)
    async with factory() as session:
        repo = DocumentRepository(session)
        await _parse(_service(repo, _queue(transport)))
        deadline = datetime.now(tz=UTC) + timedelta(hours=1)
        await session.execute(
            update(EmbeddingJobModel).values(
                status=status, attempt_count=4, error_code="ORIGINAL_ERROR", next_retry_at=deadline,
            )
        )
        if status == "failed_terminal":
            await session.execute(update(DocumentVersionModel).values(status="failed_terminal"))
        await session.commit()
        before = await repo.get_embedding_job(tenant_id="tenant-1", job_id="embed-job-1")
        await _parse(_service(repo, _queue(transport), replay=True, remaining=0))
        after = await repo.get_embedding_job(tenant_id="tenant-1", job_id="embed-job-1")
        assert after == before
        assert transport.calls == 1


async def test_lost_ack_can_finish_embedding_before_ingestion_redelivery(factory) -> None:
    await _seed(factory)
    transport = FaultQueue("after_accept")
    async with factory() as session:
        repo = DocumentRepository(session)
        with pytest.raises(EmbeddingJobEnqueueError):
            await _parse(_service(repo, _queue(transport)))
    async with factory() as session:
        repo = DocumentRepository(session)
        embedder = EmbeddingJobService(
            repository=repo,
            provider=FakeEmbeddingProvider(dim=4),
            audit=InMemoryAuditPort(),
            vector_store=FakeVectorStore(index_dim=4),
        )
        await embedder.embed_job(
            _context(), job_id="embed-job-1", document_id="doc-1", version_id="ver-1",
        )
    async with factory() as session:
        repo = DocumentRepository(session)
        await _parse(_service(repo, _queue(transport), replay=True, remaining=0))
        assert transport.calls == len(transport.jobs) == 1
        status = await repo.get_document_version_status(
            tenant_id="tenant-1", document_id="doc-1", version_id="ver-1",
        )
        assert status.status == "retrieval_ready" and status.attempt_count == 1


@pytest.mark.parametrize("delete_version_only", [False, True])
async def test_deleted_checkpoint_is_not_reenqueued(factory, delete_version_only) -> None:
    await _seed(factory)
    transport = FaultQueue("before_accept")
    async with factory() as session:
        repo = DocumentRepository(session)
        with pytest.raises(EmbeddingJobEnqueueError):
            await _parse(_service(repo, _queue(transport)))
        if delete_version_only:
            await repo.soft_delete_document_version(
                tenant_id="tenant-1", document_id="doc-1", version_id="ver-1", deleted_by="user-1",
            )
        else:
            await repo.soft_delete_document(
                tenant_id="tenant-1", document_id="doc-1", deleted_by="user-1",
            )
        await repo.commit()
    async with factory() as session:
        repo = DocumentRepository(session)
        await _parse(_service(repo, _queue(transport), replay=True))
        assert transport.calls == 1 and transport.jobs == {}
        version = await repo.get_version(tenant_id="tenant-1", version_id="ver-1")
        assert version.status == "deleted"


async def test_delete_after_checkpoint_commit_prevents_initial_enqueue(factory) -> None:
    await _seed(factory)

    class DeleteAfterCheckpointRepository(DocumentRepository):
        async def commit(self) -> None:
            await super().commit()
            job = await self.get_ingestion_job(tenant_id="tenant-1", job_id="job-1")
            if job.status == "chunked":
                await self.soft_delete_document(
                    tenant_id="tenant-1", document_id="doc-1", deleted_by="user-1",
                )
                await super().commit()

    transport = FaultQueue(None)
    async with factory() as session:
        repo = DeleteAfterCheckpointRepository(session)
        await _parse(_service(repo, _queue(transport)))
        assert transport.calls == 0 and transport.jobs == {}
        version = await repo.get_version(tenant_id="tenant-1", version_id="ver-1")
        assert version.status == "deleted"


async def test_delete_between_lookup_and_enqueue_cannot_revive_document(factory) -> None:
    await _seed(factory)

    class DeleteAtEnqueue:
        async def enqueue_embedding_job(self, payload: QueuePayload) -> EnqueuedJob:
            async with factory() as deletion_session:
                deletion_repo = DocumentRepository(deletion_session)
                await deletion_repo.soft_delete_document(
                    tenant_id="tenant-1", document_id="doc-1", deleted_by="user-1",
                )
                await deletion_repo.commit()
            return EnqueuedJob(queue_job_id="accepted-after-delete", queue_name="embedding")

    async with factory() as session:
        await _parse(_service(DocumentRepository(session), DeleteAtEnqueue()))
    async with factory() as session:
        repo = DocumentRepository(session)
        embedder = EmbeddingJobService(
            repository=repo,
            provider=FakeEmbeddingProvider(dim=4),
            audit=InMemoryAuditPort(),
            vector_store=FakeVectorStore(index_dim=4),
        )
        with pytest.raises(EmbeddingJobError):
            await embedder.embed_job(
                _context(), job_id="embed-job-1", document_id="doc-1", version_id="ver-1",
            )
        version = await repo.get_version(tenant_id="tenant-1", version_id="ver-1")
        assert version.status == "deleted"
        job = await repo.get_embedding_job(tenant_id="tenant-1", job_id="embed-job-1")
        assert job.attempt_count == 0


@pytest.mark.parametrize("corruption", ["absent", "wrong_source"])
async def test_missing_or_unrelated_handoff_is_an_error_not_false_success(
    factory, corruption,
) -> None:
    await _seed(factory)
    transport = FaultQueue("before_accept")
    async with factory() as session:
        repo = DocumentRepository(session)
        with pytest.raises(EmbeddingJobEnqueueError):
            await _parse(_service(repo, _queue(transport)))
        if corruption == "absent":
            await session.execute(delete(EmbeddingJobModel))
        else:
            await session.execute(
                update(EmbeddingJobModel).values(metadata_={"source_ingestion_job_id": "other-job"})
            )
        await session.commit()
        with pytest.raises(StorageError) as exc:
            await _parse(_service(repo, _queue(transport), replay=True))
        assert exc.value.code == "EMBEDDING_HANDOFF_NOT_FOUND"
        assert transport.calls == 1


@pytest.mark.parametrize("field", ["tenant_id", "job_id", "document_id", "version_id"])
async def test_handoff_repository_requires_exact_scope(factory, field) -> None:
    await _seed(factory)
    async with factory() as session:
        repo = DocumentRepository(session)
        await _parse(_service(repo, _queue(FaultQueue(None))))
        scope = {
            "tenant_id": "tenant-1", "job_id": "job-1",
            "document_id": "doc-1", "version_id": "ver-1",
        }
        scope[field] = "other-scope"
        with pytest.raises(StorageError) as exc:
            await repo.get_ingestion_embedding_handoff(**scope)
        assert exc.value.code == "EMBEDDING_HANDOFF_INVALID_STATE"


async def test_stale_identity_map_does_not_replay_a_started_embedding(factory) -> None:
    await _seed(factory)
    transport = FaultQueue(None)
    async with factory() as session:
        repo = DocumentRepository(session)
        await _parse(_service(repo, _queue(transport)))
    async with factory() as stale:
        model = await stale.scalar(select(EmbeddingJobModel))
        assert model.status == "queued"
        await stale.commit()
        async with factory() as fresh:
            await fresh.execute(
                update(EmbeddingJobModel).values(status="embedding", attempt_count=1)
            )
            await fresh.commit()
        assert model.status == "queued"
        await _parse(_service(DocumentRepository(stale), _queue(transport), replay=True))
        assert transport.calls == 1
        assert model.status == "embedding" and model.attempt_count == 1
