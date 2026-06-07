from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from packages.memory import (
    CHAT_MEMORY_BUDGET_EXCEEDED,
    CHAT_MEMORY_FORBIDDEN,
    CHAT_MEMORY_INVALID_REQUEST,
    CHAT_MEMORY_STORAGE_FAILED,
    CHAT_SESSION_NOT_FOUND,
    ChatHistoryMessage,
    ChatMemoryConfig,
    ChatMessageCreate,
    ChatMessageRecord,
    ChatSessionCreate,
    ChatSessionRecord,
    PackedChatHistory,
)


def test_memory_error_codes_are_stable() -> None:
    assert CHAT_SESSION_NOT_FOUND == "CHAT_SESSION_NOT_FOUND"
    assert CHAT_MEMORY_FORBIDDEN == "CHAT_MEMORY_FORBIDDEN"
    assert CHAT_MEMORY_STORAGE_FAILED == "CHAT_MEMORY_STORAGE_FAILED"
    assert CHAT_MEMORY_INVALID_REQUEST == "CHAT_MEMORY_INVALID_REQUEST"
    assert CHAT_MEMORY_BUDGET_EXCEEDED == "CHAT_MEMORY_BUDGET_EXCEEDED"


def test_chat_session_record_validates_required_identity() -> None:
    record = ChatSessionRecord(
        id="session-1",
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-a",
        user_id="user-1",
        created_by="user-1",
        status="active",
        title="Question summary",
        last_message_at=_now(),
        message_count=0,
        metadata={"safe": True},
        created_at=_now(),
        updated_at=_now(),
    )

    assert record.id == "session-1"
    assert record.metadata["safe"] is True

    with pytest.raises(ValidationError):
        record.model_copy(update={"tenant_id": " "}, deep=True)
        ChatSessionRecord(
            id="session-1",
            request_id="req-1",
            trace_id="trace-1",
            tenant_id=" ",
            user_id="user-1",
            created_by="user-1",
            status="active",
            title="Question summary",
            last_message_at=_now(),
            message_count=0,
            created_at=_now(),
            updated_at=_now(),
        )


def test_chat_message_record_validates_role_and_token_count() -> None:
    message = _message_record()
    assert message.role == "user"
    assert message.sequence_no == 1

    with pytest.raises(ValidationError):
        _message_record(role="tool")

    with pytest.raises(ValidationError):
        _message_record(token_count=-1)

    with pytest.raises(ValidationError):
        _message_record(content="x" * 4001)


def test_create_dtos_normalize_safe_summary_and_metadata() -> None:
    session = ChatSessionCreate(
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-a",
        user_id="user-1",
        created_by="user-1",
        title="  question summary  ",
        metadata={"query_length": 20},
    )
    message = ChatMessageCreate(
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-a",
        user_id="user-1",
        session_id="session-1",
        role="assistant",
        content="Answer text",
        content_summary="short safe answer",
        token_count=3,
        metadata={"citation_count": 1},
    )

    assert session.title == "question summary"
    assert session.metadata["query_length"] == 20
    assert message.content_summary == "short safe answer"
    assert message.metadata["citation_count"] == 1


def test_chat_memory_config_defaults_and_bounds() -> None:
    config = ChatMemoryConfig()

    assert config.max_messages == 10
    assert 800 <= config.max_history_tokens <= 1200
    assert config.max_message_chars == 4000
    assert config.allowed_roles == ("user", "assistant", "system_summary")

    with pytest.raises(ValidationError):
        ChatMemoryConfig(max_messages=0)

    with pytest.raises(ValidationError):
        ChatMemoryConfig(max_history_tokens=0)

    with pytest.raises(ValidationError):
        ChatMemoryConfig(allowed_roles=())


def test_packed_chat_history_exposes_safe_counts_only() -> None:
    history = PackedChatHistory(
        session_id="session-1",
        messages=(
            ChatHistoryMessage(
                role="user",
                content="safe summary",
                token_count=2,
                sequence_no=1,
            ),
        ),
        message_count=3,
        used_count=1,
        dropped_count=2,
        token_count=2,
        safe_counts={"memory_message_count": 3, "memory_used_count": 1},
    )

    dumped = history.model_dump()
    assert dumped["messages"][0]["content"] == "safe summary"
    assert dumped["safe_counts"]["memory_dropped_count"] == 2


def _message_record(
    *,
    role: str = "user",
    token_count: int = 2,
    content: str = "Question text",
) -> ChatMessageRecord:
    return ChatMessageRecord(
        id="message-1",
        session_id="session-1",
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-a",
        user_id="user-1",
        created_by="user-1",
        status="active",
        role=role,  # type: ignore[arg-type]
        content=content,
        content_summary="question summary",
        token_count=token_count,
        sequence_no=1,
        metadata={},
        created_at=_now(),
        updated_at=_now(),
    )


def _now() -> datetime:
    return datetime.now(tz=UTC)
