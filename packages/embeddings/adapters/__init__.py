"""Embedding provider adapters."""

from packages.embeddings.adapters.fake import FakeEmbeddingProvider
from packages.embeddings.adapters.openai_compatible import OpenAICompatibleEmbeddingProvider

__all__ = ["FakeEmbeddingProvider", "OpenAICompatibleEmbeddingProvider"]
