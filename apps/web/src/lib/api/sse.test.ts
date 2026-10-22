import { describe, expect, it, vi } from "vitest";
import fixtures from "@/test/fixtures/chat-stream.json";
import type { SseEvent } from "./types";
import { parseSseEvents, readSseStream, SseProtocolError } from "./sse";

async function collect(response: Response, signal?: AbortSignal): Promise<SseEvent[]> {
  const events: SseEvent[] = [];
  for await (const event of readSseStream(response, signal)) events.push(event);
  return events;
}

function chunksResponse(chunks: Uint8Array[], cancel = vi.fn()): Response {
  return new Response(new ReadableStream<Uint8Array>({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(chunk));
      controller.close();
    },
    cancel
  }));
}

const context = { request_id: "req-test", trace_id: "trace-test" };
const frame = (type: string, data: unknown) => `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;

describe("backend SSE contract", () => {
  it("parses delta, nested citations, success status and no_answer exactly as serialized", () => {
    const events = parseSseEvents(fixtures.success);
    expect(events[0]).toMatchObject({ type: "token", data: { delta: "Working ", index: 0 } });
    expect(events[2]).toMatchObject({ type: "citation", data: { citation: { document_id: "doc-pilot" } } });
    expect(events[2]).toMatchObject({ data: { citation: { citation_id: "cite-a3b7d982cee7ed64" } } });
    expect(events.at(-1)).toMatchObject({ type: "final", data: { status: "success", no_answer: false } });
    expect(parseSseEvents(fixtures.noAnswer)[0]).toMatchObject({ type: "final", data: { status: "success", no_answer: true } });
  });

  it("normalizes only safe tool metadata for presentation", () => {
    const events = parseSseEvents(fixtures.tools);
    expect(events[0]).toMatchObject({ type: "tool_call", data: { status: "started", agent_run_id: "run-fixture" } });
    expect(events[1]).toMatchObject({ type: "tool_result", data: { status: "success", latency_ms: 12 } });
    expect(events[0].data).not.toHaveProperty("metadata");
  });

  it("removes unsafe citation fields rather than trusting type assertions", () => {
    const [event] = parseSseEvents(frame("citation", {
      ...context, citation: { document_id: "doc", chunk_id: "chunk", source_uri: "file:///private", acl: {}, prompt: "secret" }
    }));
    expect(event).toMatchObject({ type: "citation", data: { citation: { document_id: "doc", chunk_id: "chunk" } } });
    expect(JSON.stringify(event)).not.toContain("private");
    expect(JSON.stringify(event)).not.toContain("secret");
  });

  it.each<[string, unknown]>([
    ["token", { ...context, token: "old contract" }],
    ["token", { ...context, index: -1, delta: "invalid" }],
    ["citation", { ...context, document_id: "doc", chunk_id: "chunk" }],
    ["citation", { ...context, citation: { document_id: "doc", chunk_id: "chunk", title_path: [42] } }],
    ["citation", { ...context, citation: { document_id: "doc", chunk_id: "chunk", citation_id: 42 } }],
    ["citation", { ...context, citation: { document_id: "doc", chunk_id: "chunk", citation_id: "cite-not-a-canonical-id" } }],
    ["final", { ...context, answer: "done", citations: [], status: "answered", no_answer: false }],
    ["final", { ...context, answer: "done", citations: "bad", status: "success", no_answer: false }],
    ["final", { ...context, answer: "done", citations: [], status: "success", no_answer: "false" }],
    ["error", { ...context, message: 42, code: "ERROR", terminal: true }],
    ["token", []],
    ["final", null]
  ])("rejects malformed %s events without rendering raw data", (type, data) => {
    expect(() => parseSseEvents(frame(type, data))).toThrow(SseProtocolError);
  });

  it("ignores comments/unknown events even if their data is not JSON", () => {
    expect(parseSseEvents(": keepalive\n\nevent: future\ndata: private unstructured output\n\n")).toEqual([]);
    expect(() => parseSseEvents("event: token\ndata: secret invalid JSON\n\n")).toThrow("could not be read safely");
  });

  it("supports multiline data, CRLF and UTF-8 divided at every possible byte", async () => {
    const bytes = new TextEncoder().encode(fixtures.success.replace(/\n/g, "\r\n"));
    const events = await collect(chunksResponse(Array.from(bytes, (byte) => new Uint8Array([byte]))));
    expect(events).toEqual(parseSseEvents(fixtures.success));
    expect(events[1]).toMatchObject({ data: { delta: "remotely 🌍 is supported." } });
    const multiline = `event: token\ndata: {"request_id":"req",\ndata: "trace_id":"trace","index":0,"delta":"ok"}\n\n`;
    expect(parseSseEvents(multiline)[0]).toMatchObject({ data: { delta: "ok" } });
  });

  it("makes EOF without final a recoverable terminal failure with the last request ID", async () => {
    await expect(collect(new Response(fixtures.tokensOnly))).rejects.toMatchObject({ structured: {
      code: "CHAT_STREAM_INCOMPLETE", request_id: "req-stream-fixture"
    } });
    await expect(collect(new Response(fixtures.noAnswer.trimEnd()))).rejects.toMatchObject({ structured: { code: "CHAT_STREAM_INCOMPLETE" } });
    await expect(collect(new Response(null, { headers: { "x-request-id": "req-empty" } }))).rejects.toMatchObject({
      structured: { code: "CHAT_STREAM_INCOMPLETE", request_id: "req-empty" }
    });
  });

  it("stops at final and cancels an open reader instead of applying trailing events", async () => {
    const cancel = vi.fn();
    const response = new Response(new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(new TextEncoder().encode(fixtures.noAnswer + fixtures.tokensOnly)); },
      cancel
    }));
    expect(await collect(response)).toHaveLength(1);
    expect(cancel).toHaveBeenCalledOnce();
    expect(response.body?.locked).toBe(false);
  });

  it("cancels and releases the reader when aborted during a pending read", async () => {
    const cancel = vi.fn();
    const response = new Response(new ReadableStream<Uint8Array>({ cancel }));
    const controller = new AbortController();
    const events = collect(response, controller.signal);
    controller.abort();
    await expect(events).rejects.toMatchObject({ name: "AbortError" });
    expect(cancel).toHaveBeenCalledOnce();
    expect(response.body?.locked).toBe(false);
  });

  it("closes a response that arrives after its request was already aborted", async () => {
    const cancel = vi.fn();
    const response = new Response(new ReadableStream<Uint8Array>({ cancel }));
    const controller = new AbortController();
    controller.abort();
    await expect(collect(response, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(cancel).toHaveBeenCalledOnce();
    expect(response.body?.locked).toBe(false);
  });

  it("cancels malformed streams and preserves safe request context without parser details", async () => {
    const cancel = vi.fn();
    const response = new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(fixtures.tokensOnly + "event: final\ndata: private-invalid-json\n\n"));
      },
      cancel
    }));
    await expect(collect(response)).rejects.toMatchObject({ structured: {
      code: "CHAT_STREAM_INVALID_EVENT", request_id: "req-stream-fixture",
      message: "The answer stream could not be read safely. Please try again."
    } });
    expect(cancel).toHaveBeenCalledOnce();
    expect(response.body?.locked).toBe(false);
  });

  it("cancels when the consumer leaves before final", async () => {
    const cancel = vi.fn();
    const response = new Response(new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(new TextEncoder().encode(fixtures.tokensOnly)); },
      cancel
    }));
    for await (const event of readSseStream(response)) { expect(event.type).toBe("token"); break; }
    expect(cancel).toHaveBeenCalledOnce();
    expect(response.body?.locked).toBe(false);
  });
});
