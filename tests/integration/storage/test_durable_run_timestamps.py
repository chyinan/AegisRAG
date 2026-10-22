"""Durable creation must work with the shipped, migration-created schema."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.agent.durable.dto import DurableSnapshot, DurableStartup
from packages.agent.runtime import AgentRunConfig
from packages.agent.storage.durable import SqlAlchemyDurableStore
from packages.agent.storage.models import AgentRunModel, DurableAgentRunModel


def _upgrade_agent_tables(connection: Connection) -> None:
    # create_all() supplies model defaults that migration 0010 never installed.
    # Run the actual migrations so omitted timestamps fail as they do in production.
    with Operations.context(MigrationContext.configure(connection)):
        for module_name in (
            "migrations.versions.20260527_0010_agent_runs",
            "migrations.versions.20261015_0018_durable_agent_execution",
        ):
            import_module(module_name).upgrade()
    columns = {column["name"]: column for column in inspect(connection).get_columns("agent_runs")}
    for name in ("created_at", "updated_at"):
        assert columns[name]["nullable"] is False
        assert columns[name]["default"] is None


@pytest.fixture
async def migrated_sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'durable-migrated.sqlite'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_upgrade_agent_tables)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("keyed", [False, True], ids=["unkeyed", "keyed"])
async def test_durable_create_supplies_legacy_timestamps_after_migration(
    migrated_sessions: async_sessionmaker[AsyncSession], keyed: bool
) -> None:
    snapshot = DurableSnapshot(
        run_id=str(uuid4()),
        tenant_id="tenant-a",
        user_id="user-a",
        request_id="request-a",
        trace_id="trace-a",
        backend="native",
        startup=DurableStartup(command_fingerprint="a" * 64) if keyed else None,
        config=AgentRunConfig(
            max_steps=8, max_tool_calls=6, timeout_seconds=120, repeated_action_threshold=3
        ),
        started_at=0,
        deadline_at=120,
        last_observed_at=0,
        expires_at=86400,
    )
    store = SqlAlchemyDurableStore(migrated_sessions)
    token = str(uuid4())
    before = datetime.now(tz=UTC)
    if keyed:
        result = await store.create_keyed(snapshot, token=token, now=10, lease_seconds=30)
        assert result.inserted is True
        assert result.snapshot == snapshot
    else:
        await store.create(snapshot)
    after = datetime.now(tz=UTC)

    async with migrated_sessions() as session:
        public = await session.get(AgentRunModel, snapshot.run_id)
        private = await session.get(DurableAgentRunModel, snapshot.run_id)
        assert public is not None
        assert private is not None
        assert public.created_at is not None
        assert public.updated_at == public.created_at
        # SQLite drops timezone information when materializing DateTime columns.
        assert before <= public.created_at.replace(tzinfo=UTC) <= after
        assert private.snapshot == snapshot.persisted()
        assert private.lease_token == (token if keyed else None)
        assert private.lease_until == (40 if keyed else None)

    assert (
        await store.load(
            tenant_id=snapshot.tenant_id, user_id=snapshot.user_id, run_id=snapshot.run_id
        )
        == snapshot
    )
