"""Independent ordinary-transaction review with disposable in-memory dependencies.

No database, provider, external service, or permission-policy probes are used.
Assertions exercise the public stream contract and actual acknowledged snapshots.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime

import pytest

from packages.agent.dto import AgentRunCommand, AgentRunCreate, AgentRunRecord, AgentRunUpdate
from packages.agent.exceptions import AGENT_RUN_FAILED, AGENT_RUN_STORAGE_FAILED
from packages.agent.runtime import AgentRunResult, AgentRunStatus, AgentTerminationReason
from packages.agent.service import AGENT_RUN_CANCELLED, AgentRunApplicationService
from packages.agent.streaming import AgentStreamEvent
from packages.auth.context import AuthContext
from packages.common.audit import AuditEvent
from packages.common.context import AuthenticatedRequestContext


class _Gate:
    def __init__(self, *, clear_cancellation: bool = False) -> None:
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.clear_cancellation = clear_cancellation
        self.owner: asyncio.Task[object] | None = None

    async def wait(self) -> None:
        self.owner = asyncio.current_task()
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            if not self.clear_cancellation:
                raise
            assert self.owner is not None
            self.owner.uncancel()
            await self.release.wait()


class _Transactions:
    """Keep staged and committed records separate, including lost-ack commits."""

    def __init__(self) -> None:
        self.staged: AgentRunRecord | None = None
        self.committed: AgentRunRecord | None = None
        self.snapshots: list[AgentRunRecord] = []
        self.operations: list[str] = []
        self.callbacks: dict[str, Callable[[], Awaitable[None]]] = {}
        self.operation_tasks: list[asyncio.Task[object] | None] = []
        self.commit_attempts = 0
        self.active = 0
        self.maximum_active = 0

    async def _pause(self, label: str) -> None:
        self.operations.append(label)
        self.operation_tasks.append(asyncio.current_task())
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            callback = self.callbacks.get(label)
            if callback is not None:
                await callback()
        finally:
            self.active -= 1

    async def create_run(self, record: AgentRunCreate) -> AgentRunRecord:
        now = datetime.now(UTC)
        self.staged = AgentRunRecord(
            **record.model_dump(), id="review-run", created_at=now, updated_at=now,
        )
        await self._pause("create")
        return self.staged

    async def update_run_result(
        self,
        *,
        tenant_id: str,
        user_id: str,
        run_id: str,
        update: AgentRunUpdate,
    ) -> AgentRunRecord:
        assert self.staged is not None
        self.staged = self.staged.model_copy(update=update.model_dump())
        await self._pause(f"update:{update.status}")
        return self.staged

    async def commit(self) -> None:
        self.commit_attempts += 1
        assert self.staged is not None
        self.committed = self.staged
        self.snapshots.append(self.committed)
        await self._pause(f"commit:{self.commit_attempts}")

    async def rollback(self) -> None:
        await self._pause("rollback")
        self.staged = self.committed


class _Runtime:
    def __init__(self, outcome: str = "completed", *, old_cancellation: bool = False) -> None:
        self.outcome = outcome
        self.old_cancellation = old_cancellation
        self.gate = _Gate()

    async def run(self, *, context: AuthenticatedRequestContext) -> AgentRunResult:
        if self.outcome == "failed":
            raise RuntimeError("synthetic internal runtime error")
        if self.outcome == "cancelled":
            raise asyncio.CancelledError
        if self.outcome == "waiting":
            await self.gate.wait()
        if self.old_cancellation:
            current = asyncio.current_task()
            assert current is not None
            current.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.sleep(0)
            assert current.cancelling() == 1
        return AgentRunResult(
            status=AgentRunStatus.COMPLETED,
            termination_reason=AgentTerminationReason.FINAL_ANSWER,
            steps_used=1,
            tool_calls_used=0,
            final_answer="Persisted review answer.",
            request_id=context.request_id,
            trace_id=context.trace_id,
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            latency_ms=1.0,
        )


class _Audit:
    def __init__(self) -> None:
        self.callbacks: dict[str, Callable[[], Awaitable[None]]] = {}

    async def record(self, event: AuditEvent) -> None:
        callback = self.callbacks.get(event.action)
        if callback is not None:
            await callback()


async def _self_cancel() -> None:
    raise asyncio.CancelledError


def _context() -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="review-request",
        trace_id="review-trace",
        auth=AuthContext(
            tenant_id="review-tenant", user_id="review-user", permissions=("agent:run",),
        ),
    )


def _application(
    repo: _Transactions, runtime: _Runtime | None = None, audit: _Audit | None = None,
) -> AgentRunApplicationService:
    worker = runtime or _Runtime()
    return AgentRunApplicationService(
        repository=repo,
        runtime_factory=lambda _config, _run_id: worker,
        audit=audit or _Audit(),
        default_max_steps=3,
        default_max_tool_calls=0,
        default_timeout_seconds=30,
        repeated_action_threshold=3,
        persistence_phase_timeout_seconds=1,
    )


async def _collect(
    app: AgentRunApplicationService, events: list[AgentStreamEvent] | None = None,
) -> list[AgentStreamEvent]:
    captured = events if events is not None else []
    async for event in app.stream(
        context=_context(), command=AgentRunCommand(input="ordinary transaction review"),
    ):
        captured.append(event)
    return captured


def _assert_error(events: list[AgentStreamEvent], code: str, *, started: bool = True) -> None:
    assert [event.event for event in events] == (["started", "error"] if started else ["error"])
    assert events[-1].data == {"code": code, "terminal": True}
    assert all("final_answer" not in event.data for event in events)
    assert [event.sequence for event in events] == list(range(len(events)))


async def _drain(task: asyncio.Task[object], *gates: _Gate) -> None:
    for gate in gates:
        gate.release.set()
    if not task.done():
        task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await asyncio.wait_for(task, 2)


@pytest.mark.parametrize("phase", ["create", "commit:1", "update:completed", "commit:2"])
async def test_repository_self_cancel_has_storage_stream_terminal(phase: str) -> None:
    repo = _Transactions()
    repo.callbacks[phase] = _self_cancel
    events = await asyncio.wait_for(_collect(_application(repo)), 2)
    _assert_error(events, AGENT_RUN_STORAGE_FAILED, started=phase not in {"create", "commit:1"})
    assert "update:stopped" not in repo.operations
    assert repo.operations[-1] == "rollback"
    assert repo.maximum_active == 1 and repo.active == 0


@pytest.mark.parametrize("outcome", ["failed", "completed", "cancelled"])
async def test_self_cancelled_terminal_audit_keeps_stream_business_outcome(outcome: str) -> None:
    repo = _Transactions()
    audit = _Audit()
    audit.callbacks[f"agent.run.{outcome}"] = _self_cancel
    events = await asyncio.wait_for(_collect(_application(repo, _Runtime(outcome), audit)), 2)
    if outcome == "completed":
        assert [event.event for event in events] == ["started", "final"]
        assert events[-1].data["status"] == "completed"
        assert events[-1].data["final_answer"] == "Persisted review answer."
    else:
        _assert_error(events, AGENT_RUN_FAILED if outcome == "failed" else AGENT_RUN_CANCELLED)
    assert [record.status for record in repo.snapshots] == [
        "running", "stopped" if outcome == "cancelled" else outcome,
    ]


async def test_old_delivered_cancellation_does_not_remove_storage_stream_terminal() -> None:
    repo = _Transactions()
    repo.callbacks["update:completed"] = _self_cancel
    events = await asyncio.wait_for(
        _collect(_application(repo, _Runtime(old_cancellation=True))), 2,
    )
    _assert_error(events, AGENT_RUN_STORAGE_FAILED)
    assert "update:stopped" not in repo.operations
    assert [record.status for record in repo.snapshots] == ["running"]


async def test_real_consumer_cancel_during_failed_audit_preserves_failed_terminal() -> None:
    repo = _Transactions()
    audit = _Audit()
    gate = _Gate()
    audit.callbacks["agent.run.failed"] = gate.wait
    events: list[AgentStreamEvent] = []
    task = asyncio.create_task(_collect(_application(repo, _Runtime("failed"), audit), events))
    try:
        await asyncio.wait_for(gate.entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert [record.status for record in repo.snapshots] == ["running", "failed"]
        assert "update:stopped" not in repo.operations
        assert [event.event for event in events] == ["started"]
        assert gate.cancelled.is_set()
    finally:
        await _drain(task, gate)


@pytest.mark.parametrize("outcome", ["completed", "failed"])
async def test_applied_terminal_commit_with_lost_ack_has_only_fixed_storage_error(
    outcome: str,
) -> None:
    async def lose_acknowledgement() -> None:
        raise OSError("synthetic internal transaction diagnostic")

    repo = _Transactions()
    repo.callbacks["commit:2"] = lose_acknowledgement
    events = await asyncio.wait_for(_collect(_application(repo, _Runtime(outcome))), 2)
    _assert_error(events, AGENT_RUN_STORAGE_FAILED)
    assert repo.committed is not None and repo.committed.status == outcome
    assert repo.commit_attempts == 2
    assert "update:stopped" not in repo.operations


@pytest.mark.parametrize("phase", ["update:stopped", "commit:2"])
async def test_swallowed_outer_cleanup_expiry_never_reports_confirmed_cancellation(
    monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    # Observe public asyncio timeout contexts without replacing service helpers.
    contexts: list[tuple[asyncio.Task[object] | None, asyncio.Timeout]] = []
    original_timeout = asyncio.timeout

    def capture_timeout(delay: float | None) -> asyncio.Timeout:
        timeout = original_timeout(delay)
        if delay == 1:
            contexts.append((asyncio.current_task(), timeout))
        return timeout

    monkeypatch.setattr(asyncio, "timeout", capture_timeout)
    repo = _Transactions()
    gate = _Gate(clear_cancellation=True)
    repo.callbacks[phase] = gate.wait
    task = asyncio.create_task(_collect(_application(repo, _Runtime("cancelled"))))
    try:
        await asyncio.wait_for(gate.entered.wait(), 2)
        cleanup_contexts = [timeout for owner, timeout in contexts if owner is gate.owner]
        assert len(cleanup_contexts) >= 2
        envelope, inner_phase = cleanup_contexts[0], cleanup_contexts[-1]
        envelope.reschedule(asyncio.get_running_loop().time())
        await asyncio.wait_for(gate.cancelled.wait(), 2)
        assert envelope.expired() and not inner_phase.expired()
        assert not task.done() and repo.active == 1
        gate.release.set()
        events = await asyncio.wait_for(task, 2)
        _assert_error(events, AGENT_RUN_STORAGE_FAILED)
        assert repo.committed is not None
        assert repo.committed.status == ("running" if phase == "update:stopped" else "stopped")
        assert repo.commit_attempts == (1 if phase == "update:stopped" else 2)
        assert repo.operations.count("update:stopped") == 1
        assert repo.maximum_active == 1 and repo.active == 0
    finally:
        await _drain(task, gate)


async def test_repeated_consumer_cancellation_still_owns_serial_terminal_cleanup() -> None:
    repo = _Transactions()
    runtime = _Runtime("waiting")
    gate = _Gate()
    repo.callbacks["commit:2"] = gate.wait
    events: list[AgentStreamEvent] = []
    task = asyncio.create_task(_collect(_application(repo, runtime), events))
    try:
        await asyncio.wait_for(runtime.gate.entered.wait(), 2)
        task.cancel()
        await asyncio.wait_for(gate.entered.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and repo.active == 1
        gate.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert [record.status for record in repo.snapshots] == ["running", "stopped"]
        assert repo.operations[-3:] == ["rollback", "update:stopped", "commit:2"]
        assert repo.maximum_active == 1 and repo.active == 0
        assert [event.event for event in events] == ["started"]
    finally:
        await _drain(task, gate, runtime.gate)
