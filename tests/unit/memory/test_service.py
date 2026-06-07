from __future__ import annotations

from datetime import UTC, datetime

import pytest

from packages.auth.context import AuthContext
from packages.common.context import AuthenticatedRequestContext
from packages.memory import (
    CHAT_SESSION_NOT_FOUND,
    ChatMemoryConfig,
    ChatMemoryError,
    ChatMemoryService,
    ChatMessageCreate,
    ChatMessageRecord,
    ChatSessionCreate,
    ChatSessionRecord,
)


@pytest.mark.asyncio
async def test_get_or_create_session_creates_when_session_id_missing() -> None:
    repository = FakeChatMemoryRepository()
    service = ChatMemoryService(repository=repository)

    session = await service.get_or_create_session(
        context=_context(),
        session_id=None,
        query="这是一个很长的问题，title 只能保存安全摘要 sk-secret-value",
    )

    assert session.id == "session-1"
    assert session.tenant_id == "tenant-a"
    assert session.user_id == "user-1"
    assert repository.created_sessions[0].title is not None
    assert "sk-secret-value" not in repository.created_sessions[0].title


@pytest.mark.asyncio
async def test_get_or_create_session_returns_safe_not_found_for_wrong_scope() -> None:
    repository = FakeChatMemoryRepository()
    service = ChatMemoryService(repository=repository)

    with pytest.raises(ChatMemoryError) as exc_info:
        await service.get_or_create_session(
            context=_context(user_id="user-2"),
            session_id="session-1",
            query="question",
        )

    assert exc_info.value.code == CHAT_SESSION_NOT_FOUND
    assert "session-1" in str(exc_info.value.details)
    assert "wrong" not in str(exc_info.value.details).lower()


@pytest.mark.asyncio
async def test_append_messages_use_safe_summaries_and_repository_sequence() -> None:
    repository = FakeChatMemoryRepository()
    service = ChatMemoryService(repository=repository)
    session = await service.get_or_create_session(
        context=_context(),
        session_id=None,
        query="question",
    )

    user_message = await service.append_user_message(
        context=_context(),
        session_id=session.id,
        content="读取 C:\\secret\\prod.env and api_key=abc123",
    )
    assistant_message = await service.append_assistant_message(
        context=_context(),
        session_id=session.id,
        content="基于上下文的回答。",
        citations_metadata={"citation_count": 1},
    )

    assert user_message.sequence_no == 1
    assert assistant_message.sequence_no == 2
    assert "C:\\secret" not in repository.created_messages[0].content_summary
    assert "api_key=abc123" not in repository.created_messages[0].content_summary
    raw_user_message = "读取 C:\\secret\\prod.env and api_key=abc123"
    assert repository.created_messages[0].content_summary != raw_user_message
    assert repository.created_messages[1].metadata["citation_count"] == 1


@pytest.mark.asyncio
async def test_load_packed_history_filters_roles_and_budget() -> None:
    repository = FakeChatMemoryRepository(
        messages=[
            _message(sequence_no=1, role="user", token_count=4, summary="old"),
            _message(sequence_no=2, role="assistant", token_count=4, summary="answer"),
            _message(sequence_no=3, role="system_summary", token_count=4, summary="summary"),
            _message(
                sequence_no=4,
                role="user",
                token_count=9,
                summary="too many tokens " * 20,
            ),
        ]
    )
    service = ChatMemoryService(
        repository=repository,
        config=ChatMemoryConfig(max_messages=4, max_history_tokens=8, allowed_roles=("user",)),
    )

    history = await service.load_packed_history(context=_context(), session_id="session-1")

    assert [message.sequence_no for message in history.messages] == [1]
    assert history.message_count == 4
    assert history.used_count == 1
    assert history.dropped_count == 3
    assert history.safe_counts["memory_message_count"] == 4
    assert history.safe_counts["memory_used_count"] == 1
    assert history.safe_counts["memory_dropped_count"] == 3


class FakeChatMemoryRepository:
    def __init__(self, messages: list[ChatMessageRecord] | None = None) -> None:
        default_session = _session()
        self.sessions: dict[tuple[str, str, str], ChatSessionRecord] = {
            (
                default_session.tenant_id,
                default_session.user_id,
                default_session.id,
            ): default_session
        }
        self.messages = messages or []
        self.created_sessions: list[ChatSessionCreate] = []
        self.created_messages: list[ChatMessageCreate] = []
        self.commits = 0
        self.rollbacks = 0

    async def create_session(self, record: ChatSessionCreate) -> ChatSessionRecord:
        self.created_sessions.append(record)
        session = _session(
            session_id=f"session-{len(self.created_sessions)}",
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            title=record.title,
        )
        self.sessions[(record.tenant_id, record.user_id, session.id)] = session
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
        self.messages.append(message)
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
        return self.messages[-limit:]

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _context(*, user_id: str = "user-1") -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-1",
        trace_id="trace-1",
        auth=AuthContext(
            tenant_id="tenant-a",
            user_id=user_id,
            roles=("knowledge_user",),
            permissions=("document:read", "retrieval:query"),
        ),
    )


def _session(
    *,
    session_id: str = "session-1",
    tenant_id: str = "tenant-a",
    user_id: str = "user-1",
    title: str | None = None,
) -> ChatSessionRecord:
    now = datetime.now(tz=UTC)
    return ChatSessionRecord(
        id=session_id,
        request_id="req-1",
        trace_id="trace-1",
        tenant_id=tenant_id,
        user_id=user_id,
        created_by=user_id,
        status="active",
        title=title,
        last_message_at=None,
        message_count=0,
        metadata={},
        created_at=now,
        updated_at=now,
    )


def _message(
    *,
    sequence_no: int,
    role: str,
    token_count: int,
    summary: str,
    session_id: str = "session-1",
    content: str | None = None,
    metadata: dict[str, object] | None = None,
) -> ChatMessageRecord:
    now = datetime.now(tz=UTC)
    return ChatMessageRecord(
        id=f"message-{sequence_no}",
        session_id=session_id,
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-a",
        user_id="user-1",
        created_by="user-1",
        status="active",
        role=role,  # type: ignore[arg-type]
        content=content or summary,
        content_summary=summary,
        token_count=token_count,
        sequence_no=sequence_no,
        metadata=metadata or {},
        created_at=now,
        updated_at=now,
    )
