from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.factories.agent import create_agent_runtime
from packages.agent.dto import AgentRunCommand
from packages.agent.runtime import AgentEventSink, AgentRunConfig
from packages.agent.service import AGENT_RUN_CANCELLED, AgentRunApplicationService, AgentRuntimePort
from packages.agent.storage.repositories import AgentRunRepository
from packages.common.audit import InMemoryAuditPort
from packages.data.storage.base import Base
from tests.unit.agent.test_runtime import _registry
from tests.unit.agent.test_streaming_service import WaitingStepper, _allowed_context


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_closed_stream_terminal_survives_new_session(tmp_path: Path, backend: str) -> None:
    if backend == "langgraph":
        pytest.importorskip("langgraph")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agent.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    stepper = WaitingStepper()

    def factory(
        config: AgentRunConfig,
        run_id: str,
        event_sink: AgentEventSink | None = None,
    ) -> AgentRuntimePort:
        return create_agent_runtime(
            backend=backend,
            config=config,
            agent_run_id=run_id,
            stepper=stepper,
            registry=_registry(),
            audit=InMemoryAuditPort(),
            event_sink=event_sink,
        )

    try:
        async with sessions() as session:
            service = AgentRunApplicationService(
                repository=AgentRunRepository(session),
                runtime_factory=factory,
                stream_runtime_factory=factory,
                audit=InMemoryAuditPort(),
                default_max_steps=4,
                default_max_tool_calls=3,
                default_timeout_seconds=30,
                repeated_action_threshold=3,
            )
            events = service.stream(context=_allowed_context(), command=AgentRunCommand(input="x"))
            first = await anext(events)
            assert first.event == "started"
            await asyncio.wait_for(stepper.entered.wait(), 1)
            await events.aclose()
        async with sessions() as fresh_session:
            record = await AgentRunRepository(fresh_session).get_run(
                tenant_id="tenant-1",
                user_id="user-1",
                run_id=first.agent_run_id or "",
            )
            assert record is not None
            assert record.status == "stopped"
            assert record.termination_reason == AGENT_RUN_CANCELLED
            assert record.metadata["counters_complete"] is False
    finally:
        await engine.dispose()
