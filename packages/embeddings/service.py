from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Protocol

from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext
from packages.common.logging import StructuredLogger
from packages.data.dto import ChunkRecord, DocumentVersionRecord, EmbeddingJobRecord
from packages.embeddings.dto import EmbeddingRequest, EmbeddingResponse
from packages.embeddings.exceptions import (
    EMBEDDING_BATCH_SIZE_MISMATCH,
    EMBEDDING_CHUNK_SNAPSHOT_MISMATCH,
    EMBEDDING_CHUNKS_NOT_FOUND,
    EMBEDDING_DOCUMENT_VERSION_NOT_CHUNKED,
    EMBEDDING_JOB_INVALID_STATE,
    EMBEDDING_JOB_NOT_FOUND,
    EMBEDDING_JOB_PAYLOAD_MISMATCH,
    EMBEDDING_PROVIDER_FAILED,
    EMBEDDING_VECTOR_DIMENSION_MISMATCH,
    EMBEDDING_VECTOR_EMPTY,
    EmbeddingJobError,
    EmbeddingProviderError,
)
from packages.embeddings.ports import EmbeddingProvider
from packages.vectorstores.exceptions import VectorStoreError
from packages.vectorstores.ports import VectorStore
from packages.vectorstores.service import map_embedding_response_to_vector_records

EMBEDDING_STATUS = "embedding"
EMBEDDED_STATUS = "embedded"
FAILED_RETRYABLE_STATUS = "failed_retryable"
FAILED_TERMINAL_STATUS = "failed_terminal"
CHUNKED_STATUS = "chunked"
DEFAULT_EMBEDDING_STALE_AFTER_SECONDS = 15 * 60


class _Logger(Protocol):
    def info(self, event: str, **kwargs: object) -> object: ...


class EmbeddingJobRepository(Protocol):
    async def get_embedding_job(
        self,
        *,
        tenant_id: str,
        job_id: str,
    ) -> EmbeddingJobRecord | None: ...

    async def get_version(
        self,
        *,
        tenant_id: str,
        version_id: str,
    ) -> DocumentVersionRecord | None: ...

    async def claim_embedding_job(
        self,
        *,
        tenant_id: str,
        job_id: str,
        document_id: str,
        version_id: str,
        stale_before: datetime | None,
    ) -> EmbeddingJobRecord | None: ...

    async def list_chunks_for_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        status: str | None = None,
    ) -> list[ChunkRecord]: ...

    async def mark_embedding_job_embedded(
        self,
        *,
        tenant_id: str,
        job_id: str,
        embedding_metadata: dict[str, object],
    ) -> EmbeddingJobRecord: ...

    async def mark_document_version_retrieval_ready(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        index_metadata: dict[str, object] | None = None,
    ) -> DocumentVersionRecord: ...

    async def mark_embedding_job_failed(
        self,
        *,
        tenant_id: str,
        job_id: str,
        error_code: str,
        status: str,
        next_retry_at: datetime | None = None,
    ) -> EmbeddingJobRecord: ...

    async def commit(self) -> None: ...


@dataclass(frozen=True)
class EmbeddingJobResult:
    status: str
    document_id: str
    version_id: str
    job_id: str
    chunk_count: int
    dim: int | None


class EmbeddingJobService:
    def __init__(
        self,
        *,
        repository: EmbeddingJobRepository,
        provider: EmbeddingProvider,
        audit: AuditPort,
        vector_store: VectorStore | None = None,
        logger: _Logger | StructuredLogger | None = None,
        timeout_seconds: float = 10.0,
        retry_budget: int = 2,
        retry_delay_seconds: int = 60,
        embedding_stale_after_seconds: int = DEFAULT_EMBEDDING_STALE_AFTER_SECONDS,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._audit = audit
        self._vector_store = vector_store
        self._logger = logger
        self._timeout_seconds = timeout_seconds
        self._retry_budget = retry_budget
        self._retry_delay_seconds = retry_delay_seconds
        self._embedding_stale_after_seconds = embedding_stale_after_seconds

    async def embed_job(
        self,
        context: AuthenticatedRequestContext,
        *,
        job_id: str,
        document_id: str,
        version_id: str,
    ) -> EmbeddingJobResult:
        started = time.perf_counter()
        job = await self._load_job(context=context, job_id=job_id)
        version = await self._load_version(context=context, version_id=job.version_id)
        try:
            _ensure_job_matches_request(job=job, document_id=document_id, version_id=version_id)
            _ensure_job_matches_version(job=job, version=version)
            if job.status == EMBEDDED_STATUS:
                if version.status != "retrieval_ready":
                    await self._mark_retrieval_ready_if_indexed(
                        context=context,
                        started=started,
                        job_id=job_id,
                        version=version,
                        embedding_metadata=job.metadata.get("embedding_artifact_summary"),
                    )
                    await self._repository.commit()
                return EmbeddingJobResult(
                    status=EMBEDDED_STATUS,
                    document_id=document_id,
                    version_id=version_id,
                    job_id=job_id,
                    chunk_count=job.chunk_count or 0,
                    dim=job.dim,
                )
            _ensure_version_chunked(version=version)
        except EmbeddingJobError as exc:
            await self._mark_failed(
                context=context,
                started=started,
                job_id=job_id,
                version=version,
                status=FAILED_TERMINAL_STATUS,
                error_code=exc.code,
                retryable=False,
                provider=job.provider,
                model=job.model,
                dim=job.dim,
            )
            raise

        stale_before = datetime.now(tz=UTC) - timedelta(seconds=self._embedding_stale_after_seconds)
        claimed = await self._repository.claim_embedding_job(
            tenant_id=context.auth.tenant_id,
            job_id=job_id,
            document_id=document_id,
            version_id=version_id,
            stale_before=stale_before,
        )
        if claimed is None:
            error = EmbeddingJobError(
                code=EMBEDDING_JOB_INVALID_STATE,
                details={"job_id": job.id, "status": job.status},
            )
            await self._record_rejected(
                context=context,
                started=started,
                job_id=job_id,
                version=version,
                record_status=job.status,
                error_code=error.code,
            )
            raise error

        chunks = await self._repository.list_chunks_for_version(
            tenant_id=context.auth.tenant_id,
            document_id=document_id,
            version_id=version_id,
            status="active",
        )
        if not chunks:
            error = EmbeddingJobError(
                code=EMBEDDING_CHUNKS_NOT_FOUND,
                details={"job_id": job_id, "document_id": document_id, "version_id": version_id},
            )
            await self._mark_failed(
                context=context,
                started=started,
                job_id=job_id,
                version=version,
                status=FAILED_TERMINAL_STATUS,
                error_code=error.code,
                retryable=False,
                provider=claimed.provider,
                model=claimed.model,
                dim=claimed.dim,
            )
            raise error

        self._log(
            "document.embedding.started",
            context=context,
            started=started,
            job_id=job_id,
            version=version,
            status=EMBEDDING_STATUS,
            error_code=None,
            provider=claimed.provider,
            model=claimed.model,
            dim=claimed.dim,
            chunks=chunks,
        )
        await self._repository.commit()

        try:
            response = await self._provider.embed_texts(
                EmbeddingRequest(
                    texts=[chunk.content for chunk in chunks],
                    chunk_ids=[chunk.chunk_id for chunk in chunks],
                    provider=claimed.provider,
                    model=claimed.model,
                    timeout_seconds=self._timeout_seconds,
                    retry_budget=self._retry_budget,
                    rate_limit_key=context.auth.tenant_id,
                    metadata={
                        "tenant_id": context.auth.tenant_id,
                        "document_id": document_id,
                        "version_id": version_id,
                        "job_id": job_id,
                        "chunk_count": len(chunks),
                    },
                )
            )
            _validate_response(response=response, chunks=chunks)
            current_chunks = await self._repository.list_chunks_for_version(
                tenant_id=context.auth.tenant_id,
                document_id=document_id,
                version_id=version_id,
                status="active",
            )
            _ensure_chunk_snapshot_unchanged(expected=chunks, actual=current_chunks)
            vector_index_summary = await self._upsert_vectors(
                response=response,
                chunks=chunks,
                started=started,
            )
        except EmbeddingProviderError as exc:
            await self._mark_failed(
                context=context,
                started=started,
                job_id=job_id,
                version=version,
                status=FAILED_RETRYABLE_STATUS if exc.retryable else FAILED_TERMINAL_STATUS,
                error_code=exc.code,
                retryable=exc.retryable,
                provider=claimed.provider,
                model=claimed.model,
                dim=claimed.dim,
            )
            raise
        except VectorStoreError as exc:
            await self._mark_failed(
                context=context,
                started=started,
                job_id=job_id,
                version=version,
                status=FAILED_RETRYABLE_STATUS if exc.retryable else FAILED_TERMINAL_STATUS,
                error_code=exc.code,
                retryable=exc.retryable,
                provider=claimed.provider,
                model=claimed.model,
                dim=claimed.dim,
            )
            raise
        except EmbeddingJobError as exc:
            await self._mark_failed(
                context=context,
                started=started,
                job_id=job_id,
                version=version,
                status=FAILED_TERMINAL_STATUS,
                error_code=exc.code,
                retryable=False,
                provider=claimed.provider,
                model=claimed.model,
                dim=claimed.dim,
            )
            raise
        except Exception as exc:
            await self._mark_failed(
                context=context,
                started=started,
                job_id=job_id,
                version=version,
                status=FAILED_RETRYABLE_STATUS,
                error_code=EMBEDDING_PROVIDER_FAILED,
                retryable=True,
                provider=claimed.provider,
                model=claimed.model,
                dim=claimed.dim,
            )
            raise EmbeddingProviderError(
                code=EMBEDDING_PROVIDER_FAILED,
                retryable=True,
                details={"reason": "unexpected_provider_failure"},
            ) from exc

        summary = _embedding_summary(
            response=response,
            chunks=chunks,
            vector_index_summary=vector_index_summary,
        )
        embedded = await self._repository.mark_embedding_job_embedded(
            tenant_id=context.auth.tenant_id,
            job_id=job_id,
            embedding_metadata=summary,
        )
        await self._mark_retrieval_ready_if_indexed(
            context=context,
            started=started,
            job_id=job_id,
            version=version,
            embedding_metadata=summary,
        )
        self._log(
            "document.embedding.completed",
            context=context,
            started=started,
            job_id=job_id,
            version=version,
            status=EMBEDDED_STATUS,
            error_code=None,
            provider=response.provider,
            model=response.model,
            dim=response.dim,
            chunks=chunks,
        )
        await self._record_audit(
            context=context,
            status=AuditStatus.SUCCESS,
            started=started,
            job_id=job_id,
            version=version,
            record_status=EMBEDDED_STATUS,
            error_code=None,
            provider=response.provider,
            model=response.model,
            dim=response.dim,
            chunks=chunks,
        )
        await self._repository.commit()
        return EmbeddingJobResult(
            status=embedded.status,
            document_id=document_id,
            version_id=version_id,
            job_id=job_id,
            chunk_count=embedded.chunk_count or len(chunks),
            dim=embedded.dim,
        )

    async def _upsert_vectors(
        self,
        *,
        response: EmbeddingResponse,
        chunks: list[ChunkRecord],
        started: float,
    ) -> dict[str, object] | None:
        if self._vector_store is None:
            return None
        result = await self._vector_store.upsert(
            map_embedding_response_to_vector_records(response=response, chunks=chunks)
        )
        return {
            "stage": "vector_indexed",
            "status": "indexed",
            "vector_count": result.upserted_count,
            "provider": result.embedding_provider,
            "model": result.embedding_model,
            "version": result.embedding_version,
            "dim": result.embedding_dim,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }

    async def _mark_retrieval_ready_if_indexed(
        self,
        *,
        context: AuthenticatedRequestContext,
        started: float,
        job_id: str,
        version: DocumentVersionRecord,
        embedding_metadata: object,
    ) -> None:
        if not isinstance(embedding_metadata, dict):
            return
        vector_summary = embedding_metadata.get("vector_index_summary")
        if not isinstance(vector_summary, dict) or vector_summary.get("status") != "indexed":
            return
        ready_version = await self._repository.mark_document_version_retrieval_ready(
            tenant_id=context.auth.tenant_id,
            document_id=version.document_id,
            version_id=version.id,
            index_metadata=vector_summary,
        )
        await self._record_index_ready_audit(
            context=context,
            started=started,
            job_id=job_id,
            version=ready_version,
            vector_summary=vector_summary,
        )

    async def _record_index_ready_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        started: float,
        job_id: str,
        version: DocumentVersionRecord,
        vector_summary: dict[str, object],
    ) -> None:
        metadata = {
            "request_id": context.request_id,
            "trace_id": context.trace_id,
            "tenant_id": context.auth.tenant_id,
            "user_id": context.auth.user_id,
            "document_id": version.document_id,
            "version_id": version.id,
            "job_id": job_id,
            "status": "retrieval_ready",
            "error_code": None,
            "chunk_count": vector_summary.get("vector_count"),
            "vector_count": vector_summary.get("vector_count"),
            "provider": vector_summary.get("provider"),
            "model": vector_summary.get("model"),
            "dim": vector_summary.get("dim"),
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        await self._audit.record(
            AuditEvent(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                action="document.index_ready",
                resource=AuditResource(
                    type="document",
                    id=version.document_id,
                    metadata=metadata,
                ),
                status=AuditStatus.SUCCESS,
                latency_ms=(time.perf_counter() - started) * 1000,
                error_code=None,
                metadata=metadata,
            )
        )

    async def _load_job(
        self,
        *,
        context: AuthenticatedRequestContext,
        job_id: str,
    ) -> EmbeddingJobRecord:
        job = await self._repository.get_embedding_job(
            tenant_id=context.auth.tenant_id,
            job_id=job_id,
        )
        if job is None:
            raise EmbeddingJobError(code=EMBEDDING_JOB_NOT_FOUND, details={"job_id": job_id})
        return job

    async def _load_version(
        self,
        *,
        context: AuthenticatedRequestContext,
        version_id: str,
    ) -> DocumentVersionRecord:
        version = await self._repository.get_version(
            tenant_id=context.auth.tenant_id,
            version_id=version_id,
        )
        if version is None:
            raise EmbeddingJobError(
                code=EMBEDDING_JOB_PAYLOAD_MISMATCH,
                details={"version_id": version_id},
            )
        return version

    async def _mark_failed(
        self,
        *,
        context: AuthenticatedRequestContext,
        started: float,
        job_id: str,
        version: DocumentVersionRecord,
        status: str,
        error_code: str,
        retryable: bool,
        provider: str | None,
        model: str | None,
        dim: int | None,
    ) -> None:
        await self._repository.mark_embedding_job_failed(
            tenant_id=context.auth.tenant_id,
            job_id=job_id,
            status=status,
            error_code=error_code,
            next_retry_at=(
                datetime.now(tz=UTC) + timedelta(seconds=self._retry_delay_seconds)
                if retryable
                else None
            ),
        )
        self._log(
            "document.embedding.failed",
            context=context,
            started=started,
            job_id=job_id,
            version=version,
            status=status,
            error_code=error_code,
            provider=provider,
            model=model,
            dim=dim,
            chunks=None,
        )
        await self._record_audit(
            context=context,
            status=AuditStatus.FAILURE,
            started=started,
            job_id=job_id,
            version=version,
            record_status=status,
            error_code=error_code,
            provider=provider,
            model=model,
            dim=dim,
            chunks=None,
        )
        await self._repository.commit()

    async def _record_rejected(
        self,
        *,
        context: AuthenticatedRequestContext,
        started: float,
        job_id: str,
        version: DocumentVersionRecord,
        record_status: str,
        error_code: str,
    ) -> None:
        self._log(
            "document.embedding.rejected",
            context=context,
            started=started,
            job_id=job_id,
            version=version,
            status=record_status,
            error_code=error_code,
            provider=None,
            model=None,
            dim=None,
            chunks=None,
        )
        await self._record_audit(
            context=context,
            status=AuditStatus.FAILURE,
            started=started,
            job_id=job_id,
            version=version,
            record_status=record_status,
            error_code=error_code,
            provider=None,
            model=None,
            dim=None,
            chunks=None,
        )

    def _log(
        self,
        event: str,
        *,
        context: AuthenticatedRequestContext,
        started: float,
        job_id: str,
        version: DocumentVersionRecord,
        status: str,
        error_code: str | None,
        provider: str | None,
        model: str | None,
        dim: int | None,
        chunks: list[ChunkRecord] | None,
    ) -> None:
        if self._logger is None:
            return
        self._logger.info(
            event,
            **_safe_event_metadata(
                context=context,
                started=started,
                job_id=job_id,
                version=version,
                status=status,
                error_code=error_code,
                provider=provider,
                model=model,
                dim=dim,
                chunks=chunks,
            ),
        )

    async def _record_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        status: AuditStatus,
        started: float,
        job_id: str,
        version: DocumentVersionRecord,
        record_status: str,
        error_code: str | None,
        provider: str | None,
        model: str | None,
        dim: int | None,
        chunks: list[ChunkRecord] | None,
    ) -> None:
        metadata = _safe_event_metadata(
            context=context,
            started=started,
            job_id=job_id,
            version=version,
            status=record_status,
            error_code=error_code,
            provider=provider,
            model=model,
            dim=dim,
            chunks=chunks,
        )
        await self._audit.record(
            AuditEvent(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                action="document.embedding",
                resource=AuditResource(
                    type="document",
                    id=version.document_id,
                    metadata=metadata,
                ),
                status=status,
                latency_ms=(time.perf_counter() - started) * 1000,
                error_code=error_code,
                metadata=metadata,
            )
        )


def _ensure_job_matches_request(
    *,
    job: EmbeddingJobRecord,
    document_id: str,
    version_id: str,
) -> None:
    if job.document_id != document_id or job.version_id != version_id:
        raise EmbeddingJobError(
            code=EMBEDDING_JOB_PAYLOAD_MISMATCH,
            details={"job_id": job.id, "reason": "payload_id_mismatch"},
        )


def _ensure_job_matches_version(
    *,
    job: EmbeddingJobRecord,
    version: DocumentVersionRecord,
) -> None:
    if job.document_id != version.document_id or job.version_id != version.id:
        raise EmbeddingJobError(
            code=EMBEDDING_JOB_PAYLOAD_MISMATCH,
            details={"job_id": job.id, "reason": "version_mismatch"},
        )


def _ensure_version_chunked(*, version: DocumentVersionRecord) -> None:
    if version.status != CHUNKED_STATUS:
        raise EmbeddingJobError(
            code=EMBEDDING_DOCUMENT_VERSION_NOT_CHUNKED,
            details={"document_id": version.document_id, "version_id": version.id},
        )


def _validate_response(*, response: EmbeddingResponse, chunks: list[ChunkRecord]) -> None:
    if len(response.vectors) != len(chunks):
        raise EmbeddingJobError(
            code=EMBEDDING_BATCH_SIZE_MISMATCH,
            details={"expected": len(chunks), "actual": len(response.vectors)},
        )
    chunk_ids = [chunk.chunk_id for chunk in chunks]
    for expected_index, vector in enumerate(response.vectors):
        if vector.index != expected_index:
            raise EmbeddingJobError(
                code=EMBEDDING_BATCH_SIZE_MISMATCH,
                details={"reason": "vector_index_mismatch", "index": vector.index},
            )
        if vector.chunk_id is not None and vector.chunk_id != chunk_ids[expected_index]:
            raise EmbeddingJobError(
                code=EMBEDDING_BATCH_SIZE_MISMATCH,
                details={"reason": "vector_chunk_id_mismatch", "chunk_id": vector.chunk_id},
            )
        if not vector.vector:
            raise EmbeddingJobError(
                code=EMBEDDING_VECTOR_EMPTY,
                details={"index": vector.index, "chunk_id": vector.chunk_id},
            )
        if len(vector.vector) != response.dim:
            raise EmbeddingJobError(
                code=EMBEDDING_VECTOR_DIMENSION_MISMATCH,
                details={
                    "index": vector.index,
                    "expected_dim": response.dim,
                    "actual_dim": len(vector.vector),
                },
            )


def _ensure_chunk_snapshot_unchanged(
    *,
    expected: list[ChunkRecord],
    actual: list[ChunkRecord],
) -> None:
    expected_snapshot = [(chunk.chunk_id, chunk.checksum) for chunk in expected]
    actual_snapshot = [(chunk.chunk_id, chunk.checksum) for chunk in actual]
    if actual_snapshot != expected_snapshot:
        raise EmbeddingJobError(
            code=EMBEDDING_CHUNK_SNAPSHOT_MISMATCH,
            details={
                "expected_count": len(expected_snapshot),
                "actual_count": len(actual_snapshot),
            },
        )


def _embedding_summary(
    *,
    response: EmbeddingResponse,
    chunks: list[ChunkRecord],
    vector_index_summary: dict[str, object] | None = None,
) -> dict[str, object]:
    token_counts = [chunk.token_count for chunk in chunks]
    summary: dict[str, object] = {
        "stage": "embedded",
        "provider": response.provider,
        "model": response.model,
        "version": response.version,
        "dim": response.dim,
        "chunk_count": len(chunks),
        "token_count_min": min(token_counts),
        "token_count_max": max(token_counts),
        "usage": _safe_usage_summary(response.usage),
    }
    if vector_index_summary is not None:
        summary["vector_index_summary"] = vector_index_summary
    return summary


def _safe_usage_summary(usage: dict[str, object]) -> dict[str, int | float]:
    allowed_keys = {
        "text_count",
        "total_characters",
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "embedding_tokens",
        "request_count",
    }
    safe: dict[str, int | float] = {}
    for key, value in usage.items():
        normalized_key = key.strip().lower()
        if normalized_key not in allowed_keys:
            continue
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        if isinstance(value, float) and not isfinite(value):
            continue
        safe[normalized_key] = value
    return safe


def _safe_event_metadata(
    *,
    context: AuthenticatedRequestContext,
    started: float,
    job_id: str,
    version: DocumentVersionRecord,
    status: str,
    error_code: str | None,
    provider: str | None,
    model: str | None,
    dim: int | None,
    chunks: list[ChunkRecord] | None,
) -> dict[str, object]:
    token_counts = [chunk.token_count for chunk in chunks] if chunks else []
    return {
        "request_id": context.request_id,
        "trace_id": context.trace_id,
        "tenant_id": context.auth.tenant_id,
        "user_id": context.auth.user_id,
        "document_id": version.document_id,
        "version_id": version.id,
        "job_id": job_id,
        "provider": provider,
        "model": model,
        "dim": dim,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "status": status,
        "error_code": error_code,
        "chunk_count": len(chunks) if chunks is not None else None,
        "token_count_min": min(token_counts) if token_counts else None,
        "token_count_max": max(token_counts) if token_counts else None,
    }
