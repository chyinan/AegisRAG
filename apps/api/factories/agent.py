"""Choose orchestration at the composition root without changing domain DTOs."""

from __future__ import annotations

from collections.abc import Callable

from apps.api.agent_stepper import LLMJsonAgentStepper, LLMStructuredAgentStepper
from packages.agent.final_answer import FinalAnswerValidator
from packages.agent.registry import ToolRegistry
from packages.agent.runtime import AgentEventSink, AgentRunConfig, AgentRuntime, AgentStepper
from packages.common.audit import AuditPort
from packages.common.config import AppSettings
from packages.llm.decisions import DecisionToolSchema
from packages.llm.ports import DecisionProvider, LLMProvider


def create_agent_runtime(
    *,
    backend: str = "native",
    registry: ToolRegistry,
    stepper: AgentStepper,
    audit: AuditPort | None,
    config: AgentRunConfig,
    agent_run_id: str | None = None,
    final_answer_validator: FinalAnswerValidator | None = None,
    event_sink: AgentEventSink | None = None,
    perf_counter: Callable[[], float] | None = None,
) -> AgentRuntime:
    runtime_type: type[AgentRuntime]
    if backend == "native":
        runtime_type = AgentRuntime
    elif backend == "langgraph":
        from packages.agent.adapters.langgraph import LangGraphRuntime

        runtime_type = LangGraphRuntime
    else:
        raise ValueError("Unsupported agent orchestration backend. Use 'native' or 'langgraph'.")
    return runtime_type(
        registry=registry,
        stepper=stepper,
        audit=audit,
        config=config,
        agent_run_id=agent_run_id,
        final_answer_validator=final_answer_validator,
        event_sink=event_sink,
        perf_counter=perf_counter,
    )


def create_agent_stepper(
    *, settings: AppSettings, llm_provider: LLMProvider, registry: ToolRegistry
) -> AgentStepper:
    """Opt in to native decisions independently of orchestration backend."""
    if settings.agent_decision_mode == "json":
        return LLMJsonAgentStepper(
            llm_provider=llm_provider,
            model=settings.llm_model,
            provider=settings.llm_provider,
            tool_names=registry.registered_tool_names,
            timeout_seconds=settings.agent_default_timeout_seconds,
        )
    if not isinstance(llm_provider, DecisionProvider):
        raise ValueError("The configured LLM provider does not support native tool decisions")
    return LLMStructuredAgentStepper(
        decision_provider=llm_provider,
        model=settings.llm_model,
        provider=settings.llm_provider,
        tools=(
            DecisionToolSchema(
                name=definition.name,
                description=definition.description,
                parameters=definition.input_json_schema,
            )
            for definition in registry.registered_tool_definitions
        ),
        timeout_seconds=settings.agent_default_timeout_seconds,
    )
