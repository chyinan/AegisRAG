"""Independent semantic-ingestion review, using fakes and disposable SQLite only."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.common.audit import AuditEvent, AuditStatus, InMemoryAuditPort
from packages.common.retry import QueueRetryPolicy
from packages.data.storage.audit_models import AuditLogModel
from packages.data.storage.audit_repositories import SqlAlchemyAuditPort
from packages.data.storage.exceptions import StorageError
from packages.data.storage.models import (
    ChunkModel,
    DocumentModel,
    DocumentVersionModel,
    EmbeddingJobModel,
    IngestionJobModel,
)
from packages.data.storage.repositories import DocumentRepository
from packages.embeddings.dto import EmbeddingRequest, EmbeddingResponse, EmbeddingVector
from packages.embeddings.exceptions import (
    EMBEDDING_BATCH_SIZE_MISMATCH,
    EMBEDDING_PROVIDER_FAILED,
    EMBEDDING_PROVIDER_TIMEOUT,
    EMBEDDING_VECTOR_DIMENSION_MISMATCH,
    EMBEDDING_VECTOR_EMPTY,
    EmbeddingProviderError,
)
from packages.embeddings.ports import EmbeddingProvider
from packages.ingestion.chunkers.semantic import SemanticChunker, SemanticChunkerConfig
from packages.ingestion.exceptions import GenericDocumentChunkError, GenericDocumentParseError
from packages.ingestion.service import IngestionParseService, ParseJobResult
from tests.integration.storage.test_document_repositories import (
    _document,
    _job,
    _run_migrations,
    _sqlite_async_url,
    _version,
)
from tests.unit.ingestion.test_parse_service import (
    FakeEmbeddingQueue,
    FakeRepository,
    FakeStorage,
    _context,
)
from tests.unit.ingestion.test_semantic_chunker import (
    _document as _parsed_document,
)
from tests.unit.ingestion.test_semantic_chunker import _section

CONTENT = b"First sentence. Second sentence."


def _response(
    vectors: list[list[float]], *, indices: list[int] | None = None, dim: int = 2,
) -> EmbeddingResponse:
    indices = list(range(len(vectors))) if indices is None else indices
    return EmbeddingResponse(
        vectors=[
            EmbeddingVector(index=index, vector=vector)
            for index, vector in zip(indices, vectors, strict=True)
        ],
        provider="fake", model="review-semantic", dim=dim, latency_ms=0,
    )


def _success(request: EmbeddingRequest) -> EmbeddingResponse:
    return _response([[1.0, 0.0] for _ in request.texts])


def _provider() -> Mock:
    provider = Mock(spec=EmbeddingProvider)
    provider.embed_texts = AsyncMock(side_effect=_success)
    return provider


def _unit_service(
    provider: Mock, *, policy: QueueRetryPolicy | None = None, attempt: int = 0,
) -> tuple[IngestionParseService, FakeRepository, FakeEmbeddingQueue, InMemoryAuditPort]:
    repository = FakeRepository(source_type="txt", byte_size=len(CONTENT))
    repository.job = repository.job.model_copy(update={"attempt_count": attempt})
    queue = FakeEmbeddingQueue()
    audit = InMemoryAuditPort()
    service = IngestionParseService(
        repository=repository, object_storage=FakeStorage(content=CONTENT), audit=audit,
        chunker=SemanticChunker(embedding_provider=provider),
        embedding_queue=queue, embedding_provider="fake", embedding_model="review-semantic",
        embedding_dim=2, queue_retry_policy=policy,
    )
    return service, repository, queue, audit


async def _parse(service: IngestionParseService) -> ParseJobResult:
    return await service.parse_job(
        _context(), job_id="job-1", document_id="doc-1", version_id="ver-1",
    )


@pytest.mark.parametrize(
    "code,retryable,remaining,status",
    [
        (EMBEDDING_PROVIDER_TIMEOUT, False, 3, "failed_terminal"),
        ("DOCUMENT_CHUNK_EMPTY_CONTENT", True, 3, "failed_retryable"),
        (EMBEDDING_PROVIDER_TIMEOUT, True, 0, "failed_terminal"),
        (EMBEDDING_PROVIDER_TIMEOUT, True, None, "failed_retryable"),
    ],
)
async def test_provider_retryability_overrides_code_and_uses_actual_claim(
    code: str, retryable: bool, remaining: int | None, status: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider()
    policy = None if remaining is None else QueueRetryPolicy(remaining, 17)
    service, repository, queue, audit = _unit_service(provider, policy=policy, attempt=7)
    error = EmbeddingProviderError(code=code, retryable=retryable)
    provider.embed_texts.side_effect = error
    mark = AsyncMock(wraps=repository.mark_ingestion_job_failed)
    monkeypatch.setattr(repository, "mark_ingestion_job_failed", mark)
    before = datetime.now(tz=UTC)

    with pytest.raises(EmbeddingProviderError) as caught:
        await _parse(service)

    after = datetime.now(tz=UTC)
    assert caught.value is error
    mark.assert_awaited_once()
    assert mark.await_args.kwargs["expected_attempt_count"] == 8
    assert repository.job.attempt_count == 8
    assert repository.job.status == status
    assert repository.job.error_code == code
    if status == "failed_retryable" and policy is not None:
        assert policy.next_retry_at(before) <= repository.job.next_retry_at <= policy.next_retry_at(
            after,
        )
    else:
        assert repository.job.next_retry_at is None
    assert repository.chunks == repository.embedding_jobs == queue.payloads == []
    assert repository.commits == 3
    assert audit.events[-1].status == AuditStatus.FAILURE
    provider.embed_texts.assert_awaited_once()


@pytest.mark.parametrize("indices", [[], [1], [0, 0], [1, 2], [0, 1, 1], [0, 1, 2]])
async def test_complete_sentence_index_contract_is_checked_before_chunk_persistence(
    indices: list[int],
) -> None:
    provider = _provider()
    provider.embed_texts.side_effect = None
    provider.embed_texts.return_value = _response([[1.0, 0.0] for _ in indices], indices=indices)
    service, repository, queue, audit = _unit_service(provider, policy=QueueRetryPolicy(2, 5))

    with pytest.raises(EmbeddingProviderError) as caught:
        await _parse(service)

    assert caught.value.code == EMBEDDING_BATCH_SIZE_MISMATCH
    assert caught.value.retryable is False
    assert repository.job.status == "failed_terminal"
    assert repository.job.next_retry_at is None
    assert repository.chunks == repository.embedding_jobs == queue.payloads == []
    assert audit.events[-1].error_code == EMBEDDING_BATCH_SIZE_MISMATCH


@pytest.mark.parametrize(
    "vectors,dim,code",
    [
        ([[1.0, 0.0], [1.0]], 2, EMBEDDING_VECTOR_DIMENSION_MISMATCH),
        ([[1.0], [1.0]], 2, EMBEDDING_VECTOR_DIMENSION_MISMATCH),
        ([[1.0, 0.0], [1.0, 0.0]], 3, EMBEDDING_VECTOR_DIMENSION_MISMATCH),
        ([[], [1.0, 0.0]], 2, EMBEDDING_VECTOR_EMPTY),
        ([[1.0, 0.0], []], 2, EMBEDDING_VECTOR_EMPTY),
    ],
)
async def test_typed_malformed_vector_dimensions_become_terminal_provider_failures(
    vectors: list[list[float]], dim: int, code: str,
) -> None:
    provider = _provider()
    provider.embed_texts.side_effect = None
    provider.embed_texts.return_value = _response(vectors, dim=dim)
    service, repository, queue, audit = _unit_service(provider, policy=QueueRetryPolicy(3, 30))

    with pytest.raises(EmbeddingProviderError) as caught:
        await _parse(service)

    assert caught.value.code == code
    assert caught.value.retryable is False
    assert repository.job.status == "failed_terminal"
    assert repository.job.error_code == code
    assert repository.job.attempt_count == 1
    assert repository.job.next_retry_at is None
    assert repository.chunks == repository.embedding_jobs == queue.payloads == []
    assert audit.events[-1].status == AuditStatus.FAILURE


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
async def test_nonfinite_vectors_cannot_silently_determine_semantic_boundaries(
    value: float,
) -> None:
    provider = _provider()
    provider.embed_texts.side_effect = None
    provider.embed_texts.return_value = _response([[1.0, 0.0], [value, 1.0]])
    service, repository, queue, audit = _unit_service(provider, policy=QueueRetryPolicy(2, 5))

    with pytest.raises(EmbeddingProviderError) as caught:
        await _parse(service)

    assert caught.value.code == EMBEDDING_PROVIDER_FAILED
    assert caught.value.retryable is False
    assert repository.job.status == "failed_terminal"
    assert repository.job.next_retry_at is None
    assert repository.chunks == repository.embedding_jobs == queue.payloads == []
    assert audit.events[-1].status == AuditStatus.FAILURE


async def test_valid_zero_vectors_remain_accepted() -> None:
    provider = _provider()
    provider.embed_texts.side_effect = None
    provider.embed_texts.return_value = _response([[0.0, 0.0], [0.0, 0.0]])
    service, repository, queue, audit = _unit_service(provider)

    result = await _parse(service)

    assert result.status == repository.job.status == "chunked"
    assert result.chunk_count == len(repository.chunks) == 1
    assert repository.chunks[0].content == "First sentence.\n\nSecond sentence."
    assert len(repository.embedding_jobs) == len(queue.payloads) == 1
    assert repository.failed == []
    assert all(event.status == AuditStatus.SUCCESS for event in audit.events)
    provider.embed_texts.assert_awaited_once()


async def test_out_of_order_valid_vectors_preserve_exact_chunk_values() -> None:
    provider = _provider()
    provider.embed_texts.side_effect = None
    response = _response([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    provider.embed_texts.return_value = response
    chunker = SemanticChunker(
        SemanticChunkerConfig(min_chunk_sentences=1), embedding_provider=provider,
    )
    parsed = _parsed_document([_section("section-1", "Alpha one. Alpha two. Beta one.")])
    ordered = await chunker.split(parsed)
    assert [chunk.content for chunk in ordered] == ["Alpha one.\n\nAlpha two.", "Beta one."]
    provider.embed_texts.return_value = response.model_copy(update={
        "vectors": [response.vectors[2], response.vectors[0], response.vectors[1]],
    })
    assert await chunker.split(parsed) == ordered
    assert provider.embed_texts.await_count == 2


async def test_real_caller_cancellation_reaches_provider_without_failure_reclassification() -> None:
    provider = _provider()
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def block(request: EmbeddingRequest) -> EmbeddingResponse:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        raise AssertionError("unreachable provider result")

    provider.embed_texts.side_effect = block
    service, repository, queue, audit = _unit_service(provider, policy=QueueRetryPolicy(0, 0))
    task = asyncio.create_task(_parse(service))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        assert task.cancelled() and cancelled.is_set()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert repository.job.status == "parsed"
    assert repository.job.attempt_count == 1
    assert repository.failed == []
    assert repository.chunks == repository.embedding_jobs == queue.payloads == []
    assert all(event.status == AuditStatus.SUCCESS for event in audit.events)


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = _sqlite_async_url(tmp_path / "semantic-independent-review.db")
    _run_migrations(url, monkeypatch)
    return url


@pytest.fixture
async def factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed(factory: async_sessionmaker[AsyncSession], *, attempt: int = 0) -> None:
    async with factory() as session:
        repository = DocumentRepository(session)
        await repository.create_upload_records(
            document=_document(status="uploaded"),
            version=_version(status="uploaded").model_copy(update={"byte_size": len(CONTENT)}),
            job=_job(status="queued").model_copy(update={"attempt_count": attempt}),
        )
        await repository.commit()


def _persistent_service(
    session: AsyncSession, provider: Mock, queue: FakeEmbeddingQueue,
    policy: QueueRetryPolicy,
) -> IngestionParseService:
    return IngestionParseService(
        repository=DocumentRepository(session),
        object_storage=FakeStorage(content=CONTENT, checksum="checksum-ver-1"),
        audit=SqlAlchemyAuditPort(session),
        chunker=SemanticChunker(embedding_provider=provider),
        embedding_queue=queue, embedding_provider="fake", embedding_model="review-semantic",
        embedding_dim=2, id_factory=lambda: "review-embedding-job", queue_retry_policy=policy,
    )


async def _checkpoint(session: AsyncSession) -> dict[str, list[dict[str, object]]]:
    return {
        model.__tablename__: [
            dict(row) for row in (
                await session.execute(select(model.__table__).order_by(model.id))
            ).mappings()
        ]
        for model in (
            DocumentModel, DocumentVersionModel, IngestionJobModel, EmbeddingJobModel, ChunkModel,
        )
    }


async def test_fresh_session_retry_deadline_and_exhaustion_preserve_claim_continuity(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(factory, attempt=4)
    provider = _provider()
    error = EmbeddingProviderError(code=EMBEDDING_PROVIDER_TIMEOUT, retryable=True)
    provider.embed_texts.side_effect = error
    queue = FakeEmbeddingQueue()
    async with factory() as session:
        with pytest.raises(EmbeddingProviderError) as caught:
            await _parse(_persistent_service(session, provider, queue, QueueRetryPolicy(1, 60)))
        assert caught.value is error
    async with factory() as session:
        before = await _checkpoint(session)
        assert before["ingestion_jobs"][0]["status"] == "failed_retryable"
        assert before["ingestion_jobs"][0]["attempt_count"] == 5
        assert before["ingestion_jobs"][0]["next_retry_at"] is not None
        with pytest.raises(GenericDocumentParseError) as caught:
            await _parse(_persistent_service(session, provider, queue, QueueRetryPolicy(0, 0)))
        assert caught.value.details["reason"] == "claim_not_acquired"
        assert await _checkpoint(session) == before
        provider.embed_texts.assert_awaited_once()
    async with factory() as session:
        await session.execute(update(IngestionJobModel).where(
            IngestionJobModel.id == "job-1",
        ).values(next_retry_at=datetime.now(tz=UTC) - timedelta(seconds=1)))
        await session.commit()
    async with factory() as session:
        with pytest.raises(EmbeddingProviderError) as caught:
            await _parse(_persistent_service(session, provider, queue, QueueRetryPolicy(0, 0)))
        assert caught.value is error
    async with factory() as session:
        checkpoint = await _checkpoint(session)
        for table in ("documents", "document_versions", "ingestion_jobs"):
            assert checkpoint[table][0]["status"] == "failed_terminal"
        assert checkpoint["ingestion_jobs"][0]["attempt_count"] == 6
        assert checkpoint["ingestion_jobs"][0]["error_code"] == error.code
        assert checkpoint["ingestion_jobs"][0]["next_retry_at"] is None
        assert checkpoint["chunks"] == checkpoint["embedding_jobs"] == []
        failures = list(await session.scalars(select(AuditLogModel).where(
            AuditLogModel.status == "failure",
        )))
        assert len(failures) == 2
        assert all(event.error_code == error.code for event in failures)
    assert provider.embed_texts.await_count == 2
    assert queue.payloads == []


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("error_kind", ["chunk", "provider"])
async def test_ambiguous_chunk_commit_never_reclassified_and_committed_replay_skips_provider(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch,
    committed: bool, error_kind: str,
) -> None:
    await _seed(factory)
    provider = _provider()
    queue = FakeEmbeddingQueue()
    error = (
        GenericDocumentChunkError() if error_kind == "chunk"
        else EmbeddingProviderError(code=EMBEDDING_PROVIDER_TIMEOUT, retryable=True)
    )
    original_commit = DocumentRepository.commit
    commits = 0

    async def fail_third_commit(repository: DocumentRepository) -> None:
        nonlocal commits
        commits += 1
        if commits == 3:
            if committed:
                await original_commit(repository)
            raise error
        await original_commit(repository)

    with monkeypatch.context() as patch:
        patch.setattr(DocumentRepository, "commit", fail_third_commit)
        async with factory() as session:
            with pytest.raises(type(error)) as caught:
                await _parse(_persistent_service(session, provider, queue, QueueRetryPolicy(0, 0)))
            assert caught.value is error
    assert commits == 3
    assert queue.payloads == []
    async with factory() as session:
        checkpoint = await _checkpoint(session)
        assert checkpoint["ingestion_jobs"][0]["status"] == ("chunked" if committed else "parsed")
        assert checkpoint["ingestion_jobs"][0]["attempt_count"] == 1
        assert checkpoint["ingestion_jobs"][0]["error_code"] is None
        assert checkpoint["ingestion_jobs"][0]["next_retry_at"] is None
        assert len(checkpoint["chunks"]) == int(committed)
        assert len(checkpoint["embedding_jobs"]) == int(committed)
        assert list(await session.scalars(select(AuditLogModel).where(
            AuditLogModel.status == "failure",
        ))) == []
    if committed:
        async with factory() as session:
            provider.embed_texts.side_effect = AssertionError("replay must skip sentence embedding")
            result = await _parse(_persistent_service(
                session, provider, queue, QueueRetryPolicy(0, 0),
            ))
            assert result.status == "chunked"
            assert result.embedding_job_id == "review-embedding-job"
            assert await _checkpoint(session) == checkpoint
        assert len(queue.payloads) == 1
    provider.embed_texts.assert_awaited_once()


@pytest.mark.parametrize("error_kind", ["chunk", "provider"])
async def test_postcommit_handoff_error_replays_same_job_without_new_semantic_computation(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch, error_kind: str,
) -> None:
    await _seed(factory)
    provider = _provider()
    queue = FakeEmbeddingQueue()
    error = (
        GenericDocumentChunkError() if error_kind == "chunk"
        else EmbeddingProviderError(code=EMBEDDING_PROVIDER_TIMEOUT, retryable=False)
    )
    enqueue = AsyncMock(side_effect=error)
    with monkeypatch.context() as patch:
        patch.setattr(queue, "enqueue_embedding_job", enqueue)
        async with factory() as session:
            with pytest.raises(type(error)) as caught:
                await _parse(_persistent_service(session, provider, queue, QueueRetryPolicy(0, 0)))
            assert caught.value is error
    enqueue.assert_awaited_once()
    async with factory() as session:
        checkpoint = await _checkpoint(session)
        assert checkpoint["ingestion_jobs"][0]["status"] == "chunked"
        assert checkpoint["ingestion_jobs"][0]["attempt_count"] == 1
        assert len(checkpoint["embedding_jobs"]) == 1
        assert list(await session.scalars(select(AuditLogModel).where(
            AuditLogModel.status == "failure",
        ))) == []
    async with factory() as session:
        provider.embed_texts.side_effect = AssertionError("replay must skip provider")
        result = await _parse(_persistent_service(session, provider, queue, QueueRetryPolicy(0, 0)))
        assert result.embedding_job_id == "review-embedding-job"
        assert await _checkpoint(session) == checkpoint
    provider.embed_texts.assert_awaited_once()
    assert len(queue.payloads) == 1
    assert queue.payloads[0] == enqueue.await_args.args[0]


@pytest.mark.parametrize("boundary", ["audit", "commit"])
async def test_required_failure_transaction_errors_propagate_without_committing_partial_failure(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    await _seed(factory)
    provider = _provider()
    provider.embed_texts.side_effect = EmbeddingProviderError(
        code=EMBEDDING_PROVIDER_TIMEOUT, retryable=True,
    )
    queue = FakeEmbeddingQueue()
    failure = StorageError(code="REVIEW_REQUIRED_PERSISTENCE_FAILED")
    if boundary == "audit":
        original_record = SqlAlchemyAuditPort.record

        async def fail_failure_audit(audit: SqlAlchemyAuditPort, event: AuditEvent) -> None:
            if event.status == AuditStatus.FAILURE:
                raise failure
            await original_record(audit, event)

        monkeypatch.setattr(SqlAlchemyAuditPort, "record", fail_failure_audit)
    else:
        original_commit = DocumentRepository.commit
        commits = 0

        async def fail_failure_commit(repository: DocumentRepository) -> None:
            nonlocal commits
            commits += 1
            if commits == 3:
                raise failure
            await original_commit(repository)

        monkeypatch.setattr(DocumentRepository, "commit", fail_failure_commit)
    async with factory() as session:
        with pytest.raises(StorageError) as caught:
            await _parse(_persistent_service(session, provider, queue, QueueRetryPolicy(1, 5)))
        assert caught.value is failure
    async with factory() as session:
        checkpoint = await _checkpoint(session)
        for table in ("documents", "document_versions", "ingestion_jobs"):
            assert checkpoint[table][0]["status"] == "parsed"
        assert checkpoint["ingestion_jobs"][0]["attempt_count"] == 1
        assert checkpoint["ingestion_jobs"][0]["error_code"] is None
        assert checkpoint["chunks"] == checkpoint["embedding_jobs"] == []
        assert list(await session.scalars(select(AuditLogModel).where(
            AuditLogModel.status == "failure",
        ))) == []
    provider.embed_texts.assert_awaited_once()
    assert queue.payloads == []


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
async def test_untyped_provider_implementation_errors_are_not_reclassified(
    error_type: type[Exception],
) -> None:
    provider = _provider()
    failure = error_type("synthetic provider implementation failure")
    provider.embed_texts.side_effect = failure
    service, repository, queue, audit = _unit_service(provider, policy=QueueRetryPolicy(1, 5))

    with pytest.raises(error_type) as caught:
        await _parse(service)

    assert caught.value is failure
    assert repository.job.status == "parsed"
    assert repository.failed == []
    assert repository.chunks == repository.embedding_jobs == queue.payloads == []
    assert all(event.status == AuditStatus.SUCCESS for event in audit.events)


@pytest.mark.parametrize(
    "vectors,code",
    [
        ([[1.0, 0.0], [1.0]], EMBEDDING_VECTOR_DIMENSION_MISMATCH),
        ([[1.0, 0.0], []], EMBEDDING_VECTOR_EMPTY),
        ([[1.0, 0.0], [float("nan"), 1.0]], EMBEDDING_PROVIDER_FAILED),
    ],
)
async def test_malformed_vector_failure_is_terminal_and_visible_in_new_database_session(
    factory: async_sessionmaker[AsyncSession], vectors: list[list[float]], code: str,
) -> None:
    await _seed(factory)
    provider = _provider()
    provider.embed_texts.side_effect = None
    provider.embed_texts.return_value = _response(vectors)
    queue = FakeEmbeddingQueue()
    async with factory() as session:
        with pytest.raises(EmbeddingProviderError) as caught:
            await _parse(_persistent_service(session, provider, queue, QueueRetryPolicy(5, 60)))
        assert caught.value.code == code
        assert caught.value.retryable is False
    async with factory() as session:
        checkpoint = await _checkpoint(session)
        for table in ("documents", "document_versions", "ingestion_jobs"):
            assert checkpoint[table][0]["status"] == "failed_terminal"
        assert checkpoint["ingestion_jobs"][0]["attempt_count"] == 1
        assert checkpoint["ingestion_jobs"][0]["error_code"] == code
        assert checkpoint["ingestion_jobs"][0]["next_retry_at"] is None
        assert checkpoint["chunks"] == checkpoint["embedding_jobs"] == []
        failures = list(await session.scalars(select(AuditLogModel).where(
            AuditLogModel.status == "failure",
        )))
        assert len(failures) == 1
        assert failures[0].error_code == code
        assert failures[0].metadata_["status"] == "failed_terminal"
    provider.embed_texts.assert_awaited_once()
    assert queue.payloads == []
