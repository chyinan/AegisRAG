from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.service_dependencies import get_openwebui_chat_adapter
from packages.common.context import AuthenticatedRequestContext
from packages.rag.openwebui import (
    OpenAIChatChoice,
    OpenAIChatChoiceMessage,
    OpenAIChatCompletionRequest,
    OpenAIChatCompletionResponse,
    OpenAIModel,
    OpenAIModelListResponse,
    OpenAIUsage,
)


class StubOpenWebUIAdapter:
    def __init__(self) -> None:
        self.chat_calls: list[tuple[AuthenticatedRequestContext, OpenAIChatCompletionRequest]] = []
        self.stream_calls: list[
            tuple[AuthenticatedRequestContext, OpenAIChatCompletionRequest]
        ] = []

    def list_models(self) -> OpenAIModelListResponse:
        return OpenAIModelListResponse(
            data=(OpenAIModel(id="configured-rag-model", created=1, owned_by="fake"),)
        )

    async def chat_completion(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: OpenAIChatCompletionRequest,
    ) -> OpenAIChatCompletionResponse:
        self.chat_calls.append((context, request))
        return OpenAIChatCompletionResponse(
            id="chatcmpl-req-openwebui",
            created=1,
            model="configured-rag-model",
            choices=(
                OpenAIChatChoice(
                    index=0,
                    message=OpenAIChatChoiceMessage(content="trusted answer"),
                ),
            ),
            usage=OpenAIUsage(total_tokens=3),
            request_id=context.request_id,
            trace_id=context.trace_id,
            session_id="session-1",
            metadata={"safe": "ok"},
        )

    async def stream_chat_completion(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: OpenAIChatCompletionRequest,
    ) -> AsyncIterator[str]:
        self.stream_calls.append((context, request))
        yield (
            'data: {"object":"chat.completion.chunk",'
            '"choices":[{"delta":{"content":"trusted"},"index":0,"finish_reason":null}]}\n\n'
        )
        yield "data: [DONE]\n\n"


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_models_route_returns_openai_compatible_model_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    adapter = StubOpenWebUIAdapter()
    app.dependency_overrides[get_openwebui_chat_adapter] = lambda: adapter
    client = TestClient(app)

    response = client.get("/v1/models", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "configured-rag-model"
    assert response.json()["data"][0]["object"] == "model"


def test_chat_completions_non_stream_calls_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    adapter = StubOpenWebUIAdapter()
    app.dependency_overrides[get_openwebui_chat_adapter] = lambda: adapter
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers=_auth_headers(),
        json={
            "model": "configured-rag-model",
            "messages": [{"role": "user", "content": "question"}],
            "stream": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["request_id"] == "req-openwebui"
    assert body["choices"][0]["message"]["content"] == "trusted answer"
    assert len(adapter.chat_calls) == 1
    context, request = adapter.chat_calls[0]
    assert context.auth.tenant_id == "tenant-1"
    assert request.messages[-1].content == "question"


def test_chat_completions_stream_returns_openai_sse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    adapter = StubOpenWebUIAdapter()
    app.dependency_overrides[get_openwebui_chat_adapter] = lambda: adapter
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers=_auth_headers(),
        json={
            "model": "configured-rag-model",
            "messages": [{"role": "user", "content": "question"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.endswith("data: [DONE]\n\n")
    assert "event: token" not in response.text
    assert len(adapter.stream_calls) == 1


def test_chat_completions_rejects_missing_permission_before_adapter_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    adapter = StubOpenWebUIAdapter()
    app.dependency_overrides[get_openwebui_chat_adapter] = lambda: adapter
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers=_auth_headers(permissions=""),
        json={"model": "configured-rag-model", "messages": [{"role": "user", "content": "q"}]},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "RAG_QUERY_FORBIDDEN"
    assert adapter.chat_calls == []


def _auth_headers(permissions: str = "document:read,retrieval:query") -> dict[str, str]:
    return {
        "X-Request-ID": "req-openwebui",
        "X-Trace-ID": "trace-openwebui",
        "X-User-ID": "user-1",
        "X-Tenant-ID": "tenant-1",
        "X-Roles": "knowledge_user",
        "X-Permissions": permissions,
    }
