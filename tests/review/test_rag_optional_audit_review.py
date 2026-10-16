"""Independent ordinary RAG stream and audit transaction lifecycle regressions.

Review finding: a DONE yield inside adapter finally executes during generator
close, including close while suspended on an error chunk in an except handler.
A cancellation guard attached only to the main try body cannot cover that yield.
These tests exercise ordinary provider failures and local SQLite transactions.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import suppress
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.common.audit import AuditEvent, AuditResource, AuditStatus, InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError
from packages.data.storage.audit_models import AuditLogModel
from packages.data.storage.audit_repositories import AuditLogRecord, AuditLogRepository
from packages.data.storage.auxiliary_audit import SessionFactoryAuditPort
from packages.data.storage.base import Base
from packages.memory.storage.models import ChatSessionModel
from packages.rag.auxiliary_audit import BoundedBestEffortAuditPort
from packages.rag.dto import ChatResponse, QueryCommand
from packages.rag.exceptions import RAG_QUERY_FAILED, RagQueryError
from packages.rag.service_token import (
    OpenAIChatCompletionRequest,
    OpenAIChatMessage,
    ServiceTokenChatAdapter,
)
from packages.rag.streaming import RagStreamEvent, final_event, token_event
from tests.unit.rag.test_query_service import _context, _service


class ReviewChat:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def chat(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        session_id: str | None,
    ) -> ChatResponse:
        raise AssertionError("This fixture is stream-only")

    async def stream_chat(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        session_id: str | None,
    ) -> AsyncIterator[RagStreamEvent]:
        if self.error is not None:
            raise self.error
        yield token_event(
            request_id=context.request_id, trace_id=context.trace_id, index=0, delta="answer",
        )
        yield final_event(
            request_id=context.request_id, trace_id=context.trace_id,
            tenant_id=context.auth.tenant_id, user_id=context.auth.user_id,
            answer="answer", citations=(), no_answer=False, unsupported_claims=(),
            metadata={}, session_id="session-1",
        )


def _adapter_stream(
    error: Exception | None = None, audit: InMemoryAuditPort | None = None,
) -> AsyncGenerator[str, None]:
    adapter = ServiceTokenChatAdapter(
        chat_service=ReviewChat(error), model_id="fake", owned_by="local",
        audit=audit if audit is not None else InMemoryAuditPort(),
    )
    return cast(AsyncGenerator[str, None], adapter.stream_chat_completion(
        context=_context(),
        request=OpenAIChatCompletionRequest(
            model="fake", stream=True,
            messages=(OpenAIChatMessage(role="user", content="ordinary question"),),
        ),
    ))


def _provider_error(kind: str) -> Exception:
    if kind == "domain":
        return DomainError(
            code="REVIEW_PROVIDER_UNAVAILABLE", message="Provider unavailable.", status_code=503,
        )
    return RuntimeError("ordinary provider failure")


@pytest.mark.parametrize("kind", ["domain", "generic"])
@pytest.mark.parametrize("disconnect", ["close", "cancel"])
async def test_error_chunk_suspension_disconnect_never_yields_done(
    kind: str, disconnect: str,
) -> None:
    audit = InMemoryAuditPort()
    stream = _adapter_stream(_provider_error(kind), audit)
    first = await anext(stream)
    assert "error" in json.loads(first.removeprefix("data: "))
    assert audit.events == []
    try:
        if disconnect == "close":
            # The prior finally-yield raises async-generator-ignored-GeneratorExit.
            await stream.aclose()
        else:
            # Inject cancellation exactly at the suspended error-chunk yield.
            # The prior finally-yield returns DONE instead of propagating it.
            with pytest.raises(asyncio.CancelledError):
                await stream.athrow(asyncio.CancelledError())
        assert len(audit.events) == 1
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    finally:
        # Keep a failing pre-fix run from leaving its finally block suspended.
        with suppress(BaseException):
            await stream.aclose()


@pytest.mark.parametrize("kind", ["domain", "generic"])
async def test_normal_error_chunk_still_finishes_with_one_done(kind: str) -> None:
    audit = InMemoryAuditPort()
    frames = [frame async for frame in _adapter_stream(_provider_error(kind), audit)]
    assert len(frames) == 2
    assert "error" in json.loads(frames[0].removeprefix("data: "))
    assert frames[1] == "data: [DONE]\n\n"
    assert len(audit.events) == 1


async def test_normal_final_precedes_audit_and_exactly_one_done() -> None:
    audit = InMemoryAuditPort()
    stream = _adapter_stream(audit=audit)
    try:
        token = json.loads((await anext(stream)).removeprefix("data: "))
        assert token["choices"][0]["delta"]["content"] == "answer"
        final = json.loads((await anext(stream)).removeprefix("data: "))
        assert final["choices"][0]["finish_reason"] == "stop"
        assert audit.events == []
        assert await anext(stream) == "data: [DONE]\n\n"
        assert len(audit.events) == 1
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    finally:
        await stream.aclose()


async def test_required_nonstream_audit_runs_inline_and_preserves_failure() -> None:
    caller = asyncio.current_task()
    recorded: list[AuditEvent] = []

    class RequiredAudit:
        async def record(self, event: AuditEvent) -> None:
            assert asyncio.current_task() is caller
            recorded.append(event)
            raise RuntimeError("required audit unavailable")

    optional = InMemoryAuditPort()
    service = _service(audit=RequiredAudit())
    service._stream_audit = BoundedBestEffortAuditPort(
        owned_audit=optional, timeout_seconds=0.001,
    )
    with pytest.raises(RagQueryError) as error:
        await service.query(context=_context(), command=QueryCommand(query="ordinary question"))
    assert error.value.code == RAG_QUERY_FAILED
    assert len(recorded) == 2
    assert recorded[0].status.value == "success"
    assert recorded[1].status.value == "failure"
    assert optional.events == []


@pytest.fixture
async def review_sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'rag-audit-review.db'}",
        connect_args={"timeout": 0.03},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync_connection: Base.metadata.create_all(
                sync_connection, tables=[AuditLogModel.__table__, ChatSessionModel.__table__],
            ))
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _chat_session() -> ChatSessionModel:
    return ChatSessionModel(
        id="session-1", request_id="req-1", trace_id="trace-1", tenant_id="tenant-1",
        user_id="user-1", created_by="user-1", status="active", title="Ordinary chat",
    )


def _event() -> AuditEvent:
    return AuditEvent(
        request_id="req-1", trace_id="trace-1", tenant_id="tenant-1", user_id="user-1",
        action="rag.chat.stream", resource=AuditResource(type="chat_session", id="session-1"),
        status=AuditStatus.SUCCESS, latency_ms=0.0,
    )


@pytest.mark.parametrize("commit_request", [False, True])
async def test_owned_audit_commit_leaves_pending_chat_to_request_owner(
    review_sessions: async_sessionmaker[AsyncSession], commit_request: bool,
) -> None:
    audit = BoundedBestEffortAuditPort(
        owned_audit=SessionFactoryAuditPort(review_sessions), timeout_seconds=1,
    )
    async with review_sessions() as request_session:
        chat = _chat_session()
        request_session.add(chat)
        transaction = request_session.get_transaction()
        await audit.record(_event())
        assert request_session.get_transaction() is transaction
        assert chat in request_session.new
        async with review_sessions() as observer:
            assert await observer.get(ChatSessionModel, "session-1") is None
            assert len((await observer.scalars(select(AuditLogModel))).all()) == 1
        if commit_request:
            await request_session.commit()
        else:
            await request_session.rollback()
    async with review_sessions() as observer:
        assert (await observer.get(ChatSessionModel, "session-1") is not None) is commit_request
        assert len((await observer.scalars(select(AuditLogModel))).all()) == 1


async def test_optional_audit_write_failure_cannot_roll_back_flushed_chat(
    review_sessions: async_sessionmaker[AsyncSession],
) -> None:
    audit = BoundedBestEffortAuditPort(
        owned_audit=SessionFactoryAuditPort(review_sessions), timeout_seconds=1,
    )
    async with review_sessions() as request_session:
        request_session.add(_chat_session())
        await request_session.flush()
        transaction = request_session.get_transaction()
        # File SQLite's writer lock causes the separate audit transaction to fail.
        await audit.record(_event())
        assert request_session.is_active
        assert request_session.get_transaction() is transaction
        assert await request_session.get(ChatSessionModel, "session-1") is not None
        await request_session.commit()
    async with review_sessions() as observer:
        assert await observer.get(ChatSessionModel, "session-1") is not None
        assert (await observer.scalars(select(AuditLogModel))).all() == []


async def test_optional_audit_timeout_rolls_back_only_owned_transaction(
    review_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_create = AuditLogRepository.create
    flushed = asyncio.Event()
    owned_sessions: list[AsyncSession] = []

    async def pause_after_audit_flush(
        repository: AuditLogRepository, event: AuditEvent,
    ) -> AuditLogRecord:
        record = await original_create(repository, event)
        owned_sessions.append(repository._session)
        flushed.set()
        await asyncio.Event().wait()
        return record

    monkeypatch.setattr(AuditLogRepository, "create", pause_after_audit_flush)
    audit = BoundedBestEffortAuditPort(
        owned_audit=SessionFactoryAuditPort(review_sessions), timeout_seconds=0.15,
    )
    async with review_sessions() as request_session:
        chat = _chat_session()
        request_session.add(chat)
        transaction = request_session.get_transaction()
        await asyncio.wait_for(audit.record(_event()), timeout=1)
        assert flushed.is_set()
        assert len(owned_sessions) == 1
        assert owned_sessions[0] is not request_session
        assert not owned_sessions[0].in_transaction()
        assert request_session.get_transaction() is transaction
        assert request_session.is_active
        assert chat in request_session.new
        await request_session.commit()
    async with review_sessions() as observer:
        assert await observer.get(ChatSessionModel, "session-1") is not None
        assert (await observer.scalars(select(AuditLogModel))).all() == []
