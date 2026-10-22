"""Real SQLite transaction regressions for cancellable Agent audit writes."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import nullcontext
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, SessionTransaction

from packages.agent.auxiliary_io import await_auxiliary_io
from packages.agent.storage.models import AgentRunModel, ToolCallModel
from packages.common.audit import AuditEvent, AuditResource, AuditStatus
from packages.common.tenant_context import current_tenant_id, tenant_scope
from packages.data.storage.audit_models import AuditLogModel
from packages.data.storage.audit_repositories import AuditLogRecord, AuditLogRepository
from packages.data.storage.auxiliary_audit import SessionFactoryAuditPort
from packages.data.storage.base import Base
from packages.data.storage.exceptions import StorageError


@pytest.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    # A real file ensures concurrent sessions use different connections and
    # transactions. SQLite's shared in-memory connection can conceal this bug.
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'auxiliary-audit.db').as_posix()}",
        connect_args={"timeout": 0.05},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: Base.metadata.create_all(
                    sync_connection,
                    tables=[
                        AgentRunModel.__table__,
                        ToolCallModel.__table__,
                        AuditLogModel.__table__,
                    ],
                )
            )
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("commit_mandatory", [False, True])
async def test_successful_audit_leaves_mandatory_changes_for_their_owner(
    session_factory: async_sessionmaker[AsyncSession],
    commit_mandatory: bool,
) -> None:
    await _seed_run(session_factory)
    audit = SessionFactoryAuditPort(session_factory)
    async with session_factory() as mandatory:
        run = await mandatory.get(AgentRunModel, "run-1")
        assert run is not None
        run.status = "completed"
        tool_call = _tool_call()
        mandatory.add(tool_call)
        transaction = mandatory.get_transaction()

        await audit.record(_event())

        assert mandatory.get_transaction() is transaction
        assert run in mandatory.dirty
        assert tool_call in mandatory.new
        # Audit auto-commit must never flush or commit the request session.
        async with session_factory() as observer:
            stored_run = await observer.get(AgentRunModel, "run-1")
            assert stored_run is not None and stored_run.status == "running"
            assert await observer.get(ToolCallModel, "call-1") is None
            assert len((await observer.scalars(select(AuditLogModel))).all()) == 1

        if commit_mandatory:
            await mandatory.commit()
        else:
            await mandatory.rollback()

    async with session_factory() as observer:
        stored_run = await observer.get(AgentRunModel, "run-1")
        assert stored_run is not None
        assert stored_run.status == ("completed" if commit_mandatory else "running")
        stored_call = await observer.get(ToolCallModel, "call-1")
        assert (stored_call is not None) is commit_mandatory
        assert len((await observer.scalars(select(AuditLogModel))).all()) == 1


@pytest.mark.asyncio
async def test_failed_audit_cannot_roll_back_flushed_mandatory_storage(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as mandatory:
        mandatory.add(_run())
        await mandatory.flush()
        mandatory.add(_tool_call())
        await mandatory.flush()
        transaction = mandatory.get_transaction()

        # SQLite permits one writer. The mandatory transaction deliberately
        # owns the write lock, so a distinct audit transaction must fail safely.
        with pytest.raises(StorageError) as error:
            await SessionFactoryAuditPort(session_factory).record(_event())
        assert error.value.code == "AUDIT_STORAGE_WRITE_FAILED"
        assert mandatory.is_active
        assert mandatory.get_transaction() is transaction
        assert await mandatory.get(AgentRunModel, "run-1") is not None
        assert await mandatory.get(ToolCallModel, "call-1") is not None

        async with session_factory() as observer:
            assert await observer.get(AgentRunModel, "run-1") is None
            assert await observer.get(ToolCallModel, "call-1") is None
        await mandatory.commit()

    async with session_factory() as observer:
        assert await observer.get(AgentRunModel, "run-1") is not None
        assert await observer.get(ToolCallModel, "call-1") is not None
        assert (await observer.scalars(select(AuditLogModel))).all() == []
    # The failed owned transaction must not poison subsequent audit sessions.
    await SessionFactoryAuditPort(session_factory).record(_event("after-failure"))


@pytest.mark.asyncio
async def test_timed_out_audit_rolls_back_its_write_without_poisoning_mandatory_session(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_run(session_factory)
    flushed = asyncio.Event()
    cancelled = asyncio.Event()
    original_create = AuditLogRepository.create
    owned_sessions: list[AsyncSession] = []

    async def pause_after_real_flush(
        repository: AuditLogRepository, audit_event: AuditEvent,
    ) -> AuditLogRecord:
        record = await original_create(repository, audit_event)
        owned_sessions.append(repository._session)
        flushed.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return record

    monkeypatch.setattr(AuditLogRepository, "create", pause_after_real_flush)
    async with session_factory() as mandatory:
        run = await mandatory.get(AgentRunModel, "run-1")
        assert run is not None
        run.status = "completed"
        tool_call = _tool_call()
        mandatory.add(tool_call)
        transaction = mandatory.get_transaction()

        with tenant_scope("tenant-a"):
            operation = asyncio.create_task(
                await_auxiliary_io(
                    SessionFactoryAuditPort(session_factory).record(_event()),
                    timeout_seconds=0.5,
                )
            )
            try:
                await asyncio.wait_for(flushed.wait(), timeout=2.0)
                with pytest.raises(TimeoutError):
                    await operation
            finally:
                if not operation.done():
                    operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
            assert current_tenant_id() == "tenant-a"

        assert cancelled.is_set()
        assert owned_sessions and owned_sessions[0] is not mandatory
        assert not owned_sessions[0].in_transaction()
        assert mandatory.is_active
        assert mandatory.get_transaction() is transaction
        assert run in mandatory.dirty
        assert tool_call in mandatory.new
        await mandatory.commit()

    assert current_tenant_id() is None
    async with session_factory() as observer:
        stored_run = await observer.get(AgentRunModel, "run-1")
        assert stored_run is not None and stored_run.status == "completed"
        assert await observer.get(ToolCallModel, "call-1") is not None
        assert (await observer.scalars(select(AuditLogModel))).all() == []

    monkeypatch.setattr(AuditLogRepository, "create", original_create)
    await SessionFactoryAuditPort(session_factory).record(_event("after-timeout"))
    async with session_factory() as observer:
        assert [row.request_id for row in await observer.scalars(select(AuditLogModel))] == [
            "after-timeout"
        ]


@pytest.mark.asyncio
async def test_tenant_mismatch_fails_closed_before_opening_a_session() -> None:
    def forbidden_session_factory() -> AsyncSession:
        raise AssertionError("A cross-tenant audit must not obtain a database session")

    audit = SessionFactoryAuditPort(
        cast(async_sessionmaker[AsyncSession], forbidden_session_factory)
    )
    with tenant_scope("tenant-b"):
        with pytest.raises(StorageError) as error:
            await audit.record(_event())
        assert error.value.code == "AUDIT_TENANT_CONTEXT_MISMATCH"
        assert current_tenant_id() == "tenant-b"
    assert current_tenant_id() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("inherited_tenant", [None, "tenant-a"])
async def test_owned_audit_tasks_preserve_tenant_through_real_transactions(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    inherited_tenant: str | None,
) -> None:
    original_create = AuditLogRepository.create
    owned_sessions: list[AsyncSession] = []
    transaction_tenants: list[str | None] = []
    caller_task = asyncio.current_task()

    def capture_transaction_tenant(
        _session: Session, _transaction: SessionTransaction, _connection: Connection,
    ) -> None:
        transaction_tenants.append(current_tenant_id())

    async def inspect_real_write(
        repository: AuditLogRepository, audit_event: AuditEvent,
    ) -> AuditLogRecord:
        assert asyncio.current_task() is not caller_task
        assert current_tenant_id() == audit_event.tenant_id
        owned_sessions.append(repository._session)
        event.listen(repository._session.sync_session, "after_begin", capture_transaction_tenant)
        return await original_create(repository, audit_event)

    monkeypatch.setattr(AuditLogRepository, "create", inspect_real_write)
    audit = SessionFactoryAuditPort(session_factory)
    scope = tenant_scope(inherited_tenant) if inherited_tenant is not None else nullcontext()
    with scope:
        await await_auxiliary_io(audit.record(_event("first")), timeout_seconds=1.0)
        assert current_tenant_id() == inherited_tenant
        await await_auxiliary_io(audit.record(_event("second")), timeout_seconds=1.0)
        assert current_tenant_id() == inherited_tenant

    assert current_tenant_id() is None
    assert transaction_tenants == ["tenant-a", "tenant-a"]
    assert len(owned_sessions) == 2 and owned_sessions[0] is not owned_sessions[1]
    assert all(not session.in_transaction() for session in owned_sessions)
    async with session_factory() as observer:
        rows = (await observer.scalars(select(AuditLogModel))).all()
        assert {row.request_id for row in rows} == {"first", "second"}
        assert {row.tenant_id for row in rows} == {"tenant-a"}


async def _seed_run(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(_run())
        await session.commit()


def _run() -> AgentRunModel:
    return AgentRunModel(
        id="run-1", request_id="request-1", trace_id="trace-1", tenant_id="tenant-a",
        user_id="user-1", created_by="user-1", status="running", max_steps=3,
        max_tool_calls=2, timeout_seconds=5.0,
    )


def _tool_call() -> ToolCallModel:
    return ToolCallModel(
        id="call-1", agent_run_id="run-1", request_id="request-1", trace_id="trace-1",
        tenant_id="tenant-a", user_id="user-1", created_by="user-1", tool_name="calculator",
        status="success", latency_ms=1.0,
    )


def _event(request_id: str = "request-1") -> AuditEvent:
    return AuditEvent(
        request_id=request_id, trace_id="trace-1", tenant_id="tenant-a", user_id="user-1",
        action="agent.tool.execute", resource=AuditResource(type="tool_call", id="call-1"),
        status=AuditStatus.SUCCESS, latency_ms=1.0,
    )
