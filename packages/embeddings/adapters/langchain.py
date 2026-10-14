"""Optional LangChain embeddings adapter; framework types stay in infrastructure."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

import httpx

from packages.common.framework_context import isolated_framework_context
from packages.embeddings.dto import EmbeddingRequest, EmbeddingResponse, EmbeddingVector
from packages.embeddings.exceptions import (
    EMBEDDING_BATCH_SIZE_MISMATCH,
    EMBEDDING_PROVIDER_FAILED,
    EMBEDDING_PROVIDER_RATE_LIMITED,
    EMBEDDING_PROVIDER_TIMEOUT,
    EMBEDDING_VECTOR_DIMENSION_MISMATCH,
    EMBEDDING_VECTOR_EMPTY,
    EmbeddingProviderError,
)


class LangChainEmbeddingProvider:
    """Adapt ordered ``aembed_documents`` results to the EmbeddingProvider DTO.

    LangChain's embeddings interface does not expose token usage; the response
    explicitly marks it unavailable. The configured dimension is validated, not
    silently requested as a vendor-specific dimension-reduction feature.
    Each attempt gets the request timeout; retries match the native adapter.
    """

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        base_url: str,
        api_key: str | None = None,
        version: str | None = None,
        dim: int | None = None,
        embeddings: Any = None,
    ) -> None:
        self._provider = _required_text(provider, "provider")
        self._model = _required_text(model, "model")
        self._version = version.strip() if version and version.strip() else None
        base_url = _required_text(base_url, "base_url")
        if dim is not None and (isinstance(dim, bool) or not isinstance(dim, int) or dim <= 0):
            raise ValueError("dim must be a positive integer")
        self._dim = dim
        try:
            from langchain_core.embeddings import Embeddings
            from langchain_openai import OpenAIEmbeddings
            from langsmith import tracing_context
        except ImportError:
            raise RuntimeError(
                "LangChain embeddings adapter requires the optional framework dependencies; "
                "install the 'langchain' extra."
            ) from None
        self._tracing_context = tracing_context
        self._embeddings_type = OpenAIEmbeddings
        self._embeddings = embeddings
        self._model_options: dict[str, Any] = {}
        if embeddings is None:
            self._model_options = {
                "model": self._model,
                "base_url": base_url,
                # Local OpenAI-compatible servers may not require authentication.
                "api_key": api_key.strip() if api_key and api_key.strip() else "not-required",
                "max_retries": 0,
                "request_timeout": 60.0,
                # Preserve exact text/batch semantics; no tokenizer downloads,
                # splitting, averaging or normalization inside the framework.
                "check_embedding_ctx_length": False,
            }
            return
        if not isinstance(embeddings, Embeddings):
            raise ValueError("embeddings must implement LangChain Embeddings")
        configured_model = getattr(embeddings, "model", None)
        if isinstance(configured_model, str) and configured_model != self._model:
            raise ValueError("embeddings model does not match configured model")
        if getattr(embeddings, "max_retries", 0) != 0:
            raise ValueError("embeddings must have max_retries=0")
        if isinstance(embeddings, OpenAIEmbeddings):
            if embeddings.check_embedding_ctx_length:
                raise ValueError("embeddings must have check_embedding_ctx_length=False")
            # LangChain's non-tokenizing path drops API indexes. Validate and
            # sort before it does so, preserving the native ordered DTO contract.
            embeddings = embeddings.model_copy(
                update={"async_client": _OrderedAsyncClient(embeddings.async_client)}
            )
        self._embeddings = embeddings

    async def embed_texts(self, request: EmbeddingRequest) -> EmbeddingResponse:
        if (
            request.provider != self._provider
            or request.model != self._model
            or not math.isfinite(request.timeout_seconds)
        ):
            raise self._error(
                EMBEDDING_PROVIDER_FAILED,
                "Embedding request does not match the configured provider.",
                False,
            )
        started = time.perf_counter()
        for attempt in range(request.retry_budget + 1):
            failure: EmbeddingProviderError | None = None
            try:
                with self._tracing_context(enabled=False):
                    async with asyncio.timeout(request.timeout_seconds):
                        async with self._model_context() as model:
                            raw_vectors = await self._call_untraced(
                                lambda: model.aembed_documents(list(request.texts))
                            )
                vectors = self._vectors(raw_vectors, request)
                return EmbeddingResponse(
                    vectors=vectors,
                    provider=self._provider,
                    model=self._model,
                    version=self._version,
                    dim=len(vectors[0].vector),
                    usage={"text_count": len(request.texts), "usage_unavailable_count": 1},
                    latency_ms=round((time.perf_counter() - started) * 1000, 3),
                )
            except Exception as exc:
                failure = self._translate_error(exc)
            if not failure.retryable or attempt == request.retry_budget:
                raise failure from None
        raise AssertionError("unreachable retry state")

    async def _call_untraced(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        async def run() -> Any:
            from langchain_core.globals import get_debug

            unsafe_logging = get_debug() or any(
                logging.getLogger(name).isEnabledFor(logging.DEBUG)
                for name in ("openai._base_client", "openai._response")
            )
            if unsafe_logging:
                raise RuntimeError("Framework and SDK debug logging must be disabled")
            with self._tracing_context(enabled=False):
                return await operation()

        return await asyncio.create_task(run(), context=isolated_framework_context())

    @asynccontextmanager
    async def _model_context(self) -> AsyncIterator[Any]:
        if self._embeddings is not None:
            yield self._embeddings
            return
        with httpx.Client() as sync_client:
            async with httpx.AsyncClient() as async_client:
                model = self._embeddings_type(
                    **self._model_options,
                    http_client=sync_client,
                    http_async_client=async_client,
                )
                model = model.model_copy(
                    update={"async_client": _OrderedAsyncClient(model.async_client)}
                )
                yield model

    def _vectors(self, raw: Any, request: EmbeddingRequest) -> list[EmbeddingVector]:
        if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
            raise self._error(EMBEDDING_PROVIDER_FAILED, "Malformed embedding response.", False)
        if len(raw) != len(request.texts):
            raise self._error(
                EMBEDDING_BATCH_SIZE_MISMATCH, "Embedding response batch size mismatch.", False
            )
        vectors: list[EmbeddingVector] = []
        expected_dim = self._dim
        for index, values in enumerate(raw):
            if not isinstance(values, Sequence) or isinstance(values, str | bytes) or not values:
                raise self._error(EMBEDDING_VECTOR_EMPTY, "Embedding vector is empty.", False)
            if expected_dim is None:
                expected_dim = len(values)
            if len(values) != expected_dim:
                raise self._error(
                    EMBEDDING_VECTOR_DIMENSION_MISMATCH,
                    "Embedding vector dimension mismatch.",
                    False,
                )
            vector: list[float] = []
            for value in values:
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int | float)
                    or not math.isfinite(value)
                ):
                    raise self._error(
                        EMBEDDING_PROVIDER_FAILED, "Malformed embedding vector.", False
                    )
                vector.append(float(value))
            vectors.append(
                EmbeddingVector(
                    index=index,
                    vector=vector,
                    chunk_id=request.chunk_ids[index] if request.chunk_ids is not None else None,
                )
            )
        return vectors

    def _translate_error(self, exc: Exception) -> EmbeddingProviderError:
        if isinstance(exc, EmbeddingProviderError):
            return exc
        status = getattr(exc, "status_code", None)
        if (
            isinstance(exc, TimeoutError | httpx.TimeoutException)
            or type(exc).__name__ == "APITimeoutError"
        ):
            return self._error(EMBEDDING_PROVIDER_TIMEOUT, "Embedding provider timed out.", True)
        if status == 429:
            return self._error(
                EMBEDDING_PROVIDER_RATE_LIMITED,
                "Embedding provider rate limited the request.",
                True,
            )
        retryable = (
            (isinstance(status, int) and status >= 500)
            or isinstance(exc, httpx.TransportError)
            or type(exc).__name__ == "APIConnectionError"
        )
        return self._error(
            EMBEDDING_PROVIDER_FAILED, "Embedding provider request failed.", retryable
        )

    def _error(self, code: str, message: str, retryable: bool) -> EmbeddingProviderError:
        return EmbeddingProviderError(
            code=code,
            message=message,
            retryable=retryable,
            details={"provider": self._provider, "model": self._model, "error_code": code},
        )


class _OrderedAsyncClient:
    """Preserve OpenAI response indexes before LangChain discards them."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def create(self, *, input: list[str], **kwargs: Any) -> dict[str, Any]:
        raw = await self._client.create(input=input, **kwargs)
        body = raw if isinstance(raw, Mapping) else raw.model_dump()
        data = body.get("data")
        if not isinstance(data, list) or len(data) != len(input):
            raise ValueError("Embedding API response count mismatch")
        indexes: set[int] = set()
        for item in data:
            if not isinstance(item, Mapping):
                raise ValueError("Malformed embedding API response")
            index = item.get("index")
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or not 0 <= index < len(input)
                or index in indexes
            ):
                raise ValueError("Embedding API response indexes are not a permutation")
            indexes.add(index)
        return {**body, "data": sorted(data, key=lambda item: item["index"])}


def _required_text(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be blank")
    return normalized
