"""Disposable process for lost-first-response recovery, without storing a run ID."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.agent.durable.dto import DurableResumeCommand, DurableStartCommand
from packages.agent.storage.durable import SqlAlchemyDurableStore
from packages.agent.storage.models import DurableToolLedgerModel
from packages.data.storage.base import Base
from tests.unit.agent.test_durable_execution import Clock, context, service

KEY = UUID("587629a6-65a2-4db2-8e18-c17d8190c0ac")


async def main() -> None:
    directory, backend, stage, mode = sys.argv[1:5]
    path = Path(directory) / "keyed-process.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    class Store(SqlAlchemyDurableStore):
        async def create_keyed(self, snapshot, **kwargs):
            result = await super().create_keyed(snapshot, **kwargs)
            if mode == "crash" and stage == "initial_commit":
                os._exit(42)
            return result

        async def save(self, previous, snapshot, **kwargs):
            result = await super().save(previous, snapshot, **kwargs)
            ledger = kwargs.get("ledger")
            if mode == "crash" and stage == "tool_commit" and ledger is not None:
                if ledger.status == "completed":
                    os._exit(42)
            return result

    clock = Clock()
    if mode != "crash":
        clock.value += 31
    app = service(sessions, backend=backend, clock=clock, store=Store(sessions))
    request = DurableStartCommand(input="Calculate", idempotency_key=KEY)
    try:
        if mode == "crash":
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            await app.start(context=context(), command=request)
            if stage == "completed":
                os._exit(42)
            raise AssertionError("must exit before delivering the start response")
        recovered = await app.recover(context=context(), idempotency_key=KEY)
        # Changing defaults cannot affect recovery or turn a retry into a new run.
        app._defaults = (2, 1, 15)
        duplicate = await app.start(context=context(), command=request)
        assert duplicate == recovered
        assert recovered.deadline_at == Clock().value + 120
        expected_calls = 0 if stage == "initial_commit" else 1
        assert recovered.tool_calls_used == expected_calls
        if recovered.status != "completed":
            result = await app.resume(
                context=context(),
                run_id=recovered.run_id,
                command=DurableResumeCommand(expected_revision=recovered.revision),
            )
        else:
            result = recovered
        assert result.status == "completed"
        assert result.tool_calls_used == 1 and result.steps_used == 2
        assert result.deadline_at == recovered.deadline_at
        async with sessions() as session:
            entries = (await session.scalars(select(DurableToolLedgerModel))).all()
        assert len(entries) == 1 and entries[0].status == "completed"
        print("completed")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
