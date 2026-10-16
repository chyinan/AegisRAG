"""Normal durable active-budget and explicitly configured background workflows."""

from __future__ import annotations

from typing import Any, Literal, cast

import pytest

from packages.agent.durable.dto import (
    DurableActiveBudget,
    DurableResumeCommand,
    DurableSnapshot,
    DurableStartCommand,
)
from packages.agent.durable.scheduler import DurableBackgroundScheduler, DurableWorkerOwner
from packages.agent.runtime import AgentRunConfig
from packages.agent.storage.durable import SqlAlchemyDurableStore
from packages.common.tenant_context import current_tenant_id
from tests.unit.agent.test_durable_execution import Clock, Stepper, context, service
from tests.unit.agent.test_durable_execution import database as database


class TimedStepper(Stepper):
    def __init__(self, clock: Clock) -> None:
        super().__init__()
        self.clock = clock

    async def next_step(self, state):
        self.clock.value += 2
        return await super().next_step(state)


async def test_worker_discovery_owns_the_configured_owner_scope() -> None:
    class Store:
        async def ready_background(self, **kwargs: object) -> tuple[()]:
            assert current_tenant_id() == kwargs["tenant_id"] == "tenant-a"
            return ()

    class Service:
        _background_enabled = True
        _context_revalidator = object()

    async def resolve(_owner: DurableWorkerOwner, _run_id: str) -> Any:
        raise AssertionError("empty discovery has no work to resolve")

    scheduler = DurableBackgroundScheduler(
        store=cast(Any, Store()), service=cast(Any, Service()),
        owners=(DurableWorkerOwner(tenant_id="tenant-a", user_id="user-a"),),
        resolve_context=resolve,
    )
    assert await scheduler.run_once() == ()
    assert current_tenant_id() is None


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_active_approval_wait_has_its_own_deadline_and_keeps_consumed_budgets(
    database, backend: Literal["native", "langgraph"]
):
    clock = Clock()
    first = service(database, backend=backend, clock=clock, stepper=TimedStepper(clock))
    paused = await first.start(
        context=context(),
        command=DurableStartCommand(
            input="Calculate",
            budget_mode="active",
            timeout_seconds=30,
            approval_timeout_seconds=1800,
            approval_tools=("calculator",),
        ),
    )
    assert paused.status == "awaiting_approval" and paused.approval is not None
    assert paused.steps_used == 1 and paused.tool_calls_used == 0
    assert paused.active_seconds_used == 2 and paused.active_seconds_remaining == 28
    assert paused.approval.expires_at == clock.value + 1800
    clock.value += 600  # Approval waiting is longer than the entire active allowance.
    fresh = service(database, backend=backend, clock=clock, stepper=TimedStepper(clock))
    read = await fresh.get(context=context(), run_id=paused.run_id)
    assert read == paused
    completed = await fresh.resume(
        context=context(),
        run_id=paused.run_id,
        command=DurableResumeCommand(
            expected_revision=read.revision, approval_id=read.approval.approval_id, approve=True
        ),
    )
    assert completed.status == "completed" and completed.final_answer == "The result is 42"
    assert completed.steps_used == 2 and completed.tool_calls_used == 1
    assert completed.active_seconds_used == 4 and completed.active_seconds_remaining == 26
    assert completed.deadline_at == paused.deadline_at + 600


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_opted_in_longer_active_timeout_completes(database, backend):
    completed = await service(database, backend=backend).start(
        context=context(),
        command=DurableStartCommand(
            input="Calculate",
            budget_mode="active",
            timeout_seconds=1800,
        ),
    )
    assert completed.status == "completed" and completed.budget_mode == "active"
    assert completed.active_seconds_remaining == 1800


async def test_legacy_snapshot_keeps_original_shape_and_deadline(database):
    clock = Clock()
    svc = service(database, clock=clock)
    paused = await svc.start(
        context=context(),
        command=DurableStartCommand(
            input="Calculate",
            approval_tools=("calculator",),
        ),
    )
    snapshot = await SqlAlchemyDurableStore(database).load(
        tenant_id="tenant-a",
        user_id="user-a",
        run_id=paused.run_id,
    )
    assert "active_budget" not in snapshot.persisted()
    assert "dispatch" not in snapshot.persisted()
    assert paused.budget_mode == "wall_clock" and paused.execution_mode == "inline"
    assert paused.active_seconds_used is None
    assert paused.approval.expires_at == paused.deadline_at == clock.value + 120


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_background_start_poll_pause_approve_and_fresh_worker_completion(database, backend):
    clock = Clock()
    resolved: list[str] = []

    async def revalidate(current):
        return current

    async def resolve(owner, run_id):
        assert current_tenant_id() == owner.tenant_id
        resolved.append(run_id)
        return context(user=owner.user_id, tenant=owner.tenant_id)

    def configured_service():
        svc = service(database, backend=backend, clock=clock, stepper=TimedStepper(clock))
        svc._background_enabled = True
        svc._context_revalidator = revalidate
        return svc

    def scheduler(svc):
        return DurableBackgroundScheduler(
            store=SqlAlchemyDurableStore(database),
            service=svc,
            owners=(DurableWorkerOwner(tenant_id="tenant-a", user_id="user-a"),),
            resolve_context=resolve,
            wall_clock=clock,
        )

    queued = await configured_service().start(
        context=context(),
        command=DurableStartCommand(
            input="Calculate",
            budget_mode="active",
            execution_mode="background",
            timeout_seconds=30,
            approval_timeout_seconds=1800,
            approval_tools=("calculator",),
        ),
    )
    assert queued.status == "running" and queued.steps_used == queued.tool_calls_used == 0
    assert queued.execution_mode == "background" and queued.active_seconds_used == 0
    clock.value += 300  # A durable queue wait does not spend active runtime.
    (paused,) = await scheduler(configured_service()).run_once()
    assert current_tenant_id() is None
    assert paused.status == "awaiting_approval" and paused.active_seconds_used == 2
    assert await scheduler(configured_service()).run_once() == ()
    clock.value += 600
    approved = await configured_service().resume(
        context=context(),
        run_id=paused.run_id,
        command=DurableResumeCommand(
            expected_revision=paused.revision,
            approval_id=paused.approval.approval_id,
            approve=True,
        ),
    )
    assert approved.status == "running" and approved.steps_used == 1
    assert approved.tool_calls_used == 0 and approved.active_seconds_used == 2
    clock.value += 300
    (completed,) = await scheduler(configured_service()).run_once()
    assert completed.status == "completed" and completed.final_answer == "The result is 42"
    assert completed.steps_used == 2 and completed.tool_calls_used == 1
    assert completed.active_seconds_used == 4 and completed.active_seconds_remaining == 26
    assert resolved == [queued.run_id, queued.run_id]
    assert await scheduler(configured_service()).run_once() == ()


async def test_keyed_background_start_is_a_single_durable_queue_item(database):
    from uuid import uuid4

    svc = service(database)
    svc._background_enabled = True
    command = DurableStartCommand(
        input="Calculate",
        idempotency_key=uuid4(),
        budget_mode="active",
        execution_mode="background",
    )
    queued = await svc.start(context=context(), command=command)
    assert await svc.start(context=context(), command=command) == queued
    ready = await SqlAlchemyDurableStore(database).ready_background(
        tenant_id="tenant-a",
        user_id="user-a",
        now=Clock()(),
    )
    assert len(ready) == 1 and ready[0].run_id == queued.run_id
    assert vars(ready[0]) == {"run_id": queued.run_id, "revision": queued.revision}
    stored = await SqlAlchemyDurableStore(database).load(
        tenant_id="tenant-a", user_id="user-a", run_id=ready[0].run_id,
    )
    assert stored.steps_used == 0 and stored.active_budget.active_since_at is None


async def test_active_recovery_preserves_spent_counters_and_previous_lease_window(database):
    clock = Clock()
    store = SqlAlchemyDurableStore(database)
    config = AgentRunConfig(
        max_steps=10,
        max_tool_calls=10,
        timeout_seconds=100,
        repeated_action_threshold=3,
        input_text="Calculate",
    )
    snapshot = DurableSnapshot(
        run_id="active-window",
        tenant_id="tenant-a",
        user_id="user-a",
        request_id="request-a",
        trace_id="trace-a",
        backend="native",
        config=config,
        started_at=clock.value,
        deadline_at=clock.value + 93,
        last_observed_at=clock.value,
        expires_at=clock.value + 86400,
        active_budget=DurableActiveBudget(seconds_used=7),
        steps_used=3,
        tool_calls_used=2,
    )
    await store.create(snapshot)
    active = await store.acquire(snapshot, token="first-worker", now=clock.value, lease_seconds=30)
    clock.value += 5
    checkpoint = await store.save(active, active, token="first-worker", now=clock.value)
    assert checkpoint.active_budget.seconds_used == 12
    clock.value += 40  # A new normal recovery claim occurs after the old lease expired.
    recovered = await store.acquire(
        checkpoint, token="fresh-worker", now=clock.value, lease_seconds=30
    )
    assert recovered.active_budget.seconds_used == 37
    assert recovered.steps_used == 3 and recovered.tool_calls_used == 2
    assert recovered.deadline_at == clock.value + 63
    assert recovered.active_budget.active_since_at == clock.value
