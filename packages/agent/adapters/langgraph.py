"""In-memory LangGraph orchestration over the native runtime's guarded operations.

Only a routing token enters graph state. Authentication, observations, arguments,
and answers stay in a request-local execution object, outside framework state.
This deliberately does not expose checkpointing, resume, or framework callbacks.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Literal, TypedDict

from packages.agent.final_answer import FinalAnswerValidator
from packages.agent.registry import ToolRegistry
from packages.agent.runtime import (
    AgentActionType,
    AgentEventSink,
    AgentRunConfig,
    AgentRunResult,
    AgentRuntime,
    AgentStepper,
)
from packages.common.audit import AuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.common.framework_context import isolated_framework_context


class _ControlState(TypedDict):
    route: Literal["decision", "tool", "validation", "done"]


def _graph_dependencies() -> tuple[Any, str, str, Any]:
    try:
        from langgraph.graph import END, START, StateGraph
        from langsmith import tracing_context
    except ImportError:
        raise RuntimeError(
            "LangGraph orchestration requires the optional 'langgraph' extra. "
            "Install it with: uv sync --extra langgraph"
        ) from None
    return StateGraph, START, END, tracing_context


class LangGraphRuntime(AgentRuntime):
    """Implement AgentRuntimePort with a real, bounded StateGraph execution."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        stepper: AgentStepper,
        audit: AuditPort | None,
        config: AgentRunConfig,
        agent_run_id: str | None = None,
        final_answer_validator: FinalAnswerValidator | None = None,
        event_sink: AgentEventSink | None = None,
        perf_counter: Callable[[], float] | None = None,
    ) -> None:
        self._graph_factory, self._start, self._end, self._tracing_context = _graph_dependencies()
        super().__init__(
            registry=registry,
            stepper=stepper,
            audit=audit,
            config=config,
            agent_run_id=agent_run_id,
            final_answer_validator=final_answer_validator,
            event_sink=event_sink,
            perf_counter=perf_counter,
        )

    async def run(self, *, context: AuthenticatedRequestContext) -> AgentRunResult:
        execution = self._new_execution(context)
        result: AgentRunResult | None = None

        async def decide(_state: _ControlState) -> _ControlState:
            nonlocal result
            result = await self._decide(execution)
            if result is not None:
                return {"route": "done"}
            assert execution.decision is not None
            return {
                "route": (
                    "validation"
                    if execution.decision.action is AgentActionType.FINAL_ANSWER
                    else "tool"
                )
            }

        async def execute_tool(_state: _ControlState) -> _ControlState:
            nonlocal result
            result = await self._execute_tool(execution)
            return {"route": "done" if result is not None else "decision"}

        async def validate(_state: _ControlState) -> _ControlState:
            nonlocal result
            result = await self._validate_result(execution)
            return {"route": "done"}

        def route(state: _ControlState) -> str:
            return state["route"]

        builder = self._graph_factory(_ControlState)
        builder.add_node("decision", decide)
        builder.add_node("tool", execute_tool)
        builder.add_node("validation", validate)
        builder.add_edge(self._start, "decision")
        builder.add_conditional_edges(
            "decision",
            route,
            {"tool": "tool", "validation": "validation", "done": self._end},
        )
        builder.add_conditional_edges(
            "tool",
            route,
            {"decision": "decision", "done": self._end},
        )
        builder.add_edge("validation", self._end)
        graph = builder.compile(checkpointer=False)

        # Framework tracing is opt-out here regardless of ambient environment.
        # AegisRAG's redacted audit/event sinks remain the observability boundary.
        async def invoke() -> None:
            with self._tracing_context(enabled=False):
                await graph.ainvoke(
                    {"route": "decision"},
                    config={
                        "recursion_limit": 2 * self._config.max_steps + 3,
                        "callbacks": [],
                    },
                )

        # Explicit tracing/collector/RunnableConfig context variables can bypass
        # the environment flag. Never inherit them into framework execution.
        await asyncio.create_task(invoke(), context=isolated_framework_context())
        assert result is not None
        return result
