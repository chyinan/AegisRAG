"""Shared SQLite/PostgreSQL durable contracts, with explicit disposable-DB opt-in.

PostgreSQL cases use the non-privileged runtime role after the fresh migration
pilot. They do not create roles, migrate, drop tables, or contact model providers.
SQLite passes do not establish PostgreSQL concurrency or RLS correctness.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.agent.dto import ToolRateLimit
from packages.agent.durable.dto import (
    DurableResumeCommand,
    DurableSnapshot,
    DurableStartCommand,
    ToolLedgerEntry,
)
from packages.agent.runtime import AgentStepDecision
from packages.agent.storage.durable import SqlAlchemyDurableStore
from packages.agent.storage.models import (
    AgentRunModel,
    DurableAgentRunModel,
    DurableGraphCheckpointModel,
    DurableToolLedgerModel,
)
from packages.agent.tools.calculator import CalculatorInput, build_calculator_tool
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError
from packages.common.tenant_context import tenant_scope
from packages.data.storage.base import Base
from packages.data.storage.session import create_async_db_engine, create_session_factory
from tests.unit.agent.test_durable_execution import Clock, context, service


@dataclass(frozen=True)
class DatabaseCase:
    dialect: str
    sessions: async_sessionmaker[AsyncSession]
    tenant: str


@pytest.fixture(params=("sqlite", pytest.param("postgres", marks=pytest.mark.physical_postgres)))
async def durable_database(
    request: pytest.FixtureRequest,
    tmp_path,
) -> AsyncIterator[DatabaseCase]:
    dialect = str(request.param)
    if dialect == "postgres":
        url = os.getenv("AEGISRAG_PILOT_RUNTIME_URL")
        if not url:
            if os.getenv("AEGISRAG_REQUIRE_POSTGRES_DURABLE") == "1":
                pytest.fail("The required physical durable gate has no runtime database URL.")
            pytest.skip("Set the disposable PostgreSQL runtime URL for physical durable checks.")
        assert os.getenv("AEGISRAG_PILOT_DISPOSABLE_DATABASE") == "1", (
            "Physical durable checks require an explicitly disposable migrated database."
        )
        parsed = make_url(url)
        assert parsed.drivername == "postgresql+asyncpg"
        engine = create_async_db_engine(url, pool_size=2, max_overflow=0, hide_parameters=True)
    else:
        engine = create_async_db_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'durable-acceptance.sqlite'}",
            hide_parameters=True,
        )
    try:
        if dialect == "postgres":
            async with engine.connect() as connection:
                assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                    "20261016_0021"
                ), "Run the fresh migration pilot before the durable database gate."
                role = (
                    await connection.execute(
                        text(
                            "SELECT rolname, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
                            "FROM pg_roles WHERE rolname = current_user"
                        )
                    )
                ).one()
                assert role == (make_url(url).username, False, False, False, False)
        else:
            async with engine.begin() as connection:
                await connection.run_sync(
                    Base.metadata.create_all,
                    tables=[
                        model.__table__
                        for model in (
                            AgentRunModel,
                            DurableAgentRunModel,
                            DurableToolLedgerModel,
                            DurableGraphCheckpointModel,
                        )
                    ],
                )
        yield DatabaseCase(dialect, create_session_factory(engine), f"db-probe-{uuid4().hex[:12]}")
    finally:
        await engine.dispose()


@pytest.mark.parametrize("backend", ("native", "langgraph"))
async def test_durable_approval_concurrency_and_private_graph(
    durable_database: DatabaseCase,
    backend: Literal["native", "langgraph"],
) -> None:
    """Real framework scheduling and separate database sessions, no live model."""
    database = durable_database
    identity = context(tenant=database.tenant)
    calls = 0
    original = build_calculator_tool(
        timeout_seconds=5,
        rate_limit=ToolRateLimit(max_calls=100, window_seconds=60),
    )

    async def count_handler(
        payload: CalculatorInput,
        context: AuthenticatedRequestContext,
    ) -> object:
        nonlocal calls
        calls += 1
        return await original.handler(payload, context)

    definition = original.model_copy(update={"handler": count_handler})
    with tenant_scope(database.tenant):
        first = await service(database.sessions, backend=backend, definition=definition).start(
            context=identity,
            command=DurableStartCommand(
                input="SYNTHETIC PRIVATE DATABASE PROBE",
                approval_tools=("calculator",),
            ),
        )
        assert first.status == "awaiting_approval" and first.approval is not None
        assert calls == 0
        command = DurableResumeCommand(
            expected_revision=first.revision,
            approval_id=first.approval.approval_id,
            approve=True,
        )
        contenders = await asyncio.gather(
            *(
                service(database.sessions, backend=backend, definition=definition).resume(
                    context=identity,
                    run_id=first.run_id,
                    command=command,
                )
                for _ in range(2)
            ),
            return_exceptions=True,
        )
        completed = [item for item in contenders if not isinstance(item, BaseException)]
        rejected = [item for item in contenders if isinstance(item, DomainError)]
        assert len(completed) == len(rejected) == 1
        assert completed[0].status == "completed"
        assert completed[0].steps_used == 2 and completed[0].tool_calls_used == 1
        assert calls == 1
        assert rejected[0].code in {"DURABLE_RESUME_CONFLICT", "DURABLE_RUN_TERMINAL"}
        async with database.sessions() as session:
            ledger = list(
                await session.scalars(
                    select(DurableToolLedgerModel).where(
                        DurableToolLedgerModel.run_id == first.run_id,
                    )
                )
            )
            assert len(ledger) == 1 and ledger[0].status == "completed"
            graphs = list(
                await session.scalars(
                    select(DurableGraphCheckpointModel).where(
                        DurableGraphCheckpointModel.run_id == first.run_id,
                    )
                )
            )
            assert bool(graphs) is (backend == "langgraph")
            for graph in graphs:
                payload = json.dumps([graph.checkpoint, graph.metadata_, graph.writes])
                for private in (
                    "SYNTHETIC PRIVATE DATABASE PROBE",
                    "21 * 2",
                    "The result is 42",
                    database.tenant,
                    identity.auth.user_id,
                    "prompt_context",
                ):
                    assert private not in payload
        # A fresh service reads current committed state without re-driving the model/tool.
        recovered = await service(database.sessions, backend=backend, definition=definition).get(
            context=identity,
            run_id=first.run_id,
        )
        assert recovered == completed[0]
        assert calls == 1


@pytest.mark.parametrize("backend", ("native", "langgraph"))
async def test_keyed_database_creation_has_one_driver_and_retained_tombstone(
    durable_database: DatabaseCase,
    backend: Literal["native", "langgraph"],
) -> None:
    """Force both pre-reads to miss, then exercise competing atomic inserts."""
    database = durable_database
    identity = context(tenant=database.tenant)
    clock = Clock()
    key = uuid4()
    command = DurableStartCommand(input="SYNTHETIC KEYED DATABASE PROBE", idempotency_key=key)
    missing_reads = 0
    both_missing, model_entered, release_model = (asyncio.Event() for _ in range(3))
    model_calls = 0
    handler_calls = 0
    original = build_calculator_tool(
        timeout_seconds=5,
        rate_limit=ToolRateLimit(max_calls=100, window_seconds=60),
    )

    class RacingStore(SqlAlchemyDurableStore):
        async def lookup_keyed(self, **kwargs):
            nonlocal missing_reads
            result = await super().lookup_keyed(**kwargs)
            if result is None:
                missing_reads += 1
                if missing_reads == 2:
                    both_missing.set()
                await asyncio.wait_for(both_missing.wait(), 5)
            return result

    class CountedStepper:
        async def next_step(self, state):
            nonlocal model_calls
            model_calls += 1
            if not state.observations:
                model_entered.set()
                await release_model.wait()
                return AgentStepDecision.tool_call("calculator", {"expression": "21 * 2"})
            return AgentStepDecision(action="final_answer", final_answer="The result is 42")

    async def handler(payload, context):
        nonlocal handler_calls
        handler_calls += 1
        return await original.handler(payload, context)

    store = RacingStore(database.sessions)
    definition = original.model_copy(update={"handler": handler})
    with tenant_scope(database.tenant):
        tasks = [
            asyncio.create_task(
                service(
                    database.sessions,
                    backend=backend,
                    clock=clock,
                    store=store,
                    stepper=CountedStepper(),
                    definition=definition,
                ).start(context=identity, command=command)
            )
            for _ in range(2)
        ]
        try:
            await asyncio.wait_for(model_entered.wait(), 5)
            done, pending = await asyncio.wait(
                tasks, timeout=5, return_when=asyncio.FIRST_COMPLETED
            )
            assert len(done) == len(pending) == 1
            duplicate = done.pop().result()
            assert duplicate.status == "running"
            assert model_calls == 1 and handler_calls == 0
            release_model.set()
            results = await asyncio.gather(*tasks)
        finally:
            release_model.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert missing_reads == 2
        assert len({item.run_id for item in results}) == 1
        assert len({item.deadline_at for item in results}) == 1
        assert model_calls == 2 and handler_calls == 1
        recovered = await service(
            database.sessions,
            backend=backend,
            clock=clock,
            definition=definition,
            stepper=CountedStepper(),
        ).recover(context=identity, idempotency_key=key)
        assert recovered.status == "completed"
        assert recovered.steps_used == 2 and recovered.tool_calls_used == 1
        assert model_calls == 2 and handler_calls == 1
        async with database.sessions() as session:
            ledger = list(
                await session.scalars(
                    select(DurableToolLedgerModel).where(
                        DurableToolLedgerModel.run_id == recovered.run_id,
                    )
                )
            )
            assert len(ledger) == 1 and ledger[0].status == "completed"
            row = await session.get(DurableAgentRunModel, recovered.run_id)
            assert row is not None and str(key) not in json.dumps(row.snapshot)
        # Expired private rows can disappear, but the retained legacy key tombstone
        # must continue to prevent a new execution under the same startup key.
        clock.value += 86401
        await store.purge_expired(tenant_id=database.tenant, now=clock())
        async with database.sessions() as session:
            assert await session.get(DurableAgentRunModel, recovered.run_id) is None
            for model in (DurableToolLedgerModel, DurableGraphCheckpointModel):
                assert (
                    await session.scalar(
                        select(model.id).where(
                            model.run_id == recovered.run_id,
                        )
                    )
                    is None
                )
            tombstone = await session.get(AgentRunModel, recovered.run_id)
            assert tombstone is not None and tombstone.metadata_.get("keyed_start") is True
        with pytest.raises(DomainError) as expired:
            await service(
                database.sessions,
                backend=backend,
                clock=clock,
                stepper=CountedStepper(),
                definition=definition,
            ).start(context=identity, command=command)
        assert expired.value.code == "DURABLE_RUN_EXPIRED"
        assert model_calls == 2 and handler_calls == 1


async def test_durable_lease_cas_and_cancel_fence(durable_database: DatabaseCase) -> None:
    database = durable_database
    clock = Clock()
    identity = context(tenant=database.tenant)
    with tenant_scope(database.tenant):
        pending = await service(database.sessions, clock=clock).start(
            context=identity,
            command=DurableStartCommand(
                input="Synthetic CAS probe", approval_tools=("calculator",)
            ),
        )
        store = SqlAlchemyDurableStore(database.sessions)
        snapshot = await store.load(
            tenant_id=database.tenant,
            user_id=identity.auth.user_id,
            run_id=pending.run_id,
        )
        tokens = [str(uuid4()), str(uuid4())]
        outcomes = await asyncio.gather(
            *(
                store.acquire(snapshot, token=token, now=clock(), lease_seconds=30)
                for token in tokens
            ),
            return_exceptions=True,
        )
        winners = [
            index for index, item in enumerate(outcomes) if isinstance(item, DurableSnapshot)
        ]
        assert len(winners) == 1
        winner = winners[0]
        assert outcomes[winner] == snapshot
        loser = outcomes[1 - winner]
        assert isinstance(loser, DomainError) and loser.code == "DURABLE_RESUME_CONFLICT"
        token = tokens[winner]
        checkpoint_id = str(uuid4())
        await store.graph_put(
            snapshot,
            token=token,
            now=clock(),
            checkpoint={
                "id": checkpoint_id,
                "channel_values": {"run_ref": snapshot.run_id},
            },
            metadata={},
            parent_id=None,
        )
        entry = ToolLedgerEntry(
            call_id=f"{snapshot.run_id}-call",
            tool_name="calculator",
            action_digest="a" * 64,
            replay_policy="read_only",
            status="completed",
        )
        saved = await store.save(snapshot, snapshot, token=token, now=clock(), ledger=entry)
        with pytest.raises(DomainError) as immutable:
            await store.save(saved, saved, token=token, now=clock(), ledger=entry)
        assert immutable.value.code == "DURABLE_LEDGER_IMMUTABLE"
        current = await store.load(
            tenant_id=database.tenant,
            user_id=identity.auth.user_id,
            run_id=snapshot.run_id,
        )
        assert current.revision == saved.revision  # Failed ledger transaction rolled CAS back.
        cas_results = await asyncio.gather(
            *(store.save(current, current, token=token, now=clock()) for _ in range(2)),
            return_exceptions=True,
        )
        assert sum(not isinstance(item, BaseException) for item in cas_results) == 1
        cas_error = next(item for item in cas_results if isinstance(item, BaseException))
        assert isinstance(cas_error, DomainError) and cas_error.code == "DURABLE_LEASE_LOST"
        current = await store.load(
            tenant_id=database.tenant,
            user_id=identity.auth.user_id,
            run_id=snapshot.run_id,
        )
        assert current.revision == saved.revision + 1
        await asyncio.gather(
            *(
                store.graph_writes(
                    current,
                    token=token,
                    now=clock(),
                    checkpoint_id=checkpoint_id,
                    writes=[
                        {"task_id": f"synthetic-task-{index}", "index": index, "value": "done"}
                    ],
                )
                for index in (0, 1)
            )
        )
        checkpoint = await store.graph_get(current, checkpoint_id)
        assert checkpoint is not None and len(checkpoint["writes"]) == 2
        cancelled = await store.cancel(current, now=clock())
        assert cancelled.status == "cancelled"
        with pytest.raises(DomainError) as stale:
            await store.save(current, current, token=token, now=clock())
        assert stale.value.code == "DURABLE_LEASE_LOST"
        with pytest.raises(DomainError) as fenced:
            await store.graph_writes(
                current,
                token=token,
                now=clock(),
                checkpoint_id=checkpoint_id,
                writes=[],
            )
        assert fenced.value.code == "DURABLE_LEASE_LOST"
        assert not await store.owns(cancelled, token=token)


async def test_postgres_durable_tables_enforce_rls_without_application_filters(
    durable_database: DatabaseCase,
) -> None:
    database = durable_database
    if database.dialect != "postgres":
        pytest.skip("SQLite does not enforce PostgreSQL row-level security.")
    identity = context(tenant=database.tenant)
    models = (DurableAgentRunModel, DurableToolLedgerModel, DurableGraphCheckpointModel)
    originals = {}
    with tenant_scope(database.tenant):
        result = await service(database.sessions, backend="langgraph").start(
            context=identity,
            command=DurableStartCommand(input="Synthetic RLS probe"),
        )
        assert result.status == "completed"
        async with database.sessions() as session:
            for model in models:
                row = (await session.scalars(select(model))).first()
                assert row is not None
                originals[model] = {
                    attribute.key: getattr(row, attribute.key)
                    for attribute in model.__mapper__.column_attrs
                }
        with pytest.raises(DomainError) as wrong_user:
            await service(database.sessions).get(
                context=context(tenant=database.tenant, user="other-user"),
                run_id=result.run_id,
            )
        assert wrong_user.value.code == "DURABLE_RUN_NOT_FOUND"
        # WITH CHECK must prevent moving even a visible row to another tenant.
        for model in models:
            async with database.sessions() as session:
                try:
                    with pytest.raises(DBAPIError) as denied:
                        await session.execute(update(model).values(tenant_id="unowned-tenant"))
                    assert getattr(denied.value.orig, "sqlstate", None) == "42501"
                finally:
                    await session.rollback()
    # Same pool, different transaction-local tenant. Queries have no application filters.
    with tenant_scope(f"other-{database.tenant}"):
        for model in models:
            async with database.sessions() as session:
                assert list(await session.scalars(select(model))) == []
                changed = await session.execute(update(model).values(status="forbidden"))
                assert changed.rowcount == 0
                await session.rollback()
            values = dict(originals[model])
            values["id"] = str(uuid4())
            if "call_id" in values:
                values["call_id"] = str(uuid4())
            if "checkpoint_id" in values:
                values["checkpoint_id"] = str(uuid4())
            async with database.sessions() as session:
                try:
                    with pytest.raises(DBAPIError) as denied:
                        session.add(model(**values))
                        await session.flush()
                    assert getattr(denied.value.orig, "sqlstate", None) == "42501"
                finally:
                    # Roll back even if a broken policy unexpectedly allows the synthetic write.
                    await session.rollback()
    # Reusing those pooled connections with no tenant must also yield no rows.
    async with database.sessions() as session:
        for model in models:
            assert list(await session.scalars(select(model))) == []
