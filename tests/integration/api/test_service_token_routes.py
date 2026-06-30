from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient

from apps.api.factories.common import create_session_factory
from apps.api.main import app
from apps.api.service_dependencies import get_chat_adapter
from packages.common.context import AuthenticatedRequestContext
from packages.rag.service_token import (
    CitationEvidenceLink,
    OpenAIChatChoice,
    OpenAIChatChoiceMessage,
    OpenAIChatCompletionRequest,
    OpenAIChatCompletionResponse,
    OpenAIModel,
    OpenAIModelListResponse,
    OpenAIUsage,
)


class StubServiceTokenAdapter:
    def __init__(self) -> None:
        self.model_calls = 0
        self.chat_calls: list[tuple[AuthenticatedRequestContext, OpenAIChatCompletionRequest]] = []
        self.stream_calls: list[
            tuple[AuthenticatedRequestContext, OpenAIChatCompletionRequest]
        ] = []

    def list_models(self) -> OpenAIModelListResponse:
        self.model_calls += 1
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
            id="chatcmpl-req-service_token",
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
            citations=(),
            evidence_links=(
                CitationEvidenceLink(
                    citation_ref="citation-1",
                    evidence_url=(
                        "/governance?document_id=doc-1&version_id=v1&chunk_id=chunk-1"
                        "&page_start=1&page_end=1&request_id=req-service_token"
                        "&citation_ref=citation-1#source-evidence"
                    ),
                    evidence_query={
                        "document_id": "doc-1",
                        "version_id": "v1",
                        "chunk_id": "chunk-1",
                        "page_start": 1,
                        "page_end": 1,
                        "request_id": context.request_id,
                        "citation_ref": "citation-1",
                    },
                    document_id="doc-1",
                    version_id="v1",
                    chunk_id="chunk-1",
                    page_start=1,
                    page_end=1,
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    source_display_name="policy.md",
                ),
            ),
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
        yield (
            'data: {"object":"chat.completion.chunk","tool_event":'
            '{"event":"tool_call","agent_run_id":"run-1","tool_call_id":"call-1",'
            '"tool_name":"rag_search","status":"started","latency_ms":0,'
            '"error_code":null,"request_id":"req-service_token","trace_id":"trace-service_token"},'
            '"choices":[{"delta":{},"index":0,"finish_reason":null}]}\n\n'
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
    adapter = StubServiceTokenAdapter()
    app.dependency_overrides[get_chat_adapter] = lambda: adapter
    client = TestClient(app)

    response = client.get("/v1/models", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "configured-rag-model"
    assert response.json()["data"][0]["object"] == "model"
    assert adapter.model_calls == 1


def test_models_route_uses_real_provider_wiring_without_external_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_MODEL", "configured-real-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-secret")
    create_session_factory.cache_clear()
    client = TestClient(app)

    response = client.get("/v1/models", headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "configured-real-model"
    assert response.json()["data"][0]["owned_by"] == "openai_compatible"


def test_models_route_accepts_service_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_service_token(monkeypatch)
    adapter = StubServiceTokenAdapter()
    app.dependency_overrides[get_chat_adapter] = lambda: adapter
    client = TestClient(app)

    response = client.get("/v1/models", headers=_service_token_headers())

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "configured-rag-model"
    assert adapter.model_calls == 1


def test_models_route_rejects_missing_token_before_adapter_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENABLE_DEV_AUTH_HEADERS", raising=False)
    adapter = StubServiceTokenAdapter()
    app.dependency_overrides[get_chat_adapter] = lambda: adapter
    client = TestClient(app)

    response = client.get("/v1/models", headers={"X-Request-ID": "req-missing-service_token"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_CONTEXT_REQUIRED"
    assert adapter.model_calls == 0


def test_chat_completions_non_stream_calls_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    adapter = StubServiceTokenAdapter()
    app.dependency_overrides[get_chat_adapter] = lambda: adapter
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
    assert body["request_id"] == "req-service_token"
    assert body["choices"][0]["message"]["content"] == "trusted answer"
    assert body["evidence_links"][0]["document_id"] == "doc-1"
    assert body["evidence_links"][0]["trace_id"] == "trace-service_token"
    assert body["evidence_links"][0]["source_display_name"] == "policy.md"
    assert body["evidence_links"][0]["evidence_query"] == {
        "document_id": "doc-1",
        "version_id": "v1",
        "chunk_id": "chunk-1",
        "page_start": 1,
        "page_end": 1,
        "request_id": "req-service_token",
        "citation_ref": "citation-1",
    }
    forbidden_body = response.text
    for forbidden in (
        "source_uri",
        "object_key",
        "bearer",
        "access_token",
        "service_token",
        "prompt\":",
        '"query"',
        "chunk text",
    ):
        assert forbidden not in forbidden_body
    assert len(adapter.chat_calls) == 1
    context, request = adapter.chat_calls[0]
    assert context.auth.tenant_id == "tenant-1"
    assert request.messages[-1].content == "question"


def test_chat_completions_non_stream_accepts_service_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_service_token(monkeypatch)
    adapter = StubServiceTokenAdapter()
    app.dependency_overrides[get_chat_adapter] = lambda: adapter
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers=_service_token_headers(),
        json={
            "model": "configured-rag-model",
            "messages": [{"role": "user", "content": "question"}],
            "stream": False,
        },
    )

    assert response.status_code == 200
    assert len(adapter.chat_calls) == 1
    context, request = adapter.chat_calls[0]
    assert context.auth.user_id == "service_token-service"
    assert context.auth.tenant_id == "tenant-service_token"
    assert request.messages[-1].content == "question"


def test_chat_completions_stream_returns_openai_sse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    adapter = StubServiceTokenAdapter()
    app.dependency_overrides[get_chat_adapter] = lambda: adapter
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
    assert '"tool_event"' in response.text
    assert '"tool_name":"rag_search"' in response.text
    for forbidden in ("arguments", "raw_output", "observation", "source_uri", "roles"):
        assert forbidden not in response.text
    assert len(adapter.stream_calls) == 1


def test_chat_completions_stream_accepts_service_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_service_token(monkeypatch)
    adapter = StubServiceTokenAdapter()
    app.dependency_overrides[get_chat_adapter] = lambda: adapter
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers=_service_token_headers(),
        json={
            "model": "configured-rag-model",
            "messages": [{"role": "user", "content": "question"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert len(adapter.stream_calls) == 1


def test_chat_completions_rejects_invalid_bearer_before_adapter_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_service_token(monkeypatch)
    adapter = StubServiceTokenAdapter()
    app.dependency_overrides[get_chat_adapter] = lambda: adapter
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer wrong-token", "X-Request-ID": "req-invalid-token"},
        json={
            "model": "configured-rag-model",
            "messages": [{"role": "user", "content": "question"}],
            "stream": False,
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_CONTEXT_INVALID"
    assert response.json()["error"]["details"] == {"reason": "invalid_auth_context"}
    assert "wrong-token" not in response.text
    assert "jwt_secret_not_configured" not in response.text
    assert "service_token" not in response.text
    assert adapter.chat_calls == []
    assert adapter.stream_calls == []


def test_chat_completions_rejects_missing_permission_before_adapter_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    adapter = StubServiceTokenAdapter()
    app.dependency_overrides[get_chat_adapter] = lambda: adapter
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers=_auth_headers(permissions=""),
        json={"model": "configured-rag-model", "messages": [{"role": "user", "content": "q"}]},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "RAG_QUERY_FORBIDDEN"
    assert adapter.chat_calls == []


def test_chat_completions_rejects_service_token_without_rag_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_service_token(monkeypatch, permissions=["document:read"])
    adapter = StubServiceTokenAdapter()
    app.dependency_overrides[get_chat_adapter] = lambda: adapter
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers=_service_token_headers(),
        json={"model": "configured-rag-model", "messages": [{"role": "user", "content": "q"}]},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "RAG_QUERY_FORBIDDEN"
    assert adapter.chat_calls == []


@pytest.mark.parametrize("field", ["tenant_id", "user_id", "acl", "roles", "permissions"])
def test_chat_completions_rejects_authorization_metadata_filter_fields(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    _configure_service_token(monkeypatch)
    adapter = StubServiceTokenAdapter()
    app.dependency_overrides[get_chat_adapter] = lambda: adapter
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        headers=_service_token_headers(),
        json={
            "model": "configured-rag-model",
            "messages": [{"role": "user", "content": "q"}],
            "metadata_filter": {field: "attacker-value"},
        },
    )

    assert response.status_code == 422
    assert "authorization scope fields" in response.text
    assert adapter.chat_calls == []


def _auth_headers(permissions: str = "document:read,retrieval:query") -> dict[str, str]:
    return {
        "X-Request-ID": "req-service_token",
        "X-Trace-ID": "trace-service_token",
        "X-User-ID": "user-1",
        "X-Tenant-ID": "tenant-1",
        "X-Roles": "knowledge_user",
        "X-Permissions": permissions,
    }


def _service_token_headers() -> dict[str, str]:
    return {
        "X-Request-ID": "req-service_token",
        "X-Trace-ID": "trace-service_token",
        "Authorization": "Bearer local-service_token-service-token",
    }


def _configure_service_token(
    monkeypatch: pytest.MonkeyPatch,
    *,
    permissions: list[str] | None = None,
) -> None:
    monkeypatch.delenv("ENABLE_DEV_AUTH_HEADERS", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    record: dict[str, object] = {
        "token_sha256": sha256(b"local-service_token-service-token").hexdigest(),
        "user_id": "service_token-service",
        "tenant_id": "tenant-service_token",
        "roles": ["service_token"],
        "department": "platform",
    }
    if permissions is not None:
        record["permissions"] = permissions
    monkeypatch.setenv("SERVICE_TOKEN_HASHES_JSON", json.dumps([record]))
