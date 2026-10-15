"""Persisted retry lifecycle with SQLite and fake providers, not PG/Redis proof."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.common.audit import InMemoryAuditPort
from packages.common.retry import QueueRetryPolicy
from packages.data.storage.models import DocumentVersionModel, EmbeddingJobModel
from packages.data.storage.repositories import DocumentRepository
from packages.embeddings.adapters.fake import FakeEmbeddingProvider
from packages.embeddings.exceptions import EmbeddingJobError, EmbeddingProviderError
from packages.embeddings.service import EmbeddingJobService
from packages.vectorstores.adapters.fake import FakeVectorStore
from tests.integration.storage.test_document_repositories import (
    _chunk,
    _document,
    _embedding_job,
    _job,
    _run_migrations,
    _sqlite_async_url,
    _version,
)
from tests.unit.embeddings.test_embedding_service import _context


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = _sqlite_async_url(tmp_path / "retry-lifecycle.db")
    _run_migrations(url, monkeypatch)
    return url


@pytest.fixture
async def factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed(factory) -> None:
    async with factory() as session:
        repo = DocumentRepository(session)
        await repo.create_upload_records(
            document=_document(status="chunked"),
            version=_version(status="chunked"),
            job=_job(status="chunked"),
        )
        await repo.replace_chunks_for_version(
            tenant_id="tenant-1",
            document_id="doc-1",
            version_id="ver-1",
            chunks=[_chunk()],
        )
        await repo.create_embedding_job(job=_embedding_job())
        await repo.commit()


def _service(repo, remaining: int, *, failure: bool = True, delay: int = 5):
    return EmbeddingJobService(
        repository=repo,
        provider=FakeEmbeddingProvider(dim=4, failure_mode="timeout" if failure else None),
        audit=InMemoryAuditPort(),
        vector_store=FakeVectorStore(index_dim=4),
        queue_retry_policy=QueueRetryPolicy(remaining, delay),
    )


async def _embed(service):
    return await service.embed_job(
        _context(),
        job_id="embed-job-1",
        document_id="doc-1",
        version_id="ver-1",
    )


async def test_final_failure_updates_visible_status_and_preserves_original_error(factory) -> None:
    await _seed(factory)
    async with factory() as session:
        repo = DocumentRepository(session)
        with pytest.raises(EmbeddingProviderError):
            await _embed(_service(repo, 0))
        for _ in range(2):
            visible = await repo.get_document_version_status(
                tenant_id="tenant-1",
                document_id="doc-1",
                version_id="ver-1",
            )
            assert visible.status == "failed_terminal"
            assert visible.error_code == "EMBEDDING_PROVIDER_TIMEOUT"
            assert visible.next_retry_at is None
            assert (
                await repo.get_document(tenant_id="tenant-1", document_id="doc-1")
            ).status == "failed_terminal"
            with pytest.raises(EmbeddingJobError):
                await _embed(_service(repo, 2))


async def test_retryable_attempt_preserves_checkpoint_and_can_succeed_when_due(factory) -> None:
    await _seed(factory)
    async with factory() as session:
        repo = DocumentRepository(session)
        with pytest.raises(EmbeddingProviderError):
            await _embed(_service(repo, 3))
        visible = await repo.get_document_version_status(
            tenant_id="tenant-1",
            document_id="doc-1",
            version_id="ver-1",
        )
        assert visible.status == "failed_retryable"
        assert visible.next_retry_at is not None
        assert (
            await repo.get_version(tenant_id="tenant-1", version_id="ver-1")
        ).status == "chunked"
        assert (
            await repo.claim_embedding_job(
                tenant_id="tenant-1",
                job_id="embed-job-1",
                document_id="doc-1",
                version_id="ver-1",
                stale_before=None,
            )
            is None
        )
        await session.execute(
            update(EmbeddingJobModel).values(
                next_retry_at=datetime.now(tz=UTC) - timedelta(seconds=1),
            )
        )
        await session.commit()
        result = await _embed(_service(repo, 2, failure=False))
        assert result.status == "embedded"
        visible = await repo.get_document_version_status(
            tenant_id="tenant-1",
            document_id="doc-1",
            version_id="ver-1",
        )
        assert visible.status == "retrieval_ready"
        assert visible.error_code is None and visible.next_retry_at is None
        assert visible.attempt_count == 2


async def test_final_delivery_rejected_by_claim_does_not_terminalize_owned_attempt(factory) -> None:
    await _seed(factory)
    async with factory() as session:
        repo = DocumentRepository(session)
        await repo.claim_embedding_job(
            tenant_id="tenant-1",
            job_id="embed-job-1",
            document_id="doc-1",
            version_id="ver-1",
            stale_before=None,
        )
        await repo.commit()
        with pytest.raises(EmbeddingJobError):
            await _embed(_service(repo, 0))
        job = await repo.get_embedding_job(tenant_id="tenant-1", job_id="embed-job-1")
        assert job.status == "embedding" and job.error_code is None and job.attempt_count == 1


async def test_stale_identity_map_failure_cannot_overwrite_newer_claim(factory) -> None:
    await _seed(factory)
    async with factory() as stale:
        model = await stale.get(EmbeddingJobModel, "embed-job-1")
        assert model.attempt_count == 0
        await stale.commit()
        async with factory() as fresh:
            await fresh.execute(
                update(EmbeddingJobModel).values(status="embedding", attempt_count=2)
            )
            await fresh.commit()
        assert model.attempt_count == 0  # Deliberately stale ORM object.
        repo = DocumentRepository(stale)
        result = await repo.mark_embedding_job_failed(
            tenant_id="tenant-1",
            job_id="embed-job-1",
            status="failed_terminal",
            error_code="OLD_ATTEMPT",
            expected_attempt_count=0,
        )
        await repo.commit()
        assert (
            result.status == "embedding" and result.attempt_count == 2 and result.error_code is None
        )


@pytest.mark.parametrize("status", ["deleted", "embedded", "retrieval_ready"])
async def test_late_failure_does_not_downgrade_protected_version(factory, status) -> None:
    await _seed(factory)
    async with factory() as session:
        await session.execute(update(DocumentVersionModel).values(status=status))
        await session.commit()
        repo = DocumentRepository(session)
        await repo.mark_embedding_job_failed(
            tenant_id="tenant-1",
            job_id="embed-job-1",
            status="failed_terminal",
            error_code="LATE",
        )
        await repo.commit()
        assert (await repo.get_version(tenant_id="tenant-1", version_id="ver-1")).status == status
        assert (
            await repo.get_embedding_job(tenant_id="tenant-1", job_id="embed-job-1")
        ).error_code is None


async def test_old_version_failure_does_not_downgrade_latest_document(factory) -> None:
    await _seed(factory)
    async with factory() as session:
        repo = DocumentRepository(session)
        await repo.create_document_version_records(
            version=_version(version_id="ver-2", status="retrieval_ready"),
            job=_job(version_id="ver-2", job_id="job-2", status="chunked"),
        )
        await repo.commit()
        await repo.mark_embedding_job_failed(
            tenant_id="tenant-1",
            job_id="embed-job-1",
            status="failed_terminal",
            error_code="LATE",
        )
        await repo.commit()
        assert (
            await repo.get_version(tenant_id="tenant-1", version_id="ver-1")
        ).status == "failed_terminal"
        document = await repo.get_document(tenant_id="tenant-1", document_id="doc-1")
        assert (
            document.status == "retrieval_ready"
            and document.metadata["latest_version_id"] == "ver-2"
        )


async def _seed_ingestion(factory) -> None:
    async with factory() as session:
        repo = DocumentRepository(session)
        await repo.create_upload_records(
            document=_document(status="uploaded"),
            version=_version(status="uploaded"),
            job=_job(status="queued"),
        )
        await repo.commit()


def _parse_service(repo, remaining: int, *, failure: bool):
    from packages.ingestion.service import IngestionParseService
    from tests.unit.ingestion.test_parse_service import FakeStorage

    return IngestionParseService(
        repository=repo,
        object_storage=FakeStorage(
            content=b"Policy bytes",
            byte_size=12,
            checksum="checksum-ver-1",
            fail=failure,
        ),
        audit=InMemoryAuditPort(),
        queue_retry_policy=QueueRetryPolicy(remaining, 5),
    )


async def test_ingestion_retry_deadline_blocks_early_claim_then_due_retry_succeeds(factory) -> None:
    from packages.data.exceptions import DocumentStorageReadError
    from packages.data.storage.models import IngestionJobModel

    await _seed_ingestion(factory)
    async with factory() as session:
        repo = DocumentRepository(session)
        with pytest.raises(DocumentStorageReadError):
            await _parse_service(repo, 1, failure=True).parse_job(
                _context(),
                job_id="job-1",
                document_id="doc-1",
                version_id="ver-1",
            )
        failed = await repo.get_ingestion_job(tenant_id="tenant-1", job_id="job-1")
        assert failed.status == "failed_retryable" and failed.next_retry_at is not None
        assert (
            await repo.claim_ingestion_job_parsing(
                tenant_id="tenant-1",
                job_id="job-1",
                document_id="doc-1",
                version_id="ver-1",
                stale_before=None,
            )
            is None
        )
        await session.execute(
            update(IngestionJobModel).values(
                next_retry_at=datetime.now(tz=UTC) - timedelta(seconds=1),
            )
        )
        await session.commit()
        parsed = await _parse_service(repo, 0, failure=False).parse_job(
            _context(),
            job_id="job-1",
            document_id="doc-1",
            version_id="ver-1",
        )
        assert parsed.status == "parsed"
        job = await repo.get_ingestion_job(tenant_id="tenant-1", job_id="job-1")
        assert job.attempt_count == 2 and job.error_code is None and job.next_retry_at is None


async def test_ingestion_exhaustion_persists_terminal_original_error_on_redelivery(factory) -> None:
    from packages.common.errors import DomainError
    from packages.data.exceptions import DocumentStorageReadError

    await _seed_ingestion(factory)
    async with factory() as session:
        repo = DocumentRepository(session)
        with pytest.raises(DocumentStorageReadError):
            await _parse_service(repo, 0, failure=True).parse_job(
                _context(),
                job_id="job-1",
                document_id="doc-1",
                version_id="ver-1",
            )
        with pytest.raises(DomainError):
            await _parse_service(repo, 3, failure=True).parse_job(
                _context(),
                job_id="job-1",
                document_id="doc-1",
                version_id="ver-1",
            )
        job = await repo.get_ingestion_job(tenant_id="tenant-1", job_id="job-1")
        visible = await repo.get_document_version_status(
            tenant_id="tenant-1",
            document_id="doc-1",
            version_id="ver-1",
        )
        assert job.status == visible.status == "failed_terminal"
        assert job.error_code == visible.error_code == "DOCUMENT_STORAGE_READ_FAILED"
        assert job.next_retry_at is None and job.attempt_count == 1
