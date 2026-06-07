from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.data.storage.base import Base
from packages.memory import ChatMessageCreate, ChatSessionCreate
from packages.memory.exceptions import CHAT_MEMORY_STORAGE_FAILED, ChatMemoryError
from packages.memory.storage import models as memory_models  # noqa: F401
from packages.memory.storage.repositories import ChatMemoryRepository


@pytest.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'chat_memory.db').as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_chat_memory_repository_creates_session_and_messages(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repository = ChatMemoryRepository(session)

        created = await repository.create_session(_session_create())
        first = await repository.append_message(
            _message_create(session_id=created.id, role="user", content="Question")
        )
        second = await repository.append_message(
            _message_create(session_id=created.id, role="assistant", content="Answer")
        )
        await repository.commit()

        assert created.status == "active"
        assert first.sequence_no == 1
        assert second.sequence_no == 2

    async with session_factory() as session:
        repository = ChatMemoryRepository(session)
        fetched = await repository.get_active_session(
            tenant_id="tenant-a",
            user_id="user-1",
            session_id=created.id,
        )
        messages = await repository.list_recent_messages(
            tenant_id="tenant-a",
            user_id="user-1",
            session_id=created.id,
            limit=10,
        )

    assert fetched is not None
    assert fetched.message_count == 2
    assert [message.sequence_no for message in messages] == [1, 2]
    assert [message.role for message in messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_chat_memory_repository_enforces_tenant_user_and_status_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repository = ChatMemoryRepository(session)
        created = await repository.create_session(_session_create())
        closed = await repository.create_session(_session_create(user_id="user-2"))
        await repository.update_session_status(
            tenant_id="tenant-a",
            user_id="user-2",
            session_id=closed.id,
            status="closed",
        )
        await repository.commit()

    async with session_factory() as session:
        repository = ChatMemoryRepository(session)
        assert (
            await repository.get_active_session(
                tenant_id="tenant-b",
                user_id="user-1",
                session_id=created.id,
            )
            is None
        )
        assert (
            await repository.get_active_session(
                tenant_id="tenant-a",
                user_id="user-2",
                session_id=created.id,
            )
            is None
        )
        assert (
            await repository.get_active_session(
                tenant_id="tenant-a",
                user_id="user-2",
                session_id=closed.id,
            )
            is None
        )


@pytest.mark.asyncio
async def test_chat_memory_repository_storage_error_is_safe() -> None:
    session = BrokenSession()
    repository = ChatMemoryRepository(cast(AsyncSession, session))

    with pytest.raises(ChatMemoryError) as exc_info:
        await repository.create_session(_session_create())

    assert exc_info.value.code == CHAT_MEMORY_STORAGE_FAILED
    assert "select *" not in str(exc_info.value.details).lower()
    assert session.rollbacks == 1


def _session_create(*, user_id: str = "user-1") -> ChatSessionCreate:
    return ChatSessionCreate(
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-a",
        user_id=user_id,
        created_by=user_id,
        title="safe title",
        metadata={"query_length": 12},
    )


def _message_create(
    *,
    session_id: str,
    role: str,
    content: str,
) -> ChatMessageCreate:
    return ChatMessageCreate(
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-a",
        user_id="user-1",
        session_id=session_id,
        role=role,  # type: ignore[arg-type]
        content=content,
        content_summary=content,
        token_count=2,
        metadata={"safe": True},
    )


class BrokenSession:
    def __init__(self) -> None:
        self.rollbacks = 0

    def add(self, model: object) -> None:
        return None

    async def flush(self) -> None:
        raise SQLAlchemyError("select * from chat_messages where password='secret'")

    async def rollback(self) -> None:
        self.rollbacks += 1
