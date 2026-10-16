"""Generate synthetic Agent UI contracts through production DTOs and serializers.

No model, tool, application startup, database or network operation is performed.
Run ``python -m scripts.generate_web_agent_fixtures [--check]`` from the repo root.
The fixture clock is fixed; browser tests must set their own clock explicitly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from pydantic import BaseModel

from packages.agent.approval import argument_digest
from packages.agent.dto import AgentCitationRef, AgentRunRecord, AgentRunResponse
from packages.agent.durable.dto import (
    ApprovalRequest,
    DurableRunResponse,
    DurableSnapshot,
    decision_digest,
    durable_error,
)
from packages.agent.runtime import (
    AgentRunConfig,
    AgentRunResult,
    AgentRunStatus,
    AgentStepDecision,
    AgentTerminationReason,
    AgentToolEvent,
)
from packages.agent.streaming import AgentStreamSink, format_agent_sse_event
from packages.common.envelope import ApiError, error_response, success_response

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "apps/web/src/test/fixtures/agent-lifecycle.json"
)


class _Context(TypedDict):
    request_id: str
    trace_id: str


class _Identity(TypedDict):
    tenant_id: str
    user_id: str


NOW = 2_000_000_000.0
CONTEXT: _Context = {"request_id": "req-agent-fixture", "trace_id": "trace-agent-fixture"}
IDENTITY: _Identity = {"tenant_id": "tenant-pilot", "user_id": "user-pilot"}
RUN_ID = "182c57d4-cfa8-4c0f-a42b-35602053c51f"
APPROVAL_ID = "9a13ca64-3045-4b79-97fc-62d20cfe1633"


def _citation() -> AgentCitationRef:
    return AgentCitationRef(
        document_id="doc-pilot",
        version_id="version-pilot",
        chunk_id="chunk-pilot",
        source="Pilot handbook.md",
        page_start=2,
        page_end=2,
        tool_name="rag_search",
        observation_index=0,
    )


def _record() -> AgentRunRecord:
    return AgentRunRecord(
        id=RUN_ID,
        **CONTEXT,
        **IDENTITY,
        created_by=IDENTITY["user_id"],
        status="running",
        max_steps=8,
        max_tool_calls=6,
        timeout_seconds=120,
        created_at=datetime.fromtimestamp(NOW, UTC),
        updated_at=datetime.fromtimestamp(NOW, UTC),
    )


def _envelope(value: BaseModel) -> dict[str, object]:
    return success_response(request_id=CONTEXT["request_id"], data=value).model_dump(mode="json")


async def _streams() -> dict[str, str]:
    async def wire(*, terminal: str, with_started: bool = True) -> str:
        sink = AgentStreamSink(**CONTEXT)
        record = _record()
        if with_started:
            sink.started(record)
            await sink.emit(
                AgentToolEvent(
                    **CONTEXT,
                    event="tool_call",
                    tool_call_id="call-fixture",
                    tool_name="rag_search",
                    status="started",
                )
            )
            await sink.emit(
                AgentToolEvent(
                    **CONTEXT,
                    event="tool_result",
                    tool_call_id="call-fixture",
                    tool_name="rag_search",
                    status="success",
                    metadata={"latency_ms": 12.5},
                )
            )
        if terminal == "success":
            completed = record.model_copy(
                update={
                    "status": "completed",
                    "steps_used": 2,
                    "tool_calls_used": 1,
                    "termination_reason": "FINAL_ANSWER",
                }
            )
            await sink.finish(
                AgentRunResponse.from_record(
                    completed,
                    final_answer="Remote work 🌍 requires approval.\nAsk your manager.",
                    final_citations=(_citation(),),
                )
            )
        elif terminal == "stopped":
            stopped = record.model_copy(
                update={
                    "status": "stopped",
                    "steps_used": 2,
                    "tool_calls_used": 1,
                    "termination_reason": "MAX_STEPS_REACHED",
                    "error_code": "MAX_STEPS_REACHED",
                }
            )
            await sink.finish(AgentRunResponse.from_record(stopped))
        elif terminal == "error":
            await sink.fail("AGENT_RUN_FAILED")
        frames = []
        while not sink.queue.empty():
            frames.append(format_agent_sse_event(sink.queue.get_nowait()))
        return "".join(frames)

    async def rejected_before_dispatch(code: str, prior_tool: bool) -> str:
        sink = AgentStreamSink(**CONTEXT)
        record = _record()
        sink.started(record)
        if prior_tool:
            await sink.emit(
                AgentToolEvent(
                    **CONTEXT,
                    event="tool_call",
                    tool_call_id="call-previous",
                    tool_name="rag_search",
                    status="started",
                )
            )
            await sink.emit(
                AgentToolEvent(
                    **CONTEXT,
                    event="tool_result",
                    tool_call_id="call-previous",
                    tool_name="rag_search",
                    status="success",
                )
            )
        # Runtime limit/repeat guards publish a failed attempt before dispatch,
        # without a corresponding started tool_call event for this identifier.
        await sink.emit(
            AgentToolEvent(
                **CONTEXT,
                event="tool_result",
                tool_call_id="call-rejected",
                tool_name="rag_search",
                status="error",
                metadata={"error_code": code, "latency_ms": 0.0},
            )
        )
        stopped = record.model_copy(
            update={
                "status": "stopped",
                "steps_used": 2 if prior_tool else 1,
                "tool_calls_used": 1 if prior_tool else 0,
                "termination_reason": code,
                "error_code": code,
            }
        )
        await sink.finish(AgentRunResponse.from_record(stopped))
        frames = []
        while not sink.queue.empty():
            frames.append(format_agent_sse_event(sink.queue.get_nowait()))
        return "".join(frames)

    return {
        "success": await wire(terminal="success"),
        "stopped": await wire(terminal="stopped"),
        "error": await wire(terminal="error"),
        "errorBeforeStarted": await wire(terminal="error", with_started=False),
        "incomplete": await wire(terminal="incomplete"),
        "limitBeforeDispatch": await rejected_before_dispatch("MAX_TOOL_CALLS_REACHED", False),
        "repeatBeforeDispatch": await rejected_before_dispatch("REPEATED_ACTION_DETECTED", True),
    }


def agent_fixtures() -> dict[str, object]:
    decision = AgentStepDecision.tool_call(
        "rag_search",
        {"query": "Remote-work policy 🌍", "top_k": 3},
    )
    approval = ApprovalRequest(
        approval_id=APPROVAL_ID,
        tool_name="rag_search",
        arguments=dict(decision.arguments),
        action_digest=decision_digest(decision),
        definition_digest=argument_digest({"synthetic_definition_version": "1"}),
        expires_at=NOW + 120,
    )
    snapshot = DurableSnapshot(
        run_id=RUN_ID,
        **CONTEXT,
        **IDENTITY,
        backend="native",
        revision=2,
        config=AgentRunConfig(
            max_steps=8,
            max_tool_calls=6,
            timeout_seconds=120,
            repeated_action_threshold=3,
            input_text="Synthetic policy question",
        ),
        status="awaiting_approval",
        phase="tool",
        started_at=NOW,
        deadline_at=NOW + 120,
        last_observed_at=NOW + 1,
        expires_at=NOW + 86400,
        steps_used=1,
        decision=decision,
        approval_tools=("rag_search",),
        approval=approval,
    )
    result = AgentRunResult(
        **CONTEXT,
        **IDENTITY,
        status=AgentRunStatus.COMPLETED,
        termination_reason=AgentTerminationReason.FINAL_ANSWER,
        steps_used=2,
        tool_calls_used=1,
        final_answer="Remote work requires approval.",
        final_citations=(_citation(),),
        latency_ms=20,
    )
    states = {
        "awaitingApproval": snapshot,
        "running": snapshot.model_copy(update={"status": "running", "approval": None}),
        "completed": snapshot.model_copy(
            update={
                "status": "completed",
                "phase": "done",
                "revision": 5,
                "steps_used": 2,
                "tool_calls_used": 1,
                "approval": None,
                "result": result,
            }
        ),
        "cancelled": snapshot.model_copy(
            update={
                "status": "cancelled",
                "phase": "done",
                "revision": 3,
                "error_code": "DURABLE_CANCELLED",
                "approval": None,
            }
        ),
        "interventionRequired": snapshot.model_copy(
            update={
                "status": "intervention_required",
                "phase": "done",
                "revision": 4,
                "error_code": "DURABLE_TOOL_OUTCOME_UNKNOWN",
                "approval": None,
            }
        ),
    }
    errors = {}
    for name, code, status in (
        ("conflict", "DURABLE_RESUME_CONFLICT", 409),
        ("staleApproval", "DURABLE_APPROVAL_STALE", 409),
        ("disabled", "DURABLE_AGENT_DISABLED", 503),
        ("expired", "DURABLE_RUN_EXPIRED", 410),
    ):
        error = durable_error(code, status_code=status)
        errors[name] = error_response(
            request_id=CONTEXT["request_id"],
            error=ApiError(code=error.code, message=error.message, details=error.details),
        ).model_dump(mode="json")
    return {
        "clockEpochSeconds": NOW,
        "streams": asyncio.run(_streams()),
        "durable": {
            name: _envelope(DurableRunResponse.from_snapshot(value))
            for name, value in states.items()
        },
        "errors": errors,
    }


def serialized_fixtures() -> str:
    return json.dumps(agent_fixtures(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = serialized_fixtures()
    if args.check:
        if not FIXTURE_PATH.exists() or FIXTURE_PATH.read_text(encoding="utf-8") != expected:
            parser.exit(1, "Agent fixtures differ from backend contracts. Regenerate them.\n")
        return
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(expected, encoding="utf-8")


if __name__ == "__main__":
    main()
