import { AgentProtocolError, agentErrorCode, agentInteger, agentRecord, agentString, agentToolName, decodeAgentRun } from "./agent-contract";
import type { AgentStreamEvent } from "./types";

const EVENT_TYPES = new Set(["started", "tool_call", "tool_result", "error", "final"]);
const MAX_FRAME_LENGTH = 1024 * 1024;

/** The Agent wire envelope is different from chat SSE; never feed it to the chat parser. */
export function parseAgentSseEvents(payload: string): AgentStreamEvent[] {
  const events: AgentStreamEvent[] = [];
  for (const block of payload.replace(/\r\n|\r/g, "\n").split("\n\n")) {
    let kind = "message";
    let id: string | undefined;
    const lines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon < 0 ? line : line.slice(0, colon);
      const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
      if (field === "event") kind = value;
      if (field === "id") id = value;
      if (field === "data") lines.push(value);
    }
    if (!EVENT_TYPES.has(kind)) continue;
    let raw: unknown;
    try { raw = JSON.parse(lines.join("\n")) as unknown; } catch { throw new AgentProtocolError("AGENT_STREAM_INVALID_EVENT"); }
    const value = agentRecord(raw);
    if (value.event !== kind) throw new AgentProtocolError("AGENT_STREAM_INVALID_EVENT");
    const sequence = agentInteger(value.sequence, 43);
    if (id !== String(sequence)) throw new AgentProtocolError("AGENT_STREAM_INVALID_EVENT");
    const context = {
      sequence, request_id: agentString(value.request_id), trace_id: agentString(value.trace_id),
      agent_run_id: value.agent_run_id === null ? null : agentString(value.agent_run_id)
    };
    const data = agentRecord(value.data);
    switch (kind) {
      case "started":
        if (data.status !== "running" || context.agent_run_id === null) throw new AgentProtocolError();
        events.push({ ...context, event: "started", data: { status: "running" } });
        break;
      case "tool_call":
      case "tool_result": {
        if (context.agent_run_id === null || (kind === "tool_call" ? data.status !== "started" : data.status !== "success" && data.status !== "error")) {
          throw new AgentProtocolError();
        }
        const rawMetadata = agentRecord(data.metadata);
        const metadata: { latency_ms?: number; error_code?: string } = {};
        if (rawMetadata.latency_ms !== undefined) {
          if (typeof rawMetadata.latency_ms !== "number" || !Number.isFinite(rawMetadata.latency_ms) || rawMetadata.latency_ms < 0) throw new AgentProtocolError();
          metadata.latency_ms = rawMetadata.latency_ms;
        }
        if (rawMetadata.error_code !== undefined) metadata.error_code = agentErrorCode(rawMetadata.error_code) ?? undefined;
        events.push({ ...context, event: kind, data: {
          tool_call_id: agentString(data.tool_call_id), tool_name: agentToolName(data.tool_name),
          status: data.status as "started" | "success" | "error", metadata
        } });
        break;
      }
      case "error": {
        const code = agentErrorCode(data.code);
        if (code === null || data.terminal !== true) throw new AgentProtocolError();
        events.push({ ...context, event: "error", data: { code, terminal: true } });
        break;
      }
      case "final": {
        const result = decodeAgentRun(data);
        if (result.agent_run_id !== context.agent_run_id || result.request_id !== context.request_id || result.trace_id !== context.trace_id) {
          throw new AgentProtocolError();
        }
        events.push({ ...context, event: "final", data: result });
        break;
      }
    }
  }
  return events;
}

/** One connection, monotonic sequence/context, bounded frames, no replay or reconnect. */
export async function* readAgentSseStream(response: Response, signal?: AbortSignal): AsyncGenerator<AgentStreamEvent> {
  if (signal?.aborted) {
    void response.body?.cancel().catch(() => undefined);
    signal.throwIfAborted();
  }
  if (!response.headers.get("content-type")?.toLowerCase().startsWith("text/event-stream")) {
    void response.body?.cancel().catch(() => undefined);
    throw new AgentProtocolError("AGENT_STREAM_INVALID_CONTENT_TYPE");
  }
  if (response.body === null) throw new AgentProtocolError("AGENT_STREAM_INCOMPLETE");
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";
  let sequence = 0;
  let context: { request_id: string; trace_id: string; agent_run_id: string } | null = null;
  const calls = new Map<string, { name: string; finished: boolean }>();
  let preDispatchStopCode: string | null = null;
  let cancellationRequested = false;
  const cancel = () => {
    if (cancellationRequested) return;
    cancellationRequested = true;
    // A stalled transport cleanup cannot delay parser errors or a revoked view.
    // Cancellation is requested once and any late rejection is consumed safely.
    void reader.cancel().catch(() => undefined);
  };
  signal?.addEventListener("abort", cancel, { once: true });
  try {
    while (true) {
      signal?.throwIfAborted();
      const { done, value } = await reader.read();
      signal?.throwIfAborted();
      try { buffer += done ? decoder.decode() : decoder.decode(value, { stream: true }); }
      catch { throw new AgentProtocolError("AGENT_STREAM_INVALID_EVENT"); }
      buffer = buffer.replace(/\r\n/g, "\n").replace(done ? /\r/g : /\r(?!$)/g, "\n");
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        if (block.length > MAX_FRAME_LENGTH) throw new AgentProtocolError("AGENT_STREAM_INVALID_EVENT");
        for (const event of parseAgentSseEvents(block)) {
          signal?.throwIfAborted();
          if (event.sequence !== sequence++) throw new AgentProtocolError("AGENT_STREAM_SEQUENCE_MISMATCH");
          const headerRequest = response.headers.get("x-request-id");
          if (headerRequest !== null && headerRequest !== event.request_id) throw new AgentProtocolError("AGENT_STREAM_CONTEXT_MISMATCH");
          if (context === null) {
            if (event.event !== "started" && !(event.sequence === 0 && event.event === "error" && event.agent_run_id === null)) throw new AgentProtocolError();
            if (event.event === "started") context = { request_id: event.request_id, trace_id: event.trace_id, agent_run_id: event.agent_run_id! };
          } else if (event.event === "started" || event.request_id !== context.request_id || event.trace_id !== context.trace_id || event.agent_run_id !== context.agent_run_id) {
            throw new AgentProtocolError("AGENT_STREAM_CONTEXT_MISMATCH");
          }
          if (preDispatchStopCode !== null && event.event !== "final" && event.event !== "error") throw new AgentProtocolError();
          if (event.event === "tool_call") {
            if (calls.size >= 20 || calls.has(event.data.tool_call_id)) throw new AgentProtocolError();
            calls.set(event.data.tool_call_id, { name: event.data.tool_name, finished: false });
          } else if (event.event === "tool_result") {
            const call = calls.get(event.data.tool_call_id);
            if (call === undefined) {
              // Runtime budget/repeat guards reject an attempted action before
              // dispatch, so those two results intentionally have no tool_call.
              const code = event.data.metadata.error_code;
              if (event.data.status !== "error" || (event.data.metadata.latency_ms !== undefined && event.data.metadata.latency_ms !== 0) ||
                  (code !== "MAX_TOOL_CALLS_REACHED" && code !== "REPEATED_ACTION_DETECTED") ||
                  [...calls.values()].some((known) => !known.finished)) throw new AgentProtocolError();
              preDispatchStopCode = code;
            } else {
              if (call.finished || call.name !== event.data.tool_name) throw new AgentProtocolError();
              call.finished = true;
            }
          }
          if (event.event === "final" && preDispatchStopCode !== null &&
              (event.data.status !== "stopped" || event.data.termination_reason !== preDispatchStopCode)) throw new AgentProtocolError();
          if (event.event === "final" && event.data.status === "completed" && [...calls.values()].some((call) => !call.finished)) throw new AgentProtocolError();
          yield event;
          if (event.event === "final" || event.event === "error") return;
        }
      }
      if (buffer.length > MAX_FRAME_LENGTH) throw new AgentProtocolError("AGENT_STREAM_INVALID_EVENT");
      if (done) throw new AgentProtocolError("AGENT_STREAM_INCOMPLETE");
    }
  } finally {
    signal?.removeEventListener("abort", cancel);
    cancel();
    reader.releaseLock();
  }
}
