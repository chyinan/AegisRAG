import asyncio
from collections.abc import Sequence
from typing import Any, cast

import pytest
from pydantic import BaseModel, ConfigDict

from packages.agent.dto import (
    AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION,
    AGENT_FINAL_ANSWER_VALIDATION_FAILED,
    AgentCitationRef,
    FinalAnswerValidationRequest,
    FinalAnswerValidationResult,
    ToolDefinition,
    ToolExecutionResult,
    ToolInvocationStatus,
    ToolRateLimit,
)
from packages.agent.exceptions import TOOL_PERMISSION_DENIED
from packages.agent.final_answer import FINAL_ANSWER_VALIDATION_ACTION, AgentObservationEvidence
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


class SensitiveOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    api_key: str
    content_excerpt: str


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


class MalformedStepper:
    async def next_step(self, state: AgentRuntimeState) -> AgentStepDecision:
        _ = state
        return {"action": "tool_call"}  # type: ignore[return-value]


class CancellableStepper:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def next_step(self, state: AgentRuntimeState) -> AgentStepDecision:
        _ = state
        self.started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        raise AssertionError("stepper should have been cancelled")


class FailingAuditPort:
    async def record(self, event: AuditEvent) -> None:
        _ = event
        raise RuntimeError("audit backend unavailable")


class CancellableHandler:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def __call__(
        self,
        payload: DemoInput,
        context: AuthenticatedRequestContext,
    ) -> DemoOutput:
        _ = payload, context
        self.started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        raise AssertionError("handler should have been cancelled")


class BrokenRegistry(ToolRegistry):
    async def execute(
        self,
        *,
        name: str,
        arguments: object,
        context: AuthenticatedRequestContext,
        agent_run_id: str | None = None,
    ) -> ToolExecutionResult:
        _ = name, arguments, context, agent_run_id
        raise RuntimeError("registry backend token leaked")


class AgentRunIdRecordingRegistry(ToolRegistry):
    def __init__(self) -> None:
        super().__init__(audit=InMemoryAuditPort())
        self.agent_run_ids: list[str | None] = []

    async def execute(
        self,
        *,
        name: str,
        arguments: object,
        context: AuthenticatedRequestContext,
        agent_run_id: str | None = None,
    ) -> ToolExecutionResult:
        _ = name, arguments, context
        self.agent_run_ids.append(agent_run_id)
        return ToolExecutionResult(
            tool_name="demo_tool",
            status=ToolInvocationStatus.SUCCESS,
            output={"answer": "ok"},
            latency_ms=1.0,
            metadata={"status": "success"},
        )


class RagSearchRegistry(ToolRegistry):
    def __init__(self) -> None:
        super().__init__(audit=InMemoryAuditPort())

    async def execute(
        self,
        *,
        name: str,
        arguments: object,
        context: AuthenticatedRequestContext,
        agent_run_id: str | None = None,
    ) -> ToolExecutionResult:
        _ = name, arguments, context, agent_run_id
        return ToolExecutionResult(
            tool_name="rag_search",
            status=ToolInvocationStatus.SUCCESS,
            output={
                "status": "success",
                "result_count": 1,
                "results": [
                    {
                        "document_id": "doc-1",
                        "version_id": "ver-1",
                        "chunk_id": "chunk-1",
                        "source": "policy",
                        "source_uri": "https://example.invalid/policy",
                        "source_type": "markdown",
                        "page_start": 2,
                        "page_end": 3,
                        "title_path": ["Policy"],
                        "score": 0.9,
                        "retrieval_method": "hybrid",
                        "summary": "Policy page 2",
                    }
                ],
            },
            latency_ms=1.0,
            metadata={"status": "success"},
        )


class MalformedRagSearchRegistry(ToolRegistry):
    def __init__(self) -> None:
        super().__init__(audit=InMemoryAuditPort())

    async def execute(
        self,
        *,
        name: str,
        arguments: object,
        context: AuthenticatedRequestContext,
        agent_run_id: str | None = None,
    ) -> ToolExecutionResult:
        _ = name, arguments, context, agent_run_id
        return ToolExecutionResult(
            tool_name="rag_search",
            status=ToolInvocationStatus.SUCCESS,
            output={
                "status": "success",
                "result_count": 1,
                "results": [
                    {
                        "document_id": None,
                        "version_id": ["ver-1"],
                        "chunk_id": {"id": "chunk-1"},
                        "source": "policy",
                        "page_start": 1,
                        "page_end": 1,
                    }
                ],
            },
            latency_ms=1.0,
            metadata={"status": "success"},
        )


class RecordingFinalAnswerValidator:
    def __init__(
        self,
        result: FinalAnswerValidationResult | None = None,
    ) -> None:
        self.result = result
        self.requests: list[FinalAnswerValidationRequest] = []
        self.observation_counts: list[int] = []

    async def validate(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: FinalAnswerValidationRequest,
        observations: Sequence[AgentObservationEvidence],
    ) -> FinalAnswerValidationResult:
        _ = context
        self.requests.append(request)
        self.observation_counts.append(len(observations))
        if self.result is not None:
            return self.result
        return FinalAnswerValidationResult(
            status="valid",
            answer=request.answer,
            citations=request.citations,
            latency_ms=1.0,
            validated_citation_count=len(request.citations),
            metadata={
                "validation_status": "valid",
                "validated_citation_count": len(request.citations),
                "unsupported_citation_count": 0,
                "failed_tool_reference_count": 0,
            },
        )


class RaisingFinalAnswerValidator:
    async def validate(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: FinalAnswerValidationRequest,
        observations: Sequence[AgentObservationEvidence],
    ) -> FinalAnswerValidationResult:
        _ = context, request, observations
        raise RuntimeError("validator leaked raw answer")


class SlowFinalAnswerValidator:
    async def validate(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: FinalAnswerValidationRequest,
        observations: Sequence[AgentObservationEvidence],
    ) -> FinalAnswerValidationResult:
        _ = context, observations
        await asyncio.sleep(0.05)
        return FinalAnswerValidationResult(
            status="valid",
            answer=request.answer,
            citations=request.citations,
            latency_ms=1.0,
            metadata={"validation_status": "valid"},
        )


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
    handler: Any | None = None,
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
async def test_final_answer_is_validated_before_completion() -> None:
    citation = AgentCitationRef(
        document_id="doc-1",
        version_id="ver-1",
        chunk_id="chunk-1",
        source="policy",
        page_start=1,
        page_end=1,
    )
    validator = RecordingFinalAnswerValidator()
    runtime = AgentRuntime(
        registry=_registry(),
        stepper=FakeStepper(
            [
                AgentStepDecision(
                    action=AgentActionType.FINAL_ANSWER,
                    final_answer="done",
                    final_citations=(citation,),
                )
            ]
        ),
        audit=InMemoryAuditPort(),
        config=_config(),
        final_answer_validator=validator,
        agent_run_id="run-1",
        perf_counter=lambda: 100.0,
    )

    result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.COMPLETED
    assert result.final_answer == "done"
    assert result.final_citations == (citation,)
    assert validator.requests[0].agent_run_id == "run-1"
    assert validator.requests[0].citations == (citation,)
    validation_metadata = cast(dict[str, object], result.metadata["final_answer_validation"])
    assert validation_metadata["validation_status"] == "valid"


@pytest.mark.asyncio
async def test_final_answer_validation_failure_blocks_completed_status() -> None:
    validator = RecordingFinalAnswerValidator(
        FinalAnswerValidationResult(
            status="invalid",
            answer=None,
            citations=(),
            latency_ms=1.0,
            error_code=AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION,
            unsupported_citation_count=1,
            metadata={
                "validation_status": "invalid",
                "error_code": AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION,
                "validated_citation_count": 0,
                "unsupported_citation_count": 1,
                "failed_tool_reference_count": 0,
            },
        )
    )
    runtime = AgentRuntime(
        registry=_registry(),
        stepper=FakeStepper(
            [AgentStepDecision(action=AgentActionType.FINAL_ANSWER, final_answer="done")]
        ),
        audit=InMemoryAuditPort(),
        config=_config(),
        final_answer_validator=validator,
        perf_counter=lambda: 100.0,
    )

    result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.FAILED
    assert result.final_answer is None
    assert result.error_code == AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION
    assert result.termination_reason is AgentTerminationReason.FINAL_ANSWER_VALIDATION_FAILED
    assert "done" not in str(result.metadata)


@pytest.mark.asyncio
async def test_final_answer_validator_exception_maps_to_structured_validation_failure() -> None:
    audit = InMemoryAuditPort()
    runtime = AgentRuntime(
        registry=_registry(),
        stepper=FakeStepper(
            [AgentStepDecision(action=AgentActionType.FINAL_ANSWER, final_answer="done")]
        ),
        audit=audit,
        config=_config(),
        final_answer_validator=RaisingFinalAnswerValidator(),
        perf_counter=lambda: 100.0,
    )

    result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.FAILED
    assert result.error_code == AGENT_FINAL_ANSWER_VALIDATION_FAILED
    assert result.termination_reason is AgentTerminationReason.FINAL_ANSWER_VALIDATION_FAILED
    assert "validator leaked raw answer" not in str(result)
    assert any(event.action == FINAL_ANSWER_VALIDATION_ACTION for event in audit.events)


@pytest.mark.asyncio
async def test_blank_final_answer_maps_to_structured_validation_failure() -> None:
    audit = InMemoryAuditPort()
    runtime = AgentRuntime(
        registry=_registry(),
        stepper=FakeStepper(
            [AgentStepDecision(action=AgentActionType.FINAL_ANSWER, final_answer="  ")]
        ),
        audit=audit,
        config=_config(),
        perf_counter=lambda: 100.0,
    )

    result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.FAILED
    assert result.error_code == AGENT_FINAL_ANSWER_VALIDATION_FAILED
    assert result.termination_reason is AgentTerminationReason.FINAL_ANSWER_VALIDATION_FAILED
    assert any(event.action == FINAL_ANSWER_VALIDATION_ACTION for event in audit.events)


@pytest.mark.asyncio
async def test_final_answer_validation_timeout_respects_global_agent_deadline() -> None:
    clock_values = iter([100.0, 100.0, 100.0, 100.001, 100.001, 100.001, 100.001])
    audit = InMemoryAuditPort()
    runtime = AgentRuntime(
        registry=_registry(),
        stepper=FakeStepper(
            [AgentStepDecision(action=AgentActionType.FINAL_ANSWER, final_answer="done")]
        ),
        audit=audit,
        config=_config(timeout_seconds=0.001),
        final_answer_validator=SlowFinalAnswerValidator(),
        perf_counter=lambda: next(clock_values),
    )

    result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.STOPPED
    assert result.termination_reason is AgentTerminationReason.AGENT_TIMEOUT
    assert result.error_code == AGENT_TIMEOUT
    assert any(event.action == FINAL_ANSWER_VALIDATION_ACTION for event in audit.events)


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
async def test_rag_search_success_observation_exposes_only_citation_evidence() -> None:
    citation = AgentCitationRef(
        document_id="doc-1",
        version_id="ver-1",
        chunk_id="chunk-1",
        source="policy",
        page_start=2,
        page_end=3,
        tool_name="rag_search",
    )
    validator = RecordingFinalAnswerValidator()
    stepper = FakeStepper(
        [
            AgentStepDecision.tool_call("rag_search", {"query": "policy"}),
            AgentStepDecision(
                action=AgentActionType.FINAL_ANSWER,
                final_answer="done",
                final_citations=(citation,),
            ),
        ]
    )
    runtime = AgentRuntime(
        registry=RagSearchRegistry(),
        stepper=stepper,
        audit=InMemoryAuditPort(),
        config=_config(),
        final_answer_validator=validator,
        perf_counter=lambda: 100.0,
    )

    result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.COMPLETED
    observation = stepper.states[1].observations[0]
    assert observation.tool_name == "rag_search"
    assert observation.citation_refs == (citation,)
    assert observation.result_status == "success"
    assert observation.error_code is None
    assert observation.metadata["citation_ref_count"] == 1
    assert "Policy page 2" not in str(observation)


@pytest.mark.asyncio
async def test_rag_search_observation_drops_malformed_citation_identifier_values() -> None:
    stepper = FakeStepper(
        [
            AgentStepDecision.tool_call("rag_search", {"query": "policy"}),
            AgentStepDecision(action=AgentActionType.FINAL_ANSWER, final_answer="done"),
        ]
    )
    runtime = AgentRuntime(
        registry=MalformedRagSearchRegistry(),
        stepper=stepper,
        audit=InMemoryAuditPort(),
        config=_config(),
        final_answer_validator=RecordingFinalAnswerValidator(),
        perf_counter=lambda: 100.0,
    )

    result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.COMPLETED
    assert stepper.states[1].observations[0].citation_refs == ()
    assert "['ver-1']" not in str(result.observations)


@pytest.mark.asyncio
async def test_runtime_passes_service_created_agent_run_id_to_registry() -> None:
    registry = AgentRunIdRecordingRegistry()
    runtime = AgentRuntime(
        registry=registry,
        stepper=FakeStepper(
            [
                AgentStepDecision.tool_call("demo_tool", {"query": "classified"}),
                AgentStepDecision(action=AgentActionType.FINAL_ANSWER, final_answer="done"),
            ]
        ),
        audit=InMemoryAuditPort(),
        config=_config(),
        agent_run_id="run-1",
        perf_counter=lambda: 100.0,
    )

    result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.COMPLETED
    assert registry.agent_run_ids == ["run-1"]


@pytest.mark.asyncio
async def test_multiple_successful_tool_calls_are_carried_through_runtime_state() -> None:
    handler = HandlerProbe()
    stepper = FakeStepper(
        [
            AgentStepDecision.tool_call("demo_tool", {"query": "one"}),
            AgentStepDecision.tool_call("demo_tool", {"query": "two"}),
            AgentStepDecision(action=AgentActionType.FINAL_ANSWER, final_answer="done"),
        ]
    )
    runtime = AgentRuntime(
        registry=_registry(handler=handler),
        stepper=stepper,
        audit=InMemoryAuditPort(),
        config=_config(max_tool_calls=3, repeated_action_threshold=3),
        perf_counter=lambda: 100.0,
    )

    result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.COMPLETED
    assert result.steps_used == 3
    assert result.tool_calls_used == 2
    assert handler.call_count == 2
    assert len(stepper.states) == 3
    assert len(stepper.states[1].observations) == 1
    assert len(stepper.states[2].observations) == 2


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
async def test_external_cancellation_cancels_in_flight_stepper() -> None:
    stepper = CancellableStepper()
    runtime = AgentRuntime(
        registry=_registry(),
        stepper=stepper,
        audit=InMemoryAuditPort(),
        config=_config(timeout_seconds=10.0),
        perf_counter=lambda: 100.0,
    )

    task = asyncio.create_task(runtime.run(context=_context()))
    await stepper.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert stepper.cancelled.is_set()


@pytest.mark.asyncio
async def test_tool_stage_timeout_records_safe_attempted_tool_context() -> None:
    handler = CancellableHandler()
    runtime = AgentRuntime(
        registry=_registry(handler=handler),
        stepper=FakeStepper([AgentStepDecision.tool_call("demo_tool", {"query": "one"})]),
        audit=InMemoryAuditPort(),
        config=_config(timeout_seconds=0.01),
    )

    result = await runtime.run(context=_context())
    await asyncio.wait_for(handler.cancelled.wait(), timeout=1.0)

    assert result.status is AgentRunStatus.STOPPED
    assert result.termination_reason is AgentTerminationReason.AGENT_TIMEOUT
    assert result.metadata["tool_name"] == "demo_tool"
    assert result.metadata["argument_keys"] == ["query"]
    assert isinstance(result.metadata["action_hash"], str)
    assert "one" not in str(result.metadata)


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
async def test_unexpected_registry_error_is_structured_without_leaking_exception() -> None:
    runtime = AgentRuntime(
        registry=BrokenRegistry(audit=InMemoryAuditPort()),
        stepper=FakeStepper([AgentStepDecision.tool_call("demo_tool", {"query": "one"})]),
        audit=InMemoryAuditPort(),
        config=_config(),
        perf_counter=lambda: 100.0,
    )

    result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.FAILED
    assert result.termination_reason is AgentTerminationReason.AGENT_TOOL_FAILED
    assert result.metadata["tool_error_code"] == "TOOL_EXECUTION_FAILED"
    assert "registry backend token leaked" not in str(result)


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
async def test_malformed_stepper_decision_is_structured_stepper_failure() -> None:
    audit = InMemoryAuditPort()
    runtime = AgentRuntime(
        registry=_registry(),
        stepper=MalformedStepper(),
        audit=audit,
        config=_config(),
        perf_counter=lambda: 100.0,
    )

    result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.FAILED
    assert result.termination_reason is AgentTerminationReason.AGENT_STEPPER_FAILED
    assert result.error_code == AGENT_STEPPER_FAILED
    assert audit.events[-1].error_code == AGENT_STEPPER_FAILED


@pytest.mark.asyncio
async def test_observation_summary_redacts_sensitive_output_key_names() -> None:
    handler = HandlerProbe(
        output=SensitiveOutput(
            status="success",
            api_key="secret-value",
            content_excerpt="classified content",
        )
    )
    stepper = FakeStepper(
        [
            AgentStepDecision.tool_call("demo_tool", {"query": "one"}),
            AgentStepDecision(action=AgentActionType.FINAL_ANSWER, final_answer="done"),
        ]
    )
    runtime = AgentRuntime(
        registry=_registry(handler=handler, output_schema=SensitiveOutput),
        stepper=stepper,
        audit=InMemoryAuditPort(),
        config=_config(),
        perf_counter=lambda: 100.0,
    )

    result = await runtime.run(context=_context())

    assert result.observations[0].output_keys == ("redacted_key", "status")
    assert stepper.states[1].observations[0].metadata["output_keys"] == [
        "redacted_key",
        "status",
    ]
    assert "api_key" not in str(result.observations)
    assert "content_excerpt" not in str(result.observations)


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
