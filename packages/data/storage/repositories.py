from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TypeVar, cast

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from packages.data.dto import (
    ChunkRecord,
    DocumentRecord,
    DocumentVersionRecord,
    DocumentVersionStatusResult,
    EmbeddingJobRecord,
    IngestionJobRecord,
)
from packages.data.storage.exceptions import StorageError
from packages.data.storage.models import (
    ChunkModel,
    DocumentModel,
    DocumentVersionModel,
    EmbeddingJobModel,
    IngestionJobModel,
)
from packages.memory.storage.models import ChatMessageModel
from packages.retrieval.storage.models import RetrievalLogModel

_ModelT = TypeVar("_ModelT")


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_upload_records(
        self,
        *,
        document: DocumentRecord,
        version: DocumentVersionRecord,
        job: IngestionJobRecord,
    ) -> tuple[DocumentRecord, DocumentVersionRecord, IngestionJobRecord]:
        document_model = _document_model(document)
        version_model = _version_model(version)
        job_model = _job_model(job)
        self._session.add_all([document_model, version_model, job_model])
        try:
            await self._session.flush()
            await self._session.refresh(document_model)
            await self._session.refresh(version_model)
            await self._session.refresh(job_model)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="DOCUMENT_STORAGE_WRITE_FAILED",
                message="Document metadata storage write failed.",
                details={"tenant_id": document.tenant_id, "document_id": document.id},
            ) from exc
        return (
            document_record_from_model(document_model),
            document_version_record_from_model(version_model),
            ingestion_job_record_from_model(job_model),
        )

    async def get_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
    ) -> DocumentRecord | None:
        try:
            model = await self._session.scalar(
                select(DocumentModel).where(
                    DocumentModel.tenant_id == tenant_id,
                    DocumentModel.id == document_id,
                )
            )
        except SQLAlchemyError as exc:
            raise StorageError(
                code="DOCUMENT_READ_FAILED",
                message="Document read failed.",
                details={"tenant_id": tenant_id, "document_id": document_id},
            ) from exc
        if model is None:
            return None
        return document_record_from_model(model)

    async def create_document_version_records(
        self,
        *,
        version: DocumentVersionRecord,
        job: IngestionJobRecord,
    ) -> tuple[DocumentVersionRecord, IngestionJobRecord]:
        document = await self._get_document_model(
            tenant_id=version.tenant_id,
            document_id=version.document_id,
        )
        if document.deleted_at is not None or document.status == "deleted":
            raise StorageError(
                code="DOCUMENT_NOT_FOUND",
                message="Document was not found.",
                details={"tenant_id": version.tenant_id, "document_id": version.document_id},
            )
        if job.tenant_id != version.tenant_id or job.document_id != version.document_id:
            raise StorageError(
                code="DOCUMENT_VERSION_SCOPE_MISMATCH",
                message="Document version and ingestion job scope does not match.",
                details={"tenant_id": version.tenant_id, "document_id": version.document_id},
            )
        version_model = _version_model(version)
        job_model = _job_model(job)
        document.status = version.status
        document.metadata_ = _document_latest_metadata(
            document.metadata_,
            latest_version=version_model,
        )
        self._session.add_all([version_model, job_model])
        try:
            await self._session.flush()
            await self._session.refresh(version_model)
            await self._session.refresh(job_model)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="DOCUMENT_STORAGE_WRITE_FAILED",
                message="Document version storage write failed.",
                details={"tenant_id": version.tenant_id, "document_id": version.document_id},
            ) from exc
        return (
            document_version_record_from_model(version_model),
            ingestion_job_record_from_model(job_model),
        )

    async def mark_ingestion_job_queued(
        self,
        *,
        tenant_id: str,
        job_id: str,
        queue_job_id: str | None,
    ) -> IngestionJobRecord:
        model = await self._get_job(tenant_id=tenant_id, job_id=job_id)
        model.queue_job_id = queue_job_id
        return await self._flush_job(model, code="INGESTION_JOB_UPDATE_FAILED")

    async def mark_ingestion_job_failed(
        self,
        *,
        tenant_id: str,
        job_id: str,
        error_code: str,
        status: str = "failed_retryable",
    ) -> IngestionJobRecord:
        model = await self._get_job(tenant_id=tenant_id, job_id=job_id)
        version = await self._get_version_model(
            tenant_id=tenant_id,
            version_id=model.version_id,
        )
        document = await self._get_document_model(
            tenant_id=tenant_id,
            document_id=model.document_id,
        )
        model.status = status
        model.error_code = error_code
        model.last_attempt_at = datetime.now(tz=UTC)
        version.status = status
        document.status = status
        return await self._flush_job(model, code="INGESTION_JOB_UPDATE_FAILED")

    async def get_ingestion_job(
        self,
        *,
        tenant_id: str,
        job_id: str,
    ) -> IngestionJobRecord | None:
        try:
            model = await self._session.scalar(
                select(IngestionJobModel).where(
                    IngestionJobModel.tenant_id == tenant_id,
                    IngestionJobModel.id == job_id,
                )
            )
        except SQLAlchemyError as exc:
            raise StorageError(
                code="INGESTION_JOB_READ_FAILED",
                message="Ingestion job read failed.",
                details={"tenant_id": tenant_id, "job_id": job_id},
            ) from exc
        if model is None:
            return None
        return ingestion_job_record_from_model(model)

    async def mark_ingestion_job_parsing(
        self,
        *,
        tenant_id: str,
        job_id: str,
    ) -> IngestionJobRecord:
        model = await self._get_job(tenant_id=tenant_id, job_id=job_id)
        model.status = "parsing"
        model.error_code = None
        model.attempt_count += 1
        model.last_attempt_at = datetime.now(tz=UTC)
        return await self._flush_job(model, code="INGESTION_JOB_UPDATE_FAILED")

    async def claim_ingestion_job_parsing(
        self,
        *,
        tenant_id: str,
        job_id: str,
        document_id: str,
        version_id: str,
        stale_before: datetime | None,
    ) -> IngestionJobRecord | None:
        now = datetime.now(tz=UTC)
        startable_status: ColumnElement[bool] = IngestionJobModel.status.in_(
            ["uploaded", "queued", "failed_retryable"]
        )
        if stale_before is None:
            status_filter = startable_status
        else:
            status_filter = or_(
                startable_status,
                (IngestionJobModel.status == "parsing")
                & (IngestionJobModel.last_attempt_at < stale_before),
            )
        statement = (
            update(IngestionJobModel)
            .where(
                IngestionJobModel.tenant_id == tenant_id,
                IngestionJobModel.id == job_id,
                IngestionJobModel.document_id == document_id,
                IngestionJobModel.version_id == version_id,
                status_filter,
                select(DocumentModel.id)
                .where(
                    DocumentModel.tenant_id == tenant_id,
                    DocumentModel.id == document_id,
                    DocumentModel.deleted_at.is_(None),
                    DocumentModel.status != "deleted",
                )
                .exists(),
                select(DocumentVersionModel.id)
                .where(
                    DocumentVersionModel.tenant_id == tenant_id,
                    DocumentVersionModel.id == version_id,
                    DocumentVersionModel.document_id == document_id,
                    DocumentVersionModel.deleted_at.is_(None),
                    DocumentVersionModel.status != "deleted",
                )
                .exists(),
            )
            .values(
                status="parsing",
                error_code=None,
                attempt_count=IngestionJobModel.attempt_count + 1,
                last_attempt_at=now,
            )
            .returning(IngestionJobModel)
        )
        try:
            model = await self._session.scalar(statement)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="INGESTION_JOB_UPDATE_FAILED",
                message="Ingestion job update failed.",
                details={"tenant_id": tenant_id, "job_id": job_id},
            ) from exc
        if model is None:
            return None
        return ingestion_job_record_from_model(model)

    async def mark_ingestion_job_parsed(
        self,
        *,
        tenant_id: str,
        job_id: str,
        parsed_metadata: dict[str, object],
    ) -> IngestionJobRecord:
        model = await self._get_job(tenant_id=tenant_id, job_id=job_id)
        version = await self._get_version_model(
            tenant_id=tenant_id,
            version_id=model.version_id,
        )
        document = await self._get_document_model(
            tenant_id=tenant_id,
            document_id=model.document_id,
        )
        _ensure_active_document_version(
            document=document,
            version=version,
            code="DOCUMENT_VERSION_INVALID_STATE",
        )
        model.status = "parsed"
        model.error_code = None
        version.status = "parsed"
        version.metadata_ = {
            **dict(version.metadata_ or {}),
            "parsed_artifact_summary": parsed_metadata,
        }
        await self._sync_document_status_from_latest_version(document)
        return await self._flush_job(model, code="INGESTION_JOB_UPDATE_FAILED")

    async def replace_chunks_for_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        chunks: list[ChunkRecord],
    ) -> list[ChunkRecord]:
        if not chunks:
            raise StorageError(
                code="CHUNK_STORAGE_EMPTY",
                message="Chunk replacement requires at least one chunk.",
                details={
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "version_id": version_id,
                },
            )
        for chunk in chunks:
            if (
                chunk.tenant_id != tenant_id
                or chunk.document_id != document_id
                or chunk.version_id != version_id
            ):
                raise StorageError(
                    code="CHUNK_STORAGE_WRITE_FAILED",
                    message="Chunk tenant, document, or version does not match request scope.",
                    details={
                        "tenant_id": tenant_id,
                        "document_id": document_id,
                        "version_id": version_id,
                        "chunk_id": chunk.chunk_id,
                    },
                )
        document = await self._get_document_model(tenant_id=tenant_id, document_id=document_id)
        version = await self._get_version_model(tenant_id=tenant_id, version_id=version_id)
        if version.document_id != document_id:
            raise StorageError(
                code="CHUNK_STORAGE_WRITE_FAILED",
                message="Document version does not match request document scope.",
                details={
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "version_id": version_id,
                },
            )
        _ensure_active_document_version(
            document=document,
            version=version,
            code="DOCUMENT_VERSION_INVALID_STATE",
        )

        models = [_chunk_model(chunk) for chunk in chunks]
        try:
            await self._session.execute(
                delete(ChunkModel).where(
                    ChunkModel.tenant_id == tenant_id,
                    ChunkModel.document_id == document_id,
                    ChunkModel.version_id == version_id,
                )
            )
            self._session.add_all(models)
            await self._session.flush()
            for model in models:
                await self._session.refresh(model)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="CHUNK_STORAGE_WRITE_FAILED",
                message="Chunk metadata storage write failed.",
                details={
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "version_id": version_id,
                },
            ) from exc
        return [chunk_record_from_model(model) for model in models]

    async def list_chunks_for_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        status: str | None = None,
    ) -> list[ChunkRecord]:
        statement = select(ChunkModel).where(
            ChunkModel.tenant_id == tenant_id,
            ChunkModel.document_id == document_id,
            ChunkModel.version_id == version_id,
            ChunkModel.deleted_at.is_(None),
        )
        if status is not None:
            statement = statement.where(ChunkModel.status == status)
        statement = statement.order_by(ChunkModel.created_at, ChunkModel.id)
        return [
            chunk_record_from_model(model)
            for model in await self._scalars(statement)
        ]

    async def get_chunk(
        self,
        *,
        tenant_id: str,
        chunk_id: str,
        document_id: str | None = None,
        version_id: str | None = None,
    ) -> ChunkRecord | None:
        statement = select(ChunkModel).where(
            ChunkModel.tenant_id == tenant_id,
            ChunkModel.chunk_id == chunk_id,
            ChunkModel.deleted_at.is_(None),
        )
        if document_id is not None:
            statement = statement.where(ChunkModel.document_id == document_id)
        if version_id is not None:
            statement = statement.where(ChunkModel.version_id == version_id)
        try:
            model = await self._session.scalar(statement.order_by(ChunkModel.created_at))
        except SQLAlchemyError as exc:
            raise StorageError(
                code="CHUNK_STORAGE_QUERY_FAILED",
                message="Chunk metadata query failed.",
                details={"tenant_id": tenant_id, "chunk_id": chunk_id},
            ) from exc
        if model is None:
            return None
        return chunk_record_from_model(model)

    async def get_source_citation_metadata(
        self,
        *,
        tenant_id: str,
        user_id: str,
        request_id: str | None,
        citation_ref: str | None,
        document_id: str,
        version_id: str,
        chunk_id: str,
    ) -> Mapping[str, object] | None:
        if request_id is None and citation_ref is None:
            return None
        metadata_sources: list[Mapping[str, object]] = []
        if request_id is not None:
            metadata_sources.extend(
                await self._chat_message_metadata_by_request(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    request_id=request_id,
                )
            )
            metadata_sources.extend(
                await self._retrieval_log_metadata_by_request(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    request_id=request_id,
                )
            )
        for metadata in metadata_sources:
            citation = _matching_citation_metadata(
                metadata,
                citation_ref=citation_ref,
                document_id=document_id,
                version_id=version_id,
                chunk_id=chunk_id,
            )
            if citation is not None:
                return citation
        return None

    async def mark_ingestion_job_chunked(
        self,
        *,
        tenant_id: str,
        job_id: str,
        chunk_metadata: dict[str, object],
    ) -> IngestionJobRecord:
        model = await self._get_job(tenant_id=tenant_id, job_id=job_id)
        version = await self._get_version_model(
            tenant_id=tenant_id,
            version_id=model.version_id,
        )
        document = await self._get_document_model(
            tenant_id=tenant_id,
            document_id=model.document_id,
        )
        _ensure_active_document_version(
            document=document,
            version=version,
            code="DOCUMENT_VERSION_INVALID_STATE",
        )
        expected_chunk_count = chunk_metadata.get("chunk_count")
        if model.status != "parsed" or version.status != "parsed":
            raise StorageError(
                code="INGESTION_JOB_INVALID_STATE",
                message="Ingestion job must be parsed before it can be marked chunked.",
                details={"tenant_id": tenant_id, "job_id": job_id},
            )
        persisted_chunk_count = await self._count_chunks_for_version(
            tenant_id=tenant_id,
            document_id=model.document_id,
            version_id=model.version_id,
        )
        if persisted_chunk_count <= 0:
            raise StorageError(
                code="CHUNK_STORAGE_EMPTY",
                message="Cannot mark ingestion job chunked without persisted chunks.",
                details={
                    "tenant_id": tenant_id,
                    "document_id": model.document_id,
                    "version_id": model.version_id,
                    "job_id": job_id,
                },
            )
        if expected_chunk_count != persisted_chunk_count:
            raise StorageError(
                code="CHUNK_METADATA_MISMATCH",
                message="Chunk summary count does not match persisted chunks.",
                details={
                    "tenant_id": tenant_id,
                    "document_id": model.document_id,
                    "version_id": model.version_id,
                    "job_id": job_id,
                },
            )
        model.status = "chunked"
        model.error_code = None
        version.status = "chunked"
        version.metadata_ = {
            **dict(version.metadata_ or {}),
            "chunk_artifact_summary": _safe_chunk_summary(chunk_metadata),
        }
        await self._sync_document_status_from_latest_version(document)
        return await self._flush_job(model, code="INGESTION_JOB_UPDATE_FAILED")

    async def create_embedding_job(
        self,
        *,
        job: EmbeddingJobRecord,
    ) -> EmbeddingJobRecord:
        document = await self._get_document_model(
            tenant_id=job.tenant_id,
            document_id=job.document_id,
        )
        version = await self._get_version_model(tenant_id=job.tenant_id, version_id=job.version_id)
        if version.document_id != document.id:
            raise StorageError(
                code="EMBEDDING_JOB_SCOPE_MISMATCH",
                message="Embedding job document and version scope does not match.",
                details={
                    "tenant_id": job.tenant_id,
                    "document_id": job.document_id,
                    "version_id": job.version_id,
                },
            )
        _ensure_active_document_version(
            document=document,
            version=version,
            code="DOCUMENT_VERSION_INVALID_STATE",
        )
        model = _embedding_job_model(job)
        self._session.add(model)
        try:
            await self._session.flush()
            await self._session.refresh(model)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="EMBEDDING_JOB_WRITE_FAILED",
                message="Embedding job storage write failed.",
                details={"tenant_id": job.tenant_id, "job_id": job.id},
            ) from exc
        return embedding_job_record_from_model(model)

    async def get_embedding_job(
        self,
        *,
        tenant_id: str,
        job_id: str,
    ) -> EmbeddingJobRecord | None:
        try:
            model = await self._session.scalar(
                select(EmbeddingJobModel).where(
                    EmbeddingJobModel.tenant_id == tenant_id,
                    EmbeddingJobModel.id == job_id,
                )
            )
        except SQLAlchemyError as exc:
            raise StorageError(
                code="EMBEDDING_JOB_READ_FAILED",
                message="Embedding job read failed.",
                details={"tenant_id": tenant_id, "job_id": job_id},
            ) from exc
        if model is None:
            return None
        return embedding_job_record_from_model(model)

    async def claim_embedding_job(
        self,
        *,
        tenant_id: str,
        job_id: str,
        document_id: str,
        version_id: str,
        stale_before: datetime | None,
    ) -> EmbeddingJobRecord | None:
        now = datetime.now(tz=UTC)
        retry_due: ColumnElement[bool] = or_(
            EmbeddingJobModel.next_retry_at.is_(None),
            EmbeddingJobModel.next_retry_at <= now,
        )
        startable_status: ColumnElement[bool] = or_(
            EmbeddingJobModel.status == "queued",
            and_(EmbeddingJobModel.status == "failed_retryable", retry_due),
        )
        if stale_before is None:
            status_filter = startable_status
        else:
            status_filter = or_(
                startable_status,
                (EmbeddingJobModel.status == "embedding")
                & (EmbeddingJobModel.last_attempt_at < stale_before),
            )
        statement = (
            update(EmbeddingJobModel)
            .where(
                EmbeddingJobModel.tenant_id == tenant_id,
                EmbeddingJobModel.id == job_id,
                EmbeddingJobModel.document_id == document_id,
                EmbeddingJobModel.version_id == version_id,
                status_filter,
                select(DocumentModel.id)
                .where(
                    DocumentModel.tenant_id == tenant_id,
                    DocumentModel.id == document_id,
                    DocumentModel.deleted_at.is_(None),
                    DocumentModel.status != "deleted",
                )
                .exists(),
                select(DocumentVersionModel.id)
                .where(
                    DocumentVersionModel.tenant_id == tenant_id,
                    DocumentVersionModel.id == version_id,
                    DocumentVersionModel.document_id == document_id,
                    DocumentVersionModel.deleted_at.is_(None),
                    DocumentVersionModel.status != "deleted",
                )
                .exists(),
            )
            .values(
                status="embedding",
                error_code=None,
                attempt_count=EmbeddingJobModel.attempt_count + 1,
                last_attempt_at=now,
                next_retry_at=None,
            )
            .returning(EmbeddingJobModel)
        )
        try:
            model = await self._session.scalar(statement)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="EMBEDDING_JOB_UPDATE_FAILED",
                message="Embedding job update failed.",
                details={"tenant_id": tenant_id, "job_id": job_id},
            ) from exc
        if model is None:
            return None
        return embedding_job_record_from_model(model)

    async def mark_embedding_job_embedded(
        self,
        *,
        tenant_id: str,
        job_id: str,
        embedding_metadata: dict[str, object],
    ) -> EmbeddingJobRecord:
        model = await self._get_embedding_job_model(tenant_id=tenant_id, job_id=job_id)
        version = await self._get_version_model(tenant_id=tenant_id, version_id=model.version_id)
        document = await self._get_document_model(
            tenant_id=tenant_id,
            document_id=model.document_id,
        )
        _ensure_active_document_version(
            document=document,
            version=version,
            code="DOCUMENT_VERSION_INVALID_STATE",
        )
        summary = _safe_embedding_summary(embedding_metadata)
        model.status = "embedded"
        model.error_code = None
        model.provider = str(summary["provider"])
        model.model = str(summary["model"])
        model.version = str(summary["version"]) if summary.get("version") is not None else None
        model.dim = _required_summary_int(summary, "dim")
        model.chunk_count = _required_summary_int(summary, "chunk_count")
        model.metadata_ = {"embedding_artifact_summary": summary}
        version.status = "embedded"
        version.metadata_ = {
            **dict(version.metadata_ or {}),
            "embedding_artifact_summary": summary,
        }
        await self._sync_document_status_from_latest_version(document)
        await self._update_chunk_embedding_summary(
            tenant_id=tenant_id,
            document_id=model.document_id,
            version_id=model.version_id,
            summary=summary,
        )
        return await self._flush_embedding_job(model, code="EMBEDDING_JOB_UPDATE_FAILED")

    async def mark_document_version_retrieval_ready(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        index_metadata: dict[str, object] | None = None,
    ) -> DocumentVersionRecord:
        version = await self._get_version_model(tenant_id=tenant_id, version_id=version_id)
        document = await self._get_document_model(tenant_id=tenant_id, document_id=document_id)
        if version.document_id != document_id:
            raise StorageError(
                code="DOCUMENT_VERSION_NOT_FOUND",
                message="Document version was not found.",
                details=_scope_details(
                    tenant_id=tenant_id,
                    document_id=document_id,
                    version_id=version_id,
                ),
            )
        _ensure_active_document_version(
            document=document,
            version=version,
            code="DOCUMENT_VERSION_INVALID_STATE",
        )
        active_chunk_count = await self._count_chunks_for_version(
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=version_id,
        )
        if active_chunk_count <= 0:
            raise StorageError(
                code="DOCUMENT_INDEX_NOT_READY",
                message="Document version has no active chunks.",
                details=_scope_details(
                    tenant_id=tenant_id,
                    document_id=document_id,
                    version_id=version_id,
                ),
            )
        embedding_job = await self._latest_embedding_job_model(
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=version_id,
            status="embedded",
        )
        if embedding_job is None:
            raise StorageError(
                code="DOCUMENT_INDEX_NOT_READY",
                message="Document version does not have an embedded job.",
                details=_scope_details(
                    tenant_id=tenant_id,
                    document_id=document_id,
                    version_id=version_id,
                ),
            )
        summary = _dict_from_object(
            (embedding_job.metadata_ or {}).get("embedding_artifact_summary", {})
        )
        vector_summary = _dict_from_object(summary.get("vector_index_summary", {}))
        if index_metadata:
            vector_summary = {**vector_summary, **_safe_vector_index_summary(index_metadata)}
        if vector_summary.get("status") != "indexed":
            raise StorageError(
                code="DOCUMENT_INDEX_NOT_READY",
                message="Document version vector index is not indexed.",
                details=_scope_details(
                    tenant_id=tenant_id,
                    document_id=document_id,
                    version_id=version_id,
                ),
            )
        vector_count = vector_summary.get("vector_count")
        if not isinstance(vector_count, int) or vector_count != active_chunk_count:
            raise StorageError(
                code="DOCUMENT_INDEX_NOT_READY",
                message="Vector count does not match active chunk count.",
                details={
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "version_id": version_id,
                    "chunk_count": active_chunk_count,
                    "vector_count": vector_count,
                },
            )
        retrieval_summary = {
            "stage": "retrieval_ready",
            "chunk_count": active_chunk_count,
            "provider": embedding_job.provider,
            "model": embedding_job.model,
            "version": embedding_job.version,
            "dim": embedding_job.dim,
            "vector_count": vector_count,
            "index_status": vector_summary.get("status"),
        }
        version.status = "retrieval_ready"
        version.metadata_ = {
            **dict(version.metadata_ or {}),
            "embedding_artifact_summary": summary,
            "retrieval_ready_summary": retrieval_summary,
        }
        await self._sync_document_status_from_latest_version(document)
        try:
            await self._session.flush()
            await self._session.refresh(version)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="DOCUMENT_VERSION_STATUS_UPDATE_FAILED",
                message="Document version retrieval status update failed.",
                details=_scope_details(
                    tenant_id=tenant_id,
                    document_id=document_id,
                    version_id=version_id,
                ),
            ) from exc
        return document_version_record_from_model(version)

    async def get_document_version_status(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
    ) -> DocumentVersionStatusResult | None:
        version = await self.get_version(tenant_id=tenant_id, version_id=version_id)
        if version is None or version.document_id != document_id:
            return None
        chunk_count = await self._count_chunks_for_version(
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=version_id,
        )
        embedding_job = await self._latest_embedding_job_model(
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=version_id,
            status=None,
        )
        ingestion_job = await self._latest_ingestion_job_model(
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=version_id,
        )
        status_job = embedding_job or ingestion_job
        embedding_summary: dict[str, object] = {}
        vector_summary: dict[str, object] = {}
        if embedding_job is not None:
            raw_embedding_summary = (embedding_job.metadata_ or {}).get(
                "embedding_artifact_summary",
                {},
            )
            embedding_summary = _dict_from_object(raw_embedding_summary)
            vector_summary = _dict_from_object(embedding_summary.get("vector_index_summary", {}))
        vector_count = vector_summary.get("vector_count")
        return DocumentVersionStatusResult(
            document_id=document_id,
            version_id=version_id,
            status=version.status,
            chunk_count=chunk_count,
            embedding_provider=embedding_job.provider if embedding_job is not None else None,
            embedding_model=embedding_job.model if embedding_job is not None else None,
            embedding_version=embedding_job.version if embedding_job is not None else None,
            embedding_dim=embedding_job.dim if embedding_job is not None else None,
            vector_count=(
                vector_count
                if isinstance(vector_count, int)
                else None
            ),
            index_status=(
                str(vector_summary["status"]) if vector_summary.get("status") is not None else None
            ),
            job_id=status_job.id if status_job is not None else None,
            attempt_count=status_job.attempt_count if status_job is not None else None,
            last_attempt_at=status_job.last_attempt_at if status_job is not None else None,
            next_retry_at=status_job.next_retry_at if status_job is not None else None,
            deleted_at=version.deleted_at,
            error_code=status_job.error_code if status_job is not None else None,
            error_summary=_safe_error_summary(
                status_job.error_code if status_job is not None else None,
            ),
            request_id="",
            trace_id="",
        )

    async def soft_delete_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        deleted_by: str,
    ) -> int:
        document = await self._get_document_model(tenant_id=tenant_id, document_id=document_id)
        now = datetime.now(tz=UTC)
        deleted_versions = 0
        try:
            versions = await self._session.scalars(
                select(DocumentVersionModel).where(
                    DocumentVersionModel.tenant_id == tenant_id,
                    DocumentVersionModel.document_id == document_id,
                    DocumentVersionModel.deleted_at.is_(None),
                    DocumentVersionModel.status != "deleted",
                )
            )
            for version in versions:
                version.status = "deleted"
                version.deleted_at = now
                deleted_versions += 1
            document.status = "deleted"
            document.deleted_at = now
            document.metadata_ = {**dict(document.metadata_ or {}), "deleted_by": deleted_by}
            await self._session.flush()
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="DOCUMENT_DELETE_FAILED",
                message="Document soft delete failed.",
                details={"tenant_id": tenant_id, "document_id": document_id},
            ) from exc
        return deleted_versions

    async def soft_delete_document_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        deleted_by: str,
    ) -> int:
        document = await self._get_document_model(tenant_id=tenant_id, document_id=document_id)
        version = await self._get_version_model(tenant_id=tenant_id, version_id=version_id)
        if version.document_id != document_id:
            raise StorageError(
                code="DOCUMENT_VERSION_NOT_FOUND",
                message="Document version was not found.",
                details=_scope_details(
                    tenant_id=tenant_id,
                    document_id=document_id,
                    version_id=version_id,
                ),
            )
        if version.deleted_at is not None or version.status == "deleted":
            return 0
        now = datetime.now(tz=UTC)
        version.status = "deleted"
        version.deleted_at = now
        version.metadata_ = {**dict(version.metadata_ or {}), "deleted_by": deleted_by}
        remaining = await self._count_non_deleted_versions(
            tenant_id=tenant_id,
            document_id=document_id,
            excluding_version_id=version_id,
        )
        if remaining == 0:
            document.status = "deleted"
            document.deleted_at = now
            document.metadata_ = {**dict(document.metadata_ or {}), "deleted_by": deleted_by}
        else:
            await self._sync_document_status_from_latest_version(document)
        try:
            await self._session.flush()
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="DOCUMENT_DELETE_FAILED",
                message="Document version soft delete failed.",
                details=_scope_details(
                    tenant_id=tenant_id,
                    document_id=document_id,
                    version_id=version_id,
                ),
            ) from exc
        return 1

    async def soft_delete_chunks_for_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
    ) -> int:
        now = datetime.now(tz=UTC)
        deleted_count = 0
        try:
            chunks = await self._session.scalars(
                select(ChunkModel).where(
                    ChunkModel.tenant_id == tenant_id,
                    ChunkModel.document_id == document_id,
                    ChunkModel.version_id == version_id,
                    ChunkModel.deleted_at.is_(None),
                    ChunkModel.status != "deleted",
                )
            )
            for chunk in chunks:
                chunk.status = "deleted"
                chunk.deleted_at = now
                deleted_count += 1
            await self._session.flush()
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="DOCUMENT_DELETE_FAILED",
                message="Document chunk soft delete failed.",
                details=_scope_details(
                    tenant_id=tenant_id,
                    document_id=document_id,
                    version_id=version_id,
                ),
            ) from exc
        return deleted_count

    async def mark_embedding_job_failed(
        self,
        *,
        tenant_id: str,
        job_id: str,
        error_code: str,
        status: str,
        next_retry_at: datetime | None = None,
    ) -> EmbeddingJobRecord:
        model = await self._get_embedding_job_model(tenant_id=tenant_id, job_id=job_id)
        if model.status == "embedded":
            return embedding_job_record_from_model(model)
        model.status = status
        model.error_code = error_code
        model.last_attempt_at = datetime.now(tz=UTC)
        model.next_retry_at = next_retry_at
        return await self._flush_embedding_job(model, code="EMBEDDING_JOB_UPDATE_FAILED")

    async def list_documents(
        self,
        *,
        tenant_id: str,
        status: str | None = None,
    ) -> list[DocumentRecord]:
        statement = select(DocumentModel).where(DocumentModel.tenant_id == tenant_id)
        if status is not None:
            statement = statement.where(DocumentModel.status == status)
        return [
            document_record_from_model(model)
            for model in await self._scalars(statement.order_by(DocumentModel.created_at))
        ]

    async def list_versions(
        self,
        *,
        tenant_id: str,
        document_id: str,
        status: str | None = None,
    ) -> list[DocumentVersionRecord]:
        statement = select(DocumentVersionModel).where(
            DocumentVersionModel.tenant_id == tenant_id,
            DocumentVersionModel.document_id == document_id,
        )
        if status is not None:
            statement = statement.where(DocumentVersionModel.status == status)
        return [
            document_version_record_from_model(model)
            for model in await self._scalars(statement.order_by(DocumentVersionModel.created_at))
        ]

    async def get_version(
        self,
        *,
        tenant_id: str,
        version_id: str,
    ) -> DocumentVersionRecord | None:
        try:
            model = await self._session.scalar(
                select(DocumentVersionModel).where(
                    DocumentVersionModel.tenant_id == tenant_id,
                    DocumentVersionModel.id == version_id,
                )
            )
        except SQLAlchemyError as exc:
            raise StorageError(
                code="DOCUMENT_STORAGE_QUERY_FAILED",
                message="Document version query failed.",
                details={"tenant_id": tenant_id, "version_id": version_id},
            ) from exc
        if model is None:
            return None
        return document_version_record_from_model(model)

    async def list_ingestion_jobs(
        self,
        *,
        tenant_id: str,
        status: str | None = None,
        version_id: str | None = None,
    ) -> list[IngestionJobRecord]:
        statement = select(IngestionJobModel).where(IngestionJobModel.tenant_id == tenant_id)
        if status is not None:
            statement = statement.where(IngestionJobModel.status == status)
        if version_id is not None:
            statement = statement.where(IngestionJobModel.version_id == version_id)
        return [
            ingestion_job_record_from_model(model)
            for model in await self._scalars(statement.order_by(IngestionJobModel.created_at))
        ]

    async def list_embedding_jobs(
        self,
        *,
        tenant_id: str,
        status: str | None = None,
        version_id: str | None = None,
    ) -> list[EmbeddingJobRecord]:
        statement = select(EmbeddingJobModel).where(EmbeddingJobModel.tenant_id == tenant_id)
        if status is not None:
            statement = statement.where(EmbeddingJobModel.status == status)
        if version_id is not None:
            statement = statement.where(EmbeddingJobModel.version_id == version_id)
        return [
            embedding_job_record_from_model(model)
            for model in await self._scalars(statement.order_by(EmbeddingJobModel.created_at))
        ]

    async def commit(self) -> None:
        try:
            await self._session.commit()
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="DOCUMENT_STORAGE_COMMIT_FAILED",
                message="Document storage commit failed.",
            ) from exc

    async def rollback(self) -> None:
        await self._session.rollback()

    async def _get_job(self, *, tenant_id: str, job_id: str) -> IngestionJobModel:
        try:
            model = await self._session.scalar(
                select(IngestionJobModel).where(
                    IngestionJobModel.tenant_id == tenant_id,
                    IngestionJobModel.id == job_id,
                )
            )
        except SQLAlchemyError as exc:
            raise StorageError(
                code="INGESTION_JOB_READ_FAILED",
                message="Ingestion job read failed.",
                details={"tenant_id": tenant_id, "job_id": job_id},
            ) from exc
        if model is None:
            raise StorageError(
                code="INGESTION_JOB_NOT_FOUND",
                message="Ingestion job was not found.",
                details={"tenant_id": tenant_id, "job_id": job_id},
            )
        return model

    async def _get_version_model(
        self,
        *,
        tenant_id: str,
        version_id: str,
    ) -> DocumentVersionModel:
        try:
            model = await self._session.scalar(
                select(DocumentVersionModel).where(
                    DocumentVersionModel.tenant_id == tenant_id,
                    DocumentVersionModel.id == version_id,
                )
            )
        except SQLAlchemyError as exc:
            raise StorageError(
                code="DOCUMENT_VERSION_READ_FAILED",
                message="Document version read failed.",
                details={"tenant_id": tenant_id, "version_id": version_id},
            ) from exc
        if model is None:
            raise StorageError(
                code="DOCUMENT_VERSION_NOT_FOUND",
                message="Document version was not found.",
                details={"tenant_id": tenant_id, "version_id": version_id},
            )
        return model

    async def _get_document_model(
        self,
        *,
        tenant_id: str,
        document_id: str,
    ) -> DocumentModel:
        try:
            model = await self._session.scalar(
                select(DocumentModel).where(
                    DocumentModel.tenant_id == tenant_id,
                    DocumentModel.id == document_id,
                )
            )
        except SQLAlchemyError as exc:
            raise StorageError(
                code="DOCUMENT_READ_FAILED",
                message="Document read failed.",
                details={"tenant_id": tenant_id, "document_id": document_id},
            ) from exc
        if model is None:
            raise StorageError(
                code="DOCUMENT_NOT_FOUND",
                message="Document was not found.",
                details={"tenant_id": tenant_id, "document_id": document_id},
            )
        return model

    async def _get_embedding_job_model(
        self,
        *,
        tenant_id: str,
        job_id: str,
    ) -> EmbeddingJobModel:
        try:
            model = await self._session.scalar(
                select(EmbeddingJobModel).where(
                    EmbeddingJobModel.tenant_id == tenant_id,
                    EmbeddingJobModel.id == job_id,
                )
            )
        except SQLAlchemyError as exc:
            raise StorageError(
                code="EMBEDDING_JOB_READ_FAILED",
                message="Embedding job read failed.",
                details={"tenant_id": tenant_id, "job_id": job_id},
            ) from exc
        if model is None:
            raise StorageError(
                code="EMBEDDING_JOB_NOT_FOUND",
                message="Embedding job was not found.",
                details={"tenant_id": tenant_id, "job_id": job_id},
            )
        return model

    async def _flush_job(self, model: IngestionJobModel, *, code: str) -> IngestionJobRecord:
        try:
            await self._session.flush()
            await self._session.refresh(model)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code=code,
                message="Ingestion job update failed.",
                details={"job_id": model.id},
            ) from exc
        return ingestion_job_record_from_model(model)

    async def _flush_embedding_job(
        self,
        model: EmbeddingJobModel,
        *,
        code: str,
    ) -> EmbeddingJobRecord:
        try:
            await self._session.flush()
            await self._session.refresh(model)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code=code,
                message="Embedding job update failed.",
                details={"job_id": model.id},
            ) from exc
        return embedding_job_record_from_model(model)

    async def _update_chunk_embedding_summary(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        summary: dict[str, object],
    ) -> None:
        chunk_summary = {
            "provider": summary["provider"],
            "model": summary["model"],
            "version": summary.get("version"),
            "dim": summary["dim"],
        }
        try:
            result = await self._session.scalars(
                select(ChunkModel).where(
                    ChunkModel.tenant_id == tenant_id,
                    ChunkModel.document_id == document_id,
                    ChunkModel.version_id == version_id,
                    ChunkModel.deleted_at.is_(None),
                    ChunkModel.status == "active",
                )
            )
            for chunk in result:
                chunk.metadata_ = {
                    **dict(chunk.metadata_ or {}),
                    "embedding_summary": chunk_summary,
                }
        except SQLAlchemyError as exc:
            raise StorageError(
                code="CHUNK_STORAGE_UPDATE_FAILED",
                message="Chunk embedding summary update failed.",
                details={
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "version_id": version_id,
                },
            ) from exc

    async def _count_chunks_for_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
    ) -> int:
        try:
            count = await self._session.scalar(
                select(func.count())
                .select_from(ChunkModel)
                .where(
                    ChunkModel.tenant_id == tenant_id,
                    ChunkModel.document_id == document_id,
                    ChunkModel.version_id == version_id,
                    ChunkModel.deleted_at.is_(None),
                )
            )
        except SQLAlchemyError as exc:
            raise StorageError(
                code="CHUNK_STORAGE_QUERY_FAILED",
                message="Chunk metadata query failed.",
                details={
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "version_id": version_id,
                },
            ) from exc
        return int(count or 0)

    async def _count_non_deleted_versions(
        self,
        *,
        tenant_id: str,
        document_id: str,
        excluding_version_id: str | None = None,
    ) -> int:
        statement = (
            select(func.count())
            .select_from(DocumentVersionModel)
            .where(
                DocumentVersionModel.tenant_id == tenant_id,
                DocumentVersionModel.document_id == document_id,
                DocumentVersionModel.deleted_at.is_(None),
                DocumentVersionModel.status != "deleted",
            )
        )
        if excluding_version_id is not None:
            statement = statement.where(DocumentVersionModel.id != excluding_version_id)
        try:
            count = await self._session.scalar(statement)
        except SQLAlchemyError as exc:
            raise StorageError(
                code="DOCUMENT_STORAGE_QUERY_FAILED",
                message="Document version query failed.",
                details={"tenant_id": tenant_id, "document_id": document_id},
            ) from exc
        return int(count or 0)

    async def _latest_non_deleted_version_model(
        self,
        *,
        tenant_id: str,
        document_id: str,
    ) -> DocumentVersionModel | None:
        try:
            return cast(
                DocumentVersionModel | None,
                await self._session.scalar(
                    select(DocumentVersionModel)
                    .where(
                        DocumentVersionModel.tenant_id == tenant_id,
                        DocumentVersionModel.document_id == document_id,
                        DocumentVersionModel.deleted_at.is_(None),
                        DocumentVersionModel.status != "deleted",
                    )
                    .order_by(
                        DocumentVersionModel.created_at.desc(),
                        DocumentVersionModel.id.desc(),
                    )
                )
            )
        except SQLAlchemyError as exc:
            raise StorageError(
                code="DOCUMENT_STORAGE_QUERY_FAILED",
                message="Document version query failed.",
                details={"tenant_id": tenant_id, "document_id": document_id},
            ) from exc

    async def _sync_document_status_from_latest_version(
        self,
        document: DocumentModel,
    ) -> None:
        latest = await self._latest_non_deleted_version_model(
            tenant_id=document.tenant_id,
            document_id=document.id,
        )
        if latest is None:
            document.status = "deleted"
            document.deleted_at = document.deleted_at or datetime.now(tz=UTC)
            return
        document.status = latest.status
        document.metadata_ = _document_latest_metadata(
            document.metadata_,
            latest_version=latest,
        )

    async def _latest_embedding_job_model(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        status: str | None,
    ) -> EmbeddingJobModel | None:
        statement = select(EmbeddingJobModel).where(
            EmbeddingJobModel.tenant_id == tenant_id,
            EmbeddingJobModel.document_id == document_id,
            EmbeddingJobModel.version_id == version_id,
        )
        if status is not None:
            statement = statement.where(EmbeddingJobModel.status == status)
        try:
            return cast(
                EmbeddingJobModel | None,
                await self._session.scalar(
                    statement.order_by(
                        EmbeddingJobModel.created_at.desc(),
                        EmbeddingJobModel.id.desc(),
                    )
                ),
            )
        except SQLAlchemyError as exc:
            raise StorageError(
                code="EMBEDDING_JOB_READ_FAILED",
                message="Embedding job read failed.",
                details={
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "version_id": version_id,
                },
            ) from exc

    async def _latest_ingestion_job_model(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
    ) -> IngestionJobModel | None:
        try:
            return cast(
                IngestionJobModel | None,
                await self._session.scalar(
                    select(IngestionJobModel)
                    .where(
                        IngestionJobModel.tenant_id == tenant_id,
                        IngestionJobModel.document_id == document_id,
                        IngestionJobModel.version_id == version_id,
                    )
                    .order_by(
                        IngestionJobModel.created_at.desc(),
                        IngestionJobModel.id.desc(),
                    )
                ),
            )
        except SQLAlchemyError as exc:
            raise StorageError(
                code="INGESTION_JOB_READ_FAILED",
                message="Ingestion job read failed.",
                details={
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "version_id": version_id,
                },
            ) from exc

    async def _chat_message_metadata_by_request(
        self,
        *,
        tenant_id: str,
        user_id: str,
        request_id: str,
    ) -> list[Mapping[str, object]]:
        try:
            models = list(
                await self._session.scalars(
                    select(ChatMessageModel)
                    .where(
                        ChatMessageModel.tenant_id == tenant_id,
                        ChatMessageModel.user_id == user_id,
                        ChatMessageModel.request_id == request_id,
                        ChatMessageModel.role == "assistant",
                        ChatMessageModel.status == "active",
                    )
                    .order_by(ChatMessageModel.created_at.desc())
                )
            )
        except SQLAlchemyError as exc:
            raise StorageError(
                code="SOURCE_CITATION_METADATA_READ_FAILED",
                message="Source citation metadata read failed.",
                details={"tenant_id": tenant_id, "request_id": request_id},
            ) from exc
        return [dict(model.metadata_ or {}) for model in models]

    async def _retrieval_log_metadata_by_request(
        self,
        *,
        tenant_id: str,
        user_id: str,
        request_id: str,
    ) -> list[Mapping[str, object]]:
        try:
            models = list(
                await self._session.scalars(
                    select(RetrievalLogModel)
                    .where(
                        RetrievalLogModel.tenant_id == tenant_id,
                        RetrievalLogModel.user_id == user_id,
                        RetrievalLogModel.request_id == request_id,
                        RetrievalLogModel.status == "success",
                    )
                    .order_by(RetrievalLogModel.created_at.desc())
                )
            )
        except SQLAlchemyError as exc:
            raise StorageError(
                code="SOURCE_CITATION_METADATA_READ_FAILED",
                message="Source citation metadata read failed.",
                details={"tenant_id": tenant_id, "request_id": request_id},
            ) from exc
        return [dict(model.metadata_ or {}) for model in models]

    async def _scalars(self, statement: Select[tuple[_ModelT]]) -> Sequence[_ModelT]:
        try:
            result = await self._session.scalars(statement)
            return list(result)
        except SQLAlchemyError as exc:
            raise StorageError(
                code="DOCUMENT_STORAGE_QUERY_FAILED",
                message="Document storage query failed.",
            ) from exc


def _document_model(record: DocumentRecord) -> DocumentModel:
    return DocumentModel(
        id=record.id,
        tenant_id=record.tenant_id,
        created_by=record.created_by,
        status=record.status,
        source_type=record.source_type,
        source_uri=record.source_uri,
        title=record.title,
        acl=record.acl,
        checksum=record.checksum,
        metadata_=record.metadata,
        deleted_at=record.deleted_at,
    )


def _version_model(record: DocumentVersionRecord) -> DocumentVersionModel:
    return DocumentVersionModel(
        id=record.id,
        document_id=record.document_id,
        tenant_id=record.tenant_id,
        created_by=record.created_by,
        status=record.status,
        source_type=record.source_type,
        source_uri=record.source_uri,
        object_key=record.object_key,
        filename=record.filename,
        content_type=record.content_type,
        byte_size=record.byte_size,
        acl=record.acl,
        checksum=record.checksum,
        metadata_=record.metadata,
        deleted_at=record.deleted_at,
    )


def _job_model(record: IngestionJobRecord) -> IngestionJobModel:
    return IngestionJobModel(
        id=record.id,
        tenant_id=record.tenant_id,
        created_by=record.created_by,
        status=record.status,
        document_id=record.document_id,
        version_id=record.version_id,
        queue_name=record.queue_name,
        queue_job_id=record.queue_job_id,
        attempt_count=record.attempt_count,
        error_code=record.error_code,
        last_attempt_at=record.last_attempt_at,
        next_retry_at=record.next_retry_at,
    )


def _chunk_model(record: ChunkRecord) -> ChunkModel:
    values = dict(
        tenant_id=record.tenant_id,
        document_id=record.document_id,
        version_id=record.version_id,
        chunk_id=record.chunk_id,
        created_by=record.created_by,
        status=record.status,
        source_type=record.source_type,
        source_uri=record.source_uri,
        title_path=record.title_path,
        content=record.content,
        page_start=record.page_start,
        page_end=record.page_end,
        token_count=record.token_count,
        acl=record.acl,
        checksum=record.checksum,
        section_ids=record.section_ids,
        metadata_=record.metadata,
        deleted_at=record.deleted_at,
    )
    if record.id is not None:
        values["id"] = record.id
    return ChunkModel(**values)


def _embedding_job_model(record: EmbeddingJobRecord) -> EmbeddingJobModel:
    return EmbeddingJobModel(
        id=record.id,
        tenant_id=record.tenant_id,
        created_by=record.created_by,
        status=record.status,
        document_id=record.document_id,
        version_id=record.version_id,
        provider=record.provider,
        model=record.model,
        version=record.version,
        dim=record.dim,
        chunk_count=record.chunk_count,
        attempt_count=record.attempt_count,
        error_code=record.error_code,
        last_attempt_at=record.last_attempt_at,
        next_retry_at=record.next_retry_at,
        metadata_=record.metadata,
    )


def document_record_from_model(model: DocumentModel) -> DocumentRecord:
    return DocumentRecord(
        id=model.id,
        tenant_id=model.tenant_id,
        created_by=model.created_by,
        status=model.status,
        source_type=model.source_type,
        source_uri=model.source_uri,
        title=model.title,
        acl=dict(model.acl or {}),
        checksum=model.checksum,
        metadata=dict(model.metadata_ or {}),
        deleted_at=model.deleted_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def document_version_record_from_model(model: DocumentVersionModel) -> DocumentVersionRecord:
    return DocumentVersionRecord(
        id=model.id,
        document_id=model.document_id,
        tenant_id=model.tenant_id,
        created_by=model.created_by,
        status=model.status,
        source_type=model.source_type,
        source_uri=model.source_uri,
        object_key=model.object_key,
        filename=model.filename,
        content_type=model.content_type,
        byte_size=model.byte_size,
        acl=dict(model.acl or {}),
        checksum=model.checksum,
        metadata=dict(model.metadata_ or {}),
        deleted_at=model.deleted_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def ingestion_job_record_from_model(model: IngestionJobModel) -> IngestionJobRecord:
    return IngestionJobRecord(
        id=model.id,
        tenant_id=model.tenant_id,
        created_by=model.created_by,
        status=model.status,
        document_id=model.document_id,
        version_id=model.version_id,
        queue_name=model.queue_name,
        queue_job_id=model.queue_job_id,
        attempt_count=model.attempt_count,
        error_code=model.error_code,
        last_attempt_at=model.last_attempt_at,
        next_retry_at=model.next_retry_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def embedding_job_record_from_model(model: EmbeddingJobModel) -> EmbeddingJobRecord:
    return EmbeddingJobRecord(
        id=model.id,
        tenant_id=model.tenant_id,
        created_by=model.created_by,
        status=model.status,
        document_id=model.document_id,
        version_id=model.version_id,
        provider=model.provider,
        model=model.model,
        version=model.version,
        dim=model.dim,
        chunk_count=model.chunk_count,
        attempt_count=model.attempt_count,
        error_code=model.error_code,
        last_attempt_at=model.last_attempt_at,
        next_retry_at=model.next_retry_at,
        metadata=dict(model.metadata_ or {}),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def chunk_record_from_model(model: ChunkModel) -> ChunkRecord:
    return ChunkRecord(
        id=model.id,
        tenant_id=model.tenant_id,
        document_id=model.document_id,
        version_id=model.version_id,
        chunk_id=model.chunk_id,
        created_by=model.created_by,
        status=model.status,
        source_type=model.source_type,
        source_uri=model.source_uri,
        title_path=list(model.title_path or []),
        content=model.content,
        page_start=model.page_start,
        page_end=model.page_end,
        token_count=model.token_count,
        acl=dict(model.acl or {}),
        checksum=model.checksum,
        section_ids=list(model.section_ids or []),
        metadata=dict(model.metadata_ or {}),
        deleted_at=model.deleted_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _safe_chunk_summary(chunk_metadata: dict[str, object]) -> dict[str, object]:
    allowed_keys = {
        "chunk_count",
        "token_count_min",
        "token_count_max",
        "checksum_summary",
    }
    return {key: chunk_metadata[key] for key in allowed_keys if key in chunk_metadata}


def _safe_embedding_summary(embedding_metadata: dict[str, object]) -> dict[str, object]:
    allowed_keys = {
        "stage",
        "provider",
        "model",
        "version",
        "dim",
        "chunk_count",
        "token_count_min",
        "token_count_max",
        "usage",
        "vector_index_summary",
    }
    return {key: embedding_metadata[key] for key in allowed_keys if key in embedding_metadata}


def _safe_vector_index_summary(index_metadata: dict[str, object]) -> dict[str, object]:
    allowed_keys = {
        "stage",
        "status",
        "vector_count",
        "provider",
        "model",
        "version",
        "dim",
        "latency_ms",
    }
    return {key: index_metadata[key] for key in allowed_keys if key in index_metadata}


def _safe_error_summary(error_code: str | None) -> dict[str, object] | None:
    if error_code is None:
        return None
    return {"error_code": error_code}


def _matching_citation_metadata(
    metadata: Mapping[str, object],
    *,
    citation_ref: str | None,
    document_id: str,
    version_id: str,
    chunk_id: str,
) -> dict[str, object] | None:
    citations = metadata.get("citations")
    if not isinstance(citations, Sequence) or isinstance(citations, str):
        return None
    for index, citation in enumerate(citations):
        if not isinstance(citation, Mapping):
            continue
        if citation_ref is not None and not _citation_ref_matches(citation, citation_ref, index):
            continue
        if (
            citation.get("document_id") != document_id
            or citation.get("version_id") != version_id
            or citation.get("chunk_id") != chunk_id
        ):
            continue
        result: dict[str, object] = {}
        retrieval_method = citation.get("retrieval_method")
        if isinstance(retrieval_method, str) and retrieval_method.strip():
            result["retrieval_method"] = retrieval_method.strip()
        score = citation.get("score")
        if isinstance(score, int | float) and not isinstance(score, bool):
            result["score"] = float(score)
        return result or None
    return None


def _citation_ref_matches(citation: Mapping[str, object], citation_ref: str, index: int) -> bool:
    normalized = citation_ref.strip()
    if not normalized:
        return True
    for key in ("citation_ref", "ref", "id"):
        value = citation.get(key)
        if isinstance(value, str) and value.strip() == normalized:
            return True
    return normalized in {str(index), str(index + 1), str(citation.get("chunk_id", "")).strip()}


def _ensure_active_document_version(
    *,
    document: DocumentModel,
    version: DocumentVersionModel,
    code: str,
) -> None:
    if version.document_id != document.id:
        raise StorageError(
            code="DOCUMENT_VERSION_NOT_FOUND",
            message="Document version was not found.",
            details=_scope_details(
                tenant_id=document.tenant_id,
                document_id=document.id,
                version_id=version.id,
            ),
        )
    if (
        document.deleted_at is not None
        or document.status == "deleted"
        or version.deleted_at is not None
        or version.status == "deleted"
    ):
        raise StorageError(
            code=code,
            message="Document version is not active.",
            details=_scope_details(
                tenant_id=document.tenant_id,
                document_id=document.id,
                version_id=version.id,
            ),
        )


def _document_latest_metadata(
    metadata: Mapping[str, object] | None,
    *,
    latest_version: DocumentVersionModel,
) -> dict[str, object]:
    result = dict(metadata or {})
    result["latest_version_id"] = latest_version.id
    if latest_version.status == "retrieval_ready":
        retrieval_summary = dict(latest_version.metadata_ or {}).get("retrieval_ready_summary")
        if isinstance(retrieval_summary, Mapping):
            result["retrieval_ready_summary"] = dict(retrieval_summary)
    else:
        result.pop("retrieval_ready_summary", None)
    return result


def _dict_from_object(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return dict(value)


def _scope_details(
    *,
    tenant_id: str,
    document_id: str,
    version_id: str,
) -> dict[str, str]:
    return {
        "tenant_id": tenant_id,
        "document_id": document_id,
        "version_id": version_id,
    }


def _required_summary_int(summary: dict[str, object], key: str) -> int:
    value = summary.get(key)
    if not isinstance(value, int):
        raise StorageError(
            code="EMBEDDING_METADATA_INVALID",
            message="Embedding metadata summary is invalid.",
            details={"field": key},
        )
    return value
