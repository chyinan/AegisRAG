"""Provider-neutral LLM AgentStepper tests."""

import json

import pytest

from apps.api.agent_stepper import LLMJsonAgentStepper
from packages.agent.dto import ToolInvocationStatus
from packages.agent.runtime import AgentObservationSummary, AgentRuntimeState, AgentStepDecision
from packages.llm.dto import GenerateResponse, GenerationMetadata, TokenUsage


class _FakeLLM:
    def __init__(self, text: str) -> None:
        self.text = text
        self.requests: list[object] = []

    async def generate(self, request: object) -> GenerateResponse:
        self.requests.append(request)
        usage = TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2)
        metadata = GenerationMetadata(
            request_id="req-a",
            trace_id="trace-a",
            tenant_id="tenant-a",
            user_id="user-a",
            provider="fake",
            model="fake",
            usage=usage,
            latency_ms=1.0,
            finish_reason="stop",
        )
        return GenerateResponse(
            text=self.text,
            provider="fake",
            model="fake",
            usage=usage,
            latency_ms=1.0,
            finish_reason="stop",
            request_id="req-a",
            trace_id="trace-a",
            tenant_id="tenant-a",
            user_id="user-a",
            metadata=metadata,
        )


def _state() -> AgentRuntimeState:
    return AgentRuntimeState(
        request_id="req-a",
        trace_id="trace-a",
        tenant_id="tenant-a",
        user_id="user-a",
        steps_used=0,
        tool_calls_used=0,
        input_text="calculate 2+2",
    )


@pytest.mark.asyncio
async def test_llm_stepper_parses_strict_json_and_forwards_security_context() -> None:
    llm = _FakeLLM(
        json.dumps(
            {
                "action": "tool_call",
                "tool_name": "calculator",
                "arguments": {"expression": "2+2"},
            }
        )
    )
    stepper = LLMJsonAgentStepper(
        llm_provider=llm,
        model="fake",
        provider="fake",
        tool_names=("calculator",),
    )

    decision = await stepper.next_step(_state())

    assert decision == AgentStepDecision.tool_call("calculator", {"expression": "2+2"})
    request = llm.requests[0]
    assert request.tenant_id == "tenant-a"  # type: ignore[attr-defined]
    assert request.user_id == "user-a"  # type: ignore[attr-defined]
    assert request.request_id == "req-a"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_llm_stepper_rejects_non_json_output() -> None:
    stepper = LLMJsonAgentStepper(
        llm_provider=_FakeLLM("not-json"),
        model="fake",
        provider="fake",
        tool_names=("calculator",),
    )

    with pytest.raises(ValueError, match="JSON"):
        await stepper.next_step(_state())


@pytest.mark.asyncio
async def test_llm_stepper_receives_ephemeral_untrusted_tool_projection() -> None:
    llm = _FakeLLM(
        json.dumps(
            {
                "action": "final_answer",
                "final_answer": "The policy says 12 days.",
            }
        )
    )
    state = _state().model_copy(
        update={
            "steps_used": 1,
            "tool_calls_used": 1,
            "observations": (
                AgentObservationSummary(
                    tool_name="rag_search",
                    status=ToolInvocationStatus.SUCCESS,
                    latency_ms=2.0,
                    prompt_context={
                        "status": "success",
                        "results": [
                            {"content_excerpt": "12 working days", "citation_supported": True}
                        ],
                    },
                ),
            ),
        }
    )
    stepper = LLMJsonAgentStepper(
        llm_provider=llm,
        model="fake",
        provider="fake",
        tool_names=("rag_search",),
    )

    await stepper.next_step(state)

    request = llm.requests[0]
    prompt = request.messages[-1].content  # type: ignore[attr-defined]
    assert "12 working days" in prompt
    assert "untrusted_tool_output" in prompt
