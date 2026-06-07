from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.worker.jobs.embedding_jobs import (
    _provider_from_settings,
    _vector_store_from_settings,
    process_document_embedding,
)
from packages.auth.context import AuthContext
from packages.common.config import AppSettings
from packages.common.context import AuthenticatedRequestContext
from packages.data.queue.embedding import EMBEDDING_JOB_TYPE


def test_process_document_embedding_validates_payload_and_delegates_to_service() -> None:
    class Result:
        status = "embedded"
        document_id = "doc-1"
        version_id = "ver-1"
        job_id = "job-1"
        chunk_count = 2
        dim = 8

    class FakeEmbeddingService:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def embed_job(
            self,
            context: AuthenticatedRequestContext,
            *,
            job_id: str,
            document_id: str,
            version_id: str,
        ) -> Result:
            assert context == AuthenticatedRequestContext(
                request_id="req-1",
                trace_id="trace-1",
                auth=AuthContext(user_id="user-1", tenant_id="tenant-1"),
            )
            self.calls.append(
                {"job_id": job_id, "document_id": document_id, "version_id": version_id}
            )
            return Result()

    service = FakeEmbeddingService()

    result = process_document_embedding(
        {
            "request_id": "req-1",
            "trace_id": "trace-1",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "job_type": EMBEDDING_JOB_TYPE,
            "resource_id": "job-1",
            "parameters": {"document_id": "doc-1", "version_id": "ver-1"},
        },
        embedding_service=service,
    )

    assert result == {
        "status": "embedded",
        "job_type": EMBEDDING_JOB_TYPE,
        "resource_id": "job-1",
        "document_id": "doc-1",
        "version_id": "ver-1",
        "chunk_count": 2,
        "dim": 8,
    }
    assert service.calls == [{"job_id": "job-1", "document_id": "doc-1", "version_id": "ver-1"}]


def test_process_document_embedding_rejects_unexpected_payload_shape() -> None:
    try:
        process_document_embedding(
            {
                "request_id": "req-1",
                "trace_id": "trace-1",
                "tenant_id": "tenant-1",
                "user_id": "user-1",
                "job_type": EMBEDDING_JOB_TYPE,
                "resource_id": "job-1",
                "parameters": {
                    "document_id": "doc-1",
                    "version_id": "ver-1",
                    "document_content": "must not pass",
                },
            },
        )
    except ValueError as exc:
        assert "payload" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected payload validation failure")


def test_embedding_worker_rejects_unsupported_real_provider_until_adapter_exists() -> None:
    settings = AppSettings(
        EMBEDDING_PROVIDER="openai",
        EMBEDDING_MODEL="text-embedding-3-small",
        EMBEDDING_DIM=1536,
    )

    with pytest.raises(ValueError, match="Unsupported EMBEDDING_PROVIDER"):
        _provider_from_settings(settings)


def test_embedding_worker_defaults_to_fake_vector_store_without_external_connection() -> None:
    settings = AppSettings(VECTOR_STORE_TYPE="fake", VECTOR_INDEX_DIM=8)

    store = _vector_store_from_settings(settings, cast(AsyncSession, object()))

    assert store.__class__.__name__ == "FakeVectorStore"


def test_embedding_worker_rejects_unsupported_vector_store_type() -> None:
    settings = AppSettings(VECTOR_STORE_TYPE="milvus")

    with pytest.raises(ValueError, match="Unsupported VECTOR_STORE_TYPE"):
        _vector_store_from_settings(settings, cast(AsyncSession, object()))
