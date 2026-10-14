"""The optional graph scheduler must preserve the native runtime's public contract.

Only the LangGraph parameter requires its optional dependency. The same assertions
therefore continue protecting the native backend in the default installation.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal

import pytest

from packages.agent.dto import (
    AGENT_FINAL_ANSWER_FAILED_TOOL_REFERENCE,
    AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION,
    AGENT_FINAL_ANSWER_VALIDATION_FAILED,
    AgentCitationRef,
    FinalAnswerValidationRequest,
    FinalAnswerValidationResult,
    ToolExecutionResult,
    ToolInvocationStatus,
)
from packages.agent.exceptions import (
    TOOL_HANDLER_FAILED,
    TOOL_NOT_REGISTERED,
    TOOL_PERMISSION_DENIED,
)
from packages.agent.final_answer import (
    FINAL_ANSWER_VALIDATION_ACTION,
    AgentObservationEvidence,
    StrictFinalAnswerValidator,
)
from packages.agent.registry import ToolRegistry
from packages.agent.runtime import (
    AGENT_STEPPER_FAILED,
    AGENT_TIMEOUT,
    AGENT_TOOL_FAILED,
    MAX_STEPS_REACHED,
    MAX_TOOL_CALLS_REACHED,
    REPEATED_ACTION_DETECTED,
    TOOL_EXECUTION_FAILED,
    AgentActionType,
    AgentRunResult,
    AgentRunStatus,
    AgentRuntime,
    AgentRuntimeState,
    AgentStepDecision,
    AgentToolEvent,
)
from packages.common.audit import AuditEvent, AuditStatus, InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.common.tenant_context import current_tenant_id, tenant_scope
from tests.unit.agent.test_runtime import (
    AgentRunIdRecordingRegistry,
    BrokenRegistry,
    DemoInput,
    DemoOutput,
    FailingAuditPort,
    FailingStepper,
    FakeStepper,
    HandlerProbe,
    MalformedStepper,
    RagSearchRegistry,
    RaisingFinalAnswerValidator,
    RecordingAgentEventSink,
    RecordingFinalAnswerValidator,
    StructuredErrorOutput,
    _config,
    _context,
    _registry,
)


@pytest.fixture(params=("native", "langgraph"))
def runtime_type(request: pytest.FixtureRequest) -> type[AgentRuntime]:
    if request.param == "native":
        return AgentRuntime
    pytest.importorskip("langgraph.graph", reason="Install the optional langgraph extra")
    from packages.agent.adapters.langgraph import LangGraphRuntime

    return LangGraphRuntime


def _final(answer: str = "Completed safely.", **kwargs: object) -> AgentStepDecision:
    return AgentStepDecision.model_validate(
        {"action": AgentActionType.FINAL_ANSWER, "final_answer": answer, **kwargs}
    )


def _tool(query: str = "private-query") -> AgentStepDecision:
    return AgentStepDecision.tool_call("demo_tool", {"query": query, "b": "private-value"})


def _observable_payload(
    result: AgentRunResult,
    audit: InMemoryAuditPort,
    sink: RecordingAgentEventSink,
) -> str:
    return json.dumps(
        {
            "result": result.model_dump(mode="json"),
            "audit": [event.model_dump(mode="json") for event in audit.events],
            "events": [event.model_dump(mode="json") for event in sink.events],
        }
    )


async def test_final_answer_can_complete_on_last_step_with_no_tool_budget(
    runtime_type: type[AgentRuntime],
) -> None:
    audit, sink, handler = InMemoryAuditPort(), RecordingAgentEventSink(), HandlerProbe()
    result = await runtime_type(
        registry=_registry(handler=handler),
        stepper=FakeStepper([_final()]),
        audit=audit,
        event_sink=sink,
        config=_config(max_steps=1, max_tool_calls=0, repeated_action_threshold=1),
    ).run(context=_context())

    assert result.status is AgentRunStatus.COMPLETED
    assert result.error_code is None
    assert result.final_answer == "Completed safely."
    assert (result.steps_used, result.tool_calls_used) == (1, 0)
    assert handler.call_count == 0
    assert sink.events == []
    assert [event.action for event in audit.events] == [
        FINAL_ANSWER_VALIDATION_ACTION,
        "agent.runtime.run",
    ]


@pytest.mark.parametrize(
    ("limits", "reason", "steps", "calls", "event_types"),
    [
        ({"max_steps": 1}, MAX_STEPS_REACHED, 1, 1, ["tool_call", "tool_result"]),
        ({"max_tool_calls": 0}, MAX_TOOL_CALLS_REACHED, 1, 0, ["tool_result"]),
        (
            {"max_tool_calls": 1},
            MAX_TOOL_CALLS_REACHED,
            2,
            1,
            ["tool_call", "tool_result", "tool_result"],
        ),
        (
            {"repeated_action_threshold": 1},
            REPEATED_ACTION_DETECTED,
            1,
            0,
            ["tool_result"],
        ),
        (
            {"repeated_action_threshold": 2},
            REPEATED_ACTION_DETECTED,
            2,
            1,
            ["tool_call", "tool_result", "tool_result"],
        ),
        (
            {"max_steps": 1, "max_tool_calls": 1},
            MAX_STEPS_REACHED,
            1,
            1,
            ["tool_call", "tool_result"],
        ),
    ],
    ids=("steps", "zero-tools", "tools", "first-repeat", "repeat", "step-precedence"),
)
async def test_budget_guards_precede_execution_and_preserve_counts(
    runtime_type: type[AgentRuntime],
    limits: dict[str, int],
    reason: str,
    steps: int,
    calls: int,
    event_types: list[str],
) -> None:
    audit, sink, handler = InMemoryAuditPort(), RecordingAgentEventSink(), HandlerProbe()
    stepper = FakeStepper(
        [
            _tool(),
            AgentStepDecision.tool_call(
                "demo_tool", {"b": "private-value", "query": "private-query"}
            ),
            _final(),
        ]
    )
    runtime = runtime_type(
        registry=_registry(audit=audit, handler=handler),
        stepper=stepper,
        audit=audit,
        event_sink=sink,
        config=_config(**limits),
        perf_counter=lambda: 100.0,
    )

    result = await runtime.run(context=_context())

    assert result.status is AgentRunStatus.STOPPED
    assert result.termination_reason.value == result.error_code == reason
    assert (result.steps_used, result.tool_calls_used) == (steps, calls)
    assert len(stepper.states) == steps
    assert handler.call_count == len(result.observations) == calls
    assert [event.event for event in sink.events] == event_types
    if reason != MAX_STEPS_REACHED:
        assert sink.events[-1].status == "error"
        assert sink.events[-1].metadata["error_code"] == reason
    assert audit.events[-1].action == "agent.runtime.limit"
    assert audit.events[-1].status is AuditStatus.DENIED
    assert audit.events[-1].error_code == reason
    payload = _observable_payload(result, audit, sink)
    assert "private-query" not in payload
    assert "private-value" not in payload


async def test_framework_recursion_limit_does_not_replace_agent_step_budget(
    runtime_type: type[AgentRuntime],
) -> None:
    # More graph transitions than LangGraph's default recursion limit of 25.
    registry = AgentRunIdRecordingRegistry()
    stepper = FakeStepper([_tool(str(index)) for index in range(40)])
    runtime = runtime_type(
        registry=registry,
        stepper=stepper,
        audit=None,
        agent_run_id="run-long",
        config=_config(max_steps=40, max_tool_calls=40),
        perf_counter=lambda: 100.0,
    )

    result = await runtime.run(context=_context())

    assert result.error_code == MAX_STEPS_REACHED
    assert (result.steps_used, result.tool_calls_used) == (40, 40)
    assert registry.agent_run_ids == ["run-long"] * 40


@pytest.mark.parametrize("expiration", ("before-decision", "before-tool"))
async def test_global_deadline_is_checked_before_scheduling_work(
    runtime_type: type[AgentRuntime], expiration: str
) -> None:
    now = [100.0]

    class DeadlineStepper(FakeStepper):
        async def next_step(self, state: AgentRuntimeState) -> AgentStepDecision:
            decision = await super().next_step(state)
            now[0] += 2.0
            return decision

    clock_calls = 0

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        if expiration == "before-decision" and clock_calls > 1:
            return 102.0
        return now[0]

    handler, sink = HandlerProbe(), RecordingAgentEventSink()
    stepper = DeadlineStepper([_tool()])
    result = await runtime_type(
        registry=_registry(handler=handler),
        stepper=stepper,
        audit=None,
        event_sink=sink,
        config=_config(timeout_seconds=1.0),
        perf_counter=clock,
    ).run(context=_context())

    assert result.status is AgentRunStatus.STOPPED
    assert result.error_code == AGENT_TIMEOUT
    assert result.steps_used == (0 if expiration == "before-decision" else 1)
    assert result.tool_calls_used == handler.call_count == 0
    assert sink.events == []


@pytest.mark.parametrize("stepper_type", (FailingStepper, MalformedStepper))
async def test_stepper_exceptions_and_malformed_decisions_fail_closed(
    runtime_type: type[AgentRuntime],
    stepper_type: type[FailingStepper] | type[MalformedStepper],
) -> None:
    audit, sink, handler = InMemoryAuditPort(), RecordingAgentEventSink(), HandlerProbe()
    result = await runtime_type(
        registry=_registry(handler=handler),
        stepper=stepper_type(),
        audit=audit,
        event_sink=sink,
        config=_config(),
    ).run(context=_context())

    assert result.status is AgentRunStatus.FAILED
    assert result.error_code == AGENT_STEPPER_FAILED
    assert (result.steps_used, result.tool_calls_used) == (0, 0)
    assert handler.call_count == 0
    assert sink.events == []
    assert audit.events[-1].error_code == AGENT_STEPPER_FAILED
    assert "provider payload" not in _observable_payload(result, audit, sink)


@pytest.mark.parametrize("failure", ("registry", "handler", "structured-output"))
async def test_tool_failures_are_structured_without_raw_exceptions(
    runtime_type: type[AgentRuntime], failure: str
) -> None:
    audit, sink = InMemoryAuditPort(), RecordingAgentEventSink()

    async def broken_handler(
        payload: DemoInput, context: AuthenticatedRequestContext
    ) -> DemoOutput:
        raise RuntimeError("confidential-handler-payload")

    expected_code = {
        "registry": TOOL_EXECUTION_FAILED,
        "handler": TOOL_HANDLER_FAILED,
        "structured-output": "DOMAIN_ERROR",
    }[failure]
    registry: ToolRegistry
    if failure == "registry":
        registry = BrokenRegistry(audit=audit)
    elif failure == "handler":
        registry = _registry(audit=audit, handler=broken_handler)
    else:
        registry = _registry(
            audit=audit,
            handler=HandlerProbe(
                output=StructuredErrorOutput(status="error", error_code="DOMAIN_ERROR")
            ),
            output_schema=StructuredErrorOutput,
        )
    stepper = FakeStepper([_tool(), _final("Must never be returned.")])
    result = await runtime_type(
        registry=registry,
        stepper=stepper,
        audit=audit,
        event_sink=sink,
        config=_config(),
    ).run(context=_context())

    assert result.status is AgentRunStatus.FAILED
    assert result.error_code == AGENT_TOOL_FAILED
    assert result.metadata["tool_error_code"] == expected_code
    assert (result.steps_used, result.tool_calls_used) == (1, 1)
    assert len(stepper.states) == 1
    assert result.final_answer is None
    assert [event.event for event in sink.events] == ["tool_call", "tool_result"]
    assert sink.events[-1].metadata["error_code"] == expected_code
    payload = _observable_payload(result, audit, sink)
    assert "confidential-handler-payload" not in payload
    assert "registry backend token leaked" not in payload


@pytest.mark.parametrize("denial", ("permission", "unregistered"))
async def test_real_registry_acl_and_registration_cannot_be_bypassed(
    runtime_type: type[AgentRuntime], denial: str
) -> None:
    audit, sink, handler = InMemoryAuditPort(), RecordingAgentEventSink(), HandlerProbe()
    decision = _tool() if denial == "permission" else AgentStepDecision.tool_call("missing", {})
    context = _context(permissions=()) if denial == "permission" else _context()
    expected = TOOL_PERMISSION_DENIED if denial == "permission" else TOOL_NOT_REGISTERED
    result = await runtime_type(
        registry=_registry(audit=audit, handler=handler),
        stepper=FakeStepper([decision]),
        audit=audit,
        event_sink=sink,
        config=_config(),
    ).run(context=context)

    assert result.error_code == AGENT_TOOL_FAILED
    assert result.metadata["tool_error_code"] == expected
    assert (result.steps_used, result.tool_calls_used) == (1, 1)
    assert handler.call_count == 0
    assert result.observations == ()
    assert [event.event for event in sink.events] == ["tool_call", "tool_result"]
    assert sink.events[-1].status == "error"
    assert sink.events[-1].metadata["error_code"] == expected
    assert any(
        event.status is AuditStatus.DENIED and event.error_code == expected
        for event in audit.events
    )


@pytest.mark.parametrize(
    ("overrides", "expected_error"),
    [
        ({}, None),
        ({"document_id": "doc-other"}, AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION),
        ({"version_id": "ver-other"}, AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION),
        ({"chunk_id": "chunk-other"}, AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION),
        ({"source": "invented"}, AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION),
        ({"page_start": 1}, AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION),
        ({"tool_name": "demo_tool"}, AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION),
        ({"observation_index": 9}, AGENT_FINAL_ANSWER_FAILED_TOOL_REFERENCE),
    ],
    ids=("valid", "document", "version", "chunk", "source", "page", "tool", "index"),
)
async def test_final_citations_must_match_successful_run_evidence(
    runtime_type: type[AgentRuntime],
    overrides: dict[str, object],
    expected_error: str | None,
) -> None:
    citation = AgentCitationRef.model_validate(
        {
            "document_id": "doc-1",
            "version_id": "ver-1",
            "chunk_id": "chunk-1",
            "source": "policy",
            "page_start": 2,
            "page_end": 3,
            "tool_name": "rag_search",
            "observation_index": 0,
            **overrides,
        }
    )
    audit = InMemoryAuditPort()
    result = await runtime_type(
        registry=RagSearchRegistry(),
        stepper=FakeStepper(
            [
                AgentStepDecision.tool_call("rag_search", {"query": "policy"}),
                _final("The policy requires approval.", final_citations=(citation,)),
            ]
        ),
        audit=audit,
        agent_run_id="run-citation",
        config=_config(),
    ).run(context=_context())

    assert (result.steps_used, result.tool_calls_used) == (2, 1)
    assert result.error_code == expected_error
    assert result.status is (
        AgentRunStatus.COMPLETED if expected_error is None else AgentRunStatus.FAILED
    )
    assert result.final_citations == ((citation,) if expected_error is None else ())
    assert (result.final_answer is not None) == (expected_error is None)
    assert [event.action for event in audit.events] == [
        FINAL_ANSWER_VALIDATION_ACTION,
        "agent.runtime.run",
    ]
    assert audit.events[0].resource.id == "run-citation"
    assert "The policy requires approval." not in str(audit.events)


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        ("   ", AGENT_FINAL_ANSWER_VALIDATION_FAILED),
        ("Approved according to [doc-1/chunk-1].", AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION),
        ("The policy on page 2 approves this.", AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION),
    ],
)
async def test_empty_answers_and_unstructured_source_claims_are_rejected(
    runtime_type: type[AgentRuntime], answer: str, error: str
) -> None:
    result = await runtime_type(
        registry=_registry(),
        stepper=FakeStepper([_final(answer)]),
        audit=None,
        config=_config(),
    ).run(context=_context())

    assert result.status is AgentRunStatus.FAILED
    assert result.error_code == error
    assert (result.steps_used, result.tool_calls_used) == (1, 0)
    assert result.final_answer is None


@pytest.mark.parametrize("status", ("valid", "degraded"))
async def test_validator_controls_the_final_answer_and_receives_observations(
    runtime_type: type[AgentRuntime], status: Literal["valid", "degraded"]
) -> None:
    validator = RecordingFinalAnswerValidator(
        FinalAnswerValidationResult(status=status, answer="Reviewed answer.", latency_ms=0.0)
    )
    result = await runtime_type(
        registry=_registry(),
        stepper=FakeStepper([_tool(), _final("Unreviewed answer.")]),
        audit=None,
        final_answer_validator=validator,
        agent_run_id="run-reviewed",
        config=_config(),
    ).run(context=_context())

    assert result.status is AgentRunStatus.COMPLETED
    assert result.final_answer == "Reviewed answer."
    assert validator.observation_counts == [1]
    assert validator.requests[0].agent_run_id == "run-reviewed"
    assert validator.requests[0].answer == "Unreviewed answer."


async def test_validator_exception_is_a_structured_failure(
    runtime_type: type[AgentRuntime],
) -> None:
    audit, sink = InMemoryAuditPort(), RecordingAgentEventSink()
    result = await runtime_type(
        registry=_registry(),
        stepper=FakeStepper([_final()]),
        audit=audit,
        event_sink=sink,
        final_answer_validator=RaisingFinalAnswerValidator(),
        config=_config(),
    ).run(context=_context())

    assert result.status is AgentRunStatus.FAILED
    assert result.error_code == AGENT_FINAL_ANSWER_VALIDATION_FAILED
    assert result.final_answer is None
    assert [event.action for event in audit.events] == [
        FINAL_ANSWER_VALIDATION_ACTION,
        "agent.runtime.run",
    ]
    assert "validator leaked raw answer" not in _observable_payload(result, audit, sink)


async def test_previous_run_citations_are_not_evidence_for_the_next_run(
    runtime_type: type[AgentRuntime],
) -> None:
    citation = AgentCitationRef(
        document_id="doc-1",
        version_id="ver-1",
        chunk_id="chunk-1",
        source="policy",
        page_start=2,
        page_end=3,
        tool_name="rag_search",
    )
    stepper = FakeStepper(
        [
            AgentStepDecision.tool_call("rag_search", {"query": "policy"}),
            _final(final_citations=(citation,)),
            _final(final_citations=(citation,)),
        ]
    )
    runtime = runtime_type(
        registry=RagSearchRegistry(), stepper=stepper, audit=None, config=_config()
    )

    first = await runtime.run(context=_context())
    second = await runtime.run(context=_context())

    assert first.status is AgentRunStatus.COMPLETED
    assert first.final_citations == (citation,)
    assert second.status is AgentRunStatus.FAILED
    assert second.error_code == AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION
    assert (second.steps_used, second.tool_calls_used) == (1, 0)
    assert second.observations == ()
    assert second.final_citations == ()


async def test_guard_audit_redacts_untrusted_tool_and_argument_names(
    runtime_type: type[AgentRuntime],
) -> None:
    audit, sink = InMemoryAuditPort(), RecordingAgentEventSink()
    result = await runtime_type(
        registry=_registry(),
        stepper=FakeStepper(
            [
                AgentStepDecision.tool_call(
                    "https://private-tool.example/secret",
                    {"api_key": "private-key-value", "/home/private-file": "private-file-value"},
                )
            ]
        ),
        audit=audit,
        event_sink=sink,
        config=_config(max_tool_calls=0),
    ).run(context=_context())

    assert result.error_code == MAX_TOOL_CALLS_REACHED
    assert result.metadata["tool_name"] == "unknown_tool"
    assert result.metadata["argument_keys"] == ["redacted_key"]
    assert sink.events[0].tool_name == "unknown_tool"
    payload = _observable_payload(result, audit, sink)
    for private in (
        "https://private-tool.example/secret",
        "api_key",
        "/home/private-file",
        "private-key-value",
        "private-file-value",
    ):
        assert private not in payload


class _Gate:
    """A deterministic async boundary, optionally refusing its first cancellation."""

    def __init__(self, *, stubborn: bool = False, late_failure: bool = False) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()
        self.stubborn = stubborn
        self.late_failure = late_failure

    async def wait(self) -> None:
        self.started.set()
        try:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                if not self.stubborn:
                    raise
                await self.release.wait()
            if self.late_failure:
                raise RuntimeError("late-confidential-failure")
        finally:
            self.finished.set()


class _BlockingStepper:
    def __init__(self, gate: _Gate) -> None:
        self.gate = gate

    async def next_step(self, state: AgentRuntimeState) -> AgentStepDecision:
        await self.gate.wait()
        return _final("Late answer.")


class _BlockingRegistry(ToolRegistry):
    def __init__(self, gate: _Gate) -> None:
        super().__init__(audit=InMemoryAuditPort())
        self.gate = gate

    async def execute(
        self,
        *,
        name: str,
        arguments: object,
        context: AuthenticatedRequestContext,
        agent_run_id: str | None = None,
    ) -> ToolExecutionResult:
        await self.gate.wait()
        return ToolExecutionResult(
            tool_name=name, status=ToolInvocationStatus.SUCCESS, latency_ms=0.0
        )


class _BlockingValidator:
    def __init__(self, gate: _Gate) -> None:
        self.gate = gate

    async def validate(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: FinalAnswerValidationRequest,
        observations: Sequence[AgentObservationEvidence],
    ) -> FinalAnswerValidationResult:
        await self.gate.wait()
        return FinalAnswerValidationResult(status="valid", answer=request.answer, latency_ms=0.0)


def _blocked_runtime(
    runtime_type: type[AgentRuntime],
    *,
    stage: str,
    gate: _Gate,
    audit: InMemoryAuditPort,
    sink: RecordingAgentEventSink,
    timeout: float,
) -> AgentRuntime:
    return runtime_type(
        registry=_BlockingRegistry(gate) if stage == "tool" else _registry(),
        stepper=(
            _BlockingStepper(gate)
            if stage == "decision"
            else FakeStepper([_tool()] if stage == "tool" else [_final()])
        ),
        final_answer_validator=_BlockingValidator(gate) if stage == "validation" else None,
        audit=audit,
        event_sink=sink,
        config=_config(timeout_seconds=timeout),
        perf_counter=lambda: 100.0,
    )


@pytest.mark.parametrize("stage", ("decision", "tool", "validation"))
@pytest.mark.parametrize("late_failure", (False, True), ids=("late-result", "late-error"))
async def test_timeout_ignores_late_results_and_errors_from_cancellation_resistant_work(
    runtime_type: type[AgentRuntime], stage: str, late_failure: bool
) -> None:
    gate = _Gate(stubborn=True, late_failure=late_failure)
    audit, sink = InMemoryAuditPort(), RecordingAgentEventSink()
    task = asyncio.create_task(
        _blocked_runtime(
            runtime_type, stage=stage, gate=gate, audit=audit, sink=sink, timeout=0.1
        ).run(context=_context())
    )
    try:
        await asyncio.wait_for(gate.started.wait(), timeout=2.0)
        result = await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
        assert gate.cancelled.is_set()
        assert not gate.finished.is_set()
        assert result.status is AgentRunStatus.STOPPED
        assert result.error_code == AGENT_TIMEOUT
        assert result.steps_used == (0 if stage == "decision" else 1)
        assert result.tool_calls_used == (1 if stage == "tool" else 0)
        assert result.final_answer is None
        assert result.observations == ()
        if stage == "tool":
            assert [event.event for event in sink.events] == ["tool_call", "tool_result"]
            assert sink.events[-1].metadata["error_code"] == AGENT_TIMEOUT
        before = _observable_payload(result, audit, sink)
        gate.release.set()
        await asyncio.wait_for(gate.finished.wait(), timeout=2.0)
        await asyncio.sleep(0)  # Let the detached task's exception consumer run.
        assert _observable_payload(result, audit, sink) == before
        assert "Late answer." not in before
        assert "late-confidential-failure" not in before
    finally:
        gate.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("stage", ("decision", "tool", "validation"))
async def test_caller_cancellation_propagates_to_every_in_flight_stage(
    runtime_type: type[AgentRuntime], stage: str
) -> None:
    gate = _Gate()
    audit, sink = InMemoryAuditPort(), RecordingAgentEventSink()
    task = asyncio.create_task(
        _blocked_runtime(
            runtime_type, stage=stage, gate=gate, audit=audit, sink=sink, timeout=10.0
        ).run(context=_context())
    )
    try:
        await asyncio.wait_for(gate.started.wait(), timeout=2.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2.0)
        assert gate.cancelled.is_set()
        assert gate.finished.is_set()
        assert not any(event.action == "agent.runtime.run" for event in audit.events)
        assert not any(event.event == "tool_result" for event in sink.events)
    finally:
        gate.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class _PrivateOutputRegistry(AgentRunIdRecordingRegistry):
    async def execute(
        self,
        *,
        name: str,
        arguments: object,
        context: AuthenticatedRequestContext,
        agent_run_id: str | None = None,
    ) -> ToolExecutionResult:
        self.agent_run_ids.append(agent_run_id)
        return ToolExecutionResult(
            tool_name=name,
            status=ToolInvocationStatus.SUCCESS,
            output={
                "status": "success",
                "api_key": "private-key-value",
                "content_excerpt": "private-document-body",
                "result_count": 1,
            },
            prompt_context={"excerpt": "private-ephemeral-context"},
            latency_ms=1.0,
        )


async def test_events_are_ordered_correlated_and_never_serialize_raw_tool_data(
    runtime_type: type[AgentRuntime],
) -> None:
    audit, sink, registry = InMemoryAuditPort(), RecordingAgentEventSink(), _PrivateOutputRegistry()
    stepper = FakeStepper([_tool(), _tool("second-private-query"), _final()])
    result = await runtime_type(
        registry=registry,
        stepper=stepper,
        audit=audit,
        event_sink=sink,
        agent_run_id="run-events",
        config=_config(input_text="private-input-text"),
    ).run(context=_context())

    assert result.status is AgentRunStatus.COMPLETED
    assert [event.event for event in sink.events] == [
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
    ]
    assert [event.tool_call_id for event in sink.events] == [
        "run-events-step-1-tool-1",
        "run-events-step-1-tool-1",
        "run-events-step-2-tool-2",
        "run-events-step-2-tool-2",
    ]
    assert all(event.request_id == "req-1" and event.trace_id == "trace-1" for event in sink.events)
    assert registry.agent_run_ids == ["run-events", "run-events"]
    assert [len(state.observations) for state in stepper.states] == [0, 1, 2]
    assert stepper.states[0].input_text == "private-input-text"
    assert stepper.states[1].observations[0].prompt_context == {
        "excerpt": "private-ephemeral-context"
    }
    assert result.observations[0].output_keys == ("redacted_key", "result_count", "status")
    payload = _observable_payload(result, audit, sink)
    for private in (
        "private-query",
        "second-private-query",
        "private-key-value",
        "private-document-body",
        "private-ephemeral-context",
        "private-input-text",
    ):
        assert private not in payload
    assert "private-ephemeral-context" not in repr(result)


@pytest.mark.parametrize("concurrent", (False, True), ids=("sequential", "concurrent"))
async def test_reusing_one_runtime_does_not_share_run_state_or_repeat_counts(
    runtime_type: type[AgentRuntime], concurrent: bool
) -> None:
    class RequestAwareStepper:
        def __init__(self) -> None:
            self.states: dict[str, list[AgentRuntimeState]] = {}
            self.both_started = asyncio.Event()

        async def next_step(self, state: AgentRuntimeState) -> AgentStepDecision:
            self.states.setdefault(state.request_id, []).append(state)
            if state.steps_used == 0:
                if len(self.states) == 2:
                    self.both_started.set()
                if concurrent:
                    await asyncio.wait_for(self.both_started.wait(), timeout=2.0)
                return _tool()
            return _final(f"Completed {state.request_id}.")

    first = _context()
    second = first.model_copy(
        update={
            "request_id": "req-2",
            "trace_id": "trace-2",
            "auth": first.auth.model_copy(update={"tenant_id": "tenant-2", "user_id": "user-2"}),
        }
    )
    stepper, sink = RequestAwareStepper(), RecordingAgentEventSink()
    runtime = runtime_type(
        registry=_registry(),
        stepper=stepper,
        audit=None,
        event_sink=sink,
        config=_config(repeated_action_threshold=2),
    )
    results: Sequence[AgentRunResult]
    if concurrent:
        results = await asyncio.gather(runtime.run(context=first), runtime.run(context=second))
    else:
        results = [await runtime.run(context=first), await runtime.run(context=second)]

    for result, context in zip(results, (first, second), strict=True):
        assert result.status is AgentRunStatus.COMPLETED
        assert (result.steps_used, result.tool_calls_used) == (2, 1)
        assert len(result.observations) == 1
        assert result.final_answer == f"Completed {context.request_id}."
        assert (result.request_id, result.trace_id, result.tenant_id, result.user_id) == (
            context.request_id,
            context.trace_id,
            context.auth.tenant_id,
            context.auth.user_id,
        )
        states = stepper.states[context.request_id]
        assert [
            (state.steps_used, state.tool_calls_used, len(state.observations)) for state in states
        ] == [(0, 0, 0), (1, 1, 1)]
        events = [event for event in sink.events if event.request_id == context.request_id]
        assert [event.event for event in events] == ["tool_call", "tool_result"]
        assert all(event.trace_id == context.trace_id for event in events)
    assert stepper.states["req-1"][1].observations is not stepper.states["req-2"][1].observations


async def test_audit_and_event_sink_failure_do_not_replace_success(
    runtime_type: type[AgentRuntime],
) -> None:
    class BrokenSink:
        async def emit(self, event: AgentToolEvent) -> None:
            raise RuntimeError("sink unavailable")

    result = await runtime_type(
        registry=_registry(),
        stepper=FakeStepper([_tool(), _final()]),
        audit=FailingAuditPort(),
        event_sink=BrokenSink(),
        config=_config(),
    ).run(context=_context())

    assert result.status is AgentRunStatus.COMPLETED
    assert (result.steps_used, result.tool_calls_used) == (2, 1)
    assert result.final_answer == "Completed safely."


async def test_real_graph_uses_control_only_state_and_disables_ambient_tracing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observe a real compiled graph, including its actual executed node bodies."""
    pytest.importorskip("langgraph.graph", reason="Install the optional langgraph extra")
    import httpx
    from langsmith import Client
    from langsmith.run_helpers import get_tracing_context

    from packages.agent.adapters import langgraph as adapter

    graph_factory, start, end, real_tracing_context = adapter._graph_dependencies()
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    graph_states: list[dict[str, object]] = []
    executed_nodes: list[str] = []
    tracing_values: list[object] = []
    tracing_arguments: list[bool] = []
    compile_arguments: list[bool] = []
    invoke_configs: list[dict[str, object]] = []
    remote_trace_calls: list[str] = []

    def forbidden_trace_call(*args: object, **kwargs: object) -> None:
        remote_trace_calls.append("attempted")
        raise AssertionError("Framework tracing must not send run data")

    # A failure in suppression must be observable without allowing real HTTP.
    monkeypatch.setattr(Client, "create_run", forbidden_trace_call)
    monkeypatch.setattr(Client, "update_run", forbidden_trace_call)
    monkeypatch.setattr(httpx.Client, "send", forbidden_trace_call)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_trace_call)
    monkeypatch.setattr("requests.Session.request", forbidden_trace_call)

    @contextmanager
    def tracing_context(*, enabled: bool) -> Iterator[None]:
        tracing_arguments.append(enabled)
        with real_tracing_context(enabled=enabled):
            yield

    class ObservedCompiledGraph:
        def __init__(self, graph: Any) -> None:
            assert type(graph).__module__.startswith("langgraph.")
            self.graph = graph

        async def ainvoke(self, state: dict[str, object], *, config: dict[str, object]) -> Any:
            graph_states.append(dict(state))
            invoke_configs.append(dict(config))
            tracing_values.append(get_tracing_context()["enabled"])
            result = await self.graph.ainvoke(state, config=config)
            graph_states.append(dict(result))
            return result

    class ObservedStateGraph:
        def __init__(self, schema: Any) -> None:
            self.graph = graph_factory(schema)

        def add_node(self, name: str, node: Any) -> None:
            async def observe(state: dict[str, object]) -> Any:
                executed_nodes.append(name)
                graph_states.append(dict(state))
                tracing_values.append(get_tracing_context()["enabled"])
                result = await node(state)
                graph_states.append(dict(result))
                return result

            self.graph.add_node(name, observe)

        def add_edge(self, source: str, target: str) -> None:
            self.graph.add_edge(source, target)

        def add_conditional_edges(self, *args: Any, **kwargs: Any) -> None:
            self.graph.add_conditional_edges(*args, **kwargs)

        def compile(self, *, checkpointer: bool) -> ObservedCompiledGraph:
            compile_arguments.append(checkpointer)
            return ObservedCompiledGraph(self.graph.compile(checkpointer=checkpointer))

    monkeypatch.setattr(
        adapter, "_graph_dependencies", lambda: (ObservedStateGraph, start, end, tracing_context)
    )
    stepper = FakeStepper([_tool(), _final("private-final-answer")])
    result = await adapter.LangGraphRuntime(
        registry=_PrivateOutputRegistry(),
        stepper=stepper,
        audit=None,
        config=_config(input_text="private-user-input"),
    ).run(context=_context())

    assert result.status is AgentRunStatus.COMPLETED
    assert result.final_answer == "private-final-answer"
    assert executed_nodes == ["decision", "tool", "decision", "validation"]
    assert compile_arguments == [False]
    assert tracing_arguments == [False]
    assert tracing_values and all(value is False for value in tracing_values)
    assert invoke_configs[0]["callbacks"] == []
    assert graph_states and all(set(state) == {"route"} for state in graph_states)
    assert all(
        state["route"] in {"decision", "tool", "validation", "done"} for state in graph_states
    )
    assert remote_trace_calls == []
    assert stepper.states[0].input_text == "private-user-input"
    assert stepper.states[1].observations[0].prompt_context == {
        "excerpt": "private-ephemeral-context"
    }
    serialized_graph = json.dumps(graph_states)
    for private in (
        "req-1",
        "trace-1",
        "tenant-1",
        "user-1",
        "private-user-input",
        "private-query",
        "private-key-value",
        "private-document-body",
        "private-ephemeral-context",
        "private-final-answer",
    ):
        assert private not in serialized_graph


async def test_application_context_reaches_every_stage_without_mutating_the_caller(
    runtime_type: type[AgentRuntime],
) -> None:
    from opentelemetry import trace
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags
    from structlog.contextvars import bound_contextvars, get_contextvars

    context = _context().model_copy(update={"session_id": "session-1"})
    correlation = {
        "request_id": context.request_id,
        "trace_id": context.trace_id,
        "session_id": context.session_id,
    }
    private_var: ContextVar[str | None] = ContextVar("private_caller_context", default=None)
    span = NonRecordingSpan(
        SpanContext(
            trace_id=0x123456789ABCDEF123456789ABCDEF01,
            span_id=0x123456789ABCDEF0,
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
    )
    observations: list[tuple[str, str | None, object, dict[str, object], str | None]] = []

    def observe(stage: str) -> None:
        observations.append(
            (
                stage,
                current_tenant_id(),
                trace.get_current_span(),
                get_contextvars(),
                private_var.get(),
            )
        )

    class ObservingAudit(InMemoryAuditPort):
        async def record(self, event: AuditEvent) -> None:
            observe(f"audit:{event.action}")
            await super().record(event)

    class ObservingSink(RecordingAgentEventSink):
        async def emit(self, event: AgentToolEvent) -> None:
            observe(f"event:{event.event}")
            await super().emit(event)

    class ObservingStepper(FakeStepper):
        async def next_step(self, state: AgentRuntimeState) -> AgentStepDecision:
            observe("stepper")
            return await super().next_step(state)

    class ObservingValidator(StrictFinalAnswerValidator):
        async def validate(
            self,
            *,
            context: AuthenticatedRequestContext,
            request: FinalAnswerValidationRequest,
            observations: Sequence[AgentObservationEvidence],
        ) -> FinalAnswerValidationResult:
            observe("validator")
            return await super().validate(
                context=context, request=request, observations=observations
            )

    async def handler(payload: DemoInput, context: AuthenticatedRequestContext) -> DemoOutput:
        observe("handler")
        return DemoOutput(answer="Controlled result.")

    audit, sink = ObservingAudit(), ObservingSink()
    runtime = runtime_type(
        registry=_registry(audit=audit, handler=handler),
        stepper=ObservingStepper([_tool(), _final()]),
        final_answer_validator=ObservingValidator(audit=audit),
        audit=audit,
        event_sink=sink,
        config=_config(),
    )
    prior_tenant, prior_span, prior_logs = (
        current_tenant_id(),
        trace.get_current_span(),
        get_contextvars(),
    )
    token = private_var.set("private-caller-value")
    try:
        with (
            tenant_scope(context.auth.tenant_id),
            trace.use_span(span, end_on_exit=False),
            bound_contextvars(**correlation, private_caller_field="private-log-payload"),
        ):
            caller_logs = get_contextvars()
            result = await runtime.run(context=context)
            assert current_tenant_id() == context.auth.tenant_id
            assert trace.get_current_span() is span
            assert get_contextvars() == caller_logs
            assert private_var.get() == "private-caller-value"
    finally:
        private_var.reset(token)

    assert result.status is AgentRunStatus.COMPLETED
    assert [entry[0] for entry in observations] == [
        "stepper",
        "event:tool_call",
        "handler",
        "audit:agent.tool.execute",
        "event:tool_result",
        "stepper",
        "validator",
        "audit:agent.final_answer_validation",
        "audit:agent.runtime.run",
    ]
    for stage, tenant, observed_span, log_context, private_value in observations:
        assert tenant == context.auth.tenant_id, stage
        assert observed_span is span, stage
        expected_logs = caller_logs if runtime_type is AgentRuntime else correlation
        assert log_context == expected_logs, stage
        assert private_value == ("private-caller-value" if runtime_type is AgentRuntime else None)
    assert current_tenant_id() == prior_tenant
    assert trace.get_current_span() is prior_span
    assert get_contextvars() == prior_logs
    assert private_var.get() is None


async def test_outer_langchain_run_collector_cannot_capture_the_graph() -> None:
    pytest.importorskip("langgraph.graph", reason="Install the optional langgraph extra")
    from langchain_core.runnables import RunnableLambda
    from langchain_core.tracers.context import collect_runs, run_collector_var

    from packages.agent.adapters.langgraph import LangGraphRuntime

    runtime = LangGraphRuntime(
        registry=_PrivateOutputRegistry(),
        stepper=FakeStepper([_tool(), _final("private-final-answer")]),
        audit=None,
        config=_config(input_text="private-user-input"),
    )
    prior_collector = run_collector_var.get()
    with collect_runs() as collector:
        result = await runtime.run(context=_context())

        assert result.status is AgentRunStatus.COMPLETED
        assert collector.traced_runs == []
        assert run_collector_var.get() is collector
        # Prove the outer collector remains active, rather than being cleared.
        probe = RunnableLambda(lambda value: value).with_config(run_name="caller-probe")
        assert await probe.ainvoke("public-marker") == "public-marker"
        assert len(collector.traced_runs) == 1
        assert collector.traced_runs[0].name == "caller-probe"

    assert run_collector_var.get() is prior_collector
