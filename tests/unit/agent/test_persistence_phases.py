"""Ordinary service transaction reliability, using local, controlled I/O only."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TypeVar

import pytest

from packages.agent.dto import (
    AgentRunCommand,
    AgentRunCreate,
    AgentRunRecord,
    AgentRunResponse,
    AgentRunUpdate,
)
from packages.agent.exceptions import AGENT_RUN_STORAGE_FAILED, AgentRunError
from packages.agent.runtime import AgentRunResult, AgentRunStatus, AgentTerminationReason
from packages.agent.service import AGENT_RUN_CANCELLED, AgentRunApplicationService
from packages.agent.streaming import AgentStreamEvent
from packages.common.audit import AuditEvent, InMemoryAuditPort
from packages.common.config import AppSettings
from tests.unit.agent.test_agent_run_service import FakeAgentRunRepository, FakeRuntime, _context
from tests.unit.agent.test_runtime import FakeStepper
from tests.unit.agent.test_streaming_service import (
    WaitingStepper,
    _allowed_context,
    _final,
)
from tests.unit.agent.test_streaming_service import (
    _service as stream_service,
)

T = TypeVar("T")


class Gate:
    def __init__(self, *, resistant: bool = False) -> None:
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.resistant = resistant

    async def wait(self) -> None:
        self.entered.set()
        while True:
            try:
                await self.release.wait()
                return
            except asyncio.CancelledError:
                self.cancelled.set()
                if not self.resistant:
                    raise


class PhaseRepository(FakeAgentRunRepository):
    def __init__(self) -> None:
        super().__init__()
        self.gates: dict[str, Gate] = {}
        self.errors: dict[str, BaseException] = {}
        self.apply_first: set[str] = set()
        self.operations: list[str] = []
        self.commit_attempts = 0
        self.committed: list[str] = []
        self.active = 0
        self.maximum_active = 0

    async def operation(self, label: str, action: Callable[[], Awaitable[T]]) -> T:
        self.operations.append(label)
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            if label in self.apply_first:
                result = await action()
            if label in self.gates:
                await self.gates[label].wait()
            if label in self.errors:
                raise self.errors[label]
            if label not in self.apply_first:
                result = await action()
            return result
        finally:
            self.active -= 1

    async def create_run(self, record: AgentRunCreate) -> AgentRunRecord:
        return await self.operation(
            "create", lambda: super(PhaseRepository, self).create_run(record)
        )

    async def update_run_result(
        self,
        *,
        tenant_id: str,
        user_id: str,
        run_id: str,
        update: AgentRunUpdate,
    ) -> AgentRunRecord:
        return await self.operation(
            f"update:{update.status}",
            lambda: super(PhaseRepository, self).update_run_result(
                tenant_id=tenant_id,
                user_id=user_id,
                run_id=run_id,
                update=update,
            ),
        )

    async def commit(self) -> None:
        self.commit_attempts += 1

        async def apply() -> None:
            await super(PhaseRepository, self).commit()
            self.committed.append(self.updated.status if self.updated else "running")

        await self.operation(f"commit:{self.commit_attempts}", apply)

    async def rollback(self) -> None:
        await self.operation("rollback", lambda: super(PhaseRepository, self).rollback())


def completed_runtime() -> FakeRuntime:
    return FakeRuntime(
        AgentRunResult(
            status=AgentRunStatus.COMPLETED,
            termination_reason=AgentTerminationReason.FINAL_ANSWER,
            steps_used=1,
            tool_calls_used=0,
            final_answer="Acknowledged answer.",
            request_id="req-1",
            trace_id="trace-1",
            tenant_id="tenant-1",
            user_id="user-1",
            latency_ms=1.0,
        )
    )


def service(
    repository: PhaseRepository,
    *,
    budget: float = 0.03,
    runtime: FakeRuntime | None = None,
) -> AgentRunApplicationService:
    worker = runtime or completed_runtime()
    return AgentRunApplicationService(
        repository=repository,
        runtime_factory=lambda _config, _run_id: worker,
        audit=InMemoryAuditPort(),
        default_max_steps=3,
        default_max_tool_calls=2,
        default_timeout_seconds=30,
        repeated_action_threshold=3,
        persistence_phase_timeout_seconds=budget,
    )


async def run(app: AgentRunApplicationService) -> AgentRunResponse:
    return await app.run(context=_context(), command=AgentRunCommand(input="ordinary request"))


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), 60.01])
def test_service_phase_budget_must_be_finite_positive_and_capped(value: float) -> None:
    with pytest.raises(ValueError):
        service(PhaseRepository(), budget=value)


def test_setting_has_separate_default_and_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_PERSISTENCE_PHASE_TIMEOUT_SECONDS", raising=False)
    assert AppSettings(_env_file=None).agent_persistence_phase_timeout_seconds == 5
    monkeypatch.setenv("AGENT_PERSISTENCE_PHASE_TIMEOUT_SECONDS", "0.2")
    settings = AppSettings(_env_file=None)
    assert settings.agent_persistence_phase_timeout_seconds == 0.2
    assert settings.agent_default_timeout_seconds == 30
    for value in ("0", "-1", "nan", "inf", "60.01"):
        monkeypatch.setenv("AGENT_PERSISTENCE_PHASE_TIMEOUT_SECONDS", value)
        with pytest.raises(ValueError):
            AppSettings(_env_file=None)


@pytest.mark.parametrize("backend", ["native", "langgraph"])
@pytest.mark.parametrize("phase", ["create", "commit:1", "update:completed", "commit:2"])
async def test_cooperative_storage_stall_has_no_answer(backend: str, phase: str) -> None:
    if backend == "langgraph":
        pytest.importorskip("langgraph")
    repo = PhaseRepository()
    gate = repo.gates[phase] = Gate()
    app, _ = stream_service(backend, FakeStepper([_final()]), repo)
    app._persistence_phase_timeout_seconds = 0.03
    events = [
        event
        async for event in app.stream(
            context=_allowed_context(),
            command=AgentRunCommand(input="ordinary request"),
        )
    ]
    assert gate.entered.is_set() and gate.cancelled.is_set()
    assert events[-1].event == "error"
    assert events[-1].data == {"code": AGENT_RUN_STORAGE_FAILED, "terminal": True}
    assert not any(event.event == "final" for event in events)
    assert repo.operations[-1] == "rollback"
    assert repo.maximum_active == 1
    assert "update:stopped" not in repo.operations


async def test_known_run_id_is_preserved_in_storage_error() -> None:
    repo = PhaseRepository()
    repo.gates["commit:1"] = Gate()
    with pytest.raises(AgentRunError) as error:
        await run(service(repo))
    assert error.value.code == AGENT_RUN_STORAGE_FAILED
    assert error.value.details["agent_run_id"] == "run-1"
    assert error.value.details["reason"] == "run_storage_failed"


@pytest.mark.parametrize("phase", ["create", "update:completed", "commit:1", "commit:2"])
async def test_storage_self_cancellation_is_storage_error(phase: str) -> None:
    repo = PhaseRepository()
    repo.errors[phase] = asyncio.CancelledError()
    with pytest.raises(AgentRunError) as error:
        await run(service(repo))
    assert error.value.code == AGENT_RUN_STORAGE_FAILED
    assert "update:stopped" not in repo.operations


@pytest.mark.parametrize("phase", ["commit:1", "commit:2"])
@pytest.mark.parametrize("interruption", ["timeout", "failure", "caller_cancel"])
async def test_applied_commit_with_lost_ack_is_never_retried_or_overwritten(
    phase: str,
    interruption: str,
) -> None:
    repo = PhaseRepository()
    repo.apply_first.add(phase)
    gate = repo.gates[phase] = Gate()
    app = service(repo, budget=0.03 if interruption == "timeout" else 1)
    task = asyncio.create_task(run(app))
    await asyncio.wait_for(gate.entered.wait(), 1)
    if interruption == "caller_cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        if interruption == "failure":
            repo.errors[phase] = RuntimeError("lost acknowledgement")
            gate.release.set()
        with pytest.raises(AgentRunError) as error:
            await task
        assert error.value.code == AGENT_RUN_STORAGE_FAILED
        assert error.value.details["agent_run_id"] == "run-1"
    assert repo.committed[-1] == ("running" if phase == "commit:1" else "completed")
    assert repo.operations.count(phase) == 1
    assert "update:stopped" not in repo.operations
    assert repo.maximum_active == 1


@pytest.mark.parametrize("phase", ["create", "update:completed", "commit:2"])
async def test_resistant_storage_is_owned_and_late_return_never_succeeds(phase: str) -> None:
    repo = PhaseRepository()
    gate = repo.gates[phase] = Gate(resistant=True)
    task = asyncio.create_task(run(service(repo)))
    try:
        await asyncio.wait_for(gate.cancelled.wait(), 1)
        assert not task.done()  # Cooperative budget is not a hard driver deadline.
        assert repo.active == 1
        assert "rollback" not in repo.operations
    finally:
        gate.release.set()
    with pytest.raises(AgentRunError) as error:
        await task
    assert error.value.code == AGENT_RUN_STORAGE_FAILED
    if phase == "create":
        assert "commit:1" not in repo.operations
    elif phase == "update:completed":
        assert "commit:2" not in repo.operations
    assert repo.maximum_active == 1


async def test_write_and_commit_share_one_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    # Observe the actual timeout context; the update consumes part of its window.
    import packages.agent.service as module

    contexts: list[asyncio.Timeout] = []
    original = asyncio.timeout

    def timeout(delay: float) -> asyncio.Timeout:
        context = original(delay)
        contexts.append(context)
        return context

    monkeypatch.setattr(module.asyncio, "timeout", timeout)
    repo = PhaseRepository()
    update = repo.gates["update:completed"] = Gate()
    commit = repo.gates["commit:2"] = Gate()
    task = asyncio.create_task(run(service(repo, budget=1)))
    await update.entered.wait()
    terminal_context = contexts[-1]
    deadline = terminal_context.when()
    update.release.set()
    await commit.entered.wait()
    assert contexts[-1] is terminal_context
    assert terminal_context.when() == deadline
    commit.release.set()
    assert (await task).final_answer == "Acknowledged answer."


async def test_failed_terminal_ack_precedes_cancellable_audit() -> None:
    repo = PhaseRepository()
    app = service(repo, runtime=FakeRuntime(error=RuntimeError("runtime failed")), budget=1)
    audit_gate = Gate()

    class FailureAudit:
        async def record(self, event: AuditEvent) -> None:
            if event.action == "agent.run.failed":
                await audit_gate.wait()

    app._audit = FailureAudit()
    task = asyncio.create_task(run(app))
    await audit_gate.entered.wait()
    assert repo.committed == ["running", "failed"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert repo.committed == ["running", "failed"]
    assert "update:stopped" not in repo.operations


async def test_runtime_failure_persistence_is_bounded() -> None:
    repo = PhaseRepository()
    repo.gates["update:failed"] = Gate()
    app = service(repo, runtime=FakeRuntime(error=RuntimeError("runtime failed")))
    with pytest.raises(AgentRunError) as error:
        await run(app)
    assert error.value.code == AGENT_RUN_STORAGE_FAILED
    assert repo.commit_attempts == 1


@pytest.mark.parametrize("failure", ["raise", "stall", "self_cancel", "late_return"])
async def test_failed_rollback_never_starts_cancellation_write(failure: str) -> None:
    repo = PhaseRepository()
    if failure == "raise":
        repo.errors["rollback"] = RuntimeError("rollback failed")
    elif failure == "self_cancel":
        repo.errors["rollback"] = asyncio.CancelledError()
    else:
        repo.gates["rollback"] = Gate(resistant=failure == "late_return")
    stepper = WaitingStepper()
    app, _ = stream_service("native", stepper, repo)
    app._persistence_phase_timeout_seconds = 0.03
    task = asyncio.create_task(run(app))
    await stepper.entered.wait()
    task.cancel()
    if failure == "late_return":
        gate = repo.gates["rollback"]
        await gate.cancelled.wait()
        gate.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "update:stopped" not in repo.operations
    assert repo.maximum_active == 1


async def test_connected_runtime_cancellation_with_cleanup_failure_is_storage_error() -> None:
    class SelfCancellingStepper:
        async def next_step(self, state: object) -> object:
            raise asyncio.CancelledError

    repo = PhaseRepository()
    repo.errors["rollback"] = RuntimeError("rollback failed")
    app, _ = stream_service("native", SelfCancellingStepper(), repo)
    events = [
        event
        async for event in app.stream(
            context=_allowed_context(),
            command=AgentRunCommand(input="ordinary request"),
        )
    ]
    assert events[-1].data["code"] == AGENT_RUN_STORAGE_FAILED
    assert "update:stopped" not in repo.operations


async def test_repeated_cancellation_keeps_cleanup_serial_and_owned() -> None:
    repo = PhaseRepository()
    gate = repo.gates["commit:2"] = Gate()
    stepper = WaitingStepper()
    app, _ = stream_service("native", stepper, repo)
    app._persistence_phase_timeout_seconds = 1
    task = asyncio.create_task(run(app))
    await stepper.entered.wait()
    task.cancel()
    await gate.entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert repo.active == 1
    gate.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert repo.committed == ["running", "stopped"]
    assert repo.maximum_active == 1
    assert repo.updated is not None and repo.updated.error_code == AGENT_RUN_CANCELLED


async def test_confirmed_cancellation_commit_survives_audit_timeout() -> None:
    class SelfCancellingStepper:
        async def next_step(self, state: object) -> object:
            raise asyncio.CancelledError

    audit_gate = Gate()

    class CancellationAudit:
        async def record(self, event: AuditEvent) -> None:
            if event.action == "agent.run.cancelled":
                await audit_gate.wait()

    repo = PhaseRepository()
    app, _ = stream_service("native", SelfCancellingStepper(), repo)
    app._persistence_phase_timeout_seconds = 0.03
    app._audit = CancellationAudit()
    events = [
        event
        async for event in app.stream(
            context=_allowed_context(),
            command=AgentRunCommand(input="ordinary request"),
        )
    ]
    assert events[-1].data["code"] == AGENT_RUN_CANCELLED
    assert repo.committed == ["running", "stopped"]
    assert audit_gate.cancelled.is_set()


async def test_composition_root_wires_persistence_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import asynccontextmanager

    from apps.api.factories import domain

    settings = AppSettings(_env_file=None, AGENT_PERSISTENCE_PHASE_TIMEOUT_SECONDS=0.17)

    @asynccontextmanager
    async def session() -> AsyncIterator[object]:
        yield object()

    monkeypatch.setattr(domain, "load_settings", lambda: settings)
    monkeypatch.setattr(domain, "create_session_factory", lambda _url: session)
    monkeypatch.setattr(
        domain,
        "_agent_runtime_components",
        lambda **_kwargs: (InMemoryAuditPort(), lambda *_args: completed_runtime()),
    )
    provider = domain.get_agent_run_application_service()
    app = await anext(provider)
    try:
        assert app._persistence_phase_timeout_seconds == 0.17
        assert app._config(AgentRunCommand(input="ordinary request")).timeout_seconds == 30
    finally:
        await provider.aclose()


async def test_caller_cancel_during_uncommitted_update_rolls_back_before_terminal() -> None:
    repo = PhaseRepository()
    gate = repo.gates["update:completed"] = Gate()
    task = asyncio.create_task(run(service(repo, budget=1)))
    await gate.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert repo.operations[-3:] == ["rollback", "update:stopped", "commit:2"]
    assert repo.committed == ["running", "stopped"]
    assert repo.maximum_active == 1


async def test_caller_cancel_after_own_timeout_is_not_storage_error() -> None:
    repo = PhaseRepository()
    gate = repo.gates["update:completed"] = Gate(resistant=True)
    task = asyncio.create_task(run(service(repo)))
    await gate.cancelled.wait()
    task.cancel()
    gate.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "update:stopped" in repo.operations
    assert repo.maximum_active == 1


async def test_late_cancellation_commit_ack_is_not_an_ontime_cancellation_receipt() -> None:
    class SelfCancellingStepper:
        async def next_step(self, state: object) -> object:
            raise asyncio.CancelledError

    repo = PhaseRepository()
    gate = repo.gates["commit:2"] = Gate(resistant=True)
    app, _ = stream_service("native", SelfCancellingStepper(), repo)
    app._persistence_phase_timeout_seconds = 0.03

    async def collect() -> list[AgentStreamEvent]:
        return [
            event
            async for event in app.stream(
                context=_allowed_context(),
                command=AgentRunCommand(input="ordinary request"),
            )
        ]

    task = asyncio.create_task(collect())
    await gate.cancelled.wait()
    assert not task.done()
    gate.release.set()
    events = await task
    assert events[-1].data["code"] == AGENT_RUN_STORAGE_FAILED
    assert repo.committed == ["running", "stopped"]
    assert repo.operations.count("commit:2") == 1


async def test_late_commit_after_caller_cancel_preserves_actual_terminal() -> None:
    repo = PhaseRepository()
    gate = repo.gates["commit:2"] = Gate(resistant=True)
    task = asyncio.create_task(run(service(repo, budget=1)))
    await gate.entered.wait()
    task.cancel()
    await gate.cancelled.wait()
    assert not task.done()
    gate.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert repo.committed == ["running", "completed"]
    assert "update:stopped" not in repo.operations


@pytest.mark.parametrize("terminal", ["failed", "completed", "cancelled"])
async def test_terminal_audit_self_cancel_preserves_acknowledged_business_result(
    terminal: str,
) -> None:
    class SelfCancellingAudit:
        async def record(self, event: AuditEvent) -> None:
            if event.action == f"agent.run.{terminal}":
                raise asyncio.CancelledError

    repo = PhaseRepository()
    if terminal == "cancelled":

        class SelfCancellingStepper:
            async def next_step(self, state: object) -> object:
                raise asyncio.CancelledError

        app, _ = stream_service("native", SelfCancellingStepper(), repo)
    else:
        app = service(
            repo,
            runtime=FakeRuntime(error=RuntimeError("runtime failed"))
            if terminal == "failed"
            else completed_runtime(),
        )
    app._audit = SelfCancellingAudit()
    events = [
        event
        async for event in app.stream(
            context=_allowed_context(),
            command=AgentRunCommand(input="ordinary request"),
        )
    ]
    if terminal == "completed":
        assert events[-1].event == "final"
        assert events[-1].data["final_answer"] == "Acknowledged answer."
    else:
        expected = "AGENT_RUN_FAILED" if terminal == "failed" else AGENT_RUN_CANCELLED
        assert events[-1].event == "error"
        assert events[-1].data["code"] == expected
    assert repo.committed == ["running", "stopped" if terminal == "cancelled" else terminal]


async def test_previously_delivered_cancellation_does_not_misclassify_storage_self_cancel() -> None:
    repo = PhaseRepository()
    repo.errors["update:completed"] = asyncio.CancelledError()
    app = service(repo)

    async def reused_task() -> None:
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.sleep(0)
        assert current.cancelling() == 1
        with pytest.raises(AgentRunError) as error:
            await run(app)
        assert error.value.code == AGENT_RUN_STORAGE_FAILED

    await asyncio.create_task(reused_task())
    assert "update:stopped" not in repo.operations


async def test_already_requested_cancellation_is_delivered_before_initial_storage() -> None:
    repo = PhaseRepository()
    app = service(repo)

    async def pending_cancellation() -> object:
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        return await run(app)

    with pytest.raises(asyncio.CancelledError):
        await asyncio.create_task(pending_cancellation())
    assert "create" not in repo.operations
    assert "update:stopped" not in repo.operations


async def test_expired_cleanup_envelope_cannot_commit_after_suppressed_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import packages.agent.service as module

    class SelfCancellingStepper:
        async def next_step(self, state: object) -> object:
            raise asyncio.CancelledError

    class UncancellingGate(Gate):
        async def wait(self) -> None:
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                current = asyncio.current_task()
                assert current is not None
                current.uncancel()
                self.cancelled.set()
                await self.release.wait()

    contexts: list[asyncio.Timeout] = []
    original = asyncio.timeout

    def timeout(delay: float) -> asyncio.Timeout:
        context = original(delay)
        contexts.append(context)
        return context

    monkeypatch.setattr(module.asyncio, "timeout", timeout)
    repo = PhaseRepository()
    gate = repo.gates["update:stopped"] = UncancellingGate()
    app, _ = stream_service("native", SelfCancellingStepper(), repo)
    app._persistence_phase_timeout_seconds = 1

    async def collect() -> list[AgentStreamEvent]:
        return [
            event
            async for event in app.stream(
                context=_allowed_context(),
                command=AgentRunCommand(input="ordinary request"),
            )
        ]

    task = asyncio.create_task(collect())
    await gate.entered.wait()
    # Initial phase, cleanup envelope, rollback phase, cancellation phase.
    assert len(contexts) == 4
    envelope = contexts[1]
    envelope.reschedule(asyncio.get_running_loop().time())
    await gate.cancelled.wait()
    assert envelope.expired() and not contexts[-1].expired()
    gate.release.set()
    events = await task
    assert events[-1].data["code"] == AGENT_RUN_STORAGE_FAILED
    assert "commit:2" not in repo.operations
    assert repo.committed == ["running"]


async def test_old_caller_count_runtime_self_cancel_and_failed_cleanup_is_storage_error() -> None:
    class SelfCancellingStepper:
        async def next_step(self, state: object) -> object:
            raise asyncio.CancelledError

    repo = PhaseRepository()
    repo.errors["rollback"] = RuntimeError("rollback failed")
    app, _ = stream_service("native", SelfCancellingStepper(), repo)

    async def reused_caller() -> None:
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.sleep(0)
        assert current.cancelling() == 1
        with pytest.raises(AgentRunError) as error:
            await run(app)
        assert error.value.code == AGENT_RUN_STORAGE_FAILED
        assert error.value.details["agent_run_id"] == "run-1"

    await asyncio.create_task(reused_caller())
    assert repo.committed == ["running"]
    assert "update:stopped" not in repo.operations


async def test_old_caller_count_new_disconnect_and_failed_cleanup_still_propagates() -> None:
    repo = PhaseRepository()
    repo.errors["rollback"] = RuntimeError("rollback failed")
    stepper = WaitingStepper()
    app, _ = stream_service("native", stepper, repo)

    async def reused_caller() -> None:
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.sleep(0)
        assert current.cancelling() == 1
        await run(app)

    caller = asyncio.create_task(reused_caller())
    await asyncio.wait_for(stepper.entered.wait(), 1)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert repo.committed == ["running"]
    assert "update:stopped" not in repo.operations
