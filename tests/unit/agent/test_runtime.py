from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from packages.agent.dto import ToolDefinition, ToolInvocationStatus, ToolRateLimit
from packages.agent.exceptions import TOOL_PERMISSION_DENIED
from packages.agent.registry import InMemoryToolRateLimiter, ToolRegistry
from packages.agent.runtime import (
    AGENT_STEPPER_FAILED,
    AGENT_TIMEOUT,
    AGENT_TOOL_FAILED,
    MAX_STEPS_REACHED,
    MAX_TOOL_CALLS_REACHED,
    REPEATED_ACTION_DETECTED,
    AgentActionType,
    AgentRunConfig,
    AgentRunStatus,
    AgentRuntime,
    AgentRuntimeState,
    AgentStepDecision,
    AgentTerminationReason,
    RepeatedActionDetector,
)
from packages.auth.context import AuthContext
from packages.common.audit import AuditEvent, AuditStatus, InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext


class DemoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    b: str | None = None


class DemoOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str


class StructuredErrorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    error_code: str


class HandlerProbe:
    def __init__(self, output: object | None = None) -> None:
        self.call_count = 0
        self.context: AuthenticatedRequestContext | None = None
        self.output = output or DemoOutput(answer="safe-answer")

    async def __call__(
        self,
        payload: DemoInput,
        context: AuthenticatedRequestContext,
    ) -> object:
        _ = payload
        self.call_count += 1
        self.context = context
        return self.output


class FakeStepper:
    def __init__(self, decisions: Sequence[AgentStepDecision]) -> None:
        self._decisions = list(decisions)
        self.states: list[AgentRuntimeState] = []

    async def next_step(self, state: AgentRuntimeState) -> AgentStepDecision:
        self.states.append(state)
        if not self._decisions:
            raise RuntimeError("provider token leaked")
        return self._decisions.pop(0)


class FailingStepper:
    def __init__(self) -> None:
        self.call_count = 0

    async def next_step(self, state: AgentRuntimeState) -> AgentStepDecision:
        _ = state
        self.call_count += 1
        raise RuntimeError("prompt and provider payload leaked")


class FailingAuditPort:
    async def record(self, event: AuditEvent) -> None:
        _ = event
        raise RuntimeError("audit backend unavailable")


def _context(
    *,
    permissions: tuple[str, ...] = ("agent:tool:demo",),
) -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-1",
        trace_id="trace-1",
        auth=AuthContext(
            user_id="user-1",
            tenant_id="tenant-1",
            roles=("analyst",),
            department="risk",
            permissions=permissions,
        ),
    )


def _config(**overrides: Any) -> AgentRunConfig:
    values: dict[str, object] = {
        "max_steps": 4,
        "max_tool_calls": 3,
        "timeout_seconds": 10.0,
        "repeated_action_threshold": 3,
    }
    values.update(overrides)
    return AgentRunConfig.model_validate(values)


def _registry(
    *,
    audit: InMemoryAuditPort | None = None,
    handler: HandlerProbe | None = None,
    output_schema: type[BaseModel] = DemoOutput,
) -> ToolRegistry:
    registry = ToolRegistry(
        audit=audit or InMemoryAuditPort(),
        rate_limiter=InMemoryToolRateLimiter(clock=lambda: 100.0),
        perf_counter=lambda: 100.0,
    )
    registry.register(
        ToolDefinition(
            name="demo_tool",
            description="Demo governed tool.",
            input_schema=DemoInput,
            output_schema=output_schema,
            permission="agent:tool:demo",
            timeout_seconds=1.0,
            rate_limit=ToolRateLimit(max_calls=10, window_seconds=60.0),
            handler=handler or HandlerProbe(),
        )
    )
    return registry


@pytest.mark.asyncio
async def test_final_answer_completes_without_calling_tools() -> None:
    audit = InMemoryAuditPort()
    handler = HandlerProbe()
    runtime = AgentRuntime(
        registry=_registry(audit=audit, handler=handler),
        stepper=FakeStepper(
            [AgentStepDecision(action=AgentActionType.FINAL_ANSWER, final_answer="done")]
        ),
        audit=audit,
        config=_config(),
        perf_counter=lambda: 100.0,
    )

    result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.COMPLETED
    assert result.termination_reason is AgentTerminationReason.FINAL_ANSWER
    assert result.final_answer == "done"
    assert result.steps_used == 1
    assert result.tool_calls_used == 0
    assert handler.call_count == 0
    assert result.request_id == "req-1"
    assert result.trace_id == "trace-1"
    assert result.tenant_id == "tenant-1"
    assert result.user_id == "user-1"
    assert audit.events[-1].action == "agent.runtime.run"
    assert audit.events[-1].status is AuditStatus.SUCCESS


@pytest.mark.asyncio
async def test_tool_call_executes_only_through_registry_and_passes_safe_observation() -> None:
    audit = InMemoryAuditPort()
    handler = HandlerProbe()
    stepper = FakeStepper(
        [
            AgentStepDecision.tool_call("demo_tool", {"query": "classified"}),
            AgentStepDecision(action=AgentActionType.FINAL_ANSWER, final_answer="done"),
        ]
    )
    runtime = AgentRuntime(
        registry=_registry(audit=audit, handler=handler),
        stepper=stepper,
        audit=audit,
        config=_config(),
        perf_counter=lambda: 100.0,
    )

    result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.COMPLETED
    assert handler.call_count == 1
    assert handler.context == _context()
    assert result.tool_calls_used == 1
    assert stepper.states[1].observations[0].tool_name == "demo_tool"
    assert stepper.states[1].observations[0].status is ToolInvocationStatus.SUCCESS
    assert stepper.states[1].observations[0].output_keys == ("answer",)
    assert "safe-answer" not in str(result)
    assert "classified" not in str(result)
    assert "classified" not in str(audit.events)


@pytest.mark.asyncio
async def test_max_steps_stops_before_next_stepper_call() -> None:
    stepper = FakeStepper([AgentStepDecision.tool_call("demo_tool", {"query": "one"})])
    handler = HandlerProbe()
    runtime = AgentRuntime(
        registry=_registry(handler=handler),
        stepper=stepper,
        audit=InMemoryAuditPort(),
        config=_config(max_steps=1),
        perf_counter=lambda: 100.0,
    )

    result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.STOPPED
    assert result.termination_reason is AgentTerminationReason.MAX_STEPS_REACHED
    assert result.error_code == MAX_STEPS_REACHED
    assert result.steps_used == 1
    assert result.tool_calls_used == 1
    assert len(stepper.states) == 1
    assert handler.call_count == 1


@pytest.mark.asyncio
async def test_max_tool_calls_stops_before_registry_execute() -> None:
    handler = HandlerProbe()
    runtime = AgentRuntime(
        registry=_registry(handler=handler),
        stepper=FakeStepper([AgentStepDecision.tool_call("demo_tool", {"query": "one"})]),
        audit=InMemoryAuditPort(),
        config=_config(max_tool_calls=0),
        perf_counter=lambda: 100.0,
    )

    result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.STOPPED
    assert result.termination_reason is AgentTerminationReason.MAX_TOOL_CALLS_REACHED
    assert result.error_code == MAX_TOOL_CALLS_REACHED
    assert result.tool_calls_used == 0
    assert handler.call_count == 0


@pytest.mark.asyncio
async def test_timeout_stops_before_next_stepper_or_tool() -> None:
    clock_values = iter([100.0, 100.0, 101.0, 101.0])
    handler = HandlerProbe()
    runtime = AgentRuntime(
        registry=_registry(handler=handler),
        stepper=FakeStepper([AgentStepDecision.tool_call("demo_tool", {"query": "one"})]),
        audit=InMemoryAuditPort(),
        config=_config(timeout_seconds=0.5),
        perf_counter=lambda: next(clock_values),
    )

    result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.STOPPED
    assert result.termination_reason is AgentTerminationReason.AGENT_TIMEOUT
    assert result.error_code == AGENT_TIMEOUT
    assert result.tool_calls_used == 0
    assert handler.call_count == 0


@pytest.mark.asyncio
async def test_repeated_action_detection_stops_before_triggering_tool_call() -> None:
    audit = InMemoryAuditPort()
    handler = HandlerProbe()
    runtime = AgentRuntime(
        registry=_registry(audit=audit, handler=handler),
        stepper=FakeStepper(
            [
                AgentStepDecision.tool_call("demo_tool", {"query": "one", "b": "two"}),
                AgentStepDecision.tool_call("demo_tool", {"b": "two", "query": "one"}),
            ]
        ),
        audit=audit,
        config=_config(repeated_action_threshold=2),
        perf_counter=lambda: 100.0,
    )

    result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.STOPPED
    assert result.termination_reason is AgentTerminationReason.REPEATED_ACTION_DETECTED
    assert result.error_code == REPEATED_ACTION_DETECTED
    assert result.tool_calls_used == 1
    assert handler.call_count == 1
    assert audit.events[-1].action == "agent.runtime.limit"
    assert audit.events[-1].error_code == REPEATED_ACTION_DETECTED
    assert audit.events[-1].metadata["repeated_action_detected"] is True
    assert audit.events[-1].metadata["argument_keys"] == ["b", "query"]
    assert "one" not in str(audit.events[-1].metadata)
    assert "two" not in str(audit.events[-1].metadata)


def test_repeated_action_detector_canonicalizes_argument_order_without_raw_values() -> None:
    detector = RepeatedActionDetector(threshold=2)

    first = detector.observe(tool_name="demo_tool", arguments={"query": "one", "b": "two"})
    second = detector.observe(tool_name="demo_tool", arguments={"b": "two", "query": "one"})

    assert first.triggered is False
    assert second.triggered is True
    assert second.repeat_count == 2
    assert second.metadata["argument_keys"] == ["b", "query"]
    assert "one" not in str(second.metadata)
    assert "two" not in str(second.metadata)


@pytest.mark.asyncio
async def test_unknown_tool_and_permission_denied_terminate_without_looping() -> None:
    unknown_runtime = AgentRuntime(
        registry=ToolRegistry(audit=InMemoryAuditPort()),
        stepper=FakeStepper([AgentStepDecision.tool_call("missing_tool", {"query": "one"})]),
        audit=InMemoryAuditPort(),
        config=_config(),
        perf_counter=lambda: 100.0,
    )

    unknown_result = await unknown_runtime.run(context=_context())

    assert unknown_result.status is AgentRunStatus.FAILED
    assert unknown_result.termination_reason is AgentTerminationReason.AGENT_TOOL_FAILED
    assert unknown_result.error_code == AGENT_TOOL_FAILED
    assert unknown_result.metadata["tool_error_code"] == "TOOL_NOT_REGISTERED"

    denied_runtime = AgentRuntime(
        registry=_registry(),
        stepper=FakeStepper([AgentStepDecision.tool_call("demo_tool", {"query": "one"})]),
        audit=InMemoryAuditPort(),
        config=_config(),
        perf_counter=lambda: 100.0,
    )

    denied_result = await denied_runtime.run(context=_context(permissions=()))

    assert denied_result.status is AgentRunStatus.FAILED
    assert denied_result.termination_reason is AgentTerminationReason.AGENT_TOOL_FAILED
    assert denied_result.metadata["tool_error_code"] == TOOL_PERMISSION_DENIED


@pytest.mark.asyncio
async def test_structured_tool_error_terminates_without_infinite_loop() -> None:
    handler = HandlerProbe(output=StructuredErrorOutput(status="error", error_code="DOMAIN_ERROR"))
    runtime = AgentRuntime(
        registry=_registry(handler=handler, output_schema=StructuredErrorOutput),
        stepper=FakeStepper([AgentStepDecision.tool_call("demo_tool", {"query": "one"})]),
        audit=InMemoryAuditPort(),
        config=_config(),
        perf_counter=lambda: 100.0,
    )

    result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.FAILED
    assert result.termination_reason is AgentTerminationReason.AGENT_TOOL_FAILED
    assert result.error_code == AGENT_TOOL_FAILED
    assert result.metadata["tool_error_code"] == "DOMAIN_ERROR"
    assert result.tool_calls_used == 1


@pytest.mark.asyncio
async def test_stepper_error_is_structured_and_does_not_leak_provider_payload() -> None:
    audit = InMemoryAuditPort()
    runtime = AgentRuntime(
        registry=_registry(),
        stepper=FailingStepper(),
        audit=audit,
        config=_config(),
        perf_counter=lambda: 100.0,
    )

    result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.FAILED
    assert result.termination_reason is AgentTerminationReason.AGENT_STEPPER_FAILED
    assert result.error_code == AGENT_STEPPER_FAILED
    assert "prompt" not in str(result.metadata)
    assert "provider payload" not in str(result.metadata)
    assert audit.events[-1].error_code == AGENT_STEPPER_FAILED


@pytest.mark.asyncio
async def test_runtime_audit_failure_logs_warning_without_faking_runtime_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = AgentRuntime(
        registry=_registry(),
        stepper=FakeStepper(
            [AgentStepDecision(action=AgentActionType.FINAL_ANSWER, final_answer="done")]
        ),
        audit=FailingAuditPort(),
        config=_config(),
        perf_counter=lambda: 100.0,
    )

    with caplog.at_level("WARNING", logger="packages.agent.runtime"):
        result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.COMPLETED
    assert "agent.runtime.audit_failed" in caplog.text
