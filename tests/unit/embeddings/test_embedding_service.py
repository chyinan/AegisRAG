from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from packages.auth.context import AuthContext
from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.data.dto import ChunkRecord, DocumentVersionRecord, EmbeddingJobRecord
from packages.embeddings.adapters.fake import FakeEmbeddingProvider
from packages.embeddings.dto import EmbeddingRequest, EmbeddingResponse, EmbeddingVector
from packages.embeddings.exceptions import (
    EMBEDDING_BATCH_SIZE_MISMATCH,
    EMBEDDING_CHUNK_SNAPSHOT_MISMATCH,
    EMBEDDING_PROVIDER_TIMEOUT,
    EMBEDDING_VECTOR_DIMENSION_MISMATCH,
    EMBEDDING_VECTOR_EMPTY,
    EmbeddingJobError,
    EmbeddingProviderError,
)
from packages.embeddings.service import EmbeddingJobService
from packages.vectorstores.adapters.fake import FakeVectorStore
from packages.vectorstores.dto import VectorRecord, VectorUpsertResult
from packages.vectorstores.exceptions import INDEX_DIMENSION_MISMATCH, VectorStoreError


class FakeRepository:
    def __init__(self) -> None:
        self.job = EmbeddingJobRecord(
            id="embed-job-1",
            tenant_id="tenant-1",
            created_by="user-1",
            status="queued",
            document_id="doc-1",
            version_id="ver-1",
            provider="fake",
            model="fake-embedding",
            dim=4,
        )
        self.version = DocumentVersionRecord(
            id="ver-1",
            document_id="doc-1",
            tenant_id="tenant-1",
            created_by="user-1",
            status="chunked",
            source_type="txt",
            source_uri="kb://policy.txt",
            object_key="raw/tenant-1/doc-1/ver-1/policy.txt",
            filename="policy.txt",
            content_type="text/plain",
            byte_size=128,
            acl={"visibility": "tenant"},
            checksum="checksum-ver-1",
            metadata={"chunk_artifact_summary": {"chunk_count": 2}},
        )
        self.chunks = [
            _chunk(chunk_id="chunk-1", content="first chunk", token_count=2),
            _chunk(chunk_id="chunk-2", content="second chunk", token_count=3),
        ]
        self.commits = 0
        self.embedded_metadata: dict[str, object] | None = None
        self.ready_metadata: dict[str, object] | None = None
        self.ready_calls = 0
        self.failed_status: str | None = None
        self.failed_error_code: str | None = None
        self.list_chunk_calls = 0
        self.mutate_chunks_on_second_read = False

    async def get_embedding_job(self, *, tenant_id: str, job_id: str) -> EmbeddingJobRecord | None:
        if tenant_id != self.job.tenant_id or job_id != self.job.id:
            return None
        return self.job

    async def get_version(
        self,
        *,
        tenant_id: str,
        version_id: str,
    ) -> DocumentVersionRecord | None:
        if tenant_id != self.version.tenant_id or version_id != self.version.id:
            return None
        return self.version

    async def claim_embedding_job(
        self,
        *,
        tenant_id: str,
        job_id: str,
        document_id: str,
        version_id: str,
        stale_before: datetime | None,
    ) -> EmbeddingJobRecord | None:
        assert stale_before is not None
        if (
            tenant_id != self.job.tenant_id
            or job_id != self.job.id
            or document_id != self.job.document_id
            or version_id != self.job.version_id
            or self.job.status not in {"queued", "failed_retryable"}
        ):
            return None
        self.job = self.job.model_copy(
            update={
                "status": "embedding",
                "attempt_count": self.job.attempt_count + 1,
                "last_attempt_at": datetime.now(tz=UTC),
                "error_code": None,
            }
        )
        return self.job

    async def list_chunks_for_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        status: str | None = None,
    ) -> list[ChunkRecord]:
        assert (tenant_id, document_id, version_id, status) == (
            "tenant-1",
            "doc-1",
            "ver-1",
            "active",
        )
        self.list_chunk_calls += 1
        if self.mutate_chunks_on_second_read and self.list_chunk_calls > 1:
            return [
                self.chunks[0].model_copy(update={"checksum": "changed-checksum"}),
                self.chunks[1],
            ]
        return list(self.chunks)

    async def mark_embedding_job_embedded(
        self,
        *,
        tenant_id: str,
        job_id: str,
        embedding_metadata: dict[str, object],
    ) -> EmbeddingJobRecord:
        self.embedded_metadata = embedding_metadata
        self.job = self.job.model_copy(
            update={
                "status": "embedded",
                "provider": embedding_metadata["provider"],
                "model": embedding_metadata["model"],
                "version": embedding_metadata["version"],
                "dim": embedding_metadata["dim"],
                "chunk_count": embedding_metadata["chunk_count"],
                "metadata": {"embedding_artifact_summary": embedding_metadata},
            }
        )
        self.version = self.version.model_copy(update={"status": "embedded"})
        return self.job

    async def mark_document_version_retrieval_ready(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        index_metadata: dict[str, object] | None = None,
    ) -> DocumentVersionRecord:
        self.ready_calls += 1
        self.ready_metadata = dict(index_metadata or {})
        self.version = self.version.model_copy(update={"status": "retrieval_ready"})
        return self.version

    async def mark_embedding_job_failed(
        self,
        *,
        tenant_id: str,
        job_id: str,
        error_code: str,
        status: str,
        next_retry_at: datetime | None = None,
    ) -> EmbeddingJobRecord:
        self.failed_status = status
        self.failed_error_code = error_code
        self.job = self.job.model_copy(
            update={
                "status": status,
                "error_code": error_code,
                "last_attempt_at": datetime.now(tz=UTC),
                "next_retry_at": next_retry_at,
            }
        )
        return self.job

    async def commit(self) -> None:
        self.commits += 1


class BatchMismatchProvider:
    async def embed_texts(self, request: EmbeddingRequest) -> EmbeddingResponse:
        return EmbeddingResponse(
            vectors=[EmbeddingVector(index=0, chunk_id="chunk-1", vector=[0.1, 0.2, 0.3, 0.4])],
            provider=request.provider,
            model=request.model,
            version="fake-v1",
            dim=4,
            usage={},
            latency_ms=1.0,
        )


class DimensionMismatchProvider:
    async def embed_texts(self, request: EmbeddingRequest) -> EmbeddingResponse:
        return EmbeddingResponse(
            vectors=[
                EmbeddingVector(index=0, chunk_id="chunk-1", vector=[0.1, 0.2]),
                EmbeddingVector(index=1, chunk_id="chunk-2", vector=[0.1, 0.2, 0.3, 0.4]),
            ],
            provider=request.provider,
            model=request.model,
            version="fake-v1",
            dim=4,
            usage={},
            latency_ms=1.0,
        )


class EmptyVectorProvider:
    async def embed_texts(self, request: EmbeddingRequest) -> EmbeddingResponse:
        return EmbeddingResponse(
            vectors=[
                EmbeddingVector(index=0, chunk_id="chunk-1", vector=[]),
                EmbeddingVector(index=1, chunk_id="chunk-2", vector=[0.1, 0.2, 0.3, 0.4]),
            ],
            provider=request.provider,
            model=request.model,
            version="fake-v1",
            dim=4,
            usage={},
            latency_ms=1.0,
        )


class UnsafeUsageProvider:
    async def embed_texts(self, request: EmbeddingRequest) -> EmbeddingResponse:
        return EmbeddingResponse(
            vectors=[
                EmbeddingVector(index=0, chunk_id="chunk-1", vector=[0.1, 0.2, 0.3, 0.4]),
                EmbeddingVector(index=1, chunk_id="chunk-2", vector=[0.4, 0.3, 0.2, 0.1]),
            ],
            provider=request.provider,
            model=request.model,
            version="fake-v1",
            dim=4,
            usage={
                "text_count": 2,
                "total_tokens": 12,
                "content": "secret chunk text",
                "raw_response": {"body": "provider raw response"},
                "api_key": "sk-secret",
                "request_id": "provider-request-id",
            },
            latency_ms=1.0,
        )


class RecordingVectorStore(FakeVectorStore):
    def __init__(self, *, index_dim: int) -> None:
        super().__init__(index_dim=index_dim)
        self.upserted_records: list[VectorRecord] = []

    async def upsert(self, vectors: list[VectorRecord]) -> VectorUpsertResult:
        self.upserted_records.extend(vectors)
        return await super().upsert(vectors)


@pytest.mark.asyncio
async def test_embedding_service_embeds_active_chunks_and_records_safe_summary() -> None:
    repository = FakeRepository()
    audit = InMemoryAuditPort()
    service = EmbeddingJobService(
        repository=repository,
        provider=FakeEmbeddingProvider(dim=4),
        audit=audit,
    )

    result = await service.embed_job(
        _context(),
        job_id="embed-job-1",
        document_id="doc-1",
        version_id="ver-1",
    )

    assert result.status == "embedded"
    assert result.chunk_count == 2
    assert result.dim == 4
    assert repository.job.status == "embedded"
    assert repository.version.status == "embedded"
    assert repository.embedded_metadata == {
        "stage": "embedded",
        "provider": "fake",
        "model": "fake-embedding",
        "version": "fake-v1",
        "dim": 4,
        "chunk_count": 2,
        "token_count_min": 2,
        "token_count_max": 3,
        "usage": {"text_count": 2, "total_characters": 23},
    }
    assert "first chunk" not in str(repository.embedded_metadata)
    assert audit.events[-1].action == "document.embedding"
    assert audit.events[-1].metadata["chunk_count"] == 2
    assert audit.events[-1].metadata["dim"] == 4


@pytest.mark.asyncio
async def test_embedding_service_upserts_vectors_before_marking_embedded() -> None:
    repository = FakeRepository()
    vector_store = RecordingVectorStore(index_dim=4)
    service = EmbeddingJobService(
        repository=repository,
        provider=FakeEmbeddingProvider(dim=4),
        audit=InMemoryAuditPort(),
        vector_store=vector_store,
    )

    await service.embed_job(
        _context(),
        job_id="embed-job-1",
        document_id="doc-1",
        version_id="ver-1",
    )

    assert [record.chunk_id for record in vector_store.upserted_records] == ["chunk-1", "chunk-2"]
    assert vector_store.upserted_records[0].tenant_id == "tenant-1"
    assert vector_store.upserted_records[0].document_id == "doc-1"
    assert vector_store.upserted_records[0].version_id == "ver-1"
    assert repository.embedded_metadata is not None
    vector_index_summary = cast(
        dict[str, object],
        repository.embedded_metadata["vector_index_summary"],
    )
    assert repository.embedded_metadata["vector_index_summary"] == {
        "stage": "vector_indexed",
        "status": "indexed",
        "vector_count": 2,
        "provider": "fake",
        "model": "fake-embedding",
        "version": "fake-v1",
        "dim": 4,
        "latency_ms": vector_index_summary["latency_ms"],
    }
    assert "first chunk" not in str(repository.embedded_metadata)


@pytest.mark.asyncio
async def test_embedding_service_marks_version_retrieval_ready_after_vector_upsert() -> None:
    repository = FakeRepository()
    vector_store = RecordingVectorStore(index_dim=4)
    audit = InMemoryAuditPort()
    service = EmbeddingJobService(
        repository=repository,
        provider=FakeEmbeddingProvider(dim=4),
        audit=audit,
        vector_store=vector_store,
    )

    result = await service.embed_job(
        _context(),
        job_id="embed-job-1",
        document_id="doc-1",
        version_id="ver-1",
    )

    assert result.status == "embedded"
    assert repository.job.status == "embedded"
    assert repository.version.status == "retrieval_ready"
    assert repository.ready_calls == 1
    assert repository.ready_metadata is not None
    assert repository.ready_metadata["status"] == "indexed"
    assert repository.ready_metadata["vector_count"] == 2
    assert audit.events[-2].action == "document.index_ready"


@pytest.mark.asyncio
async def test_embedding_service_marks_vector_dimension_mismatch_terminal_without_embedded(
) -> None:
    repository = FakeRepository()
    service = EmbeddingJobService(
        repository=repository,
        provider=FakeEmbeddingProvider(dim=4),
        audit=InMemoryAuditPort(),
        vector_store=FakeVectorStore(index_dim=3),
    )

    with pytest.raises(VectorStoreError) as exc_info:
        await service.embed_job(
            _context(),
            job_id="embed-job-1",
            document_id="doc-1",
            version_id="ver-1",
        )

    assert exc_info.value.code == INDEX_DIMENSION_MISMATCH
    assert repository.failed_status == "failed_terminal"
    assert repository.failed_error_code == INDEX_DIMENSION_MISMATCH
    assert repository.embedded_metadata is None
    assert repository.ready_calls == 0


@pytest.mark.asyncio
async def test_embedding_service_is_idempotent_for_embedded_job() -> None:
    repository = FakeRepository()
    repository.job = repository.job.model_copy(
        update={"status": "embedded", "chunk_count": 2, "dim": 4}
    )
    repository.version = repository.version.model_copy(update={"status": "embedded"})
    service = EmbeddingJobService(
        repository=repository,
        provider=FakeEmbeddingProvider(dim=4),
        audit=InMemoryAuditPort(),
    )

    result = await service.embed_job(
        _context(),
        job_id="embed-job-1",
        document_id="doc-1",
        version_id="ver-1",
    )

    assert result.status == "embedded"
    assert repository.failed_status is None
    assert repository.list_chunk_calls == 0


@pytest.mark.asyncio
async def test_embedding_service_is_idempotent_for_retrieval_ready_version() -> None:
    repository = FakeRepository()
    repository.job = repository.job.model_copy(
        update={
            "status": "embedded",
            "chunk_count": 2,
            "dim": 4,
            "metadata": {
                "embedding_artifact_summary": {
                    "vector_index_summary": {"status": "indexed", "vector_count": 2}
                }
            },
        }
    )
    repository.version = repository.version.model_copy(update={"status": "retrieval_ready"})
    vector_store = RecordingVectorStore(index_dim=4)
    service = EmbeddingJobService(
        repository=repository,
        provider=FakeEmbeddingProvider(dim=4),
        audit=InMemoryAuditPort(),
        vector_store=vector_store,
    )

    result = await service.embed_job(
        _context(),
        job_id="embed-job-1",
        document_id="doc-1",
        version_id="ver-1",
    )

    assert result.status == "embedded"
    assert repository.ready_calls == 0
    assert vector_store.upserted_records == []
    assert repository.list_chunk_calls == 0


@pytest.mark.asyncio
async def test_embedding_service_marks_provider_timeout_retryable() -> None:
    repository = FakeRepository()
    audit = InMemoryAuditPort()
    service = EmbeddingJobService(
        repository=repository,
        provider=FakeEmbeddingProvider(failure_mode="timeout"),
        audit=audit,
        retry_delay_seconds=30,
    )

    with pytest.raises(EmbeddingProviderError) as exc_info:
        await service.embed_job(
            _context(),
            job_id="embed-job-1",
            document_id="doc-1",
            version_id="ver-1",
        )

    assert exc_info.value.code == EMBEDDING_PROVIDER_TIMEOUT
    assert repository.failed_status == "failed_retryable"
    assert repository.failed_error_code == EMBEDDING_PROVIDER_TIMEOUT
    assert repository.job.attempt_count == 1
    assert repository.job.next_retry_at is not None
    assert audit.events[-1].metadata["provider"] == "fake"
    assert audit.events[-1].metadata["model"] == "fake-embedding"
    assert audit.events[-1].metadata["dim"] == 4


@pytest.mark.asyncio
async def test_embedding_service_marks_batch_size_mismatch_terminal() -> None:
    repository = FakeRepository()
    service = EmbeddingJobService(
        repository=repository,
        provider=BatchMismatchProvider(),
        audit=InMemoryAuditPort(),
    )

    with pytest.raises(EmbeddingJobError) as exc_info:
        await service.embed_job(
            _context(),
            job_id="embed-job-1",
            document_id="doc-1",
            version_id="ver-1",
        )

    assert exc_info.value.code == EMBEDDING_BATCH_SIZE_MISMATCH
    assert repository.failed_status == "failed_terminal"
    assert repository.failed_error_code == EMBEDDING_BATCH_SIZE_MISMATCH


@pytest.mark.asyncio
async def test_embedding_service_marks_vector_dimension_mismatch_terminal() -> None:
    repository = FakeRepository()
    service = EmbeddingJobService(
        repository=repository,
        provider=DimensionMismatchProvider(),
        audit=InMemoryAuditPort(),
    )

    with pytest.raises(EmbeddingJobError) as exc_info:
        await service.embed_job(
            _context(),
            job_id="embed-job-1",
            document_id="doc-1",
            version_id="ver-1",
        )

    assert exc_info.value.code == EMBEDDING_VECTOR_DIMENSION_MISMATCH
    assert repository.failed_status == "failed_terminal"
    assert repository.failed_error_code == EMBEDDING_VECTOR_DIMENSION_MISMATCH


@pytest.mark.asyncio
async def test_embedding_service_marks_empty_vector_terminal() -> None:
    repository = FakeRepository()
    service = EmbeddingJobService(
        repository=repository,
        provider=EmptyVectorProvider(),
        audit=InMemoryAuditPort(),
    )

    with pytest.raises(EmbeddingJobError) as exc_info:
        await service.embed_job(
            _context(),
            job_id="embed-job-1",
            document_id="doc-1",
            version_id="ver-1",
        )

    assert exc_info.value.code == EMBEDDING_VECTOR_EMPTY
    assert repository.failed_status == "failed_terminal"
    assert repository.failed_error_code == EMBEDDING_VECTOR_EMPTY


@pytest.mark.asyncio
async def test_embedding_service_sanitizes_provider_usage_before_persisting_metadata() -> None:
    repository = FakeRepository()
    service = EmbeddingJobService(
        repository=repository,
        provider=UnsafeUsageProvider(),
        audit=InMemoryAuditPort(),
    )

    await service.embed_job(
        _context(),
        job_id="embed-job-1",
        document_id="doc-1",
        version_id="ver-1",
    )

    assert repository.embedded_metadata is not None
    assert repository.embedded_metadata["usage"] == {"text_count": 2, "total_tokens": 12}
    dumped = str(repository.embedded_metadata)
    assert "secret chunk text" not in dumped
    assert "provider raw response" not in dumped
    assert "sk-secret" not in dumped


@pytest.mark.asyncio
async def test_embedding_service_rejects_changed_chunk_snapshot_before_marking_embedded() -> None:
    repository = FakeRepository()
    repository.mutate_chunks_on_second_read = True
    service = EmbeddingJobService(
        repository=repository,
        provider=FakeEmbeddingProvider(dim=4),
        audit=InMemoryAuditPort(),
    )

    with pytest.raises(EmbeddingJobError) as exc_info:
        await service.embed_job(
            _context(),
            job_id="embed-job-1",
            document_id="doc-1",
            version_id="ver-1",
        )

    assert exc_info.value.code == EMBEDDING_CHUNK_SNAPSHOT_MISMATCH
    assert repository.failed_status == "failed_terminal"
    assert repository.embedded_metadata is None


def _context() -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-1",
        trace_id="trace-1",
        auth=AuthContext(user_id="user-1", tenant_id="tenant-1"),
    )


def _chunk(*, chunk_id: str, content: str, token_count: int) -> ChunkRecord:
    return ChunkRecord(
        tenant_id="tenant-1",
        document_id="doc-1",
        version_id="ver-1",
        chunk_id=chunk_id,
        created_by="user-1",
        status="active",
        source_type="txt",
        source_uri="kb://policy.txt",
        title_path=["Policy"],
        content=content,
        page_start=1,
        page_end=1,
        token_count=token_count,
        acl={"visibility": "tenant"},
        checksum=f"checksum-{chunk_id}",
        section_ids=["section-1"],
        metadata={},
    )
