"""Auxiliary telemetry cannot hold execution open or leak exception payloads."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Coroutine, Mapping
from typing import Any

import pytest

from apps.api.factories.agent import create_agent_runtime
from packages.agent.auxiliary_io import await_auxiliary_io
from packages.agent.dto import (
    AgentRunCommand,
    FinalAnswerValidationRequest,
    ToolCallCreate,
    ToolInvocationStatus,
    ToolRateLimit,
    ToolRateLimitDecision,
    ToolRateLimitKey,
)
from packages.agent.exceptions import TOOL_CALL_AUDIT_FAILED, AgentToolError
from packages.agent.final_answer import StrictFinalAnswerValidator
from packages.agent.registry import ToolRegistry
from packages.agent.runtime import AGENT_TIMEOUT, AgentRunConfig, AgentStepDecision, AgentToolEvent
from packages.agent.service import AGENT_RUN_CANCELLED
from packages.common.audit import AuditEvent, InMemoryAuditPort
from tests.unit.agent.test_runtime import FakeStepper, HandlerProbe, _registry
from tests.unit.agent.test_streaming_service import _allowed_context, _final, _service

CANARY = "PRIVATE_AUXILIARY_PAYLOAD_4d8c"


@pytest.fixture(params=("native", "langgraph"))
def backend(request: pytest.FixtureRequest) -> str:
    if request.param == "langgraph":
        pytest.importorskip("langgraph")
    return str(request.param)


class BlockingTelemetry:
    def __init__(self, *, stubborn: bool = False) -> None:
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()
        self.stubborn = stubborn
        self.calls = 0

    async def wait(self) -> None:
        self.calls += 1
        self.entered.set()
        try:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                if not self.stubborn:
                    raise
                await self.release.wait()
                raise RuntimeError(CANARY) from None
        finally:
            self.finished.set()

    async def record(self, event: AuditEvent) -> None:
        await self.wait()

    async def emit(self, event: AgentToolEvent) -> None:
        await self.wait()


class FailingTelemetry:
    async def record(self, event: AuditEvent) -> None:
        raise RuntimeError(CANARY)

    async def emit(self, event: AgentToolEvent) -> None:
        raise RuntimeError(CANARY)


def _config(timeout: float = 0.02) -> AgentRunConfig:
    return AgentRunConfig(
        max_steps=3,
        max_tool_calls=2,
        timeout_seconds=timeout,
        repeated_action_threshold=2,
        auxiliary_io_timeout_seconds=0.05,
    )


def _assert_private_logs(caplog: pytest.LogCaptureFixture) -> None:
    assert CANARY not in caplog.text
    assert CANARY not in repr([record.__dict__ for record in caplog.records])
    assert all(record.exc_info is None for record in caplog.records)


async def test_standalone_validator_bounds_auxiliary_audit_without_skipping_validation() -> None:
    telemetry = BlockingTelemetry()
    validator = StrictFinalAnswerValidator(audit=telemetry, auxiliary_io_timeout_seconds=0.05)
    result = await asyncio.wait_for(
        validator.validate(
            context=_allowed_context(),
            request=FinalAnswerValidationRequest(agent_run_id=None, answer="Supported answer."),
            observations=(),
        ),
        timeout=0.3,
    )
    assert result.status == "valid"
    assert telemetry.cancelled.is_set()
    assert telemetry.finished.is_set()


async def test_runtime_stalled_audit_cannot_escape_execution_timeout(backend: str) -> None:
    telemetry = BlockingTelemetry()
    runtime = create_agent_runtime(
        backend=backend,
        registry=_registry(handler=HandlerProbe()),
        stepper=FakeStepper([_final()]),
        audit=telemetry,
        config=_config(),
    )
    result = await asyncio.wait_for(runtime.run(context=_allowed_context()), 0.5)
    assert result.error_code == AGENT_TIMEOUT
    assert result.final_answer is None
    assert telemetry.cancelled.is_set()


async def test_predispatch_progress_uses_remaining_deadline(backend: str) -> None:
    telemetry, handler = BlockingTelemetry(), HandlerProbe()
    runtime = create_agent_runtime(
        backend=backend,
        registry=_registry(handler=handler),
        stepper=FakeStepper([AgentStepDecision.tool_call("demo_tool", {"query": "x"}), _final()]),
        audit=InMemoryAuditPort(),
        event_sink=telemetry,
        config=_config().model_copy(update={"auxiliary_io_timeout_seconds": 5.0}),
        # Keep the synthetic deadline untouched by graph setup. Once the sink
        # is actually entered, its 20 ms remaining allowance must cancel it and
        # the post-progress deadline check must fence the handler.
        perf_counter=lambda: 1.0 if telemetry.entered.is_set() else 0.0,
    )
    result = await asyncio.wait_for(runtime.run(context=_allowed_context()), 1.0)
    assert result.error_code == AGENT_TIMEOUT
    assert result.final_answer is None
    assert handler.call_count == 0
    assert telemetry.entered.is_set()
    assert telemetry.cancelled.is_set()
    assert telemetry.finished.is_set()


async def test_service_lifecycle_audits_are_bounded_outside_execution_budget(backend: str) -> None:
    telemetry = BlockingTelemetry()
    service, repo = _service(backend, FakeStepper([_final()]))
    service._audit = telemetry
    service._auxiliary_io_timeout_seconds = 0.05
    result = await asyncio.wait_for(
        service.run(
            context=_allowed_context(),
            command=AgentRunCommand(input="x", timeout_seconds=1),
        ),
        timeout=1,
    )
    assert result.status == "completed"
    assert result.final_answer == "Validated answer."
    assert telemetry.calls == 2
    assert repo.updated is not None and repo.updated.status == "completed"
    assert repo.events[-1] == "commit"


@pytest.mark.parametrize("stage", ("started", "terminal"))
async def test_service_audit_cancellation_never_replaces_committed_terminal(
    backend: str,
    stage: str,
) -> None:
    telemetry = BlockingTelemetry()

    class StageAudit:
        async def record(self, event: AuditEvent) -> None:
            if (event.action == "agent.run.started") == (stage == "started"):
                await telemetry.record(event)

    service, repo = _service(backend, FakeStepper([_final()]))
    service._audit = StageAudit()
    task = asyncio.create_task(
        service.run(context=_allowed_context(), command=AgentRunCommand(input="x")),
    )
    await asyncio.wait_for(telemetry.entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert telemetry.cancelled.is_set()
    assert repo.updated is not None
    assert repo.updated.termination_reason == (
        AGENT_RUN_CANCELLED if stage == "started" else "FINAL_ANSWER"
    )
    assert repo.events[-1] == "commit"


async def test_registry_auxiliary_audit_does_not_bypass_required_recorder() -> None:
    telemetry = BlockingTelemetry()
    registry = _registry(handler=HandlerProbe())
    registry._audit = telemetry
    registry._auxiliary_io_timeout_seconds = 0.05
    records: list[ToolCallCreate] = []

    class Recorder:
        async def record_tool_call(self, record: ToolCallCreate) -> None:
            records.append(record)

    registry._tool_call_recorder = Recorder()
    result = await asyncio.wait_for(
        registry.execute(
            name="demo_tool",
            arguments={"query": "x"},
            context=_allowed_context(),
            agent_run_id="run-1",
        ),
        0.3,
    )
    assert result.status is ToolInvocationStatus.SUCCESS
    assert len(records) == 1 and records[0].status == "success"
    assert telemetry.cancelled.is_set()


async def test_required_recorder_failure_still_fails_closed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = _registry(handler=HandlerProbe())

    class Recorder:
        async def record_tool_call(self, record: ToolCallCreate) -> None:
            raise RuntimeError(CANARY)

    registry._tool_call_recorder = Recorder()
    with pytest.raises(AgentToolError) as error:
        await registry.execute(
            name="demo_tool",
            arguments={"query": "x"},
            context=_allowed_context(),
            agent_run_id="run-1",
        )
    assert error.value.code == TOOL_CALL_AUDIT_FAILED
    _assert_private_logs(caplog)


async def test_required_recorder_stall_is_not_successful_best_effort(backend: str) -> None:
    blocked = BlockingTelemetry()
    registry = _registry(handler=HandlerProbe())

    class Recorder:
        async def record_tool_call(self, record: ToolCallCreate) -> None:
            await blocked.wait()

    registry._tool_call_recorder = Recorder()
    runtime = create_agent_runtime(
        backend=backend,
        registry=registry,
        stepper=FakeStepper([AgentStepDecision.tool_call("demo_tool", {"query": "x"}), _final()]),
        audit=InMemoryAuditPort(),
        config=_config(0.1),
        agent_run_id="run-1",
    )
    result = await asyncio.wait_for(runtime.run(context=_allowed_context()), 0.3)
    assert result.error_code == AGENT_TIMEOUT
    assert result.final_answer is None
    assert blocked.cancelled.is_set()


@pytest.mark.parametrize("cancel", (False, True), ids=("deadline", "disconnect"))
async def test_suppressed_predispatch_cancellation_never_starts_late_handler(
    backend: str,
    cancel: bool,
) -> None:
    blocked = BlockingTelemetry(stubborn=True)
    handler = HandlerProbe()
    registry = _registry(handler=handler)
    finished = asyncio.Event()

    class SuppressingLimiter:
        async def acquire(
            self,
            *,
            key: ToolRateLimitKey,
            limit: ToolRateLimit,
        ) -> ToolRateLimitDecision:
            try:
                await blocked.wait()
            except RuntimeError:
                # Deliberately clear cancellation too: the explicit dispatch
                # receipt, not a task's cancellation count, is authoritative.
                current = asyncio.current_task()
                assert current is not None
                current.uncancel()
            finally:
                finished.set()
            return ToolRateLimitDecision(allowed=True, remaining=1, reset_after_seconds=0)

    registry._rate_limiter = SuppressingLimiter()
    runtime = create_agent_runtime(
        backend=backend,
        registry=registry,
        stepper=FakeStepper([AgentStepDecision.tool_call("demo_tool", {"query": "x"}), _final()]),
        audit=InMemoryAuditPort(),
        config=_config(5 if cancel else 0.1),
        # Isolate the dispatch receipt from graph/setup wall-clock overhead.
        # The blocked operation still has a real 100 ms asyncio deadline in
        # the deadline case; neither result nor event wait bounds are relaxed.
        perf_counter=lambda: 0.0,
    )
    task = asyncio.create_task(runtime.run(context=_allowed_context()))
    try:
        await asyncio.wait_for(blocked.entered.wait(), 1)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 0.3)
        else:
            assert (await asyncio.wait_for(task, 0.3)).error_code == AGENT_TIMEOUT
        assert blocked.cancelled.is_set()
        assert handler.call_count == 0
    finally:
        primary_error = sys.exception()
        blocked.release.set()
        try:
            # Do not replace an entry timeout with a second timeout waiting
            # for a limiter that never started.
            if blocked.entered.is_set():
                await asyncio.wait_for(finished.wait(), 1)
                await asyncio.sleep(0)
        except TimeoutError:
            if primary_error is None:
                raise
            primary_error.add_note("Limiter cleanup also exceeded its 1 s completion bound.")
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert handler.call_count == 0


@pytest.mark.parametrize("target", ("validator", "registry", "runtime_audit", "progress"))
async def test_external_cancellation_propagates_through_auxiliary_hooks(target: str) -> None:
    blocked = BlockingTelemetry()
    operation: Coroutine[Any, Any, object]
    if target == "validator":
        operation = StrictFinalAnswerValidator(audit=blocked).validate(
            context=_allowed_context(),
            request=FinalAnswerValidationRequest(agent_run_id=None, answer="Supported answer."),
            observations=(),
        )
    elif target == "registry":
        registry = ToolRegistry(audit=blocked)
        operation = registry.get(name="unknown", context=_allowed_context())
    else:
        runtime = create_agent_runtime(
            backend="native",
            registry=_registry(handler=HandlerProbe()),
            stepper=FakeStepper(
                [AgentStepDecision.tool_call("demo_tool", {"query": "x"}), _final()]
                if target == "progress"
                else [_final()],
            ),
            audit=blocked if target == "runtime_audit" else InMemoryAuditPort(),
            event_sink=blocked if target == "progress" else None,
            config=_config(5),
        )
        operation = runtime.run(context=_allowed_context())
    task = asyncio.create_task(operation)
    await asyncio.wait_for(blocked.entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 0.3)
    assert blocked.finished.is_set()
    assert blocked.cancelled.is_set()


async def test_cancellation_during_timeout_cleanup_remains_cancellation() -> None:
    blocked = BlockingTelemetry(stubborn=True)
    task = asyncio.create_task(await_auxiliary_io(blocked.wait(), timeout_seconds=0.001))
    try:
        await asyncio.wait_for(blocked.cancelled.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        blocked.release.set()
        await asyncio.wait_for(blocked.finished.wait(), 1)
        await asyncio.sleep(0)


async def test_late_telemetry_failure_is_consumed_without_private_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    blocked = BlockingTelemetry(stubborn=True)
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                await_auxiliary_io(blocked.wait(), timeout_seconds=0.001),
                0.3,
            )
        assert not blocked.finished.is_set()
    finally:
        blocked.release.set()
        await asyncio.wait_for(blocked.finished.wait(), 1)
        await asyncio.sleep(0)
    _assert_private_logs(caplog)
    assert "agent.auxiliary_io.cancel_timeout" in caplog.text


async def test_registry_projection_and_audit_exception_payloads_never_reach_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = _registry(handler=HandlerProbe())
    registry._audit = FailingTelemetry()

    def broken_projection(output: Mapping[str, object]) -> Mapping[str, object]:
        raise ValueError(CANARY)

    definition = registry.registered_tool_definitions[0]
    registry._definitions[definition.name] = definition.model_copy(
        update={"prompt_projection": broken_projection},
    )
    result = await registry.execute(
        name="demo_tool",
        arguments={"query": CANARY},
        context=_allowed_context(),
    )
    assert result.prompt_context == {}
    assert "agent.tool.prompt_projection_failed" in caplog.text
    assert "agent.tool.audit_failed" in caplog.text
    _assert_private_logs(caplog)


async def test_service_runtime_validation_and_event_failures_have_safe_logs(
    backend: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    runtime = create_agent_runtime(
        backend=backend,
        registry=_registry(handler=HandlerProbe()),
        stepper=FakeStepper(
            [AgentStepDecision.tool_call("demo_tool", {"query": CANARY}), _final()]
        ),
        audit=FailingTelemetry(),
        event_sink=FailingTelemetry(),
        config=_config(5),
    )
    result = await runtime.run(context=_allowed_context())
    assert result.final_answer == "Validated answer."
    service, _ = _service(backend, FakeStepper([_final()]))
    service._audit = FailingTelemetry()
    await service.run(context=_allowed_context(), command=AgentRunCommand(input=CANARY))
    assert CANARY not in json.dumps(result.model_dump(mode="json"))
    for message in (
        "agent.run.audit_failed",
        "agent.runtime.audit_failed",
        "agent.final_answer_validation.audit_failed",
        "agent.runtime.event_sink_failed",
    ):
        assert message in caplog.text
    _assert_private_logs(caplog)


async def test_auxiliary_budget_is_injectable_for_healthy_remote_writes() -> None:
    done = asyncio.Event()

    async def healthy_remote_write() -> None:
        await asyncio.sleep(0.06)
        done.set()

    await await_auxiliary_io(healthy_remote_write(), timeout_seconds=0.2)
    assert done.is_set()


@pytest.mark.parametrize("value", (0.0, -1.0, float("nan"), float("inf")))
def test_auxiliary_budgets_must_be_finite_positive(value: float) -> None:
    with pytest.raises(ValueError):
        ToolRegistry(audit=InMemoryAuditPort(), auxiliary_io_timeout_seconds=value)
    with pytest.raises(ValueError):
        StrictFinalAnswerValidator(audit=None, auxiliary_io_timeout_seconds=value)
    with pytest.raises(ValueError):
        AgentRunConfig(
            max_steps=1,
            max_tool_calls=0,
            timeout_seconds=1,
            repeated_action_threshold=1,
            auxiliary_io_timeout_seconds=value,
        )


def test_auxiliary_budget_setting_has_finite_operational_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from packages.common.config import AppSettings

    monkeypatch.setenv("AGENT_AUXILIARY_IO_TIMEOUT_SECONDS", "0.8")
    assert AppSettings(_env_file=None).agent_auxiliary_io_timeout_seconds == 0.8
    for value in ("0", "nan", "inf", "31"):
        monkeypatch.setenv("AGENT_AUXILIARY_IO_TIMEOUT_SECONDS", value)
        with pytest.raises(ValueError):
            AppSettings(_env_file=None)


def test_operational_auxiliary_budget_does_not_change_business_snapshot_shape() -> None:
    config = _config().model_copy(update={"auxiliary_io_timeout_seconds": 0.8})
    assert "auxiliary_io_timeout_seconds" not in config.model_dump(mode="json")


@pytest.mark.parametrize("mode", ("success", "failure", "timeout", "caller_cancel"))
async def test_service_token_denial_keeps_permission_and_cancellation_contracts(
    mode: str, caplog: pytest.LogCaptureFixture,
) -> None:
    from packages.agent.service_token_bridge import (
        SERVICE_TOKEN_TOOL_BRIDGE_FORBIDDEN,
        ServiceTokenToolBridge,
        ServiceTokenToolBridgeCandidate,
        ServiceTokenToolChoice,
    )
    from packages.common.errors import DomainError
    from tests.unit.agent.test_service_token_bridge import (
        FakeAgentRunRepository,
        FakeToolCallRepository,
        _context,
    )

    audit = (
        InMemoryAuditPort() if mode == "success"
        else FailingTelemetry() if mode == "failure"
        else BlockingTelemetry()
    )
    repository = FakeAgentRunRepository()
    handler = HandlerProbe()
    bridge = ServiceTokenToolBridge(
        registry=_registry(handler=handler),
        agent_runs=repository,
        tool_calls=FakeToolCallRepository(),
        audit=audit,
        auxiliary_io_timeout_seconds=0.01,
    )
    operation = bridge.execute(
        context=_context(permissions=()), latest_user_message=CANARY, session_id=None,
        candidates=(ServiceTokenToolBridgeCandidate(
            name="demo_tool", description="test", schema_summary={}, declaration_type="modern",
        ),),
        tool_choice=ServiceTokenToolChoice(), requested_model="fake",
    )
    if mode == "caller_cancel":
        assert isinstance(audit, BlockingTelemetry)
        task = asyncio.create_task(operation)
        await asyncio.wait_for(audit.entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert audit.cancelled.is_set()
    else:
        with pytest.raises(DomainError) as error:
            await asyncio.wait_for(operation, 1)
        assert error.value.code == SERVICE_TOKEN_TOOL_BRIDGE_FORBIDDEN
        if mode == "timeout":
            assert isinstance(audit, BlockingTelemetry)
            assert audit.cancelled.is_set()
        if mode in {"timeout", "failure"}:
            assert "agent.service_token_bridge.audit_failed" in caplog.text
    assert repository.created == []
    assert handler.call_count == 0
    _assert_private_logs(caplog)


async def test_post_commit_cancellation_audit_timeout_does_not_claim_storage_failed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    import packages.agent.service as service_module

    class SelfCancellingStepper:
        async def next_step(self, state: object) -> AgentStepDecision:
            raise asyncio.CancelledError

    blocked = BlockingTelemetry()

    class CancellationAudit:
        async def record(self, event: AuditEvent) -> None:
            if event.action == "agent.run.cancelled":
                await blocked.record(event)

    service, repository = _service("native", SelfCancellingStepper())
    service._audit = CancellationAudit()
    original_timeout = asyncio.timeout
    # Exercise the existing outer storage-cleanup budget without a five-second
    # test. Other bounded hooks use wait/wait_for rather than asyncio.timeout.
    monkeypatch.setattr(service_module.asyncio, "timeout", lambda _: original_timeout(0.01))
    events = [
        event async for event in service.stream(
            context=_allowed_context(), command=AgentRunCommand(input="x"),
        )
    ]
    assert repository.events.count("commit") == 2
    assert events[-1].data["code"] == AGENT_RUN_CANCELLED
    assert blocked.cancelled.is_set()
    assert "agent.run.cancel_audit_incomplete" in caplog.text
    assert "agent.run.cancel_persistence_failed" not in caplog.text
