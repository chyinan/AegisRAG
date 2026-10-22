import { describe, expect, it, vi } from "vitest";
import fixtures from "@/test/fixtures/agent-lifecycle.json";
import { AgentProtocolError } from "./agent-contract";
import { parseAgentSseEvents, readAgentSseStream } from "./agent-sse";
import type { AgentStreamEvent } from "./types";

async function collect(response: Response, signal?: AbortSignal) {
  const events: AgentStreamEvent[] = [];
  for await (const event of readAgentSseStream(response, signal)) events.push(event);
  return events;
}
function response(wire: string) { return new Response(wire, { headers: { "content-type": "text/event-stream", "x-request-id": "req-agent-fixture" } }); }
function frame(event: AgentStreamEvent) { return `id: ${event.sequence}\nevent: ${event.event}\ndata: ${JSON.stringify(event)}\n\n`; }

describe("dedicated Agent SSE protocol", () => {
  it("reads all actual backend lifecycle fixtures and stops on terminal error without requiring final", async () => {
    for (const key of ["success", "stopped", "error", "errorBeforeStarted", "limitBeforeDispatch", "repeatBeforeDispatch"] as const) {
      const events = await collect(response(fixtures.streams[key]));
      expect(["error", "final"]).toContain(events.at(-1)?.event);
    }
    const events = await collect(response(fixtures.streams.success));
    expect(events.map((event) => event.event)).toEqual(["started", "tool_call", "tool_result", "final"]);
    expect(events[3]).toMatchObject({ event: "final", data: { final_answer: "Remote work 🌍 requires approval.\nAsk your manager." } });
  });

  it("preserves UTF-8 and CRLF across every one-byte chunk", async () => {
    const bytes = new TextEncoder().encode(fixtures.streams.success.replaceAll("\n", "\r\n"));
    let offset = 0;
    const body = new ReadableStream<Uint8Array>({ pull(controller) { if (offset === bytes.length) controller.close(); else controller.enqueue(bytes.slice(offset, ++offset)); } });
    const events = await collect(new Response(body, { headers: { "content-type": "text/event-stream" } }));
    const final = events.at(-1);
    expect(final?.event).toBe("final");
    if (final?.event === "final") expect(final.data.final_answer).toContain("🌍");
  });

  it("ignores comment heartbeats and unknown extensions", async () => {
    expect(parseAgentSseEvents(': heartbeat\n\nevent: provider_raw\ndata: {not-json\n\n')).toEqual([]);
    expect(await collect(response(': heartbeat\n\n' + fixtures.streams.success))).toHaveLength(4);
  });

  it.each([fixtures.streams.incomplete, fixtures.streams.success.trimEnd(), ""])("rejects missing or unterminated final events", async (wire) => {
    await expect(collect(response(wire))).rejects.toMatchObject({ structured: { code: "AGENT_STREAM_INCOMPLETE" } });
  });

  it.each(["sequence", "context", "duplicate", "orphan", "final_identity", "event_mismatch", "frame_id"])("rejects %s drift", async (kind) => {
    const events = parseAgentSseEvents(fixtures.streams.success);
    if (kind === "sequence") events[1].sequence = 5;
    if (kind === "context") events[1].trace_id = "different-trace";
    if (kind === "duplicate") events[2] = { ...events[1], sequence: 2 };
    if (kind === "orphan") events.splice(1, 1);
    if (kind === "final_identity" && events[3].event === "final") events[3].data.agent_run_id = "different-run";
    let wire = events.map(frame).join("");
    if (kind === "event_mismatch") wire = wire.replace("event: started", "event: final");
    if (kind === "frame_id") wire = wire.replace("id: 0", "id: 99");
    await expect(collect(response(wire))).rejects.toBeInstanceOf(AgentProtocolError);
  });

  it("drops unsolicited progress payloads and metadata instead of exposing raw data", async () => {
    const wire = fixtures.streams.success.replace('"metadata":{}', '"metadata":{"raw_output":"hidden","access_token":"hidden"},"arguments":{"password":"hidden"}');
    const events = await collect(response(wire));
    expect(JSON.stringify(events)).not.toContain("hidden");
  });

  it("closes the owned reader on early consumer exit", async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(new TextEncoder().encode(fixtures.streams.incomplete)); }, cancel });
    for await (const event of readAgentSseStream(new Response(body, { headers: { "content-type": "text/event-stream" } }))) { expect(event.event).toBe("started"); break; }
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("abort closes a pending reader and does not produce a fabricated terminal state", async () => {
    const cancel = vi.fn();
    const controller = new AbortController();
    const stream = new ReadableStream<Uint8Array>({ cancel });
    const pending = collect(new Response(stream, { headers: { "content-type": "text/event-stream" } }), controller.signal);
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("rejects wrong content type and oversized unterminated frames", async () => {
    await expect(collect(new Response(fixtures.streams.success))).rejects.toMatchObject({ structured: { code: "AGENT_STREAM_INVALID_CONTENT_TYPE" } });
    await expect(collect(response("x".repeat(1024 * 1024 + 1)))).rejects.toBeInstanceOf(AgentProtocolError);
  });

  it.each(["limitBeforeDispatch", "repeatBeforeDispatch"] as const)("accepts serialized pre-dispatch guard %s only through its matching stopped final", async (key) => {
    const events = await collect(response(fixtures.streams[key]));
    const terminal = events.at(-1);
    expect(terminal?.event).toBe("final");
    if (terminal?.event === "final") {
      expect(terminal.data.status).toBe("stopped");
      expect(terminal.data.final_answer).toBeNull();
    }
  });

  it.each(["orphan_success", "other_error", "positive_latency", "duplicate_rejection", "continuation", "wrong_final"])("rejects invalid pre-dispatch guard variant %s", async (kind) => {
    const events = parseAgentSseEvents(fixtures.streams.limitBeforeDispatch);
    const rejected = events[1];
    if (rejected.event !== "tool_result") throw new Error("Fixture shape changed");
    if (kind === "orphan_success") rejected.data.status = "success";
    if (kind === "other_error") rejected.data.metadata.error_code = "UNRELATED_ERROR";
    if (kind === "positive_latency") rejected.data.metadata.latency_ms = 1;
    if (kind === "duplicate_rejection") events.splice(2, 0, { ...rejected, sequence: 2 });
    if (kind === "continuation") events.splice(2, 0, { ...rejected, event: "tool_call", sequence: 2, data: { ...rejected.data, status: "started" } });
    if (kind === "wrong_final" && events[2].event === "final") events[2].data.termination_reason = "MAX_STEPS_REACHED";
    await expect(collect(response(events.map(frame).join("")))).rejects.toBeInstanceOf(AgentProtocolError);
  });

});
