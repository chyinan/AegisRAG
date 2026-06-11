from __future__ import annotations

import asyncio
from typing import Protocol

from packages.auth.context import AuthContext
from packages.common.config import load_settings
from packages.common.context import AuthenticatedRequestContext
from packages.data.adapters.minio_object_storage import MinioObjectStorage
from packages.data.queue.adapters import RQEmbeddingJobQueue
from packages.data.queue.contracts import QueuePayload
from packages.data.queue.ingestion import INGESTION_JOB_TYPE
from packages.data.storage.audit_repositories import SqlAlchemyAuditPort
from packages.data.storage.repositories import DocumentRepository
from packages.data.storage.session import create_async_db_engine, create_session_factory
from packages.ingestion.service import IngestionParseService


class _ParseJobResult(Protocol):
    status: str
    document_id: str
    version_id: str
    job_id: str
    section_count: int
    chunk_count: int | None
    embedding_job_id: str | None


class _ParseService(Protocol):
    async def parse_job(
        self,
        context: AuthenticatedRequestContext,
        *,
        job_id: str,
        document_id: str,
        version_id: str,
    ) -> _ParseJobResult: ...


def process_document_ingestion(
    payload: dict[str, object],
    *,
    parse_service: _ParseService | None = None,
) -> dict[str, object]:
    try:
        queue_payload = QueuePayload.model_validate(payload)
    except Exception as exc:
        raise ValueError("invalid ingestion queue payload") from exc

    if queue_payload.job_type != INGESTION_JOB_TYPE:
        raise ValueError("invalid ingestion queue payload: unexpected job_type")

    parameters = queue_payload.parameters
    if set(parameters) != {"document_id", "version_id"}:
        raise ValueError("invalid ingestion queue payload: expected document_id and version_id")
    document_id = parameters["document_id"]
    version_id = parameters["version_id"]
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("invalid ingestion queue payload: document_id is required")
    if not isinstance(version_id, str) or not version_id.strip():
        raise ValueError("invalid ingestion queue payload: version_id is required")

    context = AuthenticatedRequestContext(
        request_id=queue_payload.request_id,
        trace_id=queue_payload.trace_id,
        auth=AuthContext(
            user_id=queue_payload.user_id,
            tenant_id=queue_payload.tenant_id,
        ),
    )

    result = asyncio.run(
        _parse_with_service(
            context=context,
            job_id=queue_payload.resource_id,
            document_id=document_id,
            version_id=version_id,
            parse_service=parse_service,
        )
    )
    response = {
        "status": result.status,
        "job_type": queue_payload.job_type,
        "resource_id": queue_payload.resource_id,
        "document_id": result.document_id,
        "version_id": result.version_id,
        "section_count": result.section_count,
    }
    chunk_count = getattr(result, "chunk_count", None)
    embedding_job_id = getattr(result, "embedding_job_id", None)
    if chunk_count is not None:
        response["chunk_count"] = chunk_count
    if embedding_job_id is not None:
        response["embedding_job_id"] = embedding_job_id
    return response


async def _parse_with_service(
    *,
    context: AuthenticatedRequestContext,
    job_id: str,
    document_id: str,
    version_id: str,
    parse_service: _ParseService | None,
) -> _ParseJobResult:
    if parse_service is not None:
        return await parse_service.parse_job(
            context,
            job_id=job_id,
            document_id=document_id,
            version_id=version_id,
        )

    settings = load_settings()
    engine = create_async_db_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            service = IngestionParseService(
                repository=DocumentRepository(session),
                object_storage=MinioObjectStorage.from_settings(settings),
                audit=SqlAlchemyAuditPort(session),
                embedding_queue=RQEmbeddingJobQueue.from_settings(settings),
                embedding_provider=settings.embedding_provider,
                embedding_model=settings.embedding_model,
                embedding_version=settings.embedding_provider_version,
                embedding_dim=settings.embedding_dim,
            )
            return await service.parse_job(
                context,
                job_id=job_id,
                document_id=document_id,
                version_id=version_id,
            )
    finally:
        await engine.dispose()
