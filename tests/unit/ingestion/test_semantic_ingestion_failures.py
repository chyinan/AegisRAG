"""Real semantic computation with local providers and controlled failure boundaries."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from packages.common.audit import AuditStatus, InMemoryAuditPort
from packages.common.retry import QueueRetryPolicy
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
from packages.ingestion.exceptions import GenericDocumentChunkError
from packages.ingestion.service import IngestionParseService, ParseJobResult
from tests.unit.ingestion.test_parse_service import (
    FakeEmbeddingQueue,
    FakeLogger,
    FakeRepository,
    FakeStorage,
    _context,
)
from tests.unit.ingestion.test_semantic_chunker import _document, _section


def _response(indices: list[int]) -> EmbeddingResponse:
    return EmbeddingResponse(
        vectors=[EmbeddingVector(index=index, vector=[1.0, 0.0]) for index in indices],
        provider="fake", model="fake-embedding", dim=2, latency_ms=0,
    )


def _success(request: EmbeddingRequest) -> EmbeddingResponse:
    return _response(list(range(len(request.texts))))


def _provider() -> Mock:
    provider = Mock(spec=EmbeddingProvider)
    provider.embed_texts = AsyncMock(side_effect=_success)
    return provider


@dataclass
class _Case:
    service: IngestionParseService
    repository: FakeRepository
    provider: Mock
    queue: FakeEmbeddingQueue
    audit: InMemoryAuditPort
    logger: FakeLogger


def _case(
    *, policy: QueueRetryPolicy | None = None, content: bytes = b"# Policy\nBody",
) -> _Case:
    repository = FakeRepository(byte_size=len(content))
    provider = _provider()
    queue = FakeEmbeddingQueue()
    audit = InMemoryAuditPort()
    logger = FakeLogger(events=[])
    service = IngestionParseService(
        repository=repository,
        object_storage=FakeStorage(content=content),
        audit=audit,
        logger=logger,
        chunker=SemanticChunker(embedding_provider=provider),
        embedding_queue=queue,
        embedding_provider="fake",
        embedding_model="fake-embedding",
        embedding_dim=2,
        id_factory=lambda: "semantic-embedding-job",
        queue_retry_policy=policy,
    )
    return _Case(service, repository, provider, queue, audit, logger)


async def _parse(service: IngestionParseService) -> ParseJobResult:
    return await service.parse_job(
        _context(), job_id="job-1", document_id="doc-1", version_id="ver-1",
    )


@pytest.mark.parametrize("retryable", [True, False])
@pytest.mark.parametrize("remaining,delay", [(1, 5), (0, 0)])
async def test_semantic_provider_failure_uses_retry_policy_and_claim_attempt(
    retryable: bool, remaining: int, delay: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(policy=QueueRetryPolicy(remaining, delay))
    error = EmbeddingProviderError(
        code=EMBEDDING_PROVIDER_TIMEOUT, retryable=retryable,
        details={"provider_message": "synthetic private provider detail"},
    )
    case.provider.embed_texts.side_effect = error
    failure = AsyncMock(wraps=case.repository.mark_ingestion_job_failed)
    replace = AsyncMock(wraps=case.repository.replace_chunks_for_version)
    create = AsyncMock(wraps=case.repository.create_embedding_job)
    monkeypatch.setattr(case.repository, "mark_ingestion_job_failed", failure)
    monkeypatch.setattr(case.repository, "replace_chunks_for_version", replace)
    monkeypatch.setattr(case.repository, "create_embedding_job", create)
    before = datetime.now(tz=UTC)

    with pytest.raises(EmbeddingProviderError) as caught:
        await _parse(case.service)

    expected_status = "failed_retryable" if retryable and remaining else "failed_terminal"
    assert caught.value is error
    case.provider.embed_texts.assert_awaited_once()
    failure.assert_awaited_once()
    assert failure.await_args.kwargs["expected_attempt_count"] == 1
    assert case.repository.job.attempt_count == len(case.repository.claims) == 1
    assert case.repository.job.status == expected_status
    assert case.repository.job.error_code == EMBEDDING_PROVIDER_TIMEOUT
    assert case.repository.failed == [{
        "job_id": "job-1", "status": expected_status, "error_code": EMBEDDING_PROVIDER_TIMEOUT,
    }]
    if retryable and remaining:
        deadline = case.repository.job.next_retry_at
        assert deadline is not None
        assert (before + timedelta(seconds=delay)).replace(microsecond=0) <= deadline
        assert deadline <= datetime.now(tz=UTC) + timedelta(seconds=delay)
    else:
        assert case.repository.job.next_retry_at is None
    replace.assert_not_awaited()
    create.assert_not_awaited()
    assert case.repository.chunks == case.repository.chunked == case.repository.embedding_jobs == []
    assert case.queue.payloads == []
    assert case.repository.commits == 3  # Claim, parsed checkpoint, failed business state.
    assert case.audit.events[-1].status is AuditStatus.FAILURE
    assert case.audit.events[-1].error_code == EMBEDDING_PROVIDER_TIMEOUT
    assert case.audit.events[-1].metadata["status"] == expected_status
    assert case.logger.events[-1][0] == "document.parse.failed"
    recorded = repr(case.audit.events) + repr(case.logger.events)
    assert "synthetic private provider detail" not in recorded


async def test_semantic_failure_without_executor_policy_remains_retryable() -> None:
    case = _case()
    case.provider.embed_texts.side_effect = EmbeddingProviderError(
        code=EMBEDDING_PROVIDER_TIMEOUT, retryable=True,
    )
    with pytest.raises(EmbeddingProviderError):
        await _parse(case.service)
    assert case.repository.job.status == "failed_retryable"
    assert case.repository.job.next_retry_at is None


async def test_semantic_retry_claims_new_attempt_then_creates_one_handoff() -> None:
    case = _case(policy=QueueRetryPolicy(1, 0))
    case.provider.embed_texts.side_effect = EmbeddingProviderError(
        code=EMBEDDING_PROVIDER_TIMEOUT, retryable=True,
    )
    with pytest.raises(EmbeddingProviderError):
        await _parse(case.service)
    assert case.repository.job.status == "failed_retryable"
    assert case.repository.chunks == case.repository.embedding_jobs == case.queue.payloads == []
    case.provider.embed_texts.side_effect = _success

    result = await _parse(case.service)

    assert result.status == case.repository.job.status == "chunked"
    assert case.repository.version.status == "chunked"
    assert case.repository.job.attempt_count == len(case.repository.claims) == 2
    assert len(case.repository.failed) == 1
    assert case.repository.job.error_code is None
    assert case.provider.embed_texts.await_count == 2
    assert len(case.repository.chunks) == result.chunk_count == 1
    assert len(case.repository.embedding_jobs) == len(case.queue.payloads) == 1
    assert case.queue.payloads[0].resource_id == result.embedding_job_id == "semantic-embedding-job"


async def test_semantic_caller_cancellation_propagates_without_failure_mapping() -> None:
    case = _case(policy=QueueRetryPolicy(0, 0))
    cancellation = asyncio.CancelledError()
    case.provider.embed_texts.side_effect = cancellation

    with pytest.raises(asyncio.CancelledError) as caught:
        await _parse(case.service)

    assert caught.value is cancellation
    assert case.repository.job.status == "parsed"
    assert case.repository.job.attempt_count == 1
    assert case.repository.failed == []
    assert case.repository.chunks == case.repository.embedding_jobs == case.queue.payloads == []
    assert all(event.status is AuditStatus.SUCCESS for event in case.audit.events)


@pytest.mark.parametrize("indices", [[], [0], [0, 0], [0, 2], [0, 1, 2]])
async def test_semantic_malformed_sentence_indices_raise_defined_domain_error(
    indices: list[int],
) -> None:
    provider = _provider()
    provider.embed_texts.side_effect = None
    provider.embed_texts.return_value = _response(indices)
    chunker = SemanticChunker(embedding_provider=provider)

    with pytest.raises(EmbeddingProviderError) as caught:
        await chunker.split(_document([_section("section-1", "First sentence. Second sentence.")]))

    assert caught.value.code == EMBEDDING_BATCH_SIZE_MISMATCH
    assert caught.value.retryable is False
    assert caught.value.details == {
        "reason": "sentence_vector_index_mismatch", "expected": 2, "actual": len(indices),
    }
    provider.embed_texts.assert_awaited_once()


async def test_semantic_complete_out_of_order_response_preserves_output() -> None:
    provider = _provider()
    provider.embed_texts.side_effect = None
    ordered_response = _response([0, 1, 2]).model_copy(update={"vectors": [
        EmbeddingVector(index=0, vector=[1.0, 0.0]),
        EmbeddingVector(index=1, vector=[1.0, 0.0]),
        EmbeddingVector(index=2, vector=[0.0, 1.0]),
    ]})
    provider.embed_texts.return_value = ordered_response
    chunker = SemanticChunker(
        SemanticChunkerConfig(min_chunk_sentences=1), embedding_provider=provider,
    )
    document = _document([_section(
        "section-1", "First sentence. Second sentence. Different topic.",
    )])
    ordered = await chunker.split(document)
    assert len(ordered) == 2
    provider.embed_texts.return_value = ordered_response.model_copy(update={
        "vectors": list(reversed(ordered_response.vectors)),
    })

    assert await chunker.split(document) == ordered


async def test_semantic_missing_index_failure_is_terminal_in_service() -> None:
    case = _case(policy=QueueRetryPolicy(1, 5))
    case.provider.embed_texts.side_effect = None
    case.provider.embed_texts.return_value = _response([])

    with pytest.raises(EmbeddingProviderError) as caught:
        await _parse(case.service)

    assert caught.value.code == EMBEDDING_BATCH_SIZE_MISMATCH
    assert case.repository.job.status == "failed_terminal"
    assert case.repository.job.error_code == EMBEDDING_BATCH_SIZE_MISMATCH
    assert case.repository.job.next_retry_at is None
    assert case.repository.job.attempt_count == 1
    assert case.repository.chunks == case.repository.embedding_jobs == case.queue.payloads == []
    assert case.audit.events[-1].status is AuditStatus.FAILURE
    assert case.audit.events[-1].error_code == EMBEDDING_BATCH_SIZE_MISMATCH


@pytest.mark.parametrize("error_type", ["provider", "chunk"])
@pytest.mark.parametrize(
    "boundary", ["replace", "chunked", "create", "commit", "handoff", "enqueue"],
)
async def test_persistence_and_handoff_errors_are_not_chunk_computation_failures(
    boundary: str, error_type: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(policy=QueueRetryPolicy(0, 0))
    error = (
        EmbeddingProviderError(code=EMBEDDING_PROVIDER_TIMEOUT, retryable=True)
        if error_type == "provider" else GenericDocumentChunkError()
    )
    operation = {
        "replace": "replace_chunks_for_version",
        "chunked": "mark_ingestion_job_chunked",
        "create": "create_embedding_job",
        "commit": "commit",
        "handoff": "get_ingestion_embedding_handoff",
        "enqueue": "enqueue_embedding_job",
    }[boundary]
    target = case.queue if boundary == "enqueue" else case.repository
    side_effect = [None, None, error] if boundary == "commit" else error
    failure = AsyncMock(side_effect=side_effect)
    monkeypatch.setattr(target, operation, failure)

    with pytest.raises(type(error)) as caught:
        await _parse(case.service)

    assert caught.value is error
    assert failure.await_count == (3 if boundary == "commit" else 1)
    case.provider.embed_texts.assert_awaited_once()
    assert case.repository.failed == []
    assert all(event.status is AuditStatus.SUCCESS for event in case.audit.events)
    assert case.repository.job.attempt_count == 1
    assert case.queue.payloads == []
    if boundary in {"commit", "handoff", "enqueue"}:
        assert case.repository.job.status == "chunked"
        assert len(case.repository.embedding_jobs) == 1


@pytest.mark.parametrize(
    "vectors,code",
    [
        ([[], []], EMBEDDING_VECTOR_EMPTY),
        ([[1.0, 0.0], [1.0]], EMBEDDING_VECTOR_DIMENSION_MISMATCH),
        ([[1.0], [1.0]], EMBEDDING_VECTOR_DIMENSION_MISMATCH),
        ([[float("nan"), 0.0], [1.0, 0.0]], EMBEDDING_PROVIDER_FAILED),
        ([[float("inf"), 0.0], [1.0, 0.0]], EMBEDDING_PROVIDER_FAILED),
        ([[float("-inf"), 0.0], [1.0, 0.0]], EMBEDDING_PROVIDER_FAILED),
    ],
)
async def test_semantic_malformed_vector_values_are_terminal_before_downstream_writes(
    vectors: list[list[float]], code: str,
) -> None:
    content = "First sentence. Second sentence."
    case = _case(policy=QueueRetryPolicy(1, 5), content=content.encode())
    case.provider.embed_texts.side_effect = None
    case.provider.embed_texts.return_value = _response([0, 1]).model_copy(update={
        "vectors": [
            EmbeddingVector(index=index, vector=value) for index, value in enumerate(vectors)
        ],
    })
    # Cover the chunker's domain boundary directly, then the real service path.
    chunker = SemanticChunker(embedding_provider=case.provider)
    with pytest.raises(EmbeddingProviderError) as direct:
        await chunker.split(_document([_section("section-1", content)]))
    assert direct.value.code == code
    assert direct.value.retryable is False
    with pytest.raises(EmbeddingProviderError) as service:
        await _parse(case.service)
    assert service.value.code == code
    assert service.value.retryable is False
    assert case.repository.job.status == "failed_terminal"
    assert case.repository.job.error_code == code
    assert case.repository.job.next_retry_at is None
    assert case.repository.job.attempt_count == 1
    assert case.repository.chunks == case.repository.embedding_jobs == case.queue.payloads == []
    assert case.audit.events[-1].status is AuditStatus.FAILURE
    assert case.audit.events[-1].error_code == code


async def test_semantic_finite_zero_vectors_keep_existing_behavior() -> None:
    case = _case()
    case.provider.embed_texts.side_effect = None
    case.provider.embed_texts.return_value = _response([0]).model_copy(update={
        "vectors": [EmbeddingVector(index=0, vector=[0.0, 0.0])],
    })
    result = await _parse(case.service)
    assert result.status == "chunked"
    assert case.repository.failed == []
    assert len(case.queue.payloads) == len(case.repository.embedding_jobs) == 1
