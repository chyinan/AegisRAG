from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.agent.adapters.document_evidence import DocumentEvidenceRevalidator
from packages.agent.dto import ToolDefinition, ToolRateLimit
from packages.agent.durable.dto import (
    DurableResumeCommand,
    DurableStartCommand,
)
from packages.agent.durable.service import DurableAgentService
from packages.agent.registry import ToolRegistry
from packages.agent.runtime import AgentRunConfig, AgentRuntime, AgentStepDecision
from packages.agent.storage.durable import SqlAlchemyDurableStore
from packages.agent.storage.models import (
    AgentRunModel,
    DurableAgentRunModel,
    DurableGraphCheckpointModel,
    DurableToolLedgerModel,
)
from packages.agent.tools.calculator import build_calculator_tool
from packages.auth.context import AuthContext
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError
from packages.data.storage.base import Base


class Audit:
    async def record(self, event: object) -> None:
        pass


class NoDocuments:
    async def get_document(self, **kwargs: object) -> None:
        return None

    async def get_version(self, **kwargs: object) -> None:
        return None

    async def get_chunk(self, **kwargs: object) -> None:
        return None


@dataclass
class Clock:
    value: float = 1_800_000_000.0

    def __call__(self) -> float:
        return self.value


def context(*, user: str = "user-a", tenant: str = "tenant-a", allow: bool = True):
    return AuthenticatedRequestContext(
        request_id="request-a",
        trace_id="trace-a",
        auth=AuthContext(
            user_id=user,
            tenant_id=tenant,
            permissions=("agent:run", "agent:tool:calculator") if allow else ("agent:run",),
        ),
    )


class Stepper:
    def __init__(self, *, repeat: bool = False) -> None:
        self.repeat = repeat

    async def next_step(self, state):
        if not state.observations or self.repeat:
            return AgentStepDecision.tool_call("calculator", {"expression": "21 * 2"})
        assert state.observations[-1].prompt_context.get("result") == "42"
        return AgentStepDecision(action="final_answer", final_answer="The result is 42")


@pytest.fixture
async def database(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'durable.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all,
            tables=[
                AgentRunModel.__table__,
                DurableAgentRunModel.__table__,
                DurableToolLedgerModel.__table__,
                DurableGraphCheckpointModel.__table__,
            ],
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    yield sessions
    await engine.dispose()


def service(
    sessions: async_sessionmaker[AsyncSession],
    *,
    backend: Literal["native", "langgraph"] = "native",
    clock: Clock | None = None,
    stepper: object | None = None,
    definition: ToolDefinition | None = None,
    store: SqlAlchemyDurableStore | None = None,
) -> DurableAgentService:
    if backend == "langgraph":
        pytest.importorskip("langgraph.graph")
    audit = Audit()
    registry = ToolRegistry(audit=audit)
    registry.register(
        definition
        or build_calculator_tool(
            timeout_seconds=5,
            rate_limit=ToolRateLimit(max_calls=100, window_seconds=60),
        )
    )

    def factory(config: AgentRunConfig, run_id: str) -> AgentRuntime:
        return AgentRuntime(
            registry=registry,
            stepper=stepper or Stepper(),
            audit=audit,
            config=config,
            agent_run_id=run_id,
        )

    return DurableAgentService(
        store=store or SqlAlchemyDurableStore(sessions),
        runtime_factory=factory,
        evidence_revalidator=DocumentEvidenceRevalidator(repository=NoDocuments()),
        backend=backend,
        wall_clock=clock or Clock(),
    )


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_durable_run_and_real_graph_state_are_private(database, backend):
    svc = service(database, backend=backend)
    result = await svc.start(
        context=context(), command=DurableStartCommand(input="PRIVATE QUESTION")
    )
    assert result.status == "completed"
    assert result.steps_used == 2 and result.tool_calls_used == 1
    assert result.final_answer == "The result is 42"
    async with database() as session:
        run = await session.scalar(select(DurableAgentRunModel))
        assert run is not None and "prompt_context" not in json.dumps(run.snapshot)
        ledger = await session.scalar(select(DurableToolLedgerModel))
        assert ledger is not None and ledger.status == "completed"
        graphs = (await session.scalars(select(DurableGraphCheckpointModel))).all()
        assert bool(graphs) == (backend == "langgraph")
        for checkpoint in graphs:
            text = json.dumps([checkpoint.checkpoint, checkpoint.metadata_, checkpoint.writes])
            for forbidden in (
                "PRIVATE QUESTION",
                "21 * 2",
                "The result is 42",
                "prompt_context",
                "tenant-a",
                "user-a",
            ):
                assert forbidden not in text


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_approval_resume_after_recreated_service(database, backend):
    clock = Clock()
    paused = await service(database, backend=backend, clock=clock).start(
        context=context(),
        command=DurableStartCommand(input="Calculate", approval_tools=("calculator",)),
    )
    assert paused.status == "awaiting_approval" and paused.tool_calls_used == 0
    assert paused.approval is not None
    clock.value += 5
    resumed = await service(database, backend=backend, clock=clock).resume(
        context=context(),
        run_id=paused.run_id,
        command=DurableResumeCommand(
            expected_revision=paused.revision, approval_id=paused.approval.approval_id, approve=True
        ),
    )
    assert resumed.status == "completed"
    assert resumed.steps_used == 2 and resumed.tool_calls_used == 1
    assert resumed.deadline_at == paused.deadline_at
    with pytest.raises(DomainError, match="DURABLE_RESUME_CONFLICT"):
        await service(database).resume(
            context=context(),
            run_id=paused.run_id,
            command=DurableResumeCommand(expected_revision=paused.revision),
        )


async def pause(database, *, clock=None):
    return await service(database, clock=clock).start(
        context=context(),
        command=DurableStartCommand(input="Calculate", approval_tools=("calculator",)),
    )


async def test_approval_rejection_is_terminal(database):
    paused = await pause(database)
    rejected = await service(database).resume(
        context=context(),
        run_id=paused.run_id,
        command=DurableResumeCommand(
            expected_revision=paused.revision,
            approval_id=paused.approval.approval_id,
            approve=False,
        ),
    )
    assert rejected.status == "cancelled"
    with pytest.raises(DomainError, match="DURABLE_RUN_TERMINAL"):
        await service(database).resume(
            context=context(),
            run_id=rejected.run_id,
            command=DurableResumeCommand(expected_revision=rejected.revision),
        )


async def test_approval_rechecks_current_permission_and_exact_token(database):
    paused = await pause(database)
    for auth, approval_id, code in [
        (context(allow=False), paused.approval.approval_id, "TOOL_PERMISSION_DENIED"),
        (context(), "another-approval", "DURABLE_APPROVAL_STALE"),
    ]:
        with pytest.raises(DomainError, match=code):
            await service(database).resume(
                context=auth,
                run_id=paused.run_id,
                command=DurableResumeCommand(
                    expected_revision=paused.revision, approval_id=approval_id, approve=True
                ),
            )
    assert (
        await service(database).get(context=context(), run_id=paused.run_id)
    ).revision == paused.revision


async def test_approval_definition_change_fails_closed(database):
    paused = await pause(database)
    changed = build_calculator_tool(
        timeout_seconds=5,
        rate_limit=ToolRateLimit(max_calls=100, window_seconds=60),
    ).model_copy(update={"requires_approval": True})
    with pytest.raises(DomainError, match="DURABLE_APPROVAL_STALE"):
        await service(database, definition=changed).resume(
            context=context(),
            run_id=paused.run_id,
            command=DurableResumeCommand(
                expected_revision=paused.revision,
                approval_id=paused.approval.approval_id,
                approve=True,
            ),
        )


async def test_expired_approval_does_not_reset_original_deadline(database):
    clock = Clock()
    paused = await pause(database, clock=clock)
    clock.value += 121
    with pytest.raises(DomainError, match="DURABLE_APPROVAL_STALE"):
        await service(database, clock=clock).resume(
            context=context(),
            run_id=paused.run_id,
            command=DurableResumeCommand(
                expected_revision=paused.revision,
                approval_id=paused.approval.approval_id,
                approve=True,
            ),
        )


async def test_cross_user_tenant_and_future_schema_fail_closed(database):
    paused = await pause(database)
    for auth in (context(user="other"), context(tenant="other")):
        with pytest.raises(DomainError, match="DURABLE_RUN_NOT_FOUND"):
            await service(database).get(context=auth, run_id=paused.run_id)
    async with database.begin() as session:
        await session.execute(update(DurableAgentRunModel).values(schema_version=99))
    with pytest.raises(DomainError, match="DURABLE_SCHEMA_UNSUPPORTED"):
        await service(database).get(context=context(), run_id=paused.run_id)


class Crash(BaseException):
    """Simulate abrupt process loss rather than a normal caught tool failure."""


class CrashAfterCommitStore(SqlAlchemyDurableStore):
    async def save(self, previous, snapshot, **kwargs):
        saved = await super().save(previous, snapshot, **kwargs)
        ledger = kwargs.get("ledger")
        if ledger is not None and ledger.status == "completed":
            raise Crash
        return saved


async def only_snapshot(database):
    async with database() as session:
        row = await session.scalar(select(DurableAgentRunModel))
        return row


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_restart_after_committed_tool_result_does_not_repeat(database, backend):
    with pytest.raises(Crash):
        await service(database, backend=backend, store=CrashAfterCommitStore(database)).start(
            context=context(),
            command=DurableStartCommand(input="Calculate"),
        )
    row = await only_snapshot(database)
    result = await service(database, backend=backend).resume(
        context=context(),
        run_id=row.id,
        command=DurableResumeCommand(expected_revision=row.revision),
    )
    assert result.status == "completed" and result.tool_calls_used == 1
    async with database() as session:
        assert len((await session.scalars(select(DurableToolLedgerModel))).all()) == 1


@pytest.mark.parametrize(
    "policy,expected", [("never", "intervention_required"), ("read_only", "completed")]
)
async def test_unknown_dispatch_only_replays_explicit_read_only(database, policy, expected):
    calls = 0
    base = build_calculator_tool(
        timeout_seconds=5, rate_limit=ToolRateLimit(max_calls=100, window_seconds=60)
    )

    async def handler(payload, ctx):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise Crash
        return await base.handler(payload, ctx)

    definition = base.model_copy(update={"handler": handler, "durable_replay_policy": policy})
    with pytest.raises(Crash):
        await service(database, definition=definition).start(
            context=context(),
            command=DurableStartCommand(input="Calculate"),
        )
    row = await only_snapshot(database)
    result = await service(database, definition=definition).resume(
        context=context(),
        run_id=row.id,
        command=DurableResumeCommand(expected_revision=row.revision),
    )
    assert result.status == expected
    assert calls == (1 if policy == "never" else 2)
    assert result.tool_calls_used == calls


async def test_repeat_guard_and_max_steps_survive_restart(database):
    paused = await pause(database)
    result = await service(database, stepper=Stepper(repeat=True)).resume(
        context=context(),
        run_id=paused.run_id,
        command=DurableResumeCommand(
            expected_revision=paused.revision, approval_id=paused.approval.approval_id, approve=True
        ),
    )
    # Each repeated proposed action needs a new approval; a grant is single-use.
    assert result.status == "awaiting_approval" and result.tool_calls_used == 1
    assert result.approval.approval_id != paused.approval.approval_id


async def test_concurrent_resume_has_single_lease_and_cancel_fences_late_result(database):
    paused = await pause(database)
    started, release = asyncio.Event(), asyncio.Event()
    base = build_calculator_tool(
        timeout_seconds=5, rate_limit=ToolRateLimit(max_calls=100, window_seconds=60)
    )

    async def handler(payload, ctx):
        started.set()
        await release.wait()
        return await base.handler(payload, ctx)

    definition = base.model_copy(update={"handler": handler})
    svc = service(database, definition=definition)
    first = asyncio.create_task(
        svc.resume(
            context=context(),
            run_id=paused.run_id,
            command=DurableResumeCommand(
                expected_revision=paused.revision,
                approval_id=paused.approval.approval_id,
                approve=True,
            ),
        )
    )
    await asyncio.wait_for(started.wait(), 5)
    current = await service(database).get(context=context(), run_id=paused.run_id)
    with pytest.raises(DomainError, match="DURABLE_RESUME_CONFLICT"):
        await service(database).resume(
            context=context(),
            run_id=paused.run_id,
            command=DurableResumeCommand(expected_revision=current.revision),
        )
    cancelled = await service(database).cancel(
        context=context(), run_id=paused.run_id, expected_revision=current.revision
    )
    assert cancelled.status == "cancelled"
    release.set()
    with pytest.raises(DomainError, match="DURABLE_LEASE_LOST"):
        await first
    final = await service(database).get(context=context(), run_id=paused.run_id)
    assert final.status == "cancelled" and final.final_answer is None
    async with database() as session:
        legacy = await session.scalar(select(AgentRunModel))
        assert legacy.status == "stopped" and legacy.error_code == "DURABLE_CANCELLED"


async def test_retention_purges_private_content_but_retains_redacted_run(database):
    clock = Clock()
    paused = await pause(database, clock=clock)
    clock.value += 86401
    with pytest.raises(DomainError, match="DURABLE_RUN_EXPIRED"):
        await service(database, clock=clock).get(context=context(), run_id=paused.run_id)
    async with database() as session:
        assert await session.scalar(select(DurableAgentRunModel)) is None
        assert await session.scalar(select(AgentRunModel)) is not None


async def test_secret_shaped_input_is_not_persisted(database):
    with pytest.raises(DomainError, match="DURABLE_SENSITIVE_ARGUMENT"):
        await service(database).start(
            context=context(), command=DurableStartCommand(input="password=secret")
        )
    assert await only_snapshot(database) is None


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_server_required_approval_cannot_use_legacy_registry_but_durable_can(
    database, backend
):
    base = build_calculator_tool(
        timeout_seconds=5,
        rate_limit=ToolRateLimit(max_calls=100, window_seconds=60),
    ).model_copy(update={"requires_approval": True})
    registry = ToolRegistry(audit=Audit())
    registry.register(base)
    with pytest.raises(DomainError, match="TOOL_APPROVAL_REQUIRED"):
        await registry.execute(
            name="calculator", arguments={"expression": "21 * 2"}, context=context()
        )
    paused = await service(database, definition=base, backend=backend).start(
        context=context(),
        command=DurableStartCommand(input="Calculate"),
    )
    assert paused.status == "awaiting_approval"
    result = await service(database, definition=base, backend=backend).resume(
        context=context(),
        run_id=paused.run_id,
        command=DurableResumeCommand(
            expected_revision=paused.revision, approval_id=paused.approval.approval_id, approve=True
        ),
    )
    assert result.status == "completed" and result.tool_calls_used == 1


async def test_approval_tools_are_exact_unique_known_names(database):
    from pydantic import ValidationError

    for names in ((" calculator",), ("calculator", "calculator"), ("CALCULATOR",)):
        with pytest.raises(ValidationError):
            DurableStartCommand(input="Calculate", approval_tools=names)
    with pytest.raises(DomainError, match="DURABLE_APPROVAL_TOOL_UNKNOWN"):
        await service(database).start(
            context=context(),
            command=DurableStartCommand(input="Calculate", approval_tools=("calclator",)),
        )
    assert await only_snapshot(database) is None


async def test_zero_tool_budget_does_not_pause_or_dispatch(database):
    response = await service(database).start(
        context=context(),
        command=DurableStartCommand(
            input="Calculate", max_tool_calls=0, approval_tools=("calculator",)
        ),
    )
    assert response.status == "stopped" and response.termination_reason == "MAX_TOOL_CALLS_REACHED"
    assert response.tool_calls_used == 0 and response.approval is None


async def test_model_attempt_budget_reserved_before_crash(database):
    class CrashingStepper:
        calls = 0

        async def next_step(self, state):
            self.calls += 1
            raise Crash

    stepper = CrashingStepper()
    with pytest.raises(Crash):
        await service(database, stepper=stepper).start(
            context=context(),
            command=DurableStartCommand(input="Calculate", max_steps=1),
        )
    row = await only_snapshot(database)
    assert row.snapshot["steps_used"] == 1
    response = await service(database, stepper=stepper).resume(
        context=context(),
        run_id=row.id,
        command=DurableResumeCommand(expected_revision=row.revision),
    )
    assert response.status == "stopped" and response.termination_reason == "MAX_STEPS_REACHED"
    assert stepper.calls == 1


_PROCESS_SCRIPT = r"""
import asyncio, json, os, sys, time
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from packages.agent.durable.dto import DurableStartCommand, DurableResumeCommand
from packages.agent.dto import ToolRateLimit
from packages.agent.tools.calculator import build_calculator_tool
from tests.unit.agent.test_durable_execution import service, context
async def main():
    url, backend, mode = sys.argv[1:]
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    definition = None
    if mode in ("crash", "recover"):
        definition = build_calculator_tool(timeout_seconds=5,
            rate_limit=ToolRateLimit(max_calls=100, window_seconds=60))
        async def crash_handler(payload, context):
            os._exit(17)
        definition = definition.model_copy(update={"durable_replay_policy": "never",
            **({"handler": crash_handler} if mode == "crash" else {})})
    app = service(sessions, backend=backend, clock=time.time, definition=definition)
    app._lease_seconds = 2.0 if mode == "crash" else 30.0
    if mode in ("start", "crash"):
        result = await app.start(context=context(), command=DurableStartCommand(
            input="Private process restart question",
            approval_tools=("calculator",) if mode == "start" else ()))
    else:
        previous = json.load(sys.stdin)
        approval = previous.get("approval")
        result = await app.resume(context=context(), run_id=previous["run_id"],
            command=DurableResumeCommand(expected_revision=previous["revision"],
                approval_id=approval["approval_id"] if approval else None,
                approve=True if approval else None))
    print(result.model_dump_json())
    await engine.dispose()
asyncio.run(main())
"""


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_two_fresh_python_processes_resume_persisted_approval(database, backend):
    import subprocess
    import sys

    if backend == "langgraph":
        pytest.importorskip("langgraph.graph")
    url = str(database.kw["bind"].url)
    first = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", _PROCESS_SCRIPT, url, backend, "start"],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    paused = json.loads(first.stdout)
    assert paused["status"] == "awaiting_approval"
    second = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", _PROCESS_SCRIPT, url, backend, "resume"],
        input=json.dumps(paused),
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    completed = json.loads(second.stdout)
    assert completed["status"] == "completed" and completed["tool_calls_used"] == 1
    assert completed["deadline_at"] == paused["deadline_at"]


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_hard_process_exit_after_dispatch_never_repeats_side_effect(database, backend):
    import subprocess
    import sys

    if backend == "langgraph":
        pytest.importorskip("langgraph.graph")
    url = str(database.kw["bind"].url)
    crashed = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", _PROCESS_SCRIPT, url, backend, "crash"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert crashed.returncode == 17, crashed.stderr
    row = await only_snapshot(database)
    assert row.snapshot["pending_call_id"] is not None
    await asyncio.sleep(2.1)  # Let the crashed process's short test lease expire.
    restarted = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", _PROCESS_SCRIPT, url, backend, "recover"],
        input=json.dumps({"run_id": row.id, "revision": row.revision}),
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    result = json.loads(restarted.stdout)
    assert result["status"] == "intervention_required"
    assert result["tool_calls_used"] == 1


async def test_lifecycle_audit_contains_only_bounded_control_metadata(database):
    from packages.common.audit import InMemoryAuditPort

    audit = InMemoryAuditPort()
    app = service(database)
    app._audit = audit
    paused = await app.start(
        context=context(),
        command=DurableStartCommand(input="PRIVATE AUDIT QUESTION", approval_tools=("calculator",)),
    )
    completed = await app.resume(
        context=context(),
        run_id=paused.run_id,
        command=DurableResumeCommand(
            expected_revision=paused.revision,
            approval_id=paused.approval.approval_id,
            approve=True,
        ),
    )
    assert completed.status == "completed"
    assert [event.action for event in audit.events] == [
        "agent.durable.created",
        "agent.durable.awaiting_approval",
        "agent.durable.running",
        "agent.durable.completed",
    ]
    encoded = json.dumps([event.model_dump(mode="json") for event in audit.events])
    assert "PRIVATE AUDIT QUESTION" not in encoded
    assert "21 * 2" not in encoded
    assert "The result is 42" not in encoded


@pytest.mark.parametrize("finish_during_cancel", [False, True])
async def test_cancel_suppressing_predispatch_dependency_cannot_launch_late_handler(
    finish_during_cancel,
):
    from packages.agent.dto import ToolRateLimitDecision

    entered = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    handler_calls = []

    class DelayedLimiter:
        async def acquire(self, **kwargs):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                if finish_during_cancel:
                    release.set()
                await release.wait()
            finally:
                finished.set()
            return ToolRateLimitDecision(allowed=True, remaining=1, reset_after_seconds=1)

    class Journal:
        async def before_dispatch(self, execution, call_id):
            return None

        async def after_dispatch(self, execution, call_id, result):
            raise AssertionError("No handler dispatch is allowed")

    base = build_calculator_tool(
        timeout_seconds=2, rate_limit=ToolRateLimit(max_calls=10, window_seconds=60)
    )

    async def handler(payload, ctx):
        handler_calls.append(payload)
        return await base.handler(payload, ctx)

    registry = ToolRegistry(audit=Audit(), rate_limiter=DelayedLimiter())
    registry.register(base.model_copy(update={"handler": handler}))
    runtime = AgentRuntime(
        registry=registry,
        stepper=Stepper(),
        audit=Audit(),
        config=AgentRunConfig(
            max_steps=2, max_tool_calls=1, timeout_seconds=0.05, repeated_action_threshold=3
        ),
    )
    execution = runtime._new_execution(context())
    execution.decision = AgentStepDecision.tool_call("calculator", {"expression": "21 * 2"})
    task = asyncio.create_task(runtime._execute_tool(execution, journal=Journal()))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        result = await asyncio.wait_for(task, 1)
        assert result is not None and result.status.value == "stopped"
        assert execution.dispatch_state is not None and execution.dispatch_state.revoked
        release.set()
        await asyncio.wait_for(finished.wait(), 1)
        await asyncio.sleep(0.01)
        assert handler_calls == []
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("v", 999),
        ("channel_versions", {"run_ref": "PRIVATE TEXT"}),
        ("versions_seen", {"advance": {"route": {"private": "TEXT"}}}),
        ("updated_channels", ["PRIVATE TEXT"]),
        ("id", "PRIVATE TEXT"),
        ("ts", "PRIVATE TEXT"),
        ("channel_values", {"messages": ["PRIVATE TEXT"]}),
    ],
)
def test_graph_checkpoint_schema_is_explicit_and_rejects_arbitrary_bookkeeping(field, value):
    from types import SimpleNamespace

    pytest.importorskip("langgraph")
    from langgraph.pregel._checkpoint import empty_checkpoint

    from packages.agent.adapters.durable_langgraph import ReferenceCheckpointSaver

    saver = ReferenceCheckpointSaver(SimpleNamespace(snapshot=SimpleNamespace(run_id="run-id")))
    checkpoint = dict(empty_checkpoint())
    assert saver._checkpoint(checkpoint)["v"] == 4
    checkpoint[field] = value
    with pytest.raises(DomainError):
        saver._checkpoint(checkpoint)


@pytest.mark.parametrize("logging_mode", ["info", "debug", "echo", "echo_debug"])
async def test_private_storage_rejects_sql_logging_before_canary_can_be_exposed(
    database, caplog, logging_mode
):
    import logging

    canary = "SYNTHETIC PRIVATE SNAPSHOT CANARY"
    app = service(database)
    engine = database.kw["bind"].sync_engine
    original_echo = engine.echo
    try:
        if logging_mode.startswith("echo"):
            engine.echo = "debug" if logging_mode == "echo_debug" else True
        level = logging.DEBUG if logging_mode == "debug" else logging.INFO
        with caplog.at_level(level, logger="sqlalchemy.engine.Engine"):
            with pytest.raises(DomainError, match="DURABLE_STORAGE_LOGGING_UNSAFE"):
                await app.start(context=context(), command=DurableStartCommand(input=canary))
        assert canary not in caplog.text
    finally:
        engine.echo = original_echo
    async with database() as session:
        assert await session.scalar(select(DurableAgentRunModel)) is None
