"""Shared factory functions: DB sessions, vector stores, providers, circuit breakers.

Extracted from service_dependencies.py as part of DI decoupling (T1 finding).
"""
from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

if TYPE_CHECKING:
    from packages.ingestion.parsers.ocr.ports import OCRProvider
from packages.common.config import AppSettings
from packages.data.storage.exceptions import StorageConfigurationError
from packages.data.storage.session import create_async_db_engine
from packages.data.storage.session import create_session_factory as _db_create_session_factory
from packages.embeddings.adapters.fake import FakeEmbeddingProvider
from packages.embeddings.adapters.openai_compatible import OpenAICompatibleEmbeddingProvider
from packages.embeddings.ports import EmbeddingProvider
from packages.llm.adapters import OpenAICompatibleChatProvider
from packages.llm.adapters.fake import FakeLLMProvider
from packages.llm.ports import LLMProvider
from packages.vectorstores.adapters.fake import FakeVectorStore
from packages.vectorstores.adapters.pgvector import PgVectorStore
from packages.vectorstores.ports import VectorStore


@lru_cache(maxsize=8)
def create_session_factory(database_url: str | None) -> async_sessionmaker[AsyncSession]:
    engine = create_async_db_engine(database_url)
    return _db_create_session_factory(engine)


def create_vector_store(
    vector_store_type: str,
    vector_index_dim: int,
    session: AsyncSession,
    *,
    milvus_uri: str = "http://localhost:19530",
    milvus_token: str = "",
    milvus_index_type: str = "HNSW",
) -> VectorStore:
    if vector_store_type == "fake":
        return FakeVectorStore(index_dim=vector_index_dim)
    if vector_store_type == "pgvector":
        return PgVectorStore(session, index_dim=vector_index_dim)
    if vector_store_type == "milvus":
        from packages.vectorstores.adapters.milvus import (  # noqa: PLC0415 – lazy to avoid forcing pymilvus at import time
            MilvusVectorStore,
        )
        return MilvusVectorStore(
            uri=milvus_uri,
            token=milvus_token,
            index_dim=vector_index_dim,
            index_type=milvus_index_type,
        )
    raise ValueError(
        "Unsupported VECTOR_STORE_TYPE. Supported values are 'fake', 'pgvector', 'milvus'."
    )


def create_embedding_provider(
    *,
    provider: str,
    model: str,
    dim: int,
    base_url: str | None,
    api_key: SecretStr | None,
    version: str | None,
) -> EmbeddingProvider:
    normalized = provider.strip().lower()
    if normalized == "fake":
        return FakeEmbeddingProvider(
            dim=dim, provider=normalized, model=model, version="fake-v1"
        )
    if normalized in {"openai_compatible", "openai", "qwen", "deepseek", "ollama"}:
        if base_url is None:
            raise StorageConfigurationError(
                details={
                    "provider": normalized,
                    "supported_embedding_providers": ["fake", "openai_compatible", "ollama"],
                    "missing_config_count": 1,
                }
            )
        secret = api_key.get_secret_value() if api_key is not None else None
        return OpenAICompatibleEmbeddingProvider(
            provider=normalized,
            model=model,
            version=version,
            base_url=base_url,
            api_key=secret,
        )
    raise StorageConfigurationError(
        details={
            "provider": normalized,
            "supported_embedding_providers": ["fake", "openai_compatible", "ollama"],
        }
    )


def create_llm_provider(settings: AppSettings) -> LLMProvider:
    provider = settings.llm_provider.strip().lower()
    if provider == "fake":
        return FakeLLMProvider(
            provider=provider,
            model=settings.llm_model,
            version="fake-v1",
            response_text=settings.llm_fake_response_text,
        )
    if provider in {"openai_compatible", "openai", "qwen", "deepseek"}:
        if settings.llm_base_url is None or settings.llm_api_key is None:
            raise StorageConfigurationError(
                details={
                    "provider": provider,
                    "supported_llm_providers": ["fake", "openai_compatible"],
                    "missing_config_count": 1,
                }
            )
        return OpenAICompatibleChatProvider(
            provider=provider,
            model=settings.llm_model,
            version=settings.llm_provider_version,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
        )
    raise StorageConfigurationError(
        details={
            "provider": provider,
            "supported_llm_providers": ["fake", "openai_compatible"],
        }
    )


def create_ocr_provider(settings: AppSettings) -> OCRProvider:
    """Create the configured OCR provider from settings.

    Thin wrapper — delegates to the canonical factory in the ingestion layer
    to avoid reverse ``apps.api → packages.ingestion`` dependencies.
    """
    from packages.ingestion.parsers.ocr.parsers import (
        create_ocr_provider as _create,
    )
    return _create(settings)


def create_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    timeout_seconds: float = 30.0,
) -> CircuitBreaker:
    return CircuitBreaker(
        name=name,
        config=CircuitBreakerConfig(
            failure_threshold=failure_threshold,
            timeout_seconds=timeout_seconds,
        ),
    )


class CircuitBreakerRegistry:
    """Thread-safe registry of named circuit breakers."""

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}

    def get_or_create(
        self,
        name: str,
        failure_threshold: int = 5,
        timeout_seconds: float = 30.0,
    ) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = create_circuit_breaker(
                name=name,
                failure_threshold=failure_threshold,
                timeout_seconds=timeout_seconds,
            )
        return self._breakers[name]
