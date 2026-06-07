from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import pytest

from packages.auth.context import AuthContext
from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.memory import (
    ChatMemoryConfig,
    ChatMemoryService,
    ChatMessageCreate,
    ChatMessageRecord,
    ChatSessionCreate,
    ChatSessionRecord,
)
from packages.rag import (
    ChatApplicationService,
    ChatResponse,
    FinalEventPayload,
    PromptMemoryContext,
    QueryCommand,
    QueryResponse,
    RagStreamEvent,
    final_event,
    token_event,
)
from packages.rag.dto import Citation


@pytest.mark.asyncio
async def test_chat_application_service_persists_messages_and_reuses_query_service() -> None:
    repository = FakeChatMemoryRepository()
    memory_service = ChatMemoryService(repository=repository, config=ChatMemoryConfig())
    query_service = FakeRagQueryService()
    audit = InMemoryAuditPort()
    service = ChatApplicationService(
        memory_service=memory_service,
        rag_query_service=query_service,
        audit=audit,
    )

    response = await service.chat(
        context=_context(),
        command=QueryCommand(query="第一问"),
        session_id=None,
    )

    assert isinstance(response, ChatResponse)
    assert response.session_id == "session-1"
    assert response.answer == "基于上下文的回答。"
    assert [message.role for message in repository.created_messages] == ["user", "assistant"]
    assert query_service.memory_contexts[0] is not None
    assert query_service.memory_contexts[0].message_count == 0
    assert query_service.contexts[0].session_id == "session-1"
    assert audit.events[-1].action == "rag.chat"
    assert audit.events[-1].resource.type == "chat_session"
    assert audit.events[-1].resource.id == "session-1"
    assert audit.events[-1].metadata["memory_message_count"] == 0
    assert "第一问" not in str(audit.events[-1].metadata)


@pytest.mark.asyncio
async def test_chat_stream_persists_assistant_only_after_final_event() -> None:
    repository = FakeChatMemoryRepository()
    memory_service = ChatMemoryService(repository=repository, config=ChatMemoryConfig())
    query_service = FakeRagQueryService()
    service = ChatApplicationService(
        memory_service=memory_service,
        rag_query_service=query_service,
        audit=InMemoryAuditPort(),
    )

    stream = service.stream_chat(
        context=_context(),
        command=QueryCommand(query="流式问题"),
        session_id=None,
    )
    first = await anext(stream)
    assert first.event == "token"
    assert [message.role for message in repository.created_messages] == ["user"]

    rest = [event async for event in stream]
    assert [event.event for event in [first, *rest]] == ["token", "final"]
    final_payload = cast(FinalEventPayload, rest[-1].payload)
    assert final_payload.session_id == "session-1"
    assert final_payload.metadata["session_id"] == "session-1"
    assert [message.role for message in repository.created_messages] == ["user", "assistant"]


class FakeRagQueryService:
    def __init__(self) -> None:
        self.contexts: list[AuthenticatedRequestContext] = []
        self.memory_contexts: list[PromptMemoryContext | None] = []

    async def query(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        memory_context: PromptMemoryContext | None = None,
    ) -> QueryResponse:
        self.contexts.append(context)
        self.memory_contexts.append(memory_context)
        return _query_response(
            context=context,
            metadata={"memory": _memory_metadata(memory_context)},
        )

    async def stream_query(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        memory_context: PromptMemoryContext | None = None,
    ) -> AsyncIterator[RagStreamEvent]:
        self.contexts.append(context)
        self.memory_contexts.append(memory_context)
        yield token_event(
            request_id=context.request_id,
            trace_id=context.trace_id,
            index=0,
            delta="基于",
        )
        response = _query_response(
            context=context,
            metadata={"memory": _memory_metadata(memory_context), "stream": {"event_counts": []}},
        )
        yield final_event(
            request_id=context.request_id,
            trace_id=context.trace_id,
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            answer=response.answer,
            citations=response.citations,
            no_answer=response.no_answer,
            unsupported_claims=response.unsupported_claims,
            metadata=response.metadata,
            status="success",
        )


class FakeChatMemoryRepository:
    def __init__(self) -> None:
        self.sessions: dict[tuple[str, str, str], ChatSessionRecord] = {}
        self.created_messages: list[ChatMessageCreate] = []
        self.persisted_messages: list[ChatMessageRecord] = []
        self.commits = 0
        self.rollbacks = 0

    async def create_session(self, record: ChatSessionCreate) -> ChatSessionRecord:
        from tests.unit.memory.test_service import _session

        _ = record
        session = _session(session_id="session-1")
        self.sessions[("tenant-a", "user-1", "session-1")] = session
        return session

    async def get_active_session(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
    ) -> ChatSessionRecord | None:
        return self.sessions.get((tenant_id, user_id, session_id))

    async def append_message(self, record: ChatMessageCreate) -> ChatMessageRecord:
        from tests.unit.memory.test_service import _message

        self.created_messages.append(record)
        message = _message(
            sequence_no=len(self.created_messages),
            role=record.role,
            token_count=record.token_count,
            summary=record.content_summary,
            session_id=record.session_id,
            content=record.content,
            metadata=dict(record.metadata),
        )
        self.persisted_messages.append(message)
        return message

    async def list_recent_messages(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        limit: int,
    ) -> list[ChatMessageRecord]:
        _ = tenant_id, user_id, session_id
        return self.persisted_messages[-limit:]

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _context() -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-1",
        trace_id="trace-1",
        auth=AuthContext(
            tenant_id="tenant-a",
            user_id="user-1",
            roles=("knowledge_user",),
            permissions=("document:read", "retrieval:query"),
        ),
    )


def _query_response(
    *,
    context: AuthenticatedRequestContext,
    metadata: dict[str, object],
) -> QueryResponse:
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
                source_type="markdown",
                title_path=("Policy",),
                retrieval_method="hybrid",
                score=0.9,
            ),
        ),
        metadata={
            "retrieval": {"top_k": 10, "result_count": 1, "latency_ms": 1.0},
            "context": {"item_count": 1, "citation_source_count": 1},
            "generation": {
                "provider": "fake",
                "model": "fake-llm",
                "version": None,
                "token_usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
            "citation": {"citation_count": 1, "unsupported_count": 0},
            "latency_ms": 1.0,
            "error_code": None,
            **metadata,
        },
    )


def _memory_metadata(memory_context: PromptMemoryContext | None) -> dict[str, int]:
    if memory_context is None:
        return {"message_count": 0, "used_count": 0, "dropped_count": 0, "token_count": 0}
    return {
        "message_count": memory_context.message_count,
        "used_count": memory_context.used_count,
        "dropped_count": memory_context.dropped_count,
        "token_count": memory_context.token_count,
    }
