"""Real LangChain embeddings with no network or tokenizer downloads."""

# ruff: noqa: E402 - optional framework imports follow importorskip
from __future__ import annotations

import asyncio
import json
import traceback
from typing import Any

import httpx
import pytest

langchain_openai = pytest.importorskip("langchain_openai")
from langchain_core.embeddings import Embeddings
from langsmith import get_tracing_context

from packages.embeddings.adapters.langchain import LangChainEmbeddingProvider
from packages.embeddings.adapters.openai_compatible import OpenAICompatibleEmbeddingProvider
from packages.embeddings.dto import EmbeddingRequest
from packages.embeddings.exceptions import EmbeddingProviderError


def _request(**updates: Any) -> EmbeddingRequest:
    values = {
        "texts": ["private source one", "private source two"],
        "chunk_ids": ["chunk-1", "chunk-2"],
        "provider": "openai_compatible",
        "model": "test-embedding",
        "timeout_seconds": 1.0,
        "retry_budget": 0,
    }
    values.update(updates)
    return EmbeddingRequest(**values)


def _provider(client: httpx.AsyncClient, kind: str = "langchain") -> Any:
    kwargs = {
        "provider": "openai_compatible",
        "model": "test-embedding",
        "base_url": "https://models.test/v1",
        "api_key": "test-key",
        "version": "test-version",
    }
    if kind == "native":
        return OpenAICompatibleEmbeddingProvider(**kwargs, client=client)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("sync I/O"))
    ) as sync_client:
        model = langchain_openai.OpenAIEmbeddings(
            model="test-embedding",
            base_url="https://models.test/v1",
            api_key="test-key",
            max_retries=0,
            check_embedding_ctx_length=False,
            http_async_client=client,
            http_client=sync_client,
        )
    return LangChainEmbeddingProvider(**kwargs, dim=3, embeddings=model)


def _body(data: Any = None) -> dict[str, Any]:
    return {
        "object": "list",
        "model": "test-embedding",
        "data": data
        if data is not None
        else [
            {"object": "embedding", "index": 1, "embedding": [4.0, 5.0, 6.0]},
            {"object": "embedding", "index": 0, "embedding": [1.0, 2.0, 3.0]},
        ],
        "usage": {"prompt_tokens": 8, "total_tokens": 8},
    }


@pytest.mark.parametrize("kind", ["native", "langchain"])
async def test_supported_embedding_contract_matches_native_with_reordered_wire_results(
    kind: str,
) -> None:
    sent: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await _provider(client, kind).embed_texts(_request())
    assert [vector.model_dump() for vector in result.vectors] == [
        {"index": 0, "chunk_id": "chunk-1", "vector": [1.0, 2.0, 3.0]},
        {"index": 1, "chunk_id": "chunk-2", "vector": [4.0, 5.0, 6.0]},
    ]
    assert result.provider == "openai_compatible"
    assert result.model == "test-embedding"
    assert result.version == "test-version"
    assert result.dim == 3
    assert result.usage["text_count"] == 2
    assert result.latency_ms >= 0
    assert sent[0]["input"] == _request().texts
    assert sent[0]["model"] == "test-embedding"
    if kind == "langchain":
        assert result.usage == {"text_count": 2, "usage_unavailable_count": 1}


@pytest.mark.parametrize(
    "data",
    [
        [{"index": 0, "embedding": [1.0, 2.0, 3.0]}],
        [{"index": 0, "embedding": [1.0, 2.0, 3.0]}, {"index": 0, "embedding": [4.0, 5.0, 6.0]}],
        [{"index": -1, "embedding": [1.0, 2.0, 3.0]}, {"index": 1, "embedding": [4.0, 5.0, 6.0]}],
        [{"index": 0, "embedding": [1.0, 2.0, 3.0]}, {"index": 2, "embedding": [4.0, 5.0, 6.0]}],
    ],
)
async def test_invalid_indexes_or_batch_never_rebind_chunk_identity(data: Any) -> None:
    calls = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_body(data))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(EmbeddingProviderError) as caught:
            await _provider(client).embed_texts(_request(retry_budget=3))
    assert caught.value.retryable is False
    assert calls == 1


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "EMBEDDING_PROVIDER_FAILED", False),
        (400, "EMBEDDING_PROVIDER_FAILED", False),
        (429, "EMBEDDING_PROVIDER_RATE_LIMITED", True),
        (503, "EMBEDDING_PROVIDER_FAILED", True),
    ],
)
async def test_error_retry_budget_and_redaction(status: int, code: str, retryable: bool) -> None:
    calls = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            status, json={"error": {"message": "private source one sk-private-value"}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(EmbeddingProviderError) as caught:
            await _provider(client).embed_texts(_request(retry_budget=2))
    assert caught.value.code == code
    assert caught.value.retryable is retryable
    assert calls == (3 if retryable else 1)
    formatted = "".join(traceback.format_exception(caught.value))
    assert "private source one" not in formatted and "sk-private-value" not in formatted


class _ObservedEmbeddings(Embeddings):
    def __init__(self, vectors: Any = None, *, slow: bool = False) -> None:
        self.vectors = vectors if vectors is not None else [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
        self.observed: list[bool | None] = []
        self.cancelled = False
        self.slow = slow
        self.started = asyncio.Event()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("Sync embedding API must not run")

    def embed_query(self, text: str) -> list[float]:
        raise AssertionError("Query API must not run")

    async def aembed_documents(self, texts: list[str]) -> Any:
        self.observed.append(get_tracing_context()["enabled"])
        self.started.set()
        try:
            if self.slow:
                await asyncio.sleep(10)
            return self.vectors
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def _observed_provider(model: _ObservedEmbeddings) -> LangChainEmbeddingProvider:
    return LangChainEmbeddingProvider(
        provider="openai_compatible",
        model="test-embedding",
        base_url="https://models.test/v1",
        dim=3,
        embeddings=model,
    )


@pytest.mark.parametrize(
    "vectors,code",
    [
        ([], "EMBEDDING_BATCH_SIZE_MISMATCH"),
        ([[1.0, 2.0], [1.0, 2.0]], "EMBEDDING_VECTOR_DIMENSION_MISMATCH"),
        ([[], []], "EMBEDDING_VECTOR_EMPTY"),
        ([[1.0, float("nan"), 3.0], [1.0, 2.0, 3.0]], "EMBEDDING_PROVIDER_FAILED"),
        ([[1.0, float("inf"), 3.0], [1.0, 2.0, 3.0]], "EMBEDDING_PROVIDER_FAILED"),
        ([[1.0, True, 3.0], [1.0, 2.0, 3.0]], "EMBEDDING_PROVIDER_FAILED"),
    ],
)
async def test_dimension_finiteness_and_batch_validation(vectors: Any, code: str) -> None:
    model = _ObservedEmbeddings(vectors)
    with pytest.raises(EmbeddingProviderError, match=code):
        await _observed_provider(model).embed_texts(_request(retry_budget=2))
    assert len(model.observed) == 1


async def test_tracing_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    model = _ObservedEmbeddings()
    await _observed_provider(model).embed_texts(_request())
    assert model.observed == [False]


async def test_timeout_cancels_underlying_embedding_call() -> None:
    model = _ObservedEmbeddings(slow=True)
    with pytest.raises(EmbeddingProviderError, match="EMBEDDING_PROVIDER_TIMEOUT"):
        await _observed_provider(model).embed_texts(_request(timeout_seconds=0.01, retry_budget=1))
    assert len(model.observed) == 2
    assert model.cancelled


async def test_external_cancellation_is_not_retried_or_translated() -> None:
    model = _ObservedEmbeddings(slow=True)
    task = asyncio.create_task(_observed_provider(model).embed_texts(_request(retry_budget=3)))
    await model.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert model.cancelled
    assert len(model.observed) == 1


async def test_mismatched_request_never_calls_model() -> None:
    model = _ObservedEmbeddings()
    with pytest.raises(EmbeddingProviderError, match="EMBEDDING_PROVIDER_FAILED"):
        await _observed_provider(model).embed_texts(_request(model="wrong"))
    assert model.observed == []


async def test_framework_execution_does_not_inherit_ambient_context() -> None:
    from contextvars import ContextVar

    private_context: ContextVar[str | None] = ContextVar("private_context", default=None)

    class ContextObservingEmbeddings(_ObservedEmbeddings):
        async def aembed_documents(self, texts: list[str]) -> Any:
            assert private_context.get() is None
            return await super().aembed_documents(texts)

    token = private_context.set("tenant-private-context")
    try:
        await _observed_provider(ContextObservingEmbeddings()).embed_texts(_request())
        assert private_context.get() == "tenant-private-context"
    finally:
        private_context.reset(token)


async def test_global_debug_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import langchain_core.globals as globals_module

    model = _ObservedEmbeddings()
    monkeypatch.setattr(globals_module, "_debug", True)
    with pytest.raises(EmbeddingProviderError):
        await _observed_provider(model).embed_texts(_request())
    assert model.observed == []


async def test_owned_clients_close_after_each_call_and_provider_can_be_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[Any] = []
    real_sync = httpx.Client
    real_async = httpx.AsyncClient

    class LocalSyncClient(real_sync):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(transport=httpx.MockTransport(lambda _: pytest.fail("sync HTTP")))
            clients.append(self)

    class LocalAsyncClient(real_async):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_body()))
            )
            clients.append(self)

    monkeypatch.setattr(httpx, "Client", LocalSyncClient)
    monkeypatch.setattr(httpx, "AsyncClient", LocalAsyncClient)
    provider = LangChainEmbeddingProvider(
        provider="openai_compatible",
        model="test-embedding",
        base_url="https://models.test/v1",
        api_key="test-key",
        dim=3,
    )
    for _ in range(2):
        assert (await provider.embed_texts(_request())).vectors[0].vector == [1.0, 2.0, 3.0]
    assert len(clients) == 4
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("logger_name", ["openai._base_client", "openai._response"])
async def test_sdk_debug_fails_closed_without_network_or_private_log(
    logger_name: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    def handle(_: httpx.Request) -> httpx.Response:
        pytest.fail("SDK debug configuration must fail before HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = _provider(client)
        with caplog.at_level(logging.DEBUG, logger=logger_name):
            with pytest.raises(EmbeddingProviderError):
                await provider.embed_texts(_request())
    assert "private source one" not in caplog.text
    assert "test-key" not in caplog.text
