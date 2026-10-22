"""Semantic failure lifecycle in disposable SQLite, with no live provider or queue."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.common.retry import QueueRetryPolicy
from packages.data.storage.audit_models import AuditLogModel
from packages.data.storage.audit_repositories import SqlAlchemyAuditPort
from packages.data.storage.models import ChunkModel, EmbeddingJobModel, IngestionJobModel
from packages.data.storage.repositories import DocumentRepository
from packages.embeddings.dto import EmbeddingVector
from packages.embeddings.exceptions import (
    EMBEDDING_BATCH_SIZE_MISMATCH,
    EMBEDDING_PROVIDER_FAILED,
    EMBEDDING_PROVIDER_TIMEOUT,
    EMBEDDING_VECTOR_DIMENSION_MISMATCH,
    EMBEDDING_VECTOR_EMPTY,
    EmbeddingProviderError,
)
from packages.ingestion.chunkers.semantic import SemanticChunker
from packages.ingestion.exceptions import GenericDocumentParseError
from packages.ingestion.service import IngestionParseService
from tests.integration.storage.test_document_repositories import (
    _document,
    _job,
    _run_migrations,
    _sqlite_async_url,
    _version,
)
from tests.unit.ingestion.test_parse_service import FakeEmbeddingQueue, FakeStorage
from tests.unit.ingestion.test_semantic_ingestion_failures import (
    _parse,
    _provider,
    _response,
)


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = _sqlite_async_url(tmp_path / "semantic-ingestion.db")
    _run_migrations(url, monkeypatch)
    return url


@pytest.fixture
async def factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        repository = DocumentRepository(session)
        await repository.create_upload_records(
            document=_document(status="uploaded"),
            version=_version(status="uploaded"),
            job=_job(status="queued"),
        )
        await repository.commit()


def _service(
    session: AsyncSession,
    provider: Mock,
    queue: FakeEmbeddingQueue,
    policy: QueueRetryPolicy,
) -> IngestionParseService:
    return IngestionParseService(
        repository=DocumentRepository(session),
        object_storage=FakeStorage(
            content=b"Policy bytes", byte_size=12, checksum="checksum-ver-1",
        ),
        audit=SqlAlchemyAuditPort(session),
        chunker=SemanticChunker(embedding_provider=provider),
        embedding_queue=queue,
        embedding_provider="fake",
        embedding_model="fake-embedding",
        embedding_dim=2,
        id_factory=lambda: "semantic-embedding-job",
        queue_retry_policy=policy,
    )


@pytest.mark.parametrize(
    "retryable,remaining,malformed",
    [
        (True, 1, None), (True, 0, None), (False, 1, None), (False, 0, None),
        (False, 1, "missing"), (False, 1, "empty"), (False, 1, "dimension"),
        (False, 1, "nonfinite"),
    ],
)
async def test_semantic_failure_commits_visible_business_state_and_safe_audit(
    factory: async_sessionmaker[AsyncSession], retryable: bool, remaining: int,
    malformed: str | None,
) -> None:
    await _seed(factory)
    provider = _provider()
    queue = FakeEmbeddingQueue()
    code = {
        "missing": EMBEDDING_BATCH_SIZE_MISMATCH,
        "empty": EMBEDDING_VECTOR_EMPTY,
        "dimension": EMBEDDING_VECTOR_DIMENSION_MISMATCH,
        "nonfinite": EMBEDDING_PROVIDER_FAILED,
    }.get(malformed, EMBEDDING_PROVIDER_TIMEOUT)
    if malformed:
        provider.embed_texts.side_effect = None
        values = {"empty": [], "dimension": [1.0], "nonfinite": [float("nan"), 0.0]}
        provider.embed_texts.return_value = (
            _response([]) if malformed == "missing" else _response([0]).model_copy(update={
                "vectors": [EmbeddingVector(index=0, vector=values[malformed])],
            })
        )
    else:
        provider.embed_texts.side_effect = EmbeddingProviderError(code=code, retryable=retryable)
    before = datetime.now(tz=UTC)
    async with factory() as session:
        with pytest.raises(EmbeddingProviderError) as caught:
            await _parse(_service(session, provider, queue, QueueRetryPolicy(remaining, 5)))
        assert caught.value.code == code

    # Reopen the session: the result must be committed, not just ORM-local state.
    async with factory() as session:
        repository = DocumentRepository(session)
        expected = "failed_retryable" if retryable and remaining else "failed_terminal"
        job = await repository.get_ingestion_job(tenant_id="tenant-1", job_id="job-1")
        version = await repository.get_version(tenant_id="tenant-1", version_id="ver-1")
        document = await repository.get_document(tenant_id="tenant-1", document_id="doc-1")
        visible = await repository.get_document_version_status(
            tenant_id="tenant-1", document_id="doc-1", version_id="ver-1",
        )
        assert job is not None and version is not None and document is not None
        assert visible is not None
        assert job.status == version.status == document.status == visible.status == expected
        assert job.error_code == visible.error_code == code
        assert job.attempt_count == visible.attempt_count == 1
        if expected == "failed_retryable":
            assert visible.next_retry_at is not None
            deadline = visible.next_retry_at.replace(tzinfo=UTC)
            assert (before + timedelta(seconds=5)).replace(microsecond=0) <= deadline
            assert deadline <= datetime.now(tz=UTC) + timedelta(seconds=5)
        else:
            assert job.next_retry_at is visible.next_retry_at is None
        assert list(await session.scalars(select(ChunkModel))) == []
        assert list(await session.scalars(select(EmbeddingJobModel))) == []
        audits = list(await session.scalars(select(AuditLogModel).where(
            AuditLogModel.status == "failure",
        )))
        assert len(audits) == 1
        assert audits[0].action == "document.parse"
        assert audits[0].error_code == code
        assert audits[0].metadata_["status"] == expected
        assert "Policy bytes" not in repr(audits[0].metadata_)
    provider.embed_texts.assert_awaited_once()
    assert queue.payloads == []


async def test_semantic_retry_waits_until_due_then_claims_and_succeeds_once(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(factory)
    failed_provider = _provider()
    failed_provider.embed_texts.side_effect = EmbeddingProviderError(
        code=EMBEDDING_PROVIDER_TIMEOUT, retryable=True,
    )
    successful_provider = _provider()
    queue = FakeEmbeddingQueue()
    async with factory() as session:
        with pytest.raises(EmbeddingProviderError):
            await _parse(_service(session, failed_provider, queue, QueueRetryPolicy(1, 120)))

    async with factory() as session:
        with pytest.raises(GenericDocumentParseError) as caught:
            await _parse(_service(session, successful_provider, queue, QueueRetryPolicy(0, 0)))
        assert caught.value.details["reason"] == "claim_not_acquired"
        repository = DocumentRepository(session)
        job = await repository.get_ingestion_job(tenant_id="tenant-1", job_id="job-1")
        assert job is not None
        assert job.status == "failed_retryable" and job.attempt_count == 1
        assert job.error_code == EMBEDDING_PROVIDER_TIMEOUT
        assert job.next_retry_at is not None
        successful_provider.embed_texts.assert_not_awaited()
        assert list(await session.scalars(select(ChunkModel))) == []
        assert queue.payloads == []
        await session.execute(update(IngestionJobModel).where(
            IngestionJobModel.id == "job-1",
        ).values(next_retry_at=datetime.now(tz=UTC) - timedelta(seconds=1)))
        await session.commit()

    async with factory() as session:
        result = await _parse(_service(session, successful_provider, queue, QueueRetryPolicy(0, 0)))
        assert result.status == "chunked"
        assert result.embedding_job_id == "semantic-embedding-job"
    async with factory() as session:
        repository = DocumentRepository(session)
        job = await repository.get_ingestion_job(tenant_id="tenant-1", job_id="job-1")
        version = await repository.get_version(tenant_id="tenant-1", version_id="ver-1")
        document = await repository.get_document(tenant_id="tenant-1", document_id="doc-1")
        assert job is not None and version is not None and document is not None
        assert job.status == version.status == document.status == "chunked"
        assert job.attempt_count == 2
        assert job.error_code is job.next_retry_at is None
        assert len(list(await session.scalars(select(ChunkModel)))) == 1
        assert len(list(await session.scalars(select(EmbeddingJobModel)))) == 1
    failed_provider.embed_texts.assert_awaited_once()
    successful_provider.embed_texts.assert_awaited_once()
    assert len(queue.payloads) == 1
    assert queue.payloads[0].resource_id == "semantic-embedding-job"


async def test_semantic_failed_retry_exhaustion_commits_second_claim_as_terminal(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(factory)
    provider = _provider()
    provider.embed_texts.side_effect = EmbeddingProviderError(
        code=EMBEDDING_PROVIDER_TIMEOUT, retryable=True,
    )
    queue = FakeEmbeddingQueue()
    for remaining in (1, 0):
        async with factory() as session:
            with pytest.raises(EmbeddingProviderError):
                await _parse(_service(session, provider, queue, QueueRetryPolicy(remaining, 0)))
    async with factory() as session:
        repository = DocumentRepository(session)
        job = await repository.get_ingestion_job(tenant_id="tenant-1", job_id="job-1")
        visible = await repository.get_document_version_status(
            tenant_id="tenant-1", document_id="doc-1", version_id="ver-1",
        )
        assert job is not None and visible is not None
        assert job.status == visible.status == "failed_terminal"
        assert job.attempt_count == visible.attempt_count == 2
        assert job.error_code == visible.error_code == EMBEDDING_PROVIDER_TIMEOUT
        assert job.next_retry_at is visible.next_retry_at is None
        assert list(await session.scalars(select(ChunkModel))) == []
        assert list(await session.scalars(select(EmbeddingJobModel))) == []
        failures = list(await session.scalars(select(AuditLogModel).where(
            AuditLogModel.status == "failure",
        )))
        assert len(failures) == 2
    assert provider.embed_texts.await_count == 2
    assert queue.payloads == []
