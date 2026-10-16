"""Bounded application-owned Agent events, independent of orchestration backend.

Only the terminal event may contain a validated answer. Tool progress uses the
existing redacted runtime DTO; no model tokens or unvalidated decisions escape.
"""

from __future__ import annotations

import asyncio
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from packages.agent.dto import AgentRunRecord, AgentRunResponse
from packages.agent.runtime import AgentToolEvent


class AgentStreamEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event: Literal["started", "tool_call", "tool_result", "error", "final"]
    sequence: int = Field(ge=0)
    request_id: str
    trace_id: str
    agent_run_id: str | None = None
    data: dict[str, object] = Field(default_factory=dict)


class AgentStreamSink:
    """A per-invocation queue. It is never a process-global run registry."""

    def __init__(self, *, request_id: str, trace_id: str) -> None:
        # Public commands permit at most 20 tool calls: 2 progress events/call.
        # Keep a bounded queue even for programmatic or misconfigured callers.
        self.queue: asyncio.Queue[AgentStreamEvent] = asyncio.Queue(maxsize=64)
        self.request_id = request_id
        self.trace_id = trace_id
        self.run_id: str | None = None
        self.sequence = 0
        self.closed = False
        self.overflowed = False

    def _put(self, event: AgentStreamEvent) -> None:
        if self.closed:
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.overflowed = True
            # Runtime event sinks are best-effort. The producer checks this flag
            # before publishing its terminal result, so loss is never success.
            return
        self.sequence += 1

    def started(self, record: AgentRunRecord) -> None:
        self.run_id = record.id
        self._put(self._event("started", {"status": "running"}))

    async def emit(self, event: AgentToolEvent) -> None:
        if event.request_id != self.request_id or event.trace_id != self.trace_id:
            raise ValueError("Agent event context mismatch")
        # Keep the public wire boundary strict even if a custom runtime passes
        # arbitrary metadata into the otherwise redacted tool-event DTO.
        metadata: dict[str, object] = {}
        latency = event.metadata.get("latency_ms")
        if isinstance(latency, int | float) and math.isfinite(latency) and latency >= 0:
            metadata["latency_ms"] = latency
        error = event.metadata.get("error_code")
        if isinstance(error, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", error):
            metadata["error_code"] = error
        self._put(
            self._event(
                event.event,
                {
                    "tool_call_id": event.tool_call_id,
                    "tool_name": event.tool_name,
                    "status": event.status,
                    "metadata": metadata,
                },
            )
        )

    def _event(
        self,
        kind: Literal["started", "tool_call", "tool_result", "error", "final"],
        data: dict[str, object],
    ) -> AgentStreamEvent:
        return AgentStreamEvent(
            event=kind,
            sequence=self.sequence,
            request_id=self.request_id,
            trace_id=self.trace_id,
            agent_run_id=self.run_id,
            data=data,
        )

    async def finish(self, result: AgentRunResponse) -> None:
        if self.overflowed:
            await self.fail("AGENT_STREAM_OVERFLOW")
            return
        await self.queue.put(self._event("final", result.model_dump(mode="json")))
        self.sequence += 1

    async def fail(self, code: str) -> None:
        # Exception bodies/details may contain provider content or credentials.
        safe_code = code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code) else "AGENT_RUN_FAILED"
        await self.queue.put(self._event("error", {"code": safe_code, "terminal": True}))
        self.sequence += 1


def format_agent_sse_event(event: AgentStreamEvent) -> str:
    # IDs are numeric and scoped to this connection; no Last-Event-ID replay is
    # promised. JSON serialization safely escapes CR/LF in answer content.
    return f"id: {event.sequence}\nevent: {event.event}\ndata: {event.model_dump_json()}\n\n"
