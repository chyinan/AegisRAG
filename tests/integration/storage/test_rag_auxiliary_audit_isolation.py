"""Ordinary SQLite evidence for RAG's independent optional audit transaction."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.common.audit import AuditEvent, AuditResource, AuditStatus
from packages.data.storage.audit_models import AuditLogModel
from packages.data.storage.audit_repositories import (
    AuditLogRecord,
    AuditLogRepository,
    SqlAlchemyAuditPort,
)
from packages.data.storage.auxiliary_audit import SessionFactoryAuditPort
from packages.data.storage.base import Base
from packages.memory import ChatMemoryService
from packages.memory.storage.models import ChatMessageModel, ChatSessionModel
from packages.memory.storage.repositories import ChatMemoryRepository
from packages.rag.auxiliary_audit import BoundedBestEffortAuditPort
from packages.rag.chat import ChatApplicationService
from packages.rag.dto import QueryCommand
from tests.unit.memory.test_chat_application_service import FakeRagQueryService, _context
from tests.unit.rag.test_query_service import _context as _query_context
from tests.unit.rag.test_query_service import _service


@pytest.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'rag-audit.db').as_posix()}",
        connect_args={"timeout": 0.05},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda connection: Base.metadata.create_all(
                connection,
                tables=[
                    ChatSessionModel.__table__, ChatMessageModel.__table__, AuditLogModel.__table__,
                ],
            ))
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _audit(factory: async_sessionmaker[AsyncSession]) -> BoundedBestEffortAuditPort:
    return BoundedBestEffortAuditPort(
        owned_audit=SessionFactoryAuditPort(factory), timeout_seconds=0.2,
    )


def _event(request_id: str = "audit-request") -> AuditEvent:
    return AuditEvent(
        request_id=request_id, trace_id="trace-1", tenant_id="tenant-a", user_id="user-1",
        action="rag.chat", resource=AuditResource(type="chat_session", id="chat-1"),
        status=AuditStatus.SUCCESS, latency_ms=1,
    )


def _session() -> ChatSessionModel:
    return ChatSessionModel(
        id="chat-1", request_id="req-1", trace_id="trace-1", tenant_id="tenant-a",
        user_id="user-1", created_by="user-1", status="active", title="Original", message_count=0,
    )


def _message() -> ChatMessageModel:
    return ChatMessageModel(
        id="message-1", session_id="chat-1", request_id="req-1", trace_id="trace-1",
        tenant_id="tenant-a", user_id="user-1", created_by="user-1", status="active",
        role="assistant", content="Stored answer", content_summary="Stored answer",
        token_count=2, sequence_no=1,
    )


@pytest.mark.parametrize("commit_request", [False, True])
async def test_optional_audit_cannot_commit_or_rollback_pending_chat_state(
    session_factory: async_sessionmaker[AsyncSession], commit_request: bool,
) -> None:
    async with session_factory() as seed:
        seed.add(_session())
        await seed.commit()
    async with session_factory() as request:
        chat = await request.get(ChatSessionModel, "chat-1")
        assert chat is not None
        chat.title = "Changed"
        message = _message()
        request.add(message)
        transaction = request.get_transaction()

        await _audit(session_factory).record(_event())

        assert request.get_transaction() is transaction
        assert chat in request.dirty and message in request.new
        async with session_factory() as observer:
            stored = await observer.get(ChatSessionModel, "chat-1")
            assert stored is not None and stored.title == "Original"
            assert await observer.get(ChatMessageModel, "message-1") is None
            assert len((await observer.scalars(select(AuditLogModel))).all()) == 1
        if commit_request:
            await request.commit()
        else:
            await request.rollback()

    async with session_factory() as observer:
        stored = await observer.get(ChatSessionModel, "chat-1")
        assert stored is not None and stored.title == ("Changed" if commit_request else "Original")
        assert (await observer.get(ChatMessageModel, "message-1") is not None) is commit_request
        assert len((await observer.scalars(select(AuditLogModel))).all()) == 1


async def test_optional_timeout_rolls_back_only_owned_audit_transaction(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as seed:
        seed.add(_session())
        await seed.commit()
    original = AuditLogRepository.create
    owned: list[AsyncSession] = []
    flushed = asyncio.Event()
    cancelled = asyncio.Event()

    async def stall_after_flush(
        repository: AuditLogRepository, event: AuditEvent,
    ) -> AuditLogRecord:
        record = await original(repository, event)
        owned.append(repository._session)
        flushed.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return record

    monkeypatch.setattr(AuditLogRepository, "create", stall_after_flush)
    async with session_factory() as request:
        chat = await request.get(ChatSessionModel, "chat-1")
        assert chat is not None
        chat.title = "Changed"
        message = _message()
        request.add(message)
        transaction = request.get_transaction()
        operation = asyncio.create_task(_audit(session_factory).record(_event()))
        await asyncio.wait_for(flushed.wait(), timeout=1)
        await asyncio.wait_for(operation, timeout=1)
        assert cancelled.is_set()
        assert owned and owned[0] is not request
        assert not owned[0].in_transaction()
        assert request.is_active and request.get_transaction() is transaction
        assert chat in request.dirty and message in request.new
        await request.commit()

    async with session_factory() as observer:
        assert await observer.get(ChatMessageModel, "message-1") is not None
        assert (await observer.scalars(select(AuditLogModel))).all() == []
    monkeypatch.setattr(AuditLogRepository, "create", original)
    await _audit(session_factory).record(_event("later-audit"))
    async with session_factory() as observer:
        rows = (await observer.scalars(select(AuditLogModel))).all()
        assert [row.request_id for row in rows] == ["later-audit"]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("failure", ["timeout", "exception"])
async def test_real_chat_commits_assistant_before_optional_audit_failure(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    streaming: bool,
    failure: str,
) -> None:
    original = AuditLogRepository.create
    owned: list[AsyncSession] = []

    async def fail_after_flush(repository: AuditLogRepository, event: AuditEvent) -> AuditLogRecord:
        await original(repository, event)
        owned.append(repository._session)
        # The user and assistant rows must already be visible in another transaction.
        async with session_factory() as observer:
            messages = (await observer.scalars(
                select(ChatMessageModel).order_by(ChatMessageModel.sequence_no),
            )).all()
            assert [message.role for message in messages] == ["user", "assistant"]
        if failure == "timeout":
            await asyncio.Event().wait()
        raise RuntimeError("synthetic optional audit failure")

    monkeypatch.setattr(AuditLogRepository, "create", fail_after_flush)
    async with session_factory() as request:
        service = ChatApplicationService(
            memory_service=ChatMemoryService(repository=ChatMemoryRepository(request)),
            rag_query_service=FakeRagQueryService(), audit=_audit(session_factory),
        )
        if streaming:
            async with asyncio.timeout(2):
                events = [event async for event in service.stream_chat(
                    context=_context(), command=QueryCommand(query="question"), session_id=None,
                )]
            assert [event.event for event in events] == ["token", "final"]
            assert events[-1].payload.status == "success"  # type: ignore[union-attr]
        else:
            response = await asyncio.wait_for(service.chat(
                context=_context(), command=QueryCommand(query="question"), session_id=None,
            ), timeout=2)
            assert response.answer == "基于上下文的回答。"
        assert owned and all(session is not request for session in owned)
        assert all(not session.in_transaction() for session in owned)
        assert request.is_active
        await request.rollback()

    async with session_factory() as observer:
        messages = (await observer.scalars(
            select(ChatMessageModel).order_by(ChatMessageModel.sequence_no),
        )).all()
        assert [message.role for message in messages] == ["user", "assistant"]
        assert (await observer.scalars(select(AuditLogModel))).all() == []


async def test_required_nonstream_query_retains_request_transaction_commit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as request:
        # The existing required audit transaction intentionally commits request state.
        pending_chat = _session()
        pending_chat.tenant_id = "tenant-1"
        request.add(pending_chat)
        required = SqlAlchemyAuditPort(request, auto_commit=True)
        service = _service(audit=required)
        service._stream_audit = _audit(session_factory)
        response = await service.query(
            context=_query_context(), command=QueryCommand(query="question"),
        )
        assert response.answer == "基于上下文的回答。"
        assert service._audit is required
        assert not request.in_transaction()

    async with session_factory() as observer:
        assert await observer.get(ChatSessionModel, "chat-1") is not None
        audit_rows = (await observer.scalars(select(AuditLogModel))).all()
        assert len(audit_rows) == 1 and audit_rows[0].action == "rag.query"
