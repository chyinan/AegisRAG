import pytest

from packages.embeddings.adapters.fake import FakeEmbeddingProvider
from packages.embeddings.dto import EmbeddingRequest
from packages.embeddings.exceptions import (
    EMBEDDING_PROVIDER_RATE_LIMITED,
    EMBEDDING_PROVIDER_TIMEOUT,
    EmbeddingProviderError,
)


@pytest.mark.asyncio
async def test_fake_embedding_provider_returns_deterministic_vectors_in_batch_order() -> None:
    provider = FakeEmbeddingProvider(dim=4, provider="fake", model="fake-embedding")
    request = EmbeddingRequest(
        texts=["alpha", "beta"],
        chunk_ids=["chunk-1", "chunk-2"],
        provider="fake",
        model="fake-embedding",
        timeout_seconds=1.0,
        retry_budget=2,
        rate_limit_key="tenant-1",
    )

    first = await provider.embed_texts(request)
    second = await provider.embed_texts(request)

    assert first == second
    assert first.provider == "fake"
    assert first.model == "fake-embedding"
    assert first.version == "fake-v1"
    assert first.dim == 4
    assert [vector.index for vector in first.vectors] == [0, 1]
    assert [vector.chunk_id for vector in first.vectors] == ["chunk-1", "chunk-2"]
    assert all(len(vector.vector) == 4 for vector in first.vectors)
    assert first.usage["text_count"] == 2
    assert first.usage["total_characters"] == 9


@pytest.mark.asyncio
async def test_fake_embedding_provider_supports_expected_failure_modes() -> None:
    request = EmbeddingRequest(
        texts=["alpha"],
        chunk_ids=["chunk-1"],
        provider="fake",
        model="fake-embedding",
        timeout_seconds=1.0,
        retry_budget=1,
        rate_limit_key="tenant-1",
    )

    timeout_provider = FakeEmbeddingProvider(failure_mode="timeout")
    rate_limited_provider = FakeEmbeddingProvider(failure_mode="rate_limited")

    with pytest.raises(EmbeddingProviderError) as timeout_error:
        await timeout_provider.embed_texts(request)
    with pytest.raises(EmbeddingProviderError) as rate_error:
        await rate_limited_provider.embed_texts(request)

    assert timeout_error.value.code == EMBEDDING_PROVIDER_TIMEOUT
    assert timeout_error.value.retryable is True
    assert rate_error.value.code == EMBEDDING_PROVIDER_RATE_LIMITED
    assert rate_error.value.retryable is True
