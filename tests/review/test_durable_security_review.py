"""Independent durable policy/CAS/privacy probes using local SQLite and inert tools."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from packages.agent.dto import ToolRateLimit
from packages.agent.durable.dto import DurableResumeCommand, DurableStartCommand, ToolLedgerEntry
from packages.agent.runtime import AgentStepDecision
from packages.agent.storage.durable import SqlAlchemyDurableStore
from packages.agent.storage.models import DurableGraphCheckpointModel
from packages.agent.tools.calculator import build_calculator_tool
from packages.common.errors import DomainError
from tests.unit.agent.test_durable_execution import (
    Clock,
    Stepper,
    context,
    service,
)
from tests.unit.agent.test_durable_execution import (
    database as _database,
)

# Reuse the isolated local SQLite fixture, without importing its tests.
database = _database


def tracked_definition(*, policy: str = "read_only", required: bool = False):
    base = build_calculator_tool(
        timeout_seconds=2, rate_limit=ToolRateLimit(max_calls=100, window_seconds=60)
    )
    seen: list[str] = []

    async def handler(payload: Any, ctx: Any) -> Any:
        seen.append(payload.expression)
        return await base.handler(payload, ctx)

    return base.model_copy(
        update={"handler": handler, "durable_replay_policy": policy, "requires_approval": required}
    ), seen


@dataclass
class InvalidArguments:
    async def next_step(self, state: Any) -> AgentStepDecision:
        return AgentStepDecision.tool_call("calculator", {"expression": ""})


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_known_predispatch_denial_is_not_an_unknown_side_effect(database, backend) -> None:
    definition, calls = tracked_definition(policy="never")
    result = await service(
        database, backend=backend, definition=definition, stepper=InvalidArguments()
    ).start(context=context(), command=DurableStartCommand(input="Reject malformed arithmetic"))
    assert calls == []
    assert result.status == "failed", (
        "A registry schema rejection proves no handler was dispatched; it must not require "
        "operator reconciliation as an unknown side effect"
    )
    assert result.termination_reason == "AGENT_TOOL_FAILED"


@pytest.mark.parametrize("backend", ["native", "langgraph"])
@pytest.mark.parametrize("failure", ["exception", "invalid_output"])
async def test_postdispatch_failure_still_requires_reconciliation(
    database, backend, failure
) -> None:
    definition, _ = tracked_definition(policy="never")
    calls: list[str] = []

    async def handler(payload: Any, ctx: Any) -> Any:
        calls.append(payload.expression)
        if failure == "exception":
            raise RuntimeError("private-handler-failure-marker")
        return {"status": "invalid", "result": "private-handler-failure-marker"}

    definition = definition.model_copy(update={"handler": handler})
    app = service(database, backend=backend, definition=definition)
    result = await app.start(
        context=context(), command=DurableStartCommand(input="Unknown synthetic effect")
    )
    assert calls == ["21 * 2"]
    assert result.status == "intervention_required"
    assert result.termination_reason == "DURABLE_TOOL_OUTCOME_UNKNOWN"
    assert "private-handler-failure-marker" not in result.model_dump_json()
    with pytest.raises(DomainError, match="DURABLE_RUN_TERMINAL"):
        await app.resume(
            context=context(),
            run_id=result.run_id,
            command=DurableResumeCommand(expected_revision=result.revision),
        )
    assert calls == ["21 * 2"]


async def test_cross_run_approval_id_is_never_reused(database) -> None:
    definition, calls = tracked_definition(required=True)
    app = service(database, definition=definition)
    first = await app.start(context=context(), command=DurableStartCommand(input="First request"))
    second = await app.start(context=context(), command=DurableStartCommand(input="Second request"))
    assert first.approval is not None and second.approval is not None
    with pytest.raises(DomainError, match="DURABLE_APPROVAL_STALE"):
        await app.resume(
            context=context(),
            run_id=second.run_id,
            command=DurableResumeCommand(
                expected_revision=second.revision,
                approval_id=first.approval.approval_id,
                approve=True,
            ),
        )
    assert calls == []
    persisted = await app.get(context=context(), run_id=second.run_id)
    assert persisted.status == "awaiting_approval" and persisted.revision == second.revision


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_identical_next_action_requires_fresh_approval(database, backend) -> None:
    definition, calls = tracked_definition(required=True)
    app = service(database, definition=definition, backend=backend, stepper=Stepper(repeat=True))
    first = await app.start(context=context(), command=DurableStartCommand(input="Repeated action"))
    second = await app.resume(
        context=context(),
        run_id=first.run_id,
        command=DurableResumeCommand(
            expected_revision=first.revision,
            approval_id=first.approval.approval_id,
            approve=True,
        ),
    )
    assert second.status == "awaiting_approval"
    assert len(calls) == 1
    assert second.approval.approval_id != first.approval.approval_id
    with pytest.raises(DomainError, match="DURABLE_APPROVAL_STALE"):
        await app.resume(
            context=context(),
            run_id=second.run_id,
            command=DurableResumeCommand(
                expected_revision=second.revision,
                approval_id=first.approval.approval_id,
                approve=True,
            ),
        )
    assert len(calls) == 1


async def test_simultaneous_approval_resumes_dispatch_at_most_once(database) -> None:
    definition, calls = tracked_definition(required=True)
    app = service(database, definition=definition)
    paused = await app.start(context=context(), command=DurableStartCommand(input="Race approval"))
    command = DurableResumeCommand(
        expected_revision=paused.revision,
        approval_id=paused.approval.approval_id,
        approve=True,
    )
    results = await asyncio.gather(
        app.resume(context=context(), run_id=paused.run_id, command=command),
        service(database, definition=definition).resume(
            context=context(), run_id=paused.run_id, command=command
        ),
        return_exceptions=True,
    )
    assert len(calls) == 1
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    failures = [result for result in results if isinstance(result, DomainError)]
    assert len(failures) == 1 and failures[0].code == "DURABLE_RESUME_CONFLICT"


async def test_completed_ledger_rewrite_rolls_back_snapshot_cas(database) -> None:
    app = service(database)
    paused = await app.start(
        context=context(),
        command=DurableStartCommand(
            input="Ledger transaction",
            approval_tools=("calculator",),
        ),
    )
    store = SqlAlchemyDurableStore(database)
    snapshot = await store.load(tenant_id="tenant-a", user_id="user-a", run_id=paused.run_id)
    now = Clock()()
    await store.acquire(snapshot, token="owner", now=now, lease_seconds=30)
    entry = ToolLedgerEntry(
        call_id="review-call",
        tool_name="calculator",
        action_digest="digest",
        replay_policy="read_only",
        status="started",
    )
    started = await store.save(snapshot, snapshot, token="owner", now=now, ledger=entry)
    completed = await store.save(
        started,
        started,
        token="owner",
        now=now,
        ledger=entry.model_copy(update={"status": "completed"}),
    )
    with pytest.raises(DomainError, match="DURABLE_LEDGER_IMMUTABLE"):
        await store.save(
            completed,
            completed.model_copy(update={"steps_used": 19}),
            token="owner",
            now=now,
            ledger=entry.model_copy(update={"action_digest": "changed"}),
        )
    restored = await store.load(tenant_id="tenant-a", user_id="user-a", run_id=paused.run_id)
    assert restored.revision == completed.revision
    assert restored.steps_used == completed.steps_used
    assert (await store.ledger(restored, "review-call")).status == "completed"
    await store.release(restored, token="owner")


async def test_replaced_lease_fences_old_framework_checkpoint_write(database) -> None:
    app = service(database)
    paused = await app.start(
        context=context(),
        command=DurableStartCommand(
            input="Checkpoint lease",
            approval_tools=("calculator",),
        ),
    )
    store = SqlAlchemyDurableStore(database)
    snapshot = await store.load(tenant_id="tenant-a", user_id="user-a", run_id=paused.run_id)
    now = Clock()()
    await store.acquire(snapshot, token="old-owner", now=now, lease_seconds=1)
    await store.acquire(snapshot, token="new-owner", now=now + 2, lease_seconds=30)
    with pytest.raises(DomainError, match="DURABLE_LEASE_LOST"):
        await store.graph_put(
            snapshot,
            token="old-owner",
            now=now + 2,
            checkpoint={"id": "checkpoint-review"},
            metadata={},
            parent_id=None,
        )
    async with database() as session:
        assert await session.scalar(select(DurableGraphCheckpointModel)) is None
    await store.release(snapshot, token="old-owner")
    assert await store.owns(snapshot, token="new-owner")
    await store.release(snapshot, token="new-owner")


async def test_real_graph_writes_tolerate_checkpoint_insert_latency(database) -> None:
    class DelayedCheckpointStore(SqlAlchemyDurableStore):
        async def graph_put(self, *args: Any, **kwargs: Any) -> None:
            # Actual Pregel may emit pending writes while this checkpoint's
            # insert is in flight. Normal storage latency must not fail a run.
            await asyncio.sleep(0.05)
            await super().graph_put(*args, **kwargs)

    result = await service(
        database,
        backend="langgraph",
        store=DelayedCheckpointStore(database),
    ).start(context=context(), command=DurableStartCommand(input="Delayed checkpoint persistence"))
    assert result.status == "completed"
    assert result.final_answer == "The result is 42"


async def test_graph_checkpointing_excludes_ambient_callbacks_and_private_content(
    database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("langgraph")
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.runnables.config import set_config_context
    from langchain_core.tracers.context import collect_runs, tracing_v2_enabled
    from langchain_core.tracers.langchain import LangChainTracer

    seen: list[Any] = []

    class Capture(BaseCallbackHandler):
        def on_chain_start(self, *args: Any, **kwargs: Any) -> None:
            seen.append(args)

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setattr(LangChainTracer, "_persist_run", lambda self, run: seen.append(run))
    app = service(database, backend="langgraph")
    with collect_runs() as collector, tracing_v2_enabled(client=MagicMock()):
        with set_config_context(
            {"callbacks": [Capture()], "metadata": {"private": "private-durable-marker"}}
        ) as inherited:
            result = await asyncio.create_task(
                app.start(
                    context=context(), command=DurableStartCommand(input="private-durable-marker")
                ),
                context=inherited,
            )
        assert not collector.traced_runs
    assert result.status == "completed" and seen == []
    async with database() as session:
        rows = (await session.scalars(select(DurableGraphCheckpointModel))).all()
        assert rows
        for row in rows:
            payload = json.dumps([row.checkpoint, row.writes, row.metadata_])
            assert "private-durable-marker" not in payload
            assert "21 * 2" not in payload
