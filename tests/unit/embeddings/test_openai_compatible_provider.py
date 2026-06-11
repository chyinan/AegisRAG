from __future__ import annotations

import json

import httpx
import pytest

from packages.embeddings.adapters.openai_compatible import OpenAICompatibleEmbeddingProvider
from packages.embeddings.dto import EmbeddingRequest
from packages.embeddings.exceptions import (
    EMBEDDING_PROVIDER_FAILED,
    EMBEDDING_PROVIDER_RATE_LIMITED,
    EMBEDDING_PROVIDER_TIMEOUT,
    EmbeddingProviderError,
)


@pytest.mark.asyncio
async def test_embed_texts_posts_openai_compatible_body_and_maps_vectors() -> None:
    captured: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        assert request.url == "https://embedding.example/v1/embeddings"
        assert request.headers["authorization"] == "Bearer test-secret"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                    {"index": 1, "embedding": [0.4, 0.5, 0.6]},
                ],
                "model": "provider-returned-model",
                "usage": {"prompt_tokens": 9, "total_tokens": 9},
            },
        )

    provider = _provider(handler, api_key="test-secret")

    response = await provider.embed_texts(_request())

    assert response.provider == "openai_compatible"
    assert response.model == "configured-embedding"
    assert response.version is None
    assert response.dim == 3
    assert response.usage == {"prompt_tokens": 9, "total_tokens": 9, "text_count": 2}
    assert [item.index for item in response.vectors] == [0, 1]
    assert [item.chunk_id for item in response.vectors] == ["chunk-1", "chunk-2"]
    assert response.vectors[0].vector == [0.1, 0.2, 0.3]
    assert captured[0] == {
        "model": "configured-embedding",
        "input": ["alpha private text", "beta private text"],
    }
    assert "test-secret" not in json.dumps(captured[0])


@pytest.mark.asyncio
async def test_embed_texts_omits_authorization_when_api_key_is_not_configured() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})

    provider = _provider(handler, api_key=None)

    response = await provider.embed_texts(
        _request(texts=["local ollama text"], chunk_ids=["chunk-local"])
    )

    assert response.vectors[0].chunk_id == "chunk-local"
    assert response.dim == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_code", "retryable"),
    [
        (400, EMBEDDING_PROVIDER_FAILED, False),
        (401, EMBEDDING_PROVIDER_FAILED, False),
        (403, EMBEDDING_PROVIDER_FAILED, False),
        (429, EMBEDDING_PROVIDER_RATE_LIMITED, True),
        (500, EMBEDDING_PROVIDER_FAILED, True),
    ],
)
async def test_embed_texts_maps_http_errors_to_safe_provider_errors(
    status_code: int,
    expected_code: str,
    retryable: bool,
) -> None:
    provider = _provider(
        lambda _request: httpx.Response(status_code, json={"error": "secret provider body"}),
        api_key="test-secret",
    )

    with pytest.raises(EmbeddingProviderError) as exc_info:
        await provider.embed_texts(_request())

    assert exc_info.value.code == expected_code
    assert exc_info.value.retryable is retryable
    assert exc_info.value.details == {
        "provider": "openai_compatible",
        "model": "configured-embedding",
        "error_code": expected_code,
    }
    assert "secret provider body" not in str(exc_info.value.details)
    assert "test-secret" not in repr(exc_info.value)


@pytest.mark.asyncio
async def test_embed_texts_maps_timeout_and_malformed_response() -> None:
    timeout_provider = _provider(
        lambda request: (_ for _ in ()).throw(
            httpx.TimeoutException("secret timeout", request=request)
        ),
        api_key="test-secret",
    )
    malformed_provider = _provider(
        lambda _request: httpx.Response(200, json={"data": [{"index": 0, "embedding": []}]}),
        api_key="test-secret",
    )

    with pytest.raises(EmbeddingProviderError) as timeout:
        await timeout_provider.embed_texts(_request())
    with pytest.raises(EmbeddingProviderError) as malformed:
        await malformed_provider.embed_texts(_request())

    assert timeout.value.code == EMBEDDING_PROVIDER_TIMEOUT
    assert timeout.value.retryable is True
    assert malformed.value.code == EMBEDDING_PROVIDER_FAILED
    assert malformed.value.retryable is False


def _provider(
    handler: httpx.MockTransport | object,
    *,
    api_key: str | None,
) -> OpenAICompatibleEmbeddingProvider:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return OpenAICompatibleEmbeddingProvider(
        provider="openai_compatible",
        model="configured-embedding",
        base_url="https://embedding.example/v1",
        api_key=api_key,
        client=httpx.AsyncClient(transport=transport),
    )


def _request(
    *,
    texts: list[str] | None = None,
    chunk_ids: list[str] | None = None,
) -> EmbeddingRequest:
    return EmbeddingRequest(
        texts=texts or ["alpha private text", "beta private text"],
        provider="openai_compatible",
        model="configured-embedding",
        timeout_seconds=1.0,
        retry_budget=0,
        chunk_ids=chunk_ids or ["chunk-1", "chunk-2"],
    )
