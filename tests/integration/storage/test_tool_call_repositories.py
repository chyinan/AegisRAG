from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.agent.dto import AgentRunCreate, ToolCallCreate, ToolCallQuery
from packages.agent.exceptions import AGENT_RUN_STORAGE_FAILED, AgentRunError
from packages.agent.storage import models as agent_models  # noqa: F401
from packages.agent.storage.models import ToolCallModel
from packages.agent.storage.repositories import AgentRunRepository, ToolCallRepository
from packages.data.storage.base import Base


@pytest.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'tool_calls.db').as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tool_call_repository_create_and_query_by_agent_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        run_repository = AgentRunRepository(session)
        run = await run_repository.create_run(_run_create())
        repository = ToolCallRepository(session)

        created = await repository.create_tool_call(_tool_call_create(agent_run_id=run.id))
        await repository.commit()

        assert created.agent_run_id == run.id
        assert created.status == "success"
        assert created.arguments_summary == {
            "argument_keys": ["query"],
            "argument_count": 1,
        }

    async with session_factory() as session:
        repository = ToolCallRepository(session)
        records = await repository.list_by_agent_run(
            tenant_id="tenant-a",
            user_id="user-1",
            agent_run_id=run.id,
        )
        queried = await repository.list_tool_calls(
            ToolCallQuery(
                tenant_id="tenant-a",
                user_id="user-1",
                agent_run_id=run.id,
                tool_name="rag_search",
                status="success",
            )
        )

    assert [record.id for record in records] == [created.id]
    assert [record.id for record in queried] == [created.id]
    assert "secret policy text" not in str(records)


@pytest.mark.asyncio
async def test_tool_call_repository_enforces_tenant_user_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        run_repository = AgentRunRepository(session)
        run = await run_repository.create_run(_run_create())
        repository = ToolCallRepository(session)
        await repository.create_tool_call(_tool_call_create(agent_run_id=run.id))
        await repository.commit()

    async with session_factory() as session:
        repository = ToolCallRepository(session)
        assert (
            await repository.list_by_agent_run(
                tenant_id="tenant-b",
                user_id="user-1",
                agent_run_id=run.id,
            )
            == []
        )
        assert (
            await repository.list_by_agent_run(
                tenant_id="tenant-a",
                user_id="user-2",
                agent_run_id=run.id,
            )
            == []
        )


@pytest.mark.asyncio
async def test_tool_call_repository_filters_by_created_at_window(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = older + timedelta(hours=1)
    async with session_factory() as session:
        run_repository = AgentRunRepository(session)
        run = await run_repository.create_run(_run_create())
        repository = ToolCallRepository(session)
        old_record = await repository.create_tool_call(_tool_call_create(agent_run_id=run.id))
        new_record = await repository.create_tool_call(
            _tool_call_create(agent_run_id=run.id, tool_name="calculator")
        )
        await session.execute(
            update(ToolCallModel)
            .where(ToolCallModel.id == old_record.id)
            .values(created_at=older, updated_at=older)
        )
        await session.execute(
            update(ToolCallModel)
            .where(ToolCallModel.id == new_record.id)
            .values(created_at=newer, updated_at=newer)
        )
        await repository.commit()

    async with session_factory() as session:
        repository = ToolCallRepository(session)
        records = await repository.list_tool_calls(
            ToolCallQuery(
                tenant_id="tenant-a",
                user_id="user-1",
                agent_run_id=run.id,
                created_at_from=newer,
                created_at_to=newer,
            )
        )

    assert [record.id for record in records] == [new_record.id]


@pytest.mark.asyncio
async def test_tool_call_recorder_commits_record_before_later_rollback(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        run_repository = AgentRunRepository(session)
        run = await run_repository.create_run(_run_create())
        await run_repository.commit()

        repository = ToolCallRepository(session)
        await repository.record_tool_call(_tool_call_create(agent_run_id=run.id))
        await repository.rollback()

    async with session_factory() as session:
        repository = ToolCallRepository(session)
        records = await repository.list_by_agent_run(
            tenant_id="tenant-a",
            user_id="user-1",
            agent_run_id=run.id,
        )

    assert len(records) == 1
    assert records[0].agent_run_id == run.id


@pytest.mark.asyncio
async def test_tool_call_repository_storage_error_is_safe() -> None:
    session = BrokenSession()
    repository = ToolCallRepository(cast(AsyncSession, session))

    with pytest.raises(AgentRunError) as exc_info:
        await repository.create_tool_call(
            _tool_call_create(
                agent_run_id="run-1",
                arguments_summary={"argument_keys": ["query"], "hash": "abc123"},
            )
        )

    assert exc_info.value.code == AGENT_RUN_STORAGE_FAILED
    assert "select *" not in str(exc_info.value.details).lower()
    assert "secret" not in str(exc_info.value.details).lower()
    assert session.rollbacks == 1


def _run_create() -> AgentRunCreate:
    return AgentRunCreate(
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-a",
        user_id="user-1",
        created_by="user-1",
        status="running",
        max_steps=8,
        max_tool_calls=5,
        timeout_seconds=30.0,
        input_summary={"length": 12, "sha256": "abc"},
        metadata={"safe": True},
    )


def _tool_call_create(
    *,
    agent_run_id: str,
    tool_name: str = "rag_search",
    arguments_summary: dict[str, object] | None = None,
) -> ToolCallCreate:
    return ToolCallCreate(
        agent_run_id=agent_run_id,
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-a",
        user_id="user-1",
        tool_name=tool_name,
        permission="agent:tool:rag_search",
        status="success",
        latency_ms=12.5,
        error_code=None,
        arguments_summary=arguments_summary
        or {
            "argument_keys": ["query"],
            "argument_count": 1,
        },
        result_summary={"result_keys": ["citations"], "status": "success"},
    )


class BrokenSession:
    def __init__(self) -> None:
        self.rollbacks = 0

    def add(self, model: object) -> None:
        return None

    async def flush(self) -> None:
        raise SQLAlchemyError("select * from tool_calls where token='secret'")

    async def rollback(self) -> None:
        self.rollbacks += 1
