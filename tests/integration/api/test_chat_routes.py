from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.service_dependencies import get_chat_application_service
from packages.common.context import AuthenticatedRequestContext
from packages.rag import ChatHistoryMessageResponse, ChatHistoryResponse, ChatResponse, Citation, QueryCommand
from packages.rag.streaming import FinalEventPayload, RagStreamEvent, TokenEventPayload


class StubChatApplicationService:
    def __init__(self) -> None:
        self.calls: list[tuple[AuthenticatedRequestContext, QueryCommand, str | None]] = []
        self.stream_calls: list[tuple[AuthenticatedRequestContext, QueryCommand, str | None]] = []
        self.history_calls: list[tuple[AuthenticatedRequestContext, str, int]] = []

    async def chat(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        session_id: str | None,
    ) -> ChatResponse:
        self.calls.append((context, command, session_id))
        return ChatResponse(
            request_id=context.request_id,
            trace_id=context.trace_id,
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            session_id=session_id or "session-created",
            answer="基于上下文的回答。",
            citations=(_citation(),),
            metadata={"memory": {"message_count": 1, "used_count": 1, "dropped_count": 0}},
        )

    async def stream_chat(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        session_id: str | None,
    ) -> AsyncIterator[RagStreamEvent]:
        self.stream_calls.append((context, command, session_id))
        yield RagStreamEvent(
            event="token",
            payload=TokenEventPayload(
                request_id=context.request_id,
                trace_id=context.trace_id,
                index=0,
                delta="基于",
            ),
        )
        yield RagStreamEvent(
            event="final",
            payload=FinalEventPayload(
                request_id=context.request_id,
                trace_id=context.trace_id,
                session_id=session_id or "session-created",
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                answer="基于上下文的回答。",
                citations=(_citation(),),
                metadata={"session_id": session_id or "session-created"},
            ),
        )

    async def history(
        self,
        *,
        context: AuthenticatedRequestContext,
        session_id: str,
        limit: int = 50,
    ) -> ChatHistoryResponse:
        self.history_calls.append((context, session_id, limit))
        return ChatHistoryResponse(
            session_id=session_id,
            messages=(
                ChatHistoryMessageResponse(
                    role="user",
                    content="policy",
                    sequence_no=1,
                    request_id="req-user",
                    trace_id="trace-user",
                    created_at="2026-06-10T12:00:00+00:00",
                ),
                ChatHistoryMessageResponse(
                    role="assistant",
                    content="基于上下文的回答。",
                    sequence_no=2,
                    request_id="req-assistant",
                    trace_id="trace-assistant",
                    created_at="2026-06-10T12:00:01+00:00",
                    citations=(_citation(),),
                ),
            ),
        )


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_chat_route_returns_session_response_and_calls_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubChatApplicationService()
    app.dependency_overrides[get_chat_application_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/chat",
        headers=_auth_headers(),
        json={"query": "policy", "session_id": "session-1", "top_k": 3},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["session_id"] == "session-1"
    assert body["data"]["answer"] == "基于上下文的回答。"
    assert body["data"]["citations"][0]["source_display_name"] == "policy.md"
    assert "source_uri" not in body["data"]["citations"][0]
    assert len(service.calls) == 1
    context, command, session_id = service.calls[0]
    assert context.auth.tenant_id == "tenant-1"
    assert command.query == "policy"
    assert command.top_k == 3
    assert session_id == "session-1"


def test_chat_route_rejects_missing_permission_before_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubChatApplicationService()
    app.dependency_overrides[get_chat_application_service] = lambda: service
    client = TestClient(app)

    response = client.post("/chat", headers=_auth_headers(permissions=""), json={"query": "x"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "RAG_QUERY_FORBIDDEN"
    assert service.calls == []


def test_chat_route_rejects_missing_auth_before_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENABLE_DEV_AUTH_HEADERS", raising=False)
    service = StubChatApplicationService()
    app.dependency_overrides[get_chat_application_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/chat",
        headers={"X-Request-ID": "req-no-auth"},
        json={"query": "policy"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_CONTEXT_REQUIRED"
    assert service.calls == []


def test_chat_stream_route_returns_sse_final_with_session_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubChatApplicationService()
    app.dependency_overrides[get_chat_application_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/chat/stream",
        headers=_auth_headers(),
        json={"query": "policy", "session_id": "session-1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: token\n" in response.text
    assert "event: final\n" in response.text
    assert '"session_id":"session-1"' in response.text
    assert len(service.stream_calls) == 1


def test_chat_history_route_returns_authorized_session_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubChatApplicationService()
    app.dependency_overrides[get_chat_application_service] = lambda: service
    client = TestClient(app)

    response = client.get("/chat/history?session_id=session-1&limit=20", headers=_auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["session_id"] == "session-1"
    assert body["data"]["messages"][0]["role"] == "user"
    assert body["data"]["messages"][1]["citations"][0]["chunk_id"] == "chunk-1"
    assert len(service.history_calls) == 1
    context, session_id, limit = service.history_calls[0]
    assert context.auth.tenant_id == "tenant-1"
    assert session_id == "session-1"
    assert limit == 20


def _auth_headers(permissions: str = "document:read,retrieval:query") -> dict[str, str]:
    return {
        "X-Request-ID": "req-chat",
        "X-Trace-ID": "trace-chat",
        "X-User-ID": "user-1",
        "X-Tenant-ID": "tenant-1",
        "X-Roles": "knowledge_user",
        "X-Permissions": permissions,
    }


def _citation() -> Citation:
    return Citation(
        document_id="doc-1",
        version_id="v1",
        chunk_id="chunk-1",
        source_display_name="policy.md",
        source_type="markdown",
        title_path=("Policy",),
        retrieval_method="hybrid",
        score=0.9,
    )
