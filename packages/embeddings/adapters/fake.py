from __future__ import annotations

import asyncio
import hashlib
from typing import Literal

from packages.embeddings.dto import EmbeddingRequest, EmbeddingResponse, EmbeddingVector
from packages.embeddings.exceptions import (
    EMBEDDING_PROVIDER_FAILED,
    EMBEDDING_PROVIDER_RATE_LIMITED,
    EMBEDDING_PROVIDER_TIMEOUT,
    EmbeddingProviderError,
)

FailureMode = Literal[
    "timeout",
    "rate_limited",
    "failed",
    "batch_mismatch",
    "dimension_mismatch",
]


class FakeEmbeddingProvider:
    def __init__(
        self,
        *,
        dim: int = 8,
        provider: str = "fake",
        model: str = "fake-embedding",
        version: str = "fake-v1",
        failure_mode: FailureMode | None = None,
    ) -> None:
        if dim <= 0:
            raise ValueError("dim must be greater than 0")
        self._dim = dim
        self._provider = provider
        self._model = model
        self._version = version
        self._failure_mode = failure_mode

    async def embed_texts(self, request: EmbeddingRequest) -> EmbeddingResponse:
        await asyncio.sleep(0)
        self._raise_if_configured_failure()

        vectors = [
            EmbeddingVector(
                index=index,
                chunk_id=request.chunk_ids[index] if request.chunk_ids is not None else None,
                vector=_deterministic_vector(
                    text=text,
                    index=index,
                    chunk_id=request.chunk_ids[index] if request.chunk_ids is not None else None,
                    dim=self._dim,
                    provider=self._provider,
                    model=self._model,
                    version=self._version,
                ),
            )
            for index, text in enumerate(request.texts)
        ]
        if self._failure_mode == "batch_mismatch":
            vectors = vectors[:-1]
        elif self._failure_mode == "dimension_mismatch" and vectors:
            first = vectors[0]
            vectors[0] = first.model_copy(update={"vector": first.vector[:-1]})

        return EmbeddingResponse(
            vectors=vectors,
            provider=self._provider,
            model=self._model,
            version=self._version,
            dim=self._dim,
            usage={
                "text_count": len(request.texts),
                "total_characters": sum(len(text) for text in request.texts),
            },
            latency_ms=0.0,
        )

    def _raise_if_configured_failure(self) -> None:
        if self._failure_mode == "timeout":
            raise EmbeddingProviderError(
                code=EMBEDDING_PROVIDER_TIMEOUT,
                message="Fake embedding provider timeout.",
                retryable=True,
            )
        if self._failure_mode == "rate_limited":
            raise EmbeddingProviderError(
                code=EMBEDDING_PROVIDER_RATE_LIMITED,
                message="Fake embedding provider rate limited.",
                retryable=True,
            )
        if self._failure_mode == "failed":
            raise EmbeddingProviderError(
                code=EMBEDDING_PROVIDER_FAILED,
                message="Fake embedding provider failed.",
                retryable=True,
            )
        if self._failure_mode == "batch_mismatch":
            return
        if self._failure_mode == "dimension_mismatch":
            return


def _deterministic_vector(
    *,
    text: str,
    index: int,
    chunk_id: str | None,
    dim: int,
    provider: str,
    model: str,
    version: str,
) -> list[float]:
    seed = f"{provider}|{model}|{version}|{index}|{chunk_id or ''}|{text}".encode()
    digest = hashlib.sha256(seed).digest()
    values: list[float] = []
    counter = 0
    while len(values) < dim:
        block = hashlib.sha256(digest + counter.to_bytes(4, "big")).digest()
        for byte in block:
            values.append(round((byte / 255.0) * 2 - 1, 6))
            if len(values) == dim:
                break
        counter += 1
    return values
