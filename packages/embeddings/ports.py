from __future__ import annotations

from typing import Protocol

from packages.embeddings.dto import EmbeddingRequest, EmbeddingResponse


class EmbeddingProvider(Protocol):
    async def embed_texts(self, request: EmbeddingRequest) -> EmbeddingResponse: ...
