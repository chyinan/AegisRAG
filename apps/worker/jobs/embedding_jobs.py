from __future__ import annotations

import asyncio
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from packages.auth.context import AuthContext
from packages.common.config import AppSettings, load_settings
from packages.common.context import AuthenticatedRequestContext
from packages.data.queue.contracts import QueuePayload
from packages.data.queue.embedding import EMBEDDING_JOB_TYPE
from packages.data.storage.audit_repositories import SqlAlchemyAuditPort
from packages.data.storage.repositories import DocumentRepository
from packages.data.storage.session import create_async_db_engine, create_session_factory
from packages.embeddings.adapters.fake import FakeEmbeddingProvider
from packages.embeddings.adapters.openai_compatible import OpenAICompatibleEmbeddingProvider
from packages.embeddings.ports import EmbeddingProvider
from packages.embeddings.service import EmbeddingJobService
from packages.vectorstores.adapters.fake import FakeVectorStore
from packages.vectorstores.adapters.pgvector import PgVectorStore
from packages.vectorstores.ports import VectorStore


class _EmbeddingJobResult(Protocol):
    @property
    def status(self) -> str: ...

    @property
    def document_id(self) -> str: ...

    @property
    def version_id(self) -> str: ...

    @property
    def job_id(self) -> str: ...

    @property
    def chunk_count(self) -> int: ...

    @property
    def dim(self) -> int | None: ...


class _EmbeddingService(Protocol):
    async def embed_job(
        self,
        context: AuthenticatedRequestContext,
        *,
        job_id: str,
        document_id: str,
        version_id: str,
    ) -> _EmbeddingJobResult: ...


def process_document_embedding(
    payload: dict[str, object],
    *,
    embedding_service: _EmbeddingService | None = None,
) -> dict[str, object]:
    try:
        queue_payload = QueuePayload.model_validate(payload)
    except Exception as exc:
        raise ValueError("invalid embedding queue payload") from exc

    if queue_payload.job_type != EMBEDDING_JOB_TYPE:
        raise ValueError("invalid embedding queue payload: unexpected job_type")

    parameters = queue_payload.parameters
    if set(parameters) != {"document_id", "version_id"}:
        raise ValueError("invalid embedding queue payload: expected document_id and version_id")
    document_id = parameters["document_id"]
    version_id = parameters["version_id"]
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("invalid embedding queue payload: document_id is required")
    if not isinstance(version_id, str) or not version_id.strip():
        raise ValueError("invalid embedding queue payload: version_id is required")

    context = AuthenticatedRequestContext(
        request_id=queue_payload.request_id,
        trace_id=queue_payload.trace_id,
        auth=AuthContext(
            user_id=queue_payload.user_id,
            tenant_id=queue_payload.tenant_id,
        ),
    )
    result = asyncio.run(
        _embed_with_service(
            context=context,
            job_id=queue_payload.resource_id,
            document_id=document_id,
            version_id=version_id,
            embedding_service=embedding_service,
        )
    )
    return {
        "status": result.status,
        "job_type": queue_payload.job_type,
        "resource_id": queue_payload.resource_id,
        "document_id": result.document_id,
        "version_id": result.version_id,
        "chunk_count": result.chunk_count,
        "dim": result.dim,
    }


async def _embed_with_service(
    *,
    context: AuthenticatedRequestContext,
    job_id: str,
    document_id: str,
    version_id: str,
    embedding_service: _EmbeddingService | None,
) -> _EmbeddingJobResult:
    if embedding_service is not None:
        return await embedding_service.embed_job(
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
            service = EmbeddingJobService(
                repository=DocumentRepository(session),
                provider=_provider_from_settings(settings),
                audit=SqlAlchemyAuditPort(session),
                vector_store=_vector_store_from_settings(settings, session),
                timeout_seconds=settings.embedding_timeout_seconds,
                retry_budget=settings.embedding_retry_budget,
            )
            return await service.embed_job(
                context,
                job_id=job_id,
                document_id=document_id,
                version_id=version_id,
            )
    finally:
        await engine.dispose()


def _provider_from_settings(settings: AppSettings) -> EmbeddingProvider:
    provider = settings.embedding_provider.strip().lower()
    if provider == "fake":
        return FakeEmbeddingProvider(
            dim=settings.embedding_dim,
            provider=provider,
            model=settings.embedding_model,
        )
    if provider in {"openai_compatible", "openai", "qwen", "deepseek", "ollama"}:
        if settings.embedding_base_url is None:
            raise ValueError("EMBEDDING_BASE_URL is required for real embedding providers.")
        return OpenAICompatibleEmbeddingProvider(
            provider=provider,
            model=settings.embedding_model,
            version=settings.embedding_provider_version,
            base_url=settings.embedding_base_url,
            api_key=(
                settings.embedding_api_key.get_secret_value()
                if settings.embedding_api_key is not None
                else None
            ),
        )
    raise ValueError(
        "Unsupported EMBEDDING_PROVIDER. Supported values are 'fake', "
        "'openai_compatible', and 'ollama'."
    )


def _vector_store_from_settings(settings: AppSettings, session: AsyncSession) -> VectorStore:
    if settings.vector_store_type == "fake":
        return FakeVectorStore(index_dim=settings.vector_index_dim)
    if settings.vector_store_type == "pgvector":
        return PgVectorStore(session, index_dim=settings.vector_index_dim)
    if settings.vector_store_type == "milvus":
        from packages.vectorstores.adapters.milvus import (  # noqa: PLC0415
            MilvusVectorStore,
        )
        return MilvusVectorStore(
            uri=settings.milvus_uri,
            token=settings.milvus_token,
            index_dim=settings.vector_index_dim,
            index_type=settings.milvus_index_type,
        )
    raise ValueError(
        "Unsupported VECTOR_STORE_TYPE. Supported values are 'fake', 'pgvector', 'milvus'."
    )
