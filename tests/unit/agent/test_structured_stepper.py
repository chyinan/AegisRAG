"""Structured decisions use the same governed runtime and final validation."""

from __future__ import annotations

from typing import cast

import pytest
from pydantic import ValidationError

from apps.api.agent_stepper import LLMJsonAgentStepper, LLMStructuredAgentStepper
from apps.api.factories.agent import create_agent_runtime, create_agent_stepper
from packages.agent.dto import AgentCitationRef
from packages.agent.runtime import AgentTerminationReason
from packages.common.config import AppSettings
from packages.llm.decisions import DecisionRequest, DecisionResponse, DecisionToolSchema
from packages.llm.dto import GenerationMetadata
from packages.llm.ports import LLMProvider
from tests.unit.agent.test_llm_stepper import _FakeLLM, _state
from tests.unit.agent.test_runtime import (
    HandlerProbe,
    RagSearchRegistry,
    _config,
    _context,
    _registry,
)


def _decision(**values: object) -> DecisionResponse:
    return DecisionResponse.model_validate(
        {
            "metadata": GenerationMetadata(
                provider="fake",
                model="fake",
                request_id="req-a",
                trace_id="trace-a",
                tenant_id="tenant-a",
                user_id="user-a",
                latency_ms=1.0,
                finish_reason="tool_calls",
            ),
            **values,
        }
    )


class _Decisions:
    def __init__(self, *decisions: DecisionResponse) -> None:
        self.decisions = list(decisions)
        self.requests: list[DecisionRequest] = []

    async def decide(self, request: DecisionRequest) -> DecisionResponse:
        self.requests.append(request)
        return self.decisions.pop(0)


def _stepper(provider: _Decisions, name: str = "demo_tool") -> LLMStructuredAgentStepper:
    return LLMStructuredAgentStepper(
        decision_provider=provider,
        model="fake",
        provider="fake",
        tools=(
            DecisionToolSchema(
                name=name,
                description="Explicit registry tool",
                parameters={"type": "object"},
            ),
        ),
    )


async def test_structured_stepper_forwards_scope_and_returns_declarative_decision() -> None:
    provider = _Decisions(
        _decision(action="tool_call", tool_name="demo_tool", arguments={"query": "hi"})
    )
    decision = await _stepper(provider).next_step(_state())
    assert decision.tool_name == "demo_tool"
    assert decision.arguments == {"query": "hi"}
    request = provider.requests[0]
    assert request.request_id == "req-a"
    assert request.tenant_id == "tenant-a"
    assert request.user_id == "user-a"
    assert request.tools[0].name == "demo_tool"
    assert request.final_answer_schema["additionalProperties"] is False
    assert "final_citations" in request.final_answer_schema["properties"]
    assert "aegis_final_answer" in request.messages[0].content


@pytest.mark.parametrize(
    "payload",
    [
        {"final_answer": "ok", "tool_name": "demo_tool"},
        {"final_answer": "ok", "final_citations": [{"document_id": "missing-fields"}]},
        {"final_answer": ["not text"]},
        {},
    ],
)
async def test_structured_final_payload_is_strict(payload: dict[str, object]) -> None:
    provider = _Decisions(_decision(action="final_answer", final_answer=payload))
    with pytest.raises(ValidationError):
        await _stepper(provider).next_step(_state())


async def test_structured_stepper_rejects_tool_outside_its_catalog() -> None:
    provider = _Decisions(_decision(action="tool_call", tool_name="unknown", arguments={}))
    with pytest.raises(ValueError, match="unavailable tool"):
        await _stepper(provider).next_step(_state())


@pytest.mark.parametrize("backend", ["native", "langgraph"])
@pytest.mark.parametrize("authorized", [False, True])
async def test_both_runtimes_keep_registry_execution_and_rbac(
    backend: str, authorized: bool
) -> None:
    if backend == "langgraph":
        pytest.importorskip("langgraph")
    handler = HandlerProbe()
    registry = _registry(handler=handler)
    provider = _Decisions(
        _decision(action="tool_call", tool_name="demo_tool", arguments={"query": "hi"}),
        _decision(action="final_answer", final_answer={"final_answer": "done"}),
    )
    runtime = create_agent_runtime(
        backend=backend,
        registry=registry,
        stepper=_stepper(provider),
        audit=None,
        config=_config(),
    )
    result = await runtime.run(
        context=_context(permissions=("agent:tool:demo",) if authorized else ())
    )
    if authorized:
        assert result.termination_reason == AgentTerminationReason.FINAL_ANSWER
        assert result.tool_calls_used == 1
        assert handler.call_count == 1
        assert len(provider.requests) == 2
    else:
        assert result.termination_reason == AgentTerminationReason.AGENT_TOOL_FAILED
        assert result.metadata["tool_error_code"] == "TOOL_PERMISSION_DENIED"
        assert handler.call_count == 0
        assert len(provider.requests) == 1


@pytest.mark.parametrize("backend", ["native", "langgraph"])
@pytest.mark.parametrize("valid", [True, False])
async def test_native_final_answer_must_pass_existing_evidence_validation(
    backend: str,
    valid: bool,
) -> None:
    if backend == "langgraph":
        pytest.importorskip("langgraph")
    citation = AgentCitationRef(
        document_id="doc-1",
        version_id="ver-1",
        chunk_id="chunk-1" if valid else "invented",
        source="policy",
        page_start=2,
        page_end=3,
    )
    provider = _Decisions(
        _decision(action="tool_call", tool_name="rag_search", arguments={"query": "policy"}),
        _decision(
            action="final_answer",
            final_answer={
                "final_answer": "Policy page 2",
                "final_citations": [citation.model_dump(mode="json")],
            },
        ),
    )
    runtime = create_agent_runtime(
        backend=backend,
        registry=RagSearchRegistry(),
        stepper=_stepper(provider, "rag_search"),
        audit=None,
        config=_config(),
    )
    result = await runtime.run(context=_context())
    if valid:
        assert result.termination_reason == AgentTerminationReason.FINAL_ANSWER
        assert result.final_citations == (citation,)
    else:
        assert result.termination_reason == AgentTerminationReason.FINAL_ANSWER_VALIDATION_FAILED
        assert result.final_answer is None


def test_factory_defaults_and_native_selection_use_only_registry_schemas() -> None:
    assert isinstance(
        create_agent_stepper(
            settings=AppSettings(),
            llm_provider=_FakeLLM("{}"),
            registry=_registry(),
        ),
        LLMJsonAgentStepper,
    )
    settings = AppSettings.model_validate(
        {
            "LLM_ADAPTER": "langchain",
            "LLM_PROVIDER": "openai_compatible",
            "LLM_BASE_URL": "https://models.invalid/v1",
            "LLM_API_KEY": "test",
            "AGENT_DECISION_MODE": " NATIVE_TOOLS ",
        }
    )
    provider = _Decisions()
    stepper = create_agent_stepper(
        settings=settings,
        llm_provider=cast(LLMProvider, provider),
        registry=_registry(),
    )
    assert isinstance(stepper, LLMStructuredAgentStepper)
    assert settings.agent_orchestration_backend == "native"
    with pytest.raises(ValueError, match="does not support"):
        create_agent_stepper(settings=settings, llm_provider=_FakeLLM("{}"), registry=_registry())
    with pytest.raises(ValidationError, match="requires LLM_ADAPTER"):
        AppSettings.model_validate({"AGENT_DECISION_MODE": "native_tools"})
    with pytest.raises(ValidationError):
        AppSettings.model_validate({"AGENT_DECISION_MODE": "unknown"})


def test_native_decision_configuration_cannot_silently_fall_back_to_fake() -> None:
    with pytest.raises(ValidationError, match="requires an OpenAI-compatible LLM_PROVIDER"):
        AppSettings.model_validate(
            {
                "LLM_ADAPTER": "langchain",
                "AGENT_DECISION_MODE": "native_tools",
                "LLM_PROVIDER": "fake",
            }
        )
