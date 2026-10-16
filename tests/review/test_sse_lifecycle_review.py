"""Independent offline review probes for shared SSE lifecycle invariants."""

from __future__ import annotations

import asyncio

import pytest

from packages.agent.dto import AgentRunCommand
from packages.agent.runtime import AgentRuntimeState, AgentStepDecision
from tests.unit.agent.test_streaming_service import _allowed_context, _service


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_provider_self_cancellation_is_terminal_for_connected_consumer(backend: str) -> None:
    if backend == "langgraph":
        pytest.importorskip("langgraph")

    class SelfCancellingStepper:
        async def next_step(self, state: AgentRuntimeState) -> AgentStepDecision:
            raise asyncio.CancelledError("provider-owned child operation was cancelled")

    service, repository = _service(backend, SelfCancellingStepper())
    visible = []
    # This consumer remains connected and has not cancelled the stream task.
    async for event in service.stream(
        context=_allowed_context(),
        command=AgentRunCommand(input="x"),
    ):
        visible.append(event)
    assert repository.updated is not None
    assert repository.updated.status in {"stopped", "failed"}
    assert visible[0].event == "started"
    assert visible[-1].event in {"error", "final"}
    assert len([event for event in visible if event.event in {"error", "final"}]) == 1
    if visible[-1].event == "final":
        assert visible[-1].data["status"] in {"stopped", "failed"}
        assert visible[-1].data["final_answer"] is None


@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_actual_sse_response_disconnect_waits_for_storage_cleanup(backend: str) -> None:
    from apps.api.routes.agent import stream_agent
    from packages.agent.dto import AgentRunRequestBody
    from packages.agent.service import AGENT_RUN_CANCELLED
    from tests.unit.agent.test_streaming_service import WaitingStepper

    if backend == "langgraph":
        pytest.importorskip("langgraph")
    stepper = WaitingStepper()
    service, repository = _service(backend, stepper)
    response = await stream_agent(
        context=_allowed_context(),
        service=service,
        body=AgentRunRequestBody(input="x"),
    )
    messages = []

    async def receive() -> dict[str, object]:
        await stepper.entered.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    await asyncio.wait_for(
        response(
            {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}},
            receive,
            send,
        ),
        2,
    )
    assert stepper.cancelled.is_set()
    assert repository.updated is not None
    assert repository.updated.termination_reason == AGENT_RUN_CANCELLED
    assert repository.events[-1] == "commit"
    bodies = b"".join(message.get("body", b"") for message in messages)
    assert b"event: started" in bodies
    assert b"event: final" not in bodies
