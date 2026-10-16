"""Ordinary missing-migration failure must not expose private snapshot SQL values."""

from __future__ import annotations

import traceback
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.agent.durable.dto import DurableSnapshot
from packages.agent.runtime import AgentRunConfig
from packages.agent.storage.durable import SqlAlchemyDurableStore
from packages.agent.storage.models import AgentRunModel
from packages.common.errors import DomainError


async def test_storage_failure_translates_and_hides_private_snapshot_parameters(
    tmp_path: Path,
) -> None:
    marker = "synthetic-private-question-that-must-not-appear-in-tracebacks"
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old-schema.db'}")
    try:
        async with engine.begin() as connection:
            # Simulate starting an upgraded application before its new migration.
            await connection.run_sync(AgentRunModel.__table__.create)
        store = SqlAlchemyDurableStore(async_sessionmaker(engine, expire_on_commit=False))
        snapshot = DurableSnapshot(
            run_id="run-1",
            tenant_id="tenant-1",
            user_id="user-1",
            request_id="req-1",
            trace_id="trace-1",
            backend="native",
            started_at=100,
            last_observed_at=100,
            deadline_at=110,
            expires_at=200,
            config=AgentRunConfig(
                max_steps=2,
                max_tool_calls=1,
                timeout_seconds=10,
                repeated_action_threshold=3,
                input_text=marker,
            ),
        )
        with pytest.raises(Exception) as caught:
            await store.create(snapshot)
        rendered = "".join(traceback.format_exception(caught.value))
        assert marker not in rendered
        assert isinstance(caught.value, DomainError)
        assert "INSERT INTO" not in str(caught.value)
    finally:
        await engine.dispose()
