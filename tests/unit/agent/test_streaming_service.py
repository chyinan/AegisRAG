from __future__ import annotations

import asyncio
import json

import pytest

from apps.api.factories.agent import create_agent_runtime
from packages.agent.dto import AgentCitationRef, AgentRunCommand
from packages.agent.runtime import (
    AgentActionType,
    AgentEventSink,
    AgentRunConfig,
    AgentRuntimeState,
    AgentStepDecision,
    AgentToolEvent,
)
from packages.agent.service import AGENT_RUN_CANCELLED, AgentRunApplicationService, AgentRuntimePort
from packages.agent.streaming import AgentStreamEvent, AgentStreamSink, format_agent_sse_event
from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from tests.unit.agent.test_agent_run_service import FakeAgentRunRepository, _context
from tests.unit.agent.test_runtime import FakeStepper, HandlerProbe, _registry


@pytest.fixture(params=("native", "langgraph"))
def backend(request: pytest.FixtureRequest) -> str:
    if request.param == "langgraph":
        pytest.importorskip("langgraph")
    return str(request.param)


def _service(
    backend: str,
    stepper: object,
    repository: FakeAgentRunRepository | None = None,
) -> tuple[AgentRunApplicationService, FakeAgentRunRepository]:
    repo = repository or FakeAgentRunRepository()
    registry = _registry(handler=HandlerProbe())

    def runtime(
        config: AgentRunConfig,
        run_id: str,
        sink: AgentEventSink | None = None,
    ) -> AgentRuntimePort:
        return create_agent_runtime(
            backend=backend,
            registry=registry,
            stepper=stepper,  # type: ignore[arg-type]
            audit=InMemoryAuditPort(),
            config=config,
            agent_run_id=run_id,
            event_sink=sink,
        )

    return AgentRunApplicationService(
        repository=repo,
        runtime_factory=runtime,
        stream_runtime_factory=runtime,
        audit=InMemoryAuditPort(),
        default_max_steps=5,
        default_max_tool_calls=3,
        default_timeout_seconds=30,
        repeated_action_threshold=3,
    ), repo


def _final(**kwargs: object) -> AgentStepDecision:
    return AgentStepDecision.model_validate(
        {
            "action": AgentActionType.FINAL_ANSWER,
            "final_answer": "Validated answer.",
            **kwargs,
        }
    )


def _allowed_context() -> AuthenticatedRequestContext:
    return _context(permissions=("agent:run", "agent:tool:demo"))


async def test_shared_stream_order_redaction_and_committed_final(backend: str) -> None:
    service, repo = _service(
        backend,
        FakeStepper(
            [
                AgentStepDecision.tool_call(
                    "demo_tool", {"query": "private-query", "b": "private-value"}
                ),
                _final(),
            ]
        ),
    )
    events = []
    async for event in service.stream(
        context=_allowed_context(),
        command=AgentRunCommand(input="x"),
    ):
        events.append(event)
        if event.event == "final":
            assert repo.updated is not None
            assert repo.updated.status == "completed"
            assert repo.events[-1] == "commit"
    assert [event.event for event in events] == ["started", "tool_call", "tool_result", "final"]
    assert [event.sequence for event in events] == list(range(4))
    assert events[-1].data["final_answer"] == "Validated answer."
    progress = json.dumps([event.model_dump(mode="json") for event in events[:-1]])
    assert "private-query" not in progress
    assert "private-value" not in progress
    assert "Validated answer" not in progress


async def test_invalid_final_never_leaks_answer(backend: str) -> None:
    service, _ = _service(
        backend,
        FakeStepper(
            [
                _final(
                    final_citations=(
                        AgentCitationRef(document_id="doc", version_id="v1", chunk_id="unknown"),
                    )
                )
            ]
        ),
    )
    events = [
        event
        async for event in service.stream(
            context=_allowed_context(),
            command=AgentRunCommand(input="x"),
        )
    ]
    assert events[-1].event == "final"
    assert events[-1].data["status"] == "failed"
    assert events[-1].data["final_answer"] is None
    assert "Validated answer" not in json.dumps(events[-1].data)


class WaitingStepper:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def next_step(self, state: AgentRuntimeState) -> AgentStepDecision:
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()
        raise AssertionError("unreachable")


async def test_consumer_close_cancels_runtime_and_persists_terminal(backend: str) -> None:
    stepper = WaitingStepper()
    service, repo = _service(backend, stepper)
    events = service.stream(context=_allowed_context(), command=AgentRunCommand(input="x"))
    assert (await anext(events)).event == "started"
    await asyncio.wait_for(stepper.entered.wait(), 1)
    await asyncio.wait_for(events.aclose(), 2)
    assert stepper.cancelled.is_set()
    assert repo.updated is not None
    assert repo.updated.status == "stopped"
    assert repo.updated.termination_reason == AGENT_RUN_CANCELLED
    assert repo.updated.metadata["counters_complete"] is False
    assert repo.events[-1] == "commit"


async def test_direct_call_cancellation_is_persisted_too(backend: str) -> None:
    stepper = WaitingStepper()
    service, repo = _service(backend, stepper)
    task = asyncio.create_task(
        service.run(
            context=_allowed_context(),
            command=AgentRunCommand(input="x"),
        )
    )
    await asyncio.wait_for(stepper.entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert repo.updated is not None
    assert repo.updated.termination_reason == AGENT_RUN_CANCELLED


async def test_storage_error_after_progress_has_error_without_success(backend: str) -> None:
    service, _ = _service(
        backend,
        FakeStepper([_final()]),
        FakeAgentRunRepository(fail_update=True),
    )
    events = [
        event
        async for event in service.stream(
            context=_allowed_context(),
            command=AgentRunCommand(input="x"),
        )
    ]
    assert [event.event for event in events] == ["started", "error"]
    assert events[-1].data == {"code": "AGENT_RUN_STORAGE_FAILED", "terminal": True}
    assert "secret" not in json.dumps(events[-1].data)


async def test_permission_denial_produces_no_started_or_run(backend: str) -> None:
    service, repo = _service(backend, FakeStepper([_final()]))
    events = [
        event
        async for event in service.stream(
            context=_context(permissions=()),
            command=AgentRunCommand(input="x"),
        )
    ]
    assert [event.event for event in events] == ["error"]
    assert repo.created is None


async def test_repeated_cancellation_waits_for_owned_storage_cleanup() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class SlowCancellationRepository(FakeAgentRunRepository):
        async def commit(self) -> None:
            if self.updated is not None and self.updated.termination_reason == AGENT_RUN_CANCELLED:
                entered.set()
                await release.wait()
            await super().commit()

    stepper = WaitingStepper()
    service, repo = _service("native", stepper, SlowCancellationRepository())
    task = asyncio.create_task(
        service.run(
            context=_allowed_context(),
            command=AgentRunCommand(input="x"),
        )
    )
    await stepper.entered.wait()
    task.cancel()
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert repo.events[-1] == "commit"


def test_sse_frame_cannot_inject_event_lines() -> None:
    event = AgentStreamEvent(
        event="final",
        sequence=2,
        request_id="req",
        trace_id="trace",
        data={"final_answer": "line\nevent: error\r\ndata: forged"},
    )
    frame = format_agent_sse_event(event)
    assert frame.count("\nevent:") == 1
    assert frame.count("\ndata:") == 1
    assert frame.startswith("id: 2\nevent: final\n")


async def test_progress_overflow_is_explicit_not_success() -> None:
    sink = AgentStreamSink(request_id="req", trace_id="trace")
    for i in range(65):
        await sink.emit(
            AgentToolEvent(
                event="tool_call",
                request_id="req",
                trace_id="trace",
                tool_call_id=str(i),
                tool_name="calculator",
                status="started",
            )
        )
    assert sink.overflowed
    assert sink.queue.qsize() == 64


@pytest.mark.parametrize("guard", ("budget", "repeat"))
async def test_predispatch_guard_emits_unpaired_error_result_before_stopped_final(
    backend: str,
    guard: str,
) -> None:
    """The UI must accept rejected attempts that never emitted a dispatch event."""
    decisions = [AgentStepDecision.tool_call("demo_tool", {"query": "synthetic"})] * 3
    app, _repo = _service(backend, FakeStepper(decisions))
    events = [
        event
        async for event in app.stream(
            context=_allowed_context(),
            command=AgentRunCommand(
                input="synthetic", max_tool_calls=0 if guard == "budget" else 3
            ),
        )
    ]
    code = "MAX_TOOL_CALLS_REACHED" if guard == "budget" else "REPEATED_ACTION_DETECTED"
    started_ids = {event.data["tool_call_id"] for event in events if event.event == "tool_call"}
    rejected = [
        event
        for event in events
        if event.event == "tool_result" and event.data["tool_call_id"] not in started_ids
    ]
    assert len(rejected) == 1
    assert rejected[0].data["status"] == "error"
    assert rejected[0].data["metadata"]["error_code"] == code
    assert events[-1].event == "final"
    assert events[-1].data["status"] == "stopped"
    assert events[-1].data["termination_reason"] == code
    assert events[-1].data["final_answer"] is None
