from __future__ import annotations

import re
from collections.abc import Mapping
from math import isfinite
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from packages.common.logging import REDACTED_VALUE, redact_sensitive_data
from packages.embeddings.dto import EmbeddingRequest, EmbeddingResponse, EmbeddingVector
from packages.embeddings.exceptions import EmbeddingProviderError
from packages.embeddings.ports import EmbeddingProvider
from packages.retrieval.dto import RetrievalCandidate, RetrievalFilterSet, RetrievalRequest
from packages.retrieval.exceptions import (
    RETRIEVAL_EMBEDDING_FAILED,
    RETRIEVAL_VECTOR_SEARCH_FAILED,
    RetrievalError,
)
from packages.retrieval.filters import to_vector_acl_filter, to_vector_metadata_filters
from packages.vectorstores.dto import DistanceMetric, VectorSearchRequest, VectorSearchResult
from packages.vectorstores.exceptions import VectorStoreError
from packages.vectorstores.ports import VectorStore

_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_RETRIEVAL_SENSITIVE_METADATA_KEYS = {
    "chunk_content",
    "chunk_text",
    "content",
    "document_content",
    "embedding",
    "embedding_vector",
    "provider_raw_response",
    "query_vector",
    "raw_response",
    "text",
    "vector",
}


class DenseRetrieverConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    embedding_provider: str
    embedding_model: str
    embedding_version: str | None = None
    timeout_seconds: float
    retry_budget: int
    distance_metric: DistanceMetric = "cosine"

    @field_validator("embedding_provider", "embedding_model")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("embedding_version")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("timeout_seconds")
    @classmethod
    def _timeout_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("timeout_seconds must be greater than 0")
        return value

    @field_validator("retry_budget")
    @classmethod
    def _retry_budget_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("retry_budget must not be negative")
        return value


class DenseRetriever:
    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        config: DenseRetrieverConfig,
    ) -> None:
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store
        self._config = config

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        embedding = await self._embed_query(request=request, filters=filters)
        embedding_version = _validated_embedding_version_or_raise(
            embedding=embedding,
            request=request,
            filters=filters,
            config=self._config,
        )
        query_vector = _single_query_vector_or_raise(
            embedding=embedding,
            request=request,
            filters=filters,
            config=self._config,
        )
        vector_request = VectorSearchRequest(
            tenant_id=filters.tenant_id,
            query_vector=query_vector,
            embedding_dim=embedding.dim,
            top_k=request.top_k,
            score_threshold=request.score_threshold,
            metadata_filters=to_vector_metadata_filters(filters),
            acl_filter=to_vector_acl_filter(filters),
            include_deleted=False,
            distance_metric=self._config.distance_metric,
            embedding_provider=self._config.embedding_provider,
            embedding_model=self._config.embedding_model,
            embedding_version=embedding_version,
        )
        try:
            results = await self._vector_store.search(vector_request)
        except VectorStoreError as exc:
            raise RetrievalError(
                code=RETRIEVAL_VECTOR_SEARCH_FAILED,
                message="Dense retrieval vector search failed.",
                details=_safe_details(
                    request=request,
                    filters=filters,
                    error_code=RETRIEVAL_VECTOR_SEARCH_FAILED,
                    embedding=embedding,
                ),
                status_code=502,
            ) from exc
        except Exception as exc:
            raise RetrievalError(
                code=RETRIEVAL_VECTOR_SEARCH_FAILED,
                message="Dense retrieval vector search failed.",
                details=_safe_details(
                    request=request,
                    filters=filters,
                    error_code=RETRIEVAL_VECTOR_SEARCH_FAILED,
                    embedding=embedding,
                ),
                status_code=502,
            ) from exc

        return [_candidate_from_vector_result(result) for result in results]

    async def _embed_query(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> EmbeddingResponse:
        embedding_request = EmbeddingRequest(
            texts=[request.query],
            provider=self._config.embedding_provider,
            model=self._config.embedding_model,
            timeout_seconds=self._config.timeout_seconds,
            retry_budget=self._config.retry_budget,
            rate_limit_key=filters.tenant_id,
            metadata={
                "request_id": request.request_id,
                "trace_id": request.trace_id,
                "tenant_id": filters.tenant_id,
                "user_id": filters.user_id,
                "retrieval_method": "dense",
            },
        )
        try:
            return await self._embedding_provider.embed_texts(embedding_request)
        except EmbeddingProviderError as exc:
            raise RetrievalError(
                code=RETRIEVAL_EMBEDDING_FAILED,
                message="Dense retrieval query embedding failed.",
                details=_safe_details(
                    request=request,
                    filters=filters,
                    error_code=RETRIEVAL_EMBEDDING_FAILED,
                    config=self._config,
                    provider_error_code=exc.code,
                ),
                status_code=502,
            ) from exc
        except Exception as exc:
            raise RetrievalError(
                code=RETRIEVAL_EMBEDDING_FAILED,
                message="Dense retrieval query embedding failed.",
                details=_safe_details(
                    request=request,
                    filters=filters,
                    error_code=RETRIEVAL_EMBEDDING_FAILED,
                    config=self._config,
                ),
                status_code=502,
            ) from exc


def _single_query_vector_or_raise(
    *,
    embedding: EmbeddingResponse,
    request: RetrievalRequest,
    filters: RetrievalFilterSet,
    config: DenseRetrieverConfig,
) -> list[float]:
    if len(embedding.vectors) != 1:
        raise RetrievalError(
            code=RETRIEVAL_EMBEDDING_FAILED,
            message="Dense retrieval query embedding response is invalid.",
            details=_safe_details(
                request=request,
                filters=filters,
                error_code=RETRIEVAL_EMBEDDING_FAILED,
                embedding=embedding,
                config=config,
            ),
            status_code=502,
        )

    vector = embedding.vectors[0]
    if _is_empty_or_dimension_mismatch(vector=vector, expected_dim=embedding.dim):
        raise RetrievalError(
            code=RETRIEVAL_EMBEDDING_FAILED,
            message="Dense retrieval query embedding response is invalid.",
            details=_safe_details(
                request=request,
                filters=filters,
                error_code=RETRIEVAL_EMBEDDING_FAILED,
                embedding=embedding,
                config=config,
            ),
            status_code=502,
        )
    return vector.vector


def _validated_embedding_version_or_raise(
    *,
    embedding: EmbeddingResponse,
    request: RetrievalRequest,
    filters: RetrievalFilterSet,
    config: DenseRetrieverConfig,
) -> str:
    if embedding.provider != config.embedding_provider or embedding.model != config.embedding_model:
        raise RetrievalError(
            code=RETRIEVAL_EMBEDDING_FAILED,
            message="Dense retrieval query embedding response is invalid.",
            details=_safe_details(
                request=request,
                filters=filters,
                error_code=RETRIEVAL_EMBEDDING_FAILED,
                embedding=embedding,
                config=config,
            ),
            status_code=502,
        )

    if config.embedding_version is not None:
        if embedding.version != config.embedding_version:
            raise RetrievalError(
                code=RETRIEVAL_EMBEDDING_FAILED,
                message="Dense retrieval query embedding response is invalid.",
                details=_safe_details(
                    request=request,
                    filters=filters,
                    error_code=RETRIEVAL_EMBEDDING_FAILED,
                    embedding=embedding,
                    config=config,
                ),
                status_code=502,
            )
        return config.embedding_version

    if embedding.version is None:
        raise RetrievalError(
            code=RETRIEVAL_EMBEDDING_FAILED,
            message="Dense retrieval query embedding response is invalid.",
            details=_safe_details(
                request=request,
                filters=filters,
                error_code=RETRIEVAL_EMBEDDING_FAILED,
                embedding=embedding,
                config=config,
            ),
            status_code=502,
        )
    return embedding.version


def _is_empty_or_dimension_mismatch(*, vector: EmbeddingVector, expected_dim: int) -> bool:
    return (
        vector.index != 0
        or not vector.vector
        or len(vector.vector) != expected_dim
        or any(not isfinite(value) for value in vector.vector)
    )


def _safe_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    if _looks_like_local_absolute_path(value):
        return REDACTED_VALUE
    redacted = redact_sensitive_data(value)
    if isinstance(redacted, str):
        return redacted
    return REDACTED_VALUE


def _safe_mapping(value: Mapping[str, object]) -> dict[str, object]:
    return {
        str(key): _safe_metadata_value(key=str(key), value=item)
        for key, item in value.items()
    }


def _safe_metadata_value(*, key: str, value: object) -> object:
    normalized_key = key.strip().lower()
    if normalized_key in _RETRIEVAL_SENSITIVE_METADATA_KEYS:
        return REDACTED_VALUE
    if isinstance(value, Mapping):
        return _safe_mapping(value)
    if isinstance(value, list | tuple):
        return [
            _safe_metadata_value(key=normalized_key, value=item)
            for item in value
        ]
    if isinstance(value, str) and _looks_like_local_absolute_path(value):
        return REDACTED_VALUE
    return redact_sensitive_data(value)


def _looks_like_local_absolute_path(value: str) -> bool:
    normalized = value.strip()
    return (
        normalized.startswith("/")
        or normalized.startswith("\\\\")
        or _WINDOWS_ABSOLUTE_PATH.match(normalized) is not None
    )


def _candidate_from_vector_result(result: VectorSearchResult) -> RetrievalCandidate:
    return RetrievalCandidate(
        document_id=result.document_id,
        version_id=result.version_id,
        chunk_id=result.chunk_id,
        source=_safe_optional_text(result.source),
        source_type=result.source_type,
        source_uri=_safe_optional_text(result.source_uri),
        page_start=result.page_start,
        page_end=result.page_end,
        title_path=tuple(result.title_path),
        score=result.score,
        retrieval_method="dense",
        tenant_id=result.tenant_id,
        acl=_safe_mapping(result.acl),
        metadata=_safe_mapping(result.metadata),
    )


def _safe_details(
    *,
    request: RetrievalRequest,
    filters: RetrievalFilterSet,
    error_code: str,
    config: DenseRetrieverConfig | None = None,
    embedding: EmbeddingResponse | None = None,
    provider_error_code: str | None = None,
) -> Mapping[str, object]:
    details: dict[str, object] = {
        "request_id": request.request_id,
        "trace_id": request.trace_id,
        "tenant_id": filters.tenant_id,
        "user_id": filters.user_id,
        "top_k": request.top_k,
    }
    _add_embedding_summary(details=details, config=config, embedding=embedding)
    if provider_error_code is not None:
        details["provider_error_code"] = provider_error_code
    details["error_code"] = error_code
    return details


def _add_embedding_summary(
    *,
    details: dict[str, object],
    config: DenseRetrieverConfig | None,
    embedding: EmbeddingResponse | None,
) -> None:
    provider = _summary_value(embedding.provider if embedding is not None else None)
    model = _summary_value(embedding.model if embedding is not None else None)
    version = _summary_value(embedding.version if embedding is not None else None)
    dim = embedding.dim if embedding is not None else None

    if config is not None:
        provider = provider or config.embedding_provider
        model = model or config.embedding_model
        version = version or config.embedding_version

    if provider is not None:
        details["embedding_provider"] = provider
    if model is not None:
        details["embedding_model"] = model
    if version is not None:
        details["embedding_version"] = version
    if dim is not None:
        details["embedding_dim"] = dim


def _summary_value(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
