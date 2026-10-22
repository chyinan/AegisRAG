"""Disposable SQLite ordinary lifecycle checks; no remote database acceptance."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.factories.agent import create_agent_runtime
from packages.agent.dto import AgentRunCommand, AgentRunCreate, AgentRunRecord, AgentRunUpdate
from packages.agent.exceptions import AGENT_RUN_STORAGE_FAILED
from packages.agent.runtime import AgentEventSink, AgentRunConfig
from packages.agent.service import AgentRunApplicationService, AgentRuntimePort
from packages.agent.storage.repositories import AgentRunRepository
from packages.agent.streaming import AgentStreamEvent
from packages.common.audit import InMemoryAuditPort
from packages.data.storage.base import Base
from tests.unit.agent.test_persistence_phases import Gate
from tests.unit.agent.test_runtime import FakeStepper, _registry
from tests.unit.agent.test_streaming_service import _allowed_context, _final


@pytest.fixture
async def sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'phase-tests.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


class ControlledRepository(AgentRunRepository):
    def __init__(self, session: AsyncSession, phase: str) -> None:
        super().__init__(session)
        self.phase = phase
        self.gate = Gate()
        self.run_id: str | None = None
        self.commits = 0
        self.updates: list[str] = []

    async def create_run(self, record: AgentRunCreate) -> AgentRunRecord:
        created = await super().create_run(record)
        self.run_id = created.id
        if self.phase == "create":
            await self.gate.wait()
        return created

    async def update_run_result(
        self,
        *,
        tenant_id: str,
        user_id: str,
        run_id: str,
        update: AgentRunUpdate,
    ) -> AgentRunRecord:
        self.updates.append(update.status)
        result = await super().update_run_result(
            tenant_id=tenant_id,
            user_id=user_id,
            run_id=run_id,
            update=update,
        )
        if self.phase == "update":
            await self.gate.wait()
        return result

    async def commit(self) -> None:
        self.commits += 1
        await super().commit()
        if self.phase == f"commit:{self.commits}":
            # The transaction is durable, but its acknowledgement is lost.
            await self.gate.wait()


def application(repo: ControlledRepository, backend: str) -> AgentRunApplicationService:
    stepper = FakeStepper([_final()])

    def factory(
        config: AgentRunConfig,
        run_id: str,
        sink: AgentEventSink | None = None,
    ) -> AgentRuntimePort:
        return create_agent_runtime(
            backend=backend,
            config=config,
            agent_run_id=run_id,
            stepper=stepper,
            registry=_registry(),
            audit=InMemoryAuditPort(),
            event_sink=sink,
        )

    return AgentRunApplicationService(
        repository=repo,
        runtime_factory=factory,
        stream_runtime_factory=factory,
        audit=InMemoryAuditPort(),
        default_max_steps=3,
        default_max_tool_calls=2,
        default_timeout_seconds=30,
        repeated_action_threshold=3,
        persistence_phase_timeout_seconds=5,
    )


@pytest.mark.parametrize("backend", ["native", "langgraph"])
@pytest.mark.parametrize("phase", ["create", "update", "commit:1", "commit:2"])
async def test_sqlite_failure_has_no_answer_and_preserves_actual_committed_state(
    sessions: async_sessionmaker[AsyncSession],
    backend: str,
    phase: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import packages.agent.service as module

    contexts: list[asyncio.Timeout] = []
    original = asyncio.timeout

    def timeout(delay: float) -> asyncio.Timeout:
        context = original(delay)
        contexts.append(context)
        return context

    monkeypatch.setattr(module.asyncio, "timeout", timeout)
    if backend == "langgraph":
        pytest.importorskip("langgraph")
    async with sessions() as session:
        repo = ControlledRepository(session, phase)
        app = application(repo, backend)

        async def collect() -> list[AgentStreamEvent]:
            return [
                event
                async for event in app.stream(
                    context=_allowed_context(),
                    command=AgentRunCommand(input="ordinary request"),
                )
            ]

        task = asyncio.create_task(collect())
        await asyncio.wait_for(repo.gate.entered.wait(), 5)
        # Expire the real active phase only after the intended database operation
        # has reached its gate; cold ORM initialization is not the tested stall.
        contexts[-1].reschedule(asyncio.get_running_loop().time())
        events = await task
        assert repo.gate.cancelled.is_set()
        assert events[-1].event == "error"
        assert events[-1].data["code"] == AGENT_RUN_STORAGE_FAILED
        assert not any(event.event == "final" for event in events)
        assert "stopped" not in repo.updates
        assert repo.run_id is not None
    async with sessions() as fresh:
        stored = await AgentRunRepository(fresh).get_run(
            tenant_id="tenant-1",
            user_id="user-1",
            run_id=repo.run_id,
        )
    if phase == "create":
        assert stored is None
    else:
        assert stored is not None
        assert stored.status == ("completed" if phase == "commit:2" else "running")
    assert repo.commits == (2 if phase == "commit:2" else 0 if phase == "create" else 1)


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_sqlite_caller_cancellation_after_applied_commit_never_overwrites(
    sessions: async_sessionmaker[AsyncSession],
    backend: str,
) -> None:
    if backend == "langgraph":
        pytest.importorskip("langgraph")
    async with sessions() as session:
        repo = ControlledRepository(session, "commit:2")
        app = application(repo, backend)
        app._persistence_phase_timeout_seconds = 2
        task = asyncio.create_task(
            app.run(
                context=_allowed_context(),
                command=AgentRunCommand(input="ordinary request"),
            )
        )
        await asyncio.wait_for(repo.gate.entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert repo.run_id is not None
        assert repo.updates == ["completed"]
    async with sessions() as fresh:
        stored = await AgentRunRepository(fresh).get_run(
            tenant_id="tenant-1",
            user_id="user-1",
            run_id=repo.run_id,
        )
        assert stored is not None and stored.status == "completed"
