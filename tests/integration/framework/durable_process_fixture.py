"""Offline child process for hard-exit recovery tests; no external providers."""
# Startup diagnostics intentionally precede third-party/application imports.
# ruff: noqa: E402
from __future__ import annotations

import asyncio
import faulthandler
import os
import sys
from pathlib import Path


def _phase(name: str) -> None:
    sys.stderr.write(f"durable_process_fixture phase={name}\n")
    sys.stderr.flush()


if __name__ == "__main__":
    _phase("bootstrap")
    # Capture a stalled import/setup/operation before the parent's unchanged
    # 30-second hard bound. This disposable synthetic child contains no inputs
    # from users and diagnostics never include snapshot or argument contents.
    faulthandler.dump_traceback_later(20, repeat=False)

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.agent.dto import ToolDefinition, ToolRateLimit
from packages.agent.durable.dto import DurableResumeCommand, DurableStartCommand
from packages.agent.durable.evidence import RehydratedEvidence
from packages.agent.durable.service import DurableAgentService
from packages.agent.registry import ToolRegistry
from packages.agent.runtime import (
    AgentRunConfig,
    AgentRuntime,
    AgentRuntimeState,
    AgentStepDecision,
)
from packages.agent.storage.durable import SqlAlchemyDurableStore
from packages.auth.context import AuthContext
from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.data.storage.base import Base

if __name__ == "__main__":
    _phase("imports_ready")


class Input(BaseModel):
    value: int


class Output(BaseModel):
    status: str


class Stepper:
    async def next_step(self, state: AgentRuntimeState) -> AgentStepDecision:
        if not state.observations:
            return AgentStepDecision.tool_call("count_once", {"value": 1})
        return AgentStepDecision(action="final_answer", final_answer="Completed safely.")


class Evidence:
    async def rehydrate(self, *, context, observations, fingerprints):
        assert not any(observation.citation_refs for observation in observations)
        return RehydratedEvidence(tuple(observations), dict(fingerprints))


async def main() -> None:
    _phase("main_entered")
    directory = Path(sys.argv[1])
    backend, mode = sys.argv[2:4]
    engine = create_async_engine(f"sqlite+aiosqlite:///{directory / 'process.db'}")
    _phase("engine_ready")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    context = AuthenticatedRequestContext(
        request_id="process-request", trace_id="process-trace", auth=AuthContext(
            tenant_id="test-tenant", user_id="test-user", permissions=("agent:run", "test:count"),
        ),
    )

    class Store(SqlAlchemyDurableStore):
        async def create(self, snapshot):
            await super().create(snapshot)
            (directory / "run-id").write_text(snapshot.run_id)
            _phase("run_created")

        async def save(self, previous, snapshot, *, token, now, ledger=None):
            result = await super().save(previous, snapshot, token=token, now=now, ledger=ledger)
            if mode == "crash" and ledger is not None and ledger.status == "completed":
                _phase("tool_result_committed")
                # Deliberately bypass every Python finally/release handler after
                # the actual database commit. Only this disposable child exits.
                os._exit(42)
            return result

    async def count(payload: Input, request: AuthenticatedRequestContext) -> Output:
        counter = directory / "dispatch-count"
        previous = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(previous + payload.value))
        _phase("tool_dispatched")
        return Output(status="success")

    registry = ToolRegistry(audit=InMemoryAuditPort())
    registry.register(ToolDefinition(
        name="count_once", description="Synthetic crash-test counter.",
        input_schema=Input, output_schema=Output, permission="test:count", timeout_seconds=2,
        rate_limit=ToolRateLimit(max_calls=10, window_seconds=60), handler=count,
        durable_replay_policy="never",
    ))

    def runtime(config: AgentRunConfig, run_id: str) -> AgentRuntime:
        # The durable service owns phase scheduling for either backend.
        return AgentRuntime(registry=registry, stepper=Stepper(), config=config,
                            audit=InMemoryAuditPort(), agent_run_id=run_id)

    store = Store(sessions)
    service = DurableAgentService(
        store=store, runtime_factory=runtime, evidence_revalidator=Evidence(), backend=backend,
        wall_clock=lambda: 1000.0 if mode == "crash" else 1031.0,
        lease_seconds=30, default_timeout_seconds=120,
    )
    try:
        if mode == "crash":
            _phase("schema_start")
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            _phase("schema_ready")
            await service.start(context=context, command=DurableStartCommand(input="Count once"))
            raise AssertionError("child must exit at the committed tool-result boundary")
        run_id = (directory / "run-id").read_text()
        _phase("resume_start")
        saved = await store.load(tenant_id="test-tenant", user_id="test-user", run_id=run_id)
        result = await service.resume(context=context, run_id=run_id, command=DurableResumeCommand(
            expected_revision=saved.revision,
        ))
        assert result.status == "completed", result.status
        assert result.tool_calls_used == 1
        assert result.steps_used == 2
        assert (directory / "dispatch-count").read_text() == "1"
        _phase("resume_completed")
        print(result.status)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        faulthandler.cancel_dump_traceback_later()
