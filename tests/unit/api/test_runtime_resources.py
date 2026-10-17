from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from starlette.types import Scope

from apps.api.factories.common import create_embedding_provider, create_llm_provider
from apps.api.factories.retrieval import _create_reranker, create_retrieval_cache
from apps.api.middleware import ApplicationResourceMiddleware
from packages.common.config import AppSettings
from packages.common.runtime_resources import (
    ApplicationResources,
    application_resources,
    current_resources,
    resource_scope,
)
from packages.embeddings.adapters.openai_compatible import OpenAICompatibleEmbeddingProvider
from packages.llm.adapters.openai_compatible import OpenAICompatibleChatProvider


async def test_resources_reuse_provider_pools_without_mixing_provider_kinds() -> None:
    settings = AppSettings(
        LLM_PROVIDER="openai_compatible", LLM_BASE_URL="https://unused.invalid/v1",
        LLM_API_KEY="synthetic", LLM_MAX_IN_FLIGHT=2, LLM_MAX_WAITERS=3,
        EMBEDDING_MAX_IN_FLIGHT=1,
    )
    async with application_resources() as resources:
        first = create_llm_provider(settings)
        second = create_llm_provider(settings)
        assert isinstance(first, OpenAICompatibleChatProvider)
        assert isinstance(second, OpenAICompatibleChatProvider)
        assert first._runtime is second._runtime
        embedding = create_embedding_provider(
            provider="openai_compatible", model="synthetic", dim=8,
            base_url="https://unused.invalid/v1", api_key=None, version=None,
            runtime_settings=settings,
        )
        assert isinstance(embedding, OpenAICompatibleEmbeddingProvider)
        assert embedding._runtime is not first._runtime
        cache = create_retrieval_cache()
        assert cache is create_retrieval_cache()
        assert cache is not create_retrieval_cache(max_size=3)
        bge_settings = AppSettings(RERANK_PROVIDER="bge_local", RERANK_MODEL="synthetic")
        bge = _create_reranker(settings=bge_settings)
        assert bge is _create_reranker(settings=bge_settings)
        assert resources._items
    assert resources._closed and resources._items == {}
    assert cache._closed
    with pytest.raises(RuntimeError, match="closed"):
        resources.get_or_create(("later",), lambda: cache)
    assert current_resources() is None


async def test_asgi_middleware_propagates_lifespan_resources_to_sibling_request_task() -> None:
    resources = ApplicationResources()
    observed: list[ApplicationResources | None] = []

    async def app(scope: object, receive: object, send: object) -> None:
        observed.append(current_resources())
        await asyncio.sleep(0)
        observed.append(current_resources())

    async def receive() -> dict[str, object]:
        return {"type": "http.disconnect"}

    async def send(message: object) -> None:
        pass

    scope = cast(Scope, {
        "type": "http", "app": SimpleNamespace(
            state=SimpleNamespace(runtime_resources=resources),
        ),
    })
    middleware = ApplicationResourceMiddleware(app)
    with resource_scope(None):
        await asyncio.create_task(middleware(scope, receive, send))
        assert current_resources() is None
    assert observed == [resources, resources]
    await resources.aclose()


async def test_resource_cleanup_runs_other_closes_when_one_close_fails() -> None:
    closed: list[int] = []

    class Resource:
        def __init__(self, number: int) -> None:
            self.number = number

        async def aclose(self) -> None:
            closed.append(self.number)
            if self.number == 2:
                raise ValueError("synthetic close failure")

    resources = ApplicationResources()
    for number in [1, 2, 3]:
        resources.get_or_create((number,), lambda number=number: Resource(number))
    with pytest.raises(ExceptionGroup):
        await resources.aclose()
    assert closed == [3, 2, 1]
    assert resources._closed and resources._items == {}


async def test_cancelled_shutdown_retains_cleanup_and_closes_all_resources() -> None:
    started, release = asyncio.Event(), asyncio.Event()
    closed: list[str] = []

    class Resource:
        def __init__(self, name: str) -> None:
            self.name = name

        async def aclose(self) -> None:
            if self.name == "slow":
                started.set()
                await release.wait()
            closed.append(self.name)

    resources = ApplicationResources()
    resources.get_or_create(("first",), lambda: Resource("first"))
    resources.get_or_create(("slow",), lambda: Resource("slow"))
    task = asyncio.create_task(resources.aclose())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert resources._closed and closed == []
    release.set()
    await asyncio.wait_for(resources.aclose(), 1)
    assert closed == ["slow", "first"]


def test_real_app_lifespan_and_sibling_requests_share_then_replace_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    import apps.api.main as main

    settings = AppSettings(
        LLM_PROVIDER="openai_compatible", LLM_BASE_URL="https://unused.invalid/v1",
        LLM_API_KEY="synthetic",
    )
    monkeypatch.setattr(main, "load_settings", lambda: settings)
    app = main.create_app()
    observed: list[object] = []

    @app.get("/_runtime_contract")
    async def contract() -> dict[str, bool]:
        provider = create_llm_provider(settings)
        assert isinstance(provider, OpenAICompatibleChatProvider)
        observed.append(provider._runtime)
        return {"owned": current_resources() is app.state.runtime_resources}

    with TestClient(app) as client:
        assert client.get("/_runtime_contract").json() == {"owned": True}
        assert client.get("/_runtime_contract").json() == {"owned": True}
        assert observed[0] is observed[1]
        first = app.state.runtime_resources
    assert first._closed
    with TestClient(app) as client:
        assert client.get("/_runtime_contract").json() == {"owned": True}
        assert observed[2] is not observed[0]
        assert app.state.runtime_resources is not first
    assert app.state.runtime_resources._closed
