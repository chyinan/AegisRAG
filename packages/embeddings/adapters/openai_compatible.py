from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.parse import urljoin

import httpx

from packages.embeddings.dto import EmbeddingRequest, EmbeddingResponse, EmbeddingVector
from packages.embeddings.exceptions import (
    EMBEDDING_PROVIDER_FAILED,
    EMBEDDING_PROVIDER_RATE_LIMITED,
    EMBEDDING_PROVIDER_TIMEOUT,
    EmbeddingProviderError,
)
from packages.providers.runtime import (
    ProviderCapacityError,
    ProviderDeadline,
    ProviderRuntime,
    ProviderRuntimeClosedError,
    positive_seconds,
)


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        base_url: str,
        api_key: str | None = None,
        version: str | None = None,
        client: httpx.AsyncClient | None = None,
        runtime: ProviderRuntime | None = None,
        total_timeout_seconds: float | None = None,
    ) -> None:
        self._provider = _required_text(provider, "provider")
        self._model = _required_text(model, "model")
        self._base_url = _required_text(base_url, "base_url").rstrip("/") + "/"
        self._api_key = _optional_text(api_key)
        self._version = _optional_text(version)
        self._client = client
        self._runtime = runtime or ProviderRuntime(client=client)
        self._owns_runtime = runtime is None
        self._total_timeout_seconds = (
            positive_seconds(total_timeout_seconds, "total timeout")
            if total_timeout_seconds is not None
            else None
        )

    async def aclose(self) -> None:
        if self._owns_runtime:
            await self._runtime.aclose()

    async def embed_texts(self, request: EmbeddingRequest) -> EmbeddingResponse:
        started = time.perf_counter()
        deadline = self._runtime.deadline(
            timeout_seconds=request.timeout_seconds,
            retry_budget=request.retry_budget,
            total_timeout_seconds=self._total_timeout_seconds,
        )
        payload = {
            "model": self._model,
            "input": request.texts,
        }
        response = await self._post_with_retries(
            request=request, payload=payload, deadline=deadline
        )
        body = _json_response(response=response, request=request)
        vectors = _vectors_from_body(body=body, request=request)
        dim = len(vectors[0].vector)
        result = EmbeddingResponse(
            vectors=vectors,
            provider=self._provider,
            model=self._model,
            version=self._version,
            dim=dim,
            usage=_usage_from_body(body, text_count=len(request.texts)),
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )
        try:
            deadline.remaining()
        except TimeoutError as exc:
            raise self._timeout_error() from exc
        return result

    async def _post_with_retries(
        self,
        *,
        request: EmbeddingRequest,
        payload: Mapping[str, object],
        deadline: ProviderDeadline,
    ) -> httpx.Response:
        lease = None
        try:
            lease = await self._runtime.acquire(deadline)
            attempt = 0
            while True:
                response = None
                try:
                    response = await deadline.wait(
                        self._post_once(request=request, payload=payload)
                    )
                    _raise_for_status(response=response, request=request)
                    return response
                except EmbeddingProviderError as exc:
                    error = exc
                    if not exc.retryable:
                        raise
                except (httpx.TimeoutException, TimeoutError) as exc:
                    error = self._timeout_error()
                    error.__cause__ = exc
                except httpx.HTTPError as exc:
                    error = self._error(
                        code=EMBEDDING_PROVIDER_FAILED,
                        message="Embedding provider request failed.",
                        retryable=True,
                    )
                    error.__cause__ = exc
                if attempt >= request.retry_budget:
                    raise error
                await self._runtime.backoff(attempt=attempt, deadline=deadline, response=response)
                attempt += 1
        except (ProviderCapacityError, ProviderRuntimeClosedError) as exc:
            error = self._error(
                code=EMBEDDING_PROVIDER_RATE_LIMITED,
                message="Embedding provider capacity is unavailable.",
                retryable=True,
            )
            error.status_code = 503
            raise error from exc
        except TimeoutError as exc:
            raise self._timeout_error() from exc
        finally:
            if lease is not None:
                lease.release()

    def _timeout_error(self) -> EmbeddingProviderError:
        return self._error(
            code=EMBEDDING_PROVIDER_TIMEOUT,
            message="Embedding provider timed out.",
            retryable=True,
        )

    async def _post_once(
        self,
        *,
        request: EmbeddingRequest,
        payload: Mapping[str, object],
    ) -> httpx.Response:
        self._runtime.ensure_open()
        if self._client is None and self._owns_runtime:
            client = httpx.AsyncClient()
            try:
                return await client.post(
                    self._endpoint(),
                    json=payload,
                    headers=self._headers(),
                    timeout=request.timeout_seconds,
                )
            finally:
                await self._runtime.close_owned_client(client)
        client = self._client if self._client is not None else self._runtime.client
        return await client.post(
            self._endpoint(),
            json=payload,
            headers=self._headers(),
            timeout=request.timeout_seconds,
        )

    def _endpoint(self) -> str:
        return urljoin(self._base_url, "embeddings")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _error(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> EmbeddingProviderError:
        return EmbeddingProviderError(
            code=code,
            message=message,
            retryable=retryable,
            details={
                "provider": self._provider,
                "model": self._model,
                "error_code": code,
            },
        )


def _json_response(*, response: httpx.Response, request: EmbeddingRequest) -> Mapping[str, Any]:
    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        raise _provider_error(
            request=request,
            code=EMBEDDING_PROVIDER_FAILED,
            message="Embedding provider returned malformed response.",
            retryable=False,
        ) from exc
    if not isinstance(body, Mapping):
        raise _provider_error(
            request=request,
            code=EMBEDDING_PROVIDER_FAILED,
            message="Embedding provider returned malformed response.",
            retryable=False,
        )
    return cast(Mapping[str, Any], body)


def _vectors_from_body(
    *,
    body: Mapping[str, Any],
    request: EmbeddingRequest,
) -> list[EmbeddingVector]:
    data = body.get("data")
    if not isinstance(data, Sequence) or isinstance(data, str | bytes) or not data:
        raise _provider_error(
            request=request,
            code=EMBEDDING_PROVIDER_FAILED,
            message="Embedding provider returned malformed response.",
            retryable=False,
        )
    vectors: list[EmbeddingVector] = []
    for position, item in enumerate(data):
        if not isinstance(item, Mapping):
            raise _malformed_response(request)
        index = item.get("index", position)
        embedding = item.get("embedding")
        if not isinstance(index, int):
            raise _malformed_response(request)
        if (
            not isinstance(embedding, Sequence)
            or isinstance(embedding, str | bytes)
            or not embedding
        ):
            raise _malformed_response(request)
        vector: list[float] = []
        for value in embedding:
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise _malformed_response(request)
            vector.append(float(value))
        vectors.append(
            EmbeddingVector(
                index=index,
                vector=vector,
                chunk_id=request.chunk_ids[index] if request.chunk_ids is not None else None,
            )
        )
    return sorted(vectors, key=lambda item: item.index)


def _usage_from_body(body: Mapping[str, Any], *, text_count: int) -> dict[str, object]:
    raw_usage = body.get("usage")
    usage = dict(raw_usage) if isinstance(raw_usage, Mapping) else {}
    usage["text_count"] = text_count
    return usage


def _raise_for_status(*, response: httpx.Response, request: EmbeddingRequest) -> None:
    if response.status_code < 400:
        return
    if response.status_code == 429:
        raise _provider_error(
            request=request,
            code=EMBEDDING_PROVIDER_RATE_LIMITED,
            message="Embedding provider rate limited the request.",
            retryable=True,
        )
    if 400 <= response.status_code < 500:
        raise _provider_error(
            request=request,
            code=EMBEDDING_PROVIDER_FAILED,
            message="Embedding provider rejected the request.",
            retryable=False,
        )
    raise _provider_error(
        request=request,
        code=EMBEDDING_PROVIDER_FAILED,
        message="Embedding provider request failed.",
        retryable=True,
    )


def _provider_error(
    *,
    request: EmbeddingRequest,
    code: str,
    message: str,
    retryable: bool,
) -> EmbeddingProviderError:
    return EmbeddingProviderError(
        code=code,
        message=message,
        retryable=retryable,
        details={
            "provider": request.provider,
            "model": request.model,
            "error_code": code,
        },
    )


def _malformed_response(request: EmbeddingRequest) -> EmbeddingProviderError:
    return _provider_error(
        request=request,
        code=EMBEDDING_PROVIDER_FAILED,
        message="Embedding provider returned malformed response.",
        retryable=False,
    )


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
