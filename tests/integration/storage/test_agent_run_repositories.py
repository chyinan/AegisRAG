from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.agent.dto import AgentRunCreate, AgentRunUpdate
from packages.agent.exceptions import AGENT_RUN_STORAGE_FAILED, AgentRunError
from packages.agent.storage import models as agent_models  # noqa: F401
from packages.agent.storage.repositories import AgentRunRepository
from packages.data.storage.base import Base


@pytest.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'agent_runs.db').as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_agent_run_repository_create_update_and_query(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repository = AgentRunRepository(session)
        created = await repository.create_run(_create())
        updated = await repository.update_run_result(
            tenant_id="tenant-a",
            user_id="user-1",
            run_id=created.id,
            update=AgentRunUpdate(
                status="completed",
                termination_reason="FINAL_ANSWER",
                steps_used=1,
                tool_calls_used=0,
                error_code=None,
                latency_ms=12.0,
                metadata={"termination_reason": "FINAL_ANSWER"},
            ),
        )
        await repository.commit()

        assert created.status == "running"
        assert updated.status == "completed"
        assert updated.steps_used == 1

    async with session_factory() as session:
        repository = AgentRunRepository(session)
        fetched = await repository.get_run(
            tenant_id="tenant-a",
            user_id="user-1",
            run_id=created.id,
        )
        by_request = await repository.get_run_by_request_id(
            tenant_id="tenant-a",
            user_id="user-1",
            request_id="req-1",
        )

    assert fetched is not None
    assert fetched.id == created.id
    assert by_request is not None
    assert by_request.id == created.id


@pytest.mark.asyncio
async def test_agent_run_repository_enforces_tenant_user_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repository = AgentRunRepository(session)
        created = await repository.create_run(_create())
        await repository.commit()

    async with session_factory() as session:
        repository = AgentRunRepository(session)
        assert (
            await repository.get_run(tenant_id="tenant-b", user_id="user-1", run_id=created.id)
            is None
        )
        assert (
            await repository.get_run(tenant_id="tenant-a", user_id="user-2", run_id=created.id)
            is None
        )


@pytest.mark.asyncio
async def test_agent_run_repository_storage_error_is_safe() -> None:
    session = BrokenSession()
    repository = AgentRunRepository(cast(AsyncSession, session))

    with pytest.raises(AgentRunError) as exc_info:
        await repository.create_run(_create(metadata={"token": "secret"}))

    assert exc_info.value.code == AGENT_RUN_STORAGE_FAILED
    assert "select *" not in str(exc_info.value.details).lower()
    assert "secret" not in str(exc_info.value.details).lower()
    assert session.rollbacks == 1


def _create(metadata: dict[str, object] | None = None) -> AgentRunCreate:
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
        metadata=metadata or {"safe": True},
    )


class BrokenSession:
    def __init__(self) -> None:
        self.rollbacks = 0

    def add(self, model: object) -> None:
        return None

    async def flush(self) -> None:
        raise SQLAlchemyError("select * from agent_runs where token='secret'")

    async def rollback(self) -> None:
        self.rollbacks += 1
