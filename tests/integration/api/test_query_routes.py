from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.service_dependencies import get_rag_query_application_service
from packages.common.context import AuthenticatedRequestContext
from packages.rag import (
    RAG_QUERY_FAILED,
    Citation,
    QueryCommand,
    QueryResponse,
    RagQueryError,
)
from packages.rag.streaming import (
    CitationEventPayload,
    ErrorEventPayload,
    FinalEventPayload,
    RagStreamEvent,
    TokenEventPayload,
)


class StubRagQueryApplicationService:
    def __init__(self, *, error: RagQueryError | None = None) -> None:
        self.calls: list[tuple[AuthenticatedRequestContext, QueryCommand]] = []
        self.stream_calls: list[tuple[AuthenticatedRequestContext, QueryCommand]] = []
        self.stream_events: tuple[RagStreamEvent, ...] | None = None
        self._error = error

    async def query(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
    ) -> QueryResponse:
        self.calls.append((context, command))
        if self._error is not None:
            raise self._error
        return QueryResponse(
            request_id=context.request_id,
            trace_id=context.trace_id,
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            answer="基于上下文的回答。",
            citations=(
                Citation(
                    document_id="doc-1",
                    version_id="v1",
                    chunk_id="chunk-1",
                    source_display_name="policy.md",
                    source_type="markdown",
                    page_start=1,
                    page_end=1,
                    title_path=("Policy",),
                    retrieval_method="hybrid",
                    score=0.91,
                ),
            ),
            metadata={
                "retrieval": {"top_k": command.top_k, "result_count": 1},
                "context": {"item_count": 1, "citation_source_count": 1},
                "generation": {
                    "provider": "fake",
                    "model": "fake-llm",
                    "token_usage": {"total_tokens": 12},
                },
                "latency_ms": 4.2,
                "error_code": None,
            },
        )

    async def stream_query(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
    ) -> AsyncIterator[RagStreamEvent]:
        self.stream_calls.append((context, command))
        events = self.stream_events or _default_stream_events(context=context, command=command)
        for event in events:
            yield event


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _auth_headers(permissions: str = "document:read,retrieval:query") -> dict[str, str]:
    return {
        "X-Request-ID": "req-query",
        "X-Trace-ID": "trace-query",
        "X-User-ID": "user-1",
        "X-Tenant-ID": "tenant-1",
        "X-Roles": "knowledge_user",
        "X-Permissions": permissions,
    }


def test_query_route_returns_success_envelope_and_calls_application_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubRagQueryApplicationService()
    app.dependency_overrides[get_rag_query_application_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/query",
        headers=_auth_headers(),
        json={
            "query": "policy leave",
            "top_k": 3,
            "metadata_filter": {"department": "HR"},
            "score_threshold": 0.2,
            "answer_style": "concise",
            "max_output_tokens": 128,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req-query"
    assert body["error"] is None
    assert body["data"]["answer"] == "基于上下文的回答。"
    assert body["data"]["citations"][0]["document_id"] == "doc-1"
    assert body["data"]["citations"][0]["chunk_id"] == "chunk-1"
    assert body["data"]["citations"][0]["source_display_name"] == "policy.md"
    assert "source_uri" not in body["data"]["citations"][0]
    assert "content" not in str(body["data"]).lower()
    assert len(service.calls) == 1
    context, command = service.calls[0]
    assert context.auth.tenant_id == "tenant-1"
    assert command.query == "policy leave"
    assert command.top_k == 3
    assert command.metadata_filter == {"department": "HR"}
    assert command.score_threshold == 0.2
    assert command.answer_style == "concise"
    assert command.max_output_tokens == 128


def test_query_route_rejects_missing_auth_before_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENABLE_DEV_AUTH_HEADERS", raising=False)
    service = StubRagQueryApplicationService()
    app.dependency_overrides[get_rag_query_application_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/query",
        headers={"X-Request-ID": "req-no-auth"},
        json={"query": "policy"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_CONTEXT_REQUIRED"
    assert service.calls == []


def test_query_route_rejects_missing_query_permission_before_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubRagQueryApplicationService()
    app.dependency_overrides[get_rag_query_application_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/query",
        headers=_auth_headers(""),
        json={"query": "policy"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "RAG_QUERY_FORBIDDEN"
    assert service.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "   "},
        {"query": "policy", "top_k": 0},
        {"query": "policy", "score_threshold": 1.5},
        {"query": "x" * 4001},
        {"query": "policy", "max_output_tokens": 4097},
        {"query": "policy", "metadata_filter": ["department", "HR"]},
        {"query": "policy", "metadata_filter": {"department": {"$ne": "HR"}}},
    ],
)
def test_query_route_invalid_body_returns_structured_error_without_service_call(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubRagQueryApplicationService()
    app.dependency_overrides[get_rag_query_application_service] = lambda: service
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/query", headers=_auth_headers(), json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"
    assert service.calls == []


def test_query_route_returns_structured_error_when_service_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubRagQueryApplicationService(
        error=RagQueryError(
            code=RAG_QUERY_FAILED,
            message="RAG query failed.",
            details={
                "request_id": "req-query",
                "trace_id": "trace-query",
                "query": "secret full query",
                "prompt": "private prompt",
                "content": "private chunk",
                "provider_raw_response": "access_token=secret",
            },
            status_code=502,
        )
    )
    app.dependency_overrides[get_rag_query_application_service] = lambda: service
    client = TestClient(app)

    response = client.post("/query", headers=_auth_headers(), json={"query": "policy"})

    assert response.status_code == 502
    body = response.json()
    assert body["request_id"] == "req-query"
    assert body["error"]["code"] == RAG_QUERY_FAILED
    assert body["error"]["details"]["query"] == "[REDACTED]"
    assert body["error"]["details"]["prompt"] == "[REDACTED]"
    assert body["error"]["details"]["content"] == "[REDACTED]"
    assert body["error"]["details"]["provider_raw_response"] == "[REDACTED]"
    assert len(service.calls) == 1


def test_query_stream_route_returns_sse_frames_and_calls_application_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubRagQueryApplicationService()
    app.dependency_overrides[get_rag_query_application_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/query/stream",
        headers=_auth_headers(),
        json={"query": "policy leave", "top_k": 3},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-request-id"] == "req-query"
    body = response.text
    assert "event: citation\n" in body
    assert "event: token\n" in body
    assert "event: final\n" in body
    assert "基于" in body
    assert "doc-1" in body
    assert len(service.stream_calls) == 1
    context, command = service.stream_calls[0]
    assert context.auth.tenant_id == "tenant-1"
    assert command.query == "policy leave"
    assert command.top_k == 3
    assert service.calls == []


def test_query_stream_route_rejects_missing_permission_before_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubRagQueryApplicationService()
    app.dependency_overrides[get_rag_query_application_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/query/stream",
        headers=_auth_headers(""),
        json={"query": "policy"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "RAG_QUERY_FORBIDDEN"
    assert service.stream_calls == []
    assert service.calls == []


def test_query_stream_route_invalid_body_returns_envelope_before_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubRagQueryApplicationService()
    app.dependency_overrides[get_rag_query_application_service] = lambda: service
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/query/stream",
        headers=_auth_headers(),
        json={"query": " "},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"
    assert service.stream_calls == []


def test_query_stream_route_passes_service_error_event_as_sse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubRagQueryApplicationService()
    service.stream_events = (
        RagStreamEvent(
            event="error",
            payload=ErrorEventPayload(
                request_id="req-query",
                trace_id="trace-query",
                code=RAG_QUERY_FAILED,
                message="RAG stream failed.",
                details={"stage": "generation_stream"},
                terminal=True,
            ),
        ),
        RagStreamEvent(
            event="final",
            payload=FinalEventPayload(
                request_id="req-query",
                trace_id="trace-query",
                status="error",
                tenant_id="tenant-1",
                user_id="user-1",
                answer="无法从给定上下文确认。",
                no_answer=True,
                metadata={"error_code": RAG_QUERY_FAILED},
            ),
        ),
    )
    app.dependency_overrides[get_rag_query_application_service] = lambda: service
    client = TestClient(app)

    response = client.post("/query/stream", headers=_auth_headers(), json={"query": "policy"})

    assert response.status_code == 200
    assert "event: error\n" in response.text
    assert "event: final\n" in response.text
    assert RAG_QUERY_FAILED in response.text
    assert len(service.stream_calls) == 1


def _default_stream_events(
    *,
    context: AuthenticatedRequestContext,
    command: QueryCommand,
) -> tuple[RagStreamEvent, ...]:
    citation = Citation(
        document_id="doc-1",
        version_id="v1",
        chunk_id="chunk-1",
        source_display_name="policy.md",
        source_type="markdown",
        page_start=1,
        page_end=1,
        title_path=("Policy",),
        retrieval_method="hybrid",
        score=0.91,
    )
    return (
        RagStreamEvent(
            event="citation",
            payload=CitationEventPayload(
                request_id=context.request_id,
                trace_id=context.trace_id,
                citation=citation,
            ),
        ),
        RagStreamEvent(
            event="token",
            payload=TokenEventPayload(
                request_id=context.request_id,
                trace_id=context.trace_id,
                index=0,
                delta="基于",
            ),
        ),
        RagStreamEvent(
            event="final",
            payload=FinalEventPayload(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                answer="基于上下文的回答。",
                citations=(citation,),
                no_answer=False,
                metadata={
                    "retrieval": {"top_k": command.top_k, "result_count": 1},
                    "generation": {"provider": "fake", "model": "fake-llm"},
                },
            ),
        ),
    )
