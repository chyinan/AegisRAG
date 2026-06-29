from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError
from packages.common.logging import StructuredLogger
from packages.data.dto import (
    ChunkRecord,
    DocumentVersionRecord,
    EmbeddingJobRecord,
    EnqueuedJob,
    IngestionJobRecord,
    StoredDocumentContent,
)
from packages.data.exceptions import DocumentStorageReadError
from packages.data.queue.contracts import QueuePayload
from packages.data.queue.embedding import build_embedding_queue_payload
from packages.ingestion.chunkers import FixedSizeChunker
from packages.ingestion.domain import Chunk, ParsedDocument, ParseRequest
from packages.ingestion.exceptions import (
    DOCUMENT_PARSE_FAILED,
    TERMINAL_PARSE_ERROR_CODES,
    DocumentChunkError,
    DocumentParseError,
    GenericDocumentParseError,
)
from packages.ingestion.parsers.registry import ParserRegistry
from packages.ingestion.ports import AsyncChunker, Chunker

PARSED_STATUS = "parsed"
PARSING_STATUS = "parsing"
FAILED_TERMINAL_STATUS = "failed_terminal"
FAILED_RETRYABLE_STATUS = "failed_retryable"
PARSER_STARTABLE_STATUSES = {"uploaded", "queued", FAILED_RETRYABLE_STATUS}
DEFAULT_PARSING_STALE_AFTER_SECONDS = 15 * 60


class _Logger(Protocol):
    def info(self, event: str, **kwargs: object) -> object: ...


class ParseJobRepository(Protocol):
    async def get_ingestion_job(
        self,
        *,
        tenant_id: str,
        job_id: str,
    ) -> IngestionJobRecord | None: ...

    async def get_version(
        self,
        *,
        tenant_id: str,
        version_id: str,
    ) -> DocumentVersionRecord | None: ...

    async def mark_ingestion_job_parsing(
        self,
        *,
        tenant_id: str,
        job_id: str,
    ) -> IngestionJobRecord: ...

    async def claim_ingestion_job_parsing(
        self,
        *,
        tenant_id: str,
        job_id: str,
        document_id: str,
        version_id: str,
        stale_before: datetime | None,
    ) -> IngestionJobRecord | None: ...

    async def mark_ingestion_job_parsed(
        self,
        *,
        tenant_id: str,
        job_id: str,
        parsed_metadata: dict[str, object],
    ) -> IngestionJobRecord: ...

    async def replace_chunks_for_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        chunks: list[ChunkRecord],
    ) -> list[ChunkRecord]: ...

    async def mark_ingestion_job_chunked(
        self,
        *,
        tenant_id: str,
        job_id: str,
        chunk_metadata: dict[str, object],
    ) -> IngestionJobRecord: ...

    async def create_embedding_job(
        self,
        *,
        job: EmbeddingJobRecord,
    ) -> EmbeddingJobRecord: ...

    async def mark_ingestion_job_failed(
        self,
        *,
        tenant_id: str,
        job_id: str,
        error_code: str,
        status: str = "failed_retryable",
    ) -> IngestionJobRecord: ...

    async def commit(self) -> None: ...


class DocumentContentReader(Protocol):
    async def get_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        object_key: str,
    ) -> StoredDocumentContent: ...


class EmbeddingJobQueue(Protocol):
    async def enqueue_embedding_job(self, payload: QueuePayload) -> EnqueuedJob: ...


class IngestionParseResult(Protocol):
    status: str
    document_id: str
    version_id: str
    job_id: str
    section_count: int


class ParseJobResult:
    def __init__(
        self,
        *,
        status: str,
        document_id: str,
        version_id: str,
        job_id: str,
        section_count: int,
        chunk_count: int | None = None,
        embedding_job_id: str | None = None,
    ) -> None:
        self.status = status
        self.document_id = document_id
        self.version_id = version_id
        self.job_id = job_id
        self.section_count = section_count
        self.chunk_count = chunk_count
        self.embedding_job_id = embedding_job_id


class IngestionParseService:
    def __init__(
        self,
        *,
        repository: ParseJobRepository,
        object_storage: DocumentContentReader,
        audit: AuditPort,
        parser_registry: ParserRegistry | None = None,
        chunker: Chunker | AsyncChunker | None = None,
        embedding_queue: EmbeddingJobQueue | None = None,
        embedding_provider: str | None = None,
        embedding_model: str | None = None,
        embedding_version: str | None = None,
        embedding_dim: int | None = None,
        logger: _Logger | StructuredLogger | None = None,
        parsing_stale_after_seconds: int = DEFAULT_PARSING_STALE_AFTER_SECONDS,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._object_storage = object_storage
        self._audit = audit
        self._parser_registry = parser_registry or ParserRegistry.default()
        self._chunker = chunker or FixedSizeChunker()
        self._embedding_queue = embedding_queue
        self._embedding_provider = embedding_provider
        self._embedding_model = embedding_model
        self._embedding_version = embedding_version
        self._embedding_dim = embedding_dim
        self._logger = logger
        self._parsing_stale_after_seconds = parsing_stale_after_seconds
        self._id_factory = id_factory or (lambda: str(uuid4()))

    async def parse_job(
        self,
        context: AuthenticatedRequestContext,
        *,
        job_id: str,
        document_id: str,
        version_id: str,
    ) -> ParseJobResult:
        started = time.perf_counter()
        job = await self._load_job(context=context, job_id=job_id)
        version = await self._load_version(context=context, version_id=job.version_id)
        try:
            self._ensure_job_matches_version(job=job, version=version)
        except DocumentParseError as exc:
            await self._mark_failed(
                context=context,
                started=started,
                job_id=job_id,
                version=version,
                status=_failure_status(exc.code),
                error_code=exc.code,
            )
            raise

        if not _job_matches_request(job=job, document_id=document_id, version_id=version_id):
            error = GenericDocumentParseError(
                details={"job_id": job.id, "reason": "payload_id_mismatch"}
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

        if job.status == PARSED_STATUS and not self._should_chunk_and_embed:
            return ParseJobResult(
                status=PARSED_STATUS,
                document_id=document_id,
                version_id=version_id,
                job_id=job_id,
                section_count=_section_count_from_metadata(version.metadata),
            )

        try:
            parser = self._parser_registry.get(version.source_type)
        except DocumentParseError as exc:
            await self._mark_failed(
                context=context,
                started=started,
                job_id=job_id,
                version=version,
                status=_failure_status(exc.code),
                error_code=exc.code,
            )
            raise

        should_rechunk_existing_parsed = (
            job.status == PARSED_STATUS and self._should_chunk_and_embed
        )
        if not should_rechunk_existing_parsed:
            stale_before = datetime.now(tz=UTC) - timedelta(
                seconds=self._parsing_stale_after_seconds
            )
            claimed = await self._repository.claim_ingestion_job_parsing(
                tenant_id=context.auth.tenant_id,
                job_id=job_id,
                document_id=document_id,
                version_id=version_id,
                stale_before=stale_before,
            )
            if claimed is None:
                error = GenericDocumentParseError(
                    details={"job_id": job.id, "reason": "claim_not_acquired", "status": job.status}
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

            self._log(
                "document.parse.started",
                context=context,
                started=started,
                job_id=job_id,
                version=version,
                status=PARSING_STATUS,
                error_code=None,
                parsed=None,
            )
            await self._record_audit(
                context=context,
                status=AuditStatus.SUCCESS,
                started=started,
                job_id=job_id,
                version=version,
                record_status=PARSING_STATUS,
                error_code=None,
                parsed=None,
            )
            await self._repository.commit()

        try:
            content = await self._object_storage.get_document(
                tenant_id=context.auth.tenant_id,
                document_id=document_id,
                version_id=version_id,
                object_key=version.object_key,
            )
            _ensure_content_matches_version(content=content, version=version)
            parsed = await parser.parse(
                ParseRequest(
                    tenant_id=context.auth.tenant_id,
                    document_id=document_id,
                    version_id=version_id,
                    source_type=version.source_type,
                    source_uri=version.source_uri,
                    filename=version.filename,
                    content=content.content,
                    acl=version.acl,
                    metadata=version.metadata,
                    checksum=version.checksum,
                )
            )
            _ensure_parsed_matches_request(parsed=parsed, version=version)
        except DocumentParseError as exc:
            await self._mark_failed(
                context=context,
                started=started,
                job_id=job_id,
                version=version,
                status=_failure_status(exc.code),
                error_code=exc.code,
            )
            raise
        except DocumentStorageReadError as exc:
            await self._mark_failed(
                context=context,
                started=started,
                job_id=job_id,
                version=version,
                status=FAILED_RETRYABLE_STATUS,
                error_code=exc.code,
            )
            raise
        except DomainError as exc:
            await self._mark_failed(
                context=context,
                started=started,
                job_id=job_id,
                version=version,
                status=FAILED_RETRYABLE_STATUS,
                error_code=exc.code,
            )
            raise
        except Exception as exc:
            await self._mark_failed(
                context=context,
                started=started,
                job_id=job_id,
                version=version,
                status=FAILED_RETRYABLE_STATUS,
                error_code=DOCUMENT_PARSE_FAILED,
            )
            raise GenericDocumentParseError(
                details={"reason": "unexpected_parser_failure"}
            ) from exc

        if not should_rechunk_existing_parsed:
            summary = _parsed_summary(parsed)
            await self._repository.mark_ingestion_job_parsed(
                tenant_id=context.auth.tenant_id,
                job_id=job_id,
                parsed_metadata=summary,
            )
        self._log(
            "document.parse.completed",
            context=context,
            started=started,
            job_id=job_id,
            version=version,
            status=PARSED_STATUS,
            error_code=None,
            parsed=parsed,
        )
        await self._record_audit(
            context=context,
            status=AuditStatus.SUCCESS,
            started=started,
            job_id=job_id,
            version=version,
            record_status=PARSED_STATUS,
            error_code=None,
            parsed=parsed,
        )
        await self._repository.commit()

        chunk_count: int | None = None
        embedding_job_id: str | None = None
        result_status = PARSED_STATUS
        if self._should_chunk_and_embed:
            chunk_count, embedding_job_id = await self._chunk_and_enqueue_embedding(
                context=context,
                started=started,
                job_id=job_id,
                version=version,
                parsed=parsed,
            )
            result_status = "chunked"

        return ParseJobResult(
            status=result_status,
            document_id=document_id,
            version_id=version_id,
            job_id=job_id,
            section_count=len(parsed.sections),
            chunk_count=chunk_count,
            embedding_job_id=embedding_job_id,
        )

    @property
    def _should_chunk_and_embed(self) -> bool:
        return (
            self._embedding_queue is not None
            and self._embedding_provider is not None
            and self._embedding_model is not None
        )

    async def _chunk_and_enqueue_embedding(
        self,
        *,
        context: AuthenticatedRequestContext,
        started: float,
        job_id: str,
        version: DocumentVersionRecord,
        parsed: ParsedDocument,
    ) -> tuple[int, str]:
        try:
            if inspect.iscoroutinefunction(self._chunker.split):
                chunks = await self._chunker.split(parsed)  # type: ignore[union-attr]
            else:
                chunks = self._chunker.split(parsed)  # type: ignore[union-attr]
            chunk_records = [
                _chunk_record_from_domain(chunk, created_by=context.auth.user_id)
                for chunk in chunks
            ]
            await self._repository.replace_chunks_for_version(
                tenant_id=context.auth.tenant_id,
                document_id=version.document_id,
                version_id=version.id,
                chunks=chunk_records,
            )
            chunk_summary = _chunk_summary(chunk_records)
            await self._repository.mark_ingestion_job_chunked(
                tenant_id=context.auth.tenant_id,
                job_id=job_id,
                chunk_metadata=chunk_summary,
            )
            embedding_job_id = self._id_factory()
            embedding_job = EmbeddingJobRecord(
                id=embedding_job_id,
                tenant_id=context.auth.tenant_id,
                created_by=context.auth.user_id,
                status="queued",
                document_id=version.document_id,
                version_id=version.id,
                provider=self._embedding_provider or "",
                model=self._embedding_model or "",
                version=self._embedding_version,
                dim=self._embedding_dim,
                chunk_count=len(chunk_records),
                metadata={
                    "source_ingestion_job_id": job_id,
                    "chunk_artifact_summary": chunk_summary,
                },
            )
            await self._repository.create_embedding_job(job=embedding_job)
            await self._repository.commit()
            queue = self._embedding_queue
            if queue is None:
                raise GenericDocumentParseError(details={"reason": "embedding_queue_missing"})
            await queue.enqueue_embedding_job(
                build_embedding_queue_payload(
                    context=context,
                    job_id=embedding_job_id,
                    document_id=version.document_id,
                    version_id=version.id,
                )
            )
        except DocumentChunkError as exc:
            await self._mark_failed(
                context=context,
                started=started,
                job_id=job_id,
                version=version,
                status=_failure_status(exc.code),
                error_code=exc.code,
            )
            raise
        self._log(
            "document.chunk.completed",
            context=context,
            started=started,
            job_id=job_id,
            version=version,
            status="chunked",
            error_code=None,
            parsed=None,
        )
        return len(chunk_records), embedding_job_id

    async def _load_job(
        self,
        *,
        context: AuthenticatedRequestContext,
        job_id: str,
    ) -> IngestionJobRecord:
        job = await self._repository.get_ingestion_job(
            tenant_id=context.auth.tenant_id,
            job_id=job_id,
        )
        if job is None:
            raise GenericDocumentParseError(details={"job_id": job_id})
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
            raise GenericDocumentParseError(details={"version_id": version_id})
        return version

    def _ensure_job_matches_version(
        self,
        *,
        job: IngestionJobRecord,
        version: DocumentVersionRecord,
    ) -> None:
        if job.document_id != version.document_id or job.version_id != version.id:
            raise GenericDocumentParseError(
                details={"job_id": job.id, "reason": "version_mismatch"}
            )

    def _ensure_job_can_start(self, job: IngestionJobRecord) -> None:
        if job.status not in PARSER_STARTABLE_STATUSES:
            raise GenericDocumentParseError(
                details={"job_id": job.id, "reason": "invalid_status", "status": job.status}
            )

    async def _mark_failed(
        self,
        *,
        context: AuthenticatedRequestContext,
        started: float,
        job_id: str,
        version: DocumentVersionRecord,
        status: str,
        error_code: str,
    ) -> None:
        await self._repository.mark_ingestion_job_failed(
            tenant_id=context.auth.tenant_id,
            job_id=job_id,
            status=status,
            error_code=error_code,
        )
        self._log(
            "document.parse.failed",
            context=context,
            started=started,
            job_id=job_id,
            version=version,
            status=status,
            error_code=error_code,
            parsed=None,
        )
        await self._record_audit(
            context=context,
            status=AuditStatus.FAILURE,
            started=started,
            job_id=job_id,
            version=version,
            record_status=status,
            error_code=error_code,
            parsed=None,
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
            "document.parse.rejected",
            context=context,
            started=started,
            job_id=job_id,
            version=version,
            status=record_status,
            error_code=error_code,
            parsed=None,
        )
        await self._record_audit(
            context=context,
            status=AuditStatus.FAILURE,
            started=started,
            job_id=job_id,
            version=version,
            record_status=record_status,
            error_code=error_code,
            parsed=None,
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
        parsed: ParsedDocument | None,
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
                parsed=parsed,
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
        parsed: ParsedDocument | None,
    ) -> None:
        metadata = _safe_event_metadata(
            context=context,
            started=started,
            job_id=job_id,
            version=version,
            status=record_status,
            error_code=error_code,
            parsed=parsed,
        )
        await self._audit.record(
            AuditEvent(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                action="document.parse",
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


def _failure_status(error_code: str) -> str:
    if error_code in TERMINAL_PARSE_ERROR_CODES:
        return FAILED_TERMINAL_STATUS
    return FAILED_RETRYABLE_STATUS


def _parsed_summary(parsed: ParsedDocument) -> dict[str, object]:
    summary: dict[str, object] = {
        "stage": "parsed",
        "section_count": len(parsed.sections),
        "checksum": parsed.checksum,
    }
    for key in ("page_count", "page_ranges", "heading_count", "page_metadata"):
        value = parsed.metadata.get(key)
        if value is not None:
            summary[key] = value
    return summary


def _chunk_record_from_domain(chunk: Chunk, *, created_by: str) -> ChunkRecord:
    return ChunkRecord(
        tenant_id=chunk.tenant_id,
        document_id=chunk.document_id,
        version_id=chunk.version_id,
        chunk_id=chunk.chunk_id,
        created_by=created_by,
        status="active",
        source_type=chunk.source_type,
        source_uri=chunk.source_uri,
        title_path=list(chunk.title_path),
        content=chunk.content,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        token_count=chunk.token_count,
        acl=dict(chunk.acl),
        checksum=chunk.checksum,
        section_ids=list(chunk.section_ids),
        metadata=dict(chunk.metadata),
    )


def _chunk_summary(chunks: list[ChunkRecord]) -> dict[str, object]:
    token_counts = [chunk.token_count for chunk in chunks]
    return {
        "stage": "chunked",
        "chunker": "fixed_size",
        "chunk_count": len(chunks),
        "token_count": sum(token_counts),
        "min_token_count": min(token_counts),
        "max_token_count": max(token_counts),
    }


def _section_count_from_metadata(metadata: dict[str, object]) -> int:
    summary = metadata.get("parsed_artifact_summary")
    if not isinstance(summary, dict):
        return 0
    section_count = summary.get("section_count")
    if isinstance(section_count, int) and section_count >= 0:
        return section_count
    return 0


def _ensure_content_matches_version(
    *,
    content: StoredDocumentContent,
    version: DocumentVersionRecord,
) -> None:
    if content.byte_size != version.byte_size or content.checksum != version.checksum:
        raise DocumentStorageReadError(
            details={
                "document_id": version.document_id,
                "version_id": version.id,
                "reason": "object_mismatch",
            }
        )


def _ensure_parsed_matches_request(
    *,
    parsed: ParsedDocument,
    version: DocumentVersionRecord,
) -> None:
    if (
        parsed.tenant_id != version.tenant_id
        or parsed.document_id != version.document_id
        or parsed.version_id != version.id
        or parsed.source_type != version.source_type
        or parsed.checksum != version.checksum
    ):
        raise GenericDocumentParseError(
            details={
                "document_id": version.document_id,
                "version_id": version.id,
                "reason": "parsed_document_mismatch",
            }
        )
    for section in parsed.sections:
        if (
            section.tenant_id != version.tenant_id
            or section.document_id != version.document_id
            or section.version_id != version.id
            or section.source_type != version.source_type
            or section.source_uri != version.source_uri
            or section.acl != version.acl
        ):
            raise GenericDocumentParseError(
                details={
                    "document_id": version.document_id,
                    "version_id": version.id,
                    "section_id": section.section_id,
                    "reason": "parsed_section_mismatch",
                }
            )


def _job_matches_request(
    *,
    job: IngestionJobRecord,
    document_id: str,
    version_id: str,
) -> bool:
    return job.document_id == document_id and job.version_id == version_id


def _safe_event_metadata(
    *,
    context: AuthenticatedRequestContext,
    started: float,
    job_id: str,
    version: DocumentVersionRecord,
    status: str,
    error_code: str | None,
    parsed: ParsedDocument | None,
) -> dict[str, object]:
    section_count = len(parsed.sections) if parsed is not None else None
    metadata: dict[str, object] = {
        "request_id": context.request_id,
        "trace_id": context.trace_id,
        "tenant_id": context.auth.tenant_id,
        "user_id": context.auth.user_id,
        "document_id": version.document_id,
        "version_id": version.id,
        "job_id": job_id,
        "source_type": version.source_type,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "status": status,
        "error_code": error_code,
        "byte_size": version.byte_size,
        "checksum": version.checksum,
        "section_count": section_count,
    }
    if parsed is not None:
        for key in ("page_count", "page_ranges", "heading_count", "page_metadata"):
            value = parsed.metadata.get(key)
            if value is not None:
                metadata[key] = value
    return metadata
