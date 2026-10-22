"""Independent lifecycle probes: real async SQLAlchemy + SQLite, synthetic tools."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.agent.dto import ToolDefinition, ToolRateLimit
from packages.agent.durable.dto import DurableResumeCommand, DurableSnapshot, DurableStartCommand
from packages.agent.durable.evidence import RehydratedEvidence
from packages.agent.durable.service import DurableAgentService
from packages.agent.registry import ToolRegistry
from packages.agent.runtime import AgentObservationSummary, AgentRunConfig, AgentRuntime
from packages.agent.storage.durable import SqlAlchemyDurableStore
from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.data.storage.base import Base
from tests.unit.agent.test_runtime import DemoInput, DemoOutput, FakeStepper, HandlerProbe
from tests.unit.agent.test_streaming_service import WaitingStepper, _allowed_context, _final


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Evidence:
    async def rehydrate(
        self,
        *,
        context: AuthenticatedRequestContext,
        observations: Sequence[AgentObservationSummary],
        fingerprints: dict[str, str],
    ) -> RehydratedEvidence:
        return RehydratedEvidence(tuple(observations), dict(fingerprints))


@pytest.fixture
async def sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[Any]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'review.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def service(
    store: SqlAlchemyDurableStore,
    clock: Clock,
    stepper: Any,
    *,
    evidence: Evidence | None = None,
    handler: HandlerProbe | None = None,
    backend: str = "native",
) -> DurableAgentService:
    registry = ToolRegistry(audit=InMemoryAuditPort())
    registry.register(
        ToolDefinition(
            name="demo_tool",
            description="Synthetic bounded review tool",
            input_schema=DemoInput,
            output_schema=DemoOutput,
            permission="agent:tool:demo",
            timeout_seconds=1,
            rate_limit=ToolRateLimit(max_calls=10, window_seconds=60),
            handler=handler or HandlerProbe(),
            durable_replay_policy="read_only",
        )
    )

    def factory(config: AgentRunConfig, run_id: str) -> AgentRuntime:
        return AgentRuntime(
            config=config,
            agent_run_id=run_id,
            registry=registry,
            stepper=stepper,
            audit=InMemoryAuditPort(),
            perf_counter=clock,
        )

    return DurableAgentService(
        store=store,
        runtime_factory=factory,
        evidence_revalidator=evidence or Evidence(),
        wall_clock=clock,
        default_timeout_seconds=5,
        backend=backend,
    )


async def test_approval_resume_rejects_regressed_wall_clock(
    sessions: async_sessionmaker[Any],
) -> None:
    from packages.agent.runtime import AgentStepDecision

    clock = Clock()
    store = SqlAlchemyDurableStore(sessions)
    stepper = FakeStepper([AgentStepDecision.tool_call("demo_tool", {"query": "x"}), _final()])
    app = service(store, clock, stepper)
    paused = await app.start(
        context=_allowed_context(),
        command=DurableStartCommand(input="x", approval_tools=("demo_tool",)),
    )
    assert paused.approval is not None
    clock.now = 90.0
    resumed = await app.resume(
        context=_allowed_context(),
        run_id=paused.run_id,
        command=DurableResumeCommand(
            expected_revision=paused.revision,
            approval_id=paused.approval.approval_id,
            approve=True,
        ),
    )
    assert resumed.status in {"failed", "stopped"}
    assert resumed.error_code == "DURABLE_CLOCK_REGRESSION"


async def test_final_revalidation_cannot_publish_after_absolute_deadline(
    sessions: async_sessionmaker[Any],
) -> None:
    clock = Clock()

    class SlowFinalEvidence(Evidence):
        calls = 0

        async def rehydrate(self, **kwargs: Any) -> RehydratedEvidence:
            self.calls += 1
            if self.calls == 3:
                clock.now = 106.0
            return await super().rehydrate(**kwargs)

    app = service(
        SqlAlchemyDurableStore(sessions),
        clock,
        FakeStepper([_final()]),
        evidence=SlowFinalEvidence(),
    )
    result = await app.start(context=_allowed_context(), command=DurableStartCommand(input="x"))
    assert result.status == "stopped"
    assert result.error_code == "AGENT_TIMEOUT"
    assert result.final_answer is None


async def test_journal_delay_cannot_dispatch_tool_after_absolute_deadline(
    sessions: async_sessionmaker[Any],
) -> None:
    from packages.agent.runtime import AgentStepDecision

    clock = Clock()

    class SlowJournalStore(SqlAlchemyDurableStore):
        async def save(
            self, previous: DurableSnapshot, snapshot: DurableSnapshot, **kwargs: Any
        ) -> DurableSnapshot:
            saved = await super().save(previous, snapshot, **kwargs)
            if snapshot.pending_call_id is not None:
                clock.now = 106.0
            return saved

    handler = HandlerProbe()
    app = service(
        SlowJournalStore(sessions),
        clock,
        FakeStepper([AgentStepDecision.tool_call("demo_tool", {"query": "x"}), _final()]),
        handler=handler,
    )
    result = await app.start(context=_allowed_context(), command=DurableStartCommand(input="x"))
    assert result.status == "stopped"
    assert result.error_code == "AGENT_TIMEOUT"
    assert handler.call_count == 0


async def test_repeated_task_cancellation_cannot_leave_resumable_running_snapshot(
    sessions: async_sessionmaker[Any],
) -> None:
    entered_cancel = asyncio.Event()
    release_cancel = asyncio.Event()

    class SlowCancelStore(SqlAlchemyDurableStore):
        created: DurableSnapshot | None = None

        async def create(self, snapshot: DurableSnapshot) -> None:
            self.created = snapshot
            await super().create(snapshot)

        async def cancel(
            self,
            previous: DurableSnapshot,
            *,
            now: float,
            **kwargs: Any,
        ) -> DurableSnapshot:
            entered_cancel.set()
            await release_cancel.wait()
            return await super().cancel(previous, now=now, **kwargs)

    store = SlowCancelStore(sessions)
    stepper = WaitingStepper()
    app = service(store, Clock(), stepper)
    task = asyncio.create_task(
        app.start(
            context=_allowed_context(),
            command=DurableStartCommand(input="x"),
        )
    )
    await asyncio.wait_for(stepper.entered.wait(), 2)
    task.cancel()
    await asyncio.wait_for(entered_cancel.wait(), 2)
    task.cancel()
    release_cancel.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.created is not None
    persisted = await store.load(
        tenant_id="tenant-1",
        user_id="user-1",
        run_id=store.created.run_id,
    )
    assert persisted.status == "cancelled"


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_fresh_service_reuses_committed_tool_observation_after_crash(
    sessions: async_sessionmaker[Any],
    backend: str,
) -> None:
    from packages.agent.runtime import AgentStepDecision

    if backend == "langgraph":
        pytest.importorskip("langgraph")

    class SimulatedProcessExit(BaseException):
        pass

    class CrashAfterResultStore(SqlAlchemyDurableStore):
        created: DurableSnapshot | None = None
        injected = False

        async def create(self, snapshot: DurableSnapshot) -> None:
            self.created = snapshot
            await super().create(snapshot)

        async def save(
            self, previous: DurableSnapshot, snapshot: DurableSnapshot, **kwargs: Any
        ) -> DurableSnapshot:
            saved = await super().save(previous, snapshot, **kwargs)
            ledger = kwargs.get("ledger")
            if ledger is not None and ledger.status == "completed" and not self.injected:
                self.injected = True
                raise SimulatedProcessExit()
            return saved

    store = CrashAfterResultStore(sessions)
    handler = HandlerProbe()
    app = service(
        store,
        Clock(),
        FakeStepper([AgentStepDecision.tool_call("demo_tool", {"query": "x"})]),
        handler=handler,
        backend=backend,
    )
    with pytest.raises(SimulatedProcessExit):
        await app.start(context=_allowed_context(), command=DurableStartCommand(input="x"))
    assert store.created is not None
    fresh_store = SqlAlchemyDurableStore(sessions)
    persisted = await fresh_store.load(
        tenant_id="tenant-1",
        user_id="user-1",
        run_id=store.created.run_id,
    )
    assert persisted.observations
    assert persisted.pending_call_id is None
    next_stepper = FakeStepper([_final()])
    fresh_app = service(fresh_store, Clock(), next_stepper, handler=handler, backend=backend)
    result = await fresh_app.resume(
        context=_allowed_context(),
        run_id=persisted.run_id,
        command=DurableResumeCommand(expected_revision=persisted.revision),
    )
    assert result.status == "completed"
    assert handler.call_count == 1
    assert result.steps_used == 2
    assert result.tool_calls_used == 1
    assert len(next_stepper.states[0].observations) == 1


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_cancellation_fences_inflight_result_and_forbids_resurrection(
    sessions: async_sessionmaker[Any],
    backend: str,
) -> None:
    from packages.agent.exceptions import AgentRunError
    from packages.agent.runtime import AgentStepDecision

    if backend == "langgraph":
        pytest.importorskip("langgraph")
    entered = asyncio.Event()
    release = asyncio.Event()

    class WaitingHandler(HandlerProbe):
        async def __call__(self, *args: Any, **kwargs: Any) -> object:
            entered.set()
            await release.wait()
            return await super().__call__(*args, **kwargs)

    class RecordingStore(SqlAlchemyDurableStore):
        created: DurableSnapshot | None = None

        async def create(self, snapshot: DurableSnapshot) -> None:
            self.created = snapshot
            await super().create(snapshot)

    store = RecordingStore(sessions)
    stepper = FakeStepper([AgentStepDecision.tool_call("demo_tool", {"query": "x"}), _final()])
    app = service(store, Clock(), stepper, handler=WaitingHandler(), backend=backend)
    pending = asyncio.create_task(
        app.start(
            context=_allowed_context(),
            command=DurableStartCommand(input="x"),
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert store.created is not None
        current = await app.get(context=_allowed_context(), run_id=store.created.run_id)
        cancelled = await app.cancel(
            context=_allowed_context(),
            run_id=current.run_id,
            expected_revision=current.revision,
        )
        release.set()
        try:
            result = await pending
        except AgentRunError as error:
            assert error.code == "DURABLE_LEASE_LOST"
        else:
            assert result.status == "cancelled"
        assert len(stepper.states) == 1
        persisted = await app.get(context=_allowed_context(), run_id=current.run_id)
        assert persisted.status == "cancelled"
        with pytest.raises(AgentRunError, match="DURABLE_RUN_TERMINAL"):
            await app.resume(
                context=_allowed_context(),
                run_id=current.run_id,
                command=DurableResumeCommand(expected_revision=cancelled.revision),
            )
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        try:
            await pending
        except (asyncio.CancelledError, AgentRunError):
            pass


async def test_clock_regression_during_revalidation_cannot_reset_budget_floor(
    sessions: async_sessionmaker[Any],
) -> None:
    clock = Clock()

    class RegressingEvidence(Evidence):
        async def rehydrate(self, **kwargs: Any) -> RehydratedEvidence:
            clock.now = 90.0
            return await super().rehydrate(**kwargs)

    app = service(
        SqlAlchemyDurableStore(sessions),
        clock,
        FakeStepper([_final()]),
        evidence=RegressingEvidence(),
    )
    result = await app.start(context=_allowed_context(), command=DurableStartCommand(input="x"))
    assert result.status in {"failed", "stopped"}
    assert result.error_code == "DURABLE_CLOCK_REGRESSION"
    assert result.final_answer is None


async def test_unknown_model_attempt_is_not_refunded_after_fresh_service(
    sessions: async_sessionmaker[Any],
) -> None:
    class ProcessExit(BaseException):
        pass

    class CrashStepper:
        async def next_step(self, state: object) -> object:
            raise ProcessExit()

    class RecordingStore(SqlAlchemyDurableStore):
        created: DurableSnapshot | None = None

        async def create(self, snapshot: DurableSnapshot) -> None:
            self.created = snapshot
            await super().create(snapshot)

    store = RecordingStore(sessions)
    app = service(store, Clock(), CrashStepper())
    with pytest.raises(ProcessExit):
        await app.start(
            context=_allowed_context(),
            command=DurableStartCommand(input="x", max_steps=1),
        )
    assert store.created is not None
    snapshot = await store.load(
        tenant_id="tenant-1",
        user_id="user-1",
        run_id=store.created.run_id,
    )
    fresh_stepper = FakeStepper([_final()])
    resumed = await service(store, Clock(), fresh_stepper).resume(
        context=_allowed_context(),
        run_id=snapshot.run_id,
        command=DurableResumeCommand(expected_revision=snapshot.revision),
    )
    assert resumed.status == "stopped"
    assert resumed.termination_reason == "MAX_STEPS_REACHED"
    assert resumed.steps_used == 1
    assert fresh_stepper.states == []


@pytest.mark.parametrize("blocking_stage", ["evidence", "authentication", "snapshot_load"])
async def test_absolute_budget_bounds_revalidation_io(
    sessions: async_sessionmaker[Any],
    blocking_stage: str,
) -> None:
    import time

    setup_budget = 5.0
    target_budget = 0.05

    class WallClock(Clock):
        anchor: float | None = None

        def activate(self) -> None:
            self.anchor = time.monotonic()

        def __call__(self) -> float:
            if self.anchor is None:
                return 100.0
            return 100.0 + setup_budget - target_budget + time.monotonic() - self.anchor

    clock = WallClock()
    entered = asyncio.Event()

    class BlockedEvidence(Evidence):
        async def rehydrate(self, **kwargs: Any) -> RehydratedEvidence:
            entered.set()
            await asyncio.Event().wait()
            return await super().rehydrate(**kwargs)

    async def blocked_auth(context: AuthenticatedRequestContext) -> AuthenticatedRequestContext:
        entered.set()
        await asyncio.Event().wait()
        return context

    class PreparedStore(SqlAlchemyDurableStore):
        first_load = True
        created: DurableSnapshot | None = None

        async def create(self, snapshot: DurableSnapshot) -> None:
            await super().create(snapshot)
            self.created = snapshot

        async def acquire(self, snapshot: DurableSnapshot, **kwargs: Any) -> None:
            await super().acquire(snapshot, **kwargs)
            # Complete real SQLite setup before consuming all but 50ms of the
            # original wall deadline. The production run-body timer is unchanged.
            clock.activate()

        async def load(self, **kwargs: Any) -> DurableSnapshot:
            if self.first_load:
                self.first_load = False
                if blocking_stage == "snapshot_load":
                    entered.set()
                    await asyncio.Event().wait()
                # The first row is known from real creation. Unrelated disk I/O
                # must not consume the tiny evidence/authentication probe window.
                # Terminal cleanup still reloads and persists through SQLite.
                assert self.created is not None
                return self.created.model_copy(deep=True)
            return await super().load(**kwargs)

    store = PreparedStore(sessions)
    app = service(
        store,
        clock,
        FakeStepper([_final()]),
        evidence=BlockedEvidence() if blocking_stage == "evidence" else Evidence(),
    )
    if blocking_stage == "authentication":
        app._context_revalidator = blocked_auth
    context = _allowed_context()
    pending = asyncio.create_task(
        app.start(
            context=context,
            command=DurableStartCommand(input="x", timeout_seconds=setup_budget),
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 2)
        done, _ = await asyncio.wait({pending}, timeout=0.5)
        assert pending in done, f"{blocking_stage} I/O outlived the absolute run budget"
        result = pending.result()
        assert result.status == "stopped"
        assert result.error_code == "AGENT_TIMEOUT"
        assert result.deadline_at == 100.0 + setup_budget
        assert result.steps_used == result.tool_calls_used == 0
        assert entered.is_set()
        persisted = await store.load(
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            run_id=result.run_id,
        )
        assert persisted.status == "stopped"
        assert persisted.error_code == "AGENT_TIMEOUT"
        assert persisted.started_at == 100.0
        assert persisted.deadline_at == result.deadline_at
    finally:
        if not pending.done():
            pending.cancel()
        try:
            await pending
        except asyncio.CancelledError:
            pass


@pytest.mark.parametrize("stage", ["creation", "acquisition"])
async def test_cancellation_during_lease_acquisition_persists_terminal_fence(
    sessions: async_sessionmaker[Any],
    stage: str,
) -> None:
    acquired = asyncio.Event()

    class SlowAcquireStore(SqlAlchemyDurableStore):
        created: DurableSnapshot | None = None

        async def create(self, snapshot: DurableSnapshot) -> None:
            self.created = snapshot
            await super().create(snapshot)
            if stage == "creation":
                acquired.set()
                await asyncio.Event().wait()

        async def acquire(self, snapshot: DurableSnapshot, **kwargs: Any) -> None:
            await super().acquire(snapshot, **kwargs)
            acquired.set()
            await asyncio.Event().wait()

    store = SlowAcquireStore(sessions)
    app = service(store, Clock(), FakeStepper([_final()]))
    task = asyncio.create_task(
        app.start(
            context=_allowed_context(),
            command=DurableStartCommand(input="x"),
        )
    )
    await asyncio.wait_for(acquired.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.created is not None
    record = await store.load(
        tenant_id="tenant-1",
        user_id="user-1",
        run_id=store.created.run_id,
    )
    assert record.status == "cancelled"


async def test_cancelled_contending_resume_cannot_cancel_existing_owner(
    sessions: async_sessionmaker[Any],
) -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    contender_acquiring = asyncio.Event()

    class FirstStepper:
        async def next_step(self, state: object) -> object:
            first_started.set()
            await release_first.wait()
            return _final()

    class RecordingStore(SqlAlchemyDurableStore):
        created: DurableSnapshot | None = None

        async def create(self, snapshot: DurableSnapshot) -> None:
            self.created = snapshot
            await super().create(snapshot)

    class ContendingStore(SqlAlchemyDurableStore):
        async def acquire(self, snapshot: DurableSnapshot, **kwargs: Any) -> None:
            contender_acquiring.set()
            await asyncio.Event().wait()
            await super().acquire(snapshot, **kwargs)

    first_store = RecordingStore(sessions)
    first_app = service(first_store, Clock(), FirstStepper())
    first_task = asyncio.create_task(
        first_app.start(
            context=_allowed_context(),
            command=DurableStartCommand(input="x"),
        )
    )
    contender: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(first_started.wait(), 2)
        assert first_store.created is not None
        current = await first_app.get(context=_allowed_context(), run_id=first_store.created.run_id)
        second_app = service(ContendingStore(sessions), Clock(), FakeStepper([_final()]))
        contender = asyncio.create_task(
            second_app.resume(
                context=_allowed_context(),
                run_id=current.run_id,
                command=DurableResumeCommand(expected_revision=current.revision),
            )
        )
        await asyncio.wait_for(contender_acquiring.wait(), 2)
        contender.cancel()
        with pytest.raises(asyncio.CancelledError):
            await contender
        still_owned = await first_app.get(context=_allowed_context(), run_id=current.run_id)
        assert still_owned.status == "running"
        assert still_owned.revision == current.revision
        release_first.set()
        assert (await first_task).status == "completed"
    finally:
        release_first.set()
        if contender is not None and not contender.done():
            contender.cancel()
        if not first_task.done():
            first_task.cancel()
        for task in [first_task, contender]:
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass


@pytest.mark.parametrize("stage", ["completed_evidence", "expired_retention_purge"])
async def test_public_get_bounds_fresh_validation_and_retention_io(
    sessions: async_sessionmaker[Any],
    stage: str,
) -> None:
    from packages.common.errors import DomainError

    clock = Clock()
    store = SqlAlchemyDurableStore(sessions)
    completed = await service(store, clock, FakeStepper([_final()])).start(
        context=_allowed_context(),
        command=DurableStartCommand(input="x"),
    )
    entered = asyncio.Event()

    class BlockedEvidence(Evidence):
        async def rehydrate(self, **kwargs: Any) -> RehydratedEvidence:
            entered.set()
            await asyncio.Event().wait()
            return await super().rehydrate(**kwargs)

    class BlockedPurgeStore(SqlAlchemyDurableStore):
        async def purge_expired(self, **kwargs: Any) -> int:
            entered.set()
            await asyncio.Event().wait()
            return await super().purge_expired(**kwargs)

    if stage == "completed_evidence":
        # Completed reads need a fresh bounded I/O window, not a renewed run budget.
        clock.now = completed.deadline_at + 10
        reader = service(store, clock, FakeStepper([]), evidence=BlockedEvidence())
    else:
        clock.now += 86401
        reader = service(BlockedPurgeStore(sessions), clock, FakeStepper([]))
    pending = asyncio.create_task(reader.get(context=_allowed_context(), run_id=completed.run_id))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        done, _ = await asyncio.wait({pending}, timeout=6)
        assert pending in done, f"public GET {stage} I/O exceeded the 5-second request bound"
        with pytest.raises(DomainError):
            pending.result()
    finally:
        if not pending.done():
            pending.cancel()
        try:
            await pending
        except (asyncio.CancelledError, DomainError):
            pass


async def test_completed_get_revalidates_after_execution_deadline(
    sessions: async_sessionmaker[Any],
) -> None:
    clock = Clock()
    store = SqlAlchemyDurableStore(sessions)
    app = service(store, clock, FakeStepper([_final()]))
    completed = await app.start(context=_allowed_context(), command=DurableStartCommand(input="x"))
    clock.now = completed.deadline_at + 10
    reread = await service(store, clock, FakeStepper([])).get(
        context=_allowed_context(),
        run_id=completed.run_id,
    )
    assert reread.status == "completed"
    assert reread.final_answer == completed.final_answer
