from __future__ import annotations

from datetime import datetime
from typing import BinaryIO, Protocol

from packages.data.dto import (
    ChunkRecord,
    DocumentRecord,
    DocumentVersionRecord,
    DocumentVersionStatusResult,
    EmbeddingJobRecord,
    EnqueuedJob,
    IngestionJobRecord,
    StoredDocumentContent,
    StoredObject,
)
from packages.data.queue.contracts import QueuePayload


class ObjectStorage(Protocol):
    async def put_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        filename: str,
        content_type: str | None,
        stream: BinaryIO,
        byte_size: int,
        checksum: str,
    ) -> StoredObject: ...

    async def delete_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        object_key: str,
    ) -> None: ...

    async def get_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        object_key: str,
    ) -> StoredDocumentContent: ...


class JobQueue(Protocol):
    async def enqueue_ingestion_job(self, payload: QueuePayload) -> EnqueuedJob: ...


class EmbeddingJobQueue(Protocol):
    async def enqueue_embedding_job(self, payload: QueuePayload) -> EnqueuedJob: ...


class DocumentRepository(Protocol):
    async def create_upload_records(
        self,
        *,
        document: DocumentRecord,
        version: DocumentVersionRecord,
        job: IngestionJobRecord,
    ) -> tuple[DocumentRecord, DocumentVersionRecord, IngestionJobRecord]: ...

    async def get_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
    ) -> DocumentRecord | None: ...

    async def create_document_version_records(
        self,
        *,
        version: DocumentVersionRecord,
        job: IngestionJobRecord,
    ) -> tuple[DocumentVersionRecord, IngestionJobRecord]: ...

    async def mark_ingestion_job_queued(
        self,
        *,
        tenant_id: str,
        job_id: str,
        queue_job_id: str | None,
    ) -> IngestionJobRecord: ...

    async def mark_ingestion_job_failed(
        self,
        *,
        tenant_id: str,
        job_id: str,
        error_code: str,
        status: str = "failed_retryable",
    ) -> IngestionJobRecord: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    async def get_version(
        self,
        *,
        tenant_id: str,
        version_id: str,
    ) -> DocumentVersionRecord | None: ...

    async def get_ingestion_job(
        self,
        *,
        tenant_id: str,
        job_id: str,
    ) -> IngestionJobRecord | None: ...

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

    async def list_chunks_for_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        status: str | None = None,
    ) -> list[ChunkRecord]: ...

    async def get_chunk(
        self,
        *,
        tenant_id: str,
        chunk_id: str,
        document_id: str | None = None,
        version_id: str | None = None,
    ) -> ChunkRecord | None: ...

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

    async def claim_embedding_job(
        self,
        *,
        tenant_id: str,
        job_id: str,
        document_id: str,
        version_id: str,
        stale_before: datetime | None,
    ) -> EmbeddingJobRecord | None: ...

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

    async def get_document_version_status(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
    ) -> DocumentVersionStatusResult | None: ...

    async def soft_delete_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        deleted_by: str,
    ) -> int: ...

    async def soft_delete_document_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        deleted_by: str,
    ) -> int: ...

    async def soft_delete_chunks_for_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
    ) -> int: ...

    async def mark_embedding_job_failed(
        self,
        *,
        tenant_id: str,
        job_id: str,
        error_code: str,
        status: str,
        next_retry_at: datetime | None = None,
    ) -> EmbeddingJobRecord: ...

    async def get_embedding_job(
        self,
        *,
        tenant_id: str,
        job_id: str,
    ) -> EmbeddingJobRecord | None: ...

    async def list_embedding_jobs(
        self,
        *,
        tenant_id: str,
        status: str | None = None,
        version_id: str | None = None,
    ) -> list[EmbeddingJobRecord]: ...
