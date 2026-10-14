"""Choose orchestration at the composition root without changing domain DTOs."""

from __future__ import annotations

from collections.abc import Callable

from packages.agent.final_answer import FinalAnswerValidator
from packages.agent.registry import ToolRegistry
from packages.agent.runtime import AgentEventSink, AgentRunConfig, AgentRuntime, AgentStepper
from packages.agent.service import AgentRuntimePort
from packages.common.audit import AuditPort


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
) -> AgentRuntimePort:
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
