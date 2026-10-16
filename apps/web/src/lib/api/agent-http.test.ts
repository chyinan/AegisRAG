import { describe, expect, it, vi } from "vitest";
import { MAX_AGENT_JSON_BYTES, parseAgentEnvelope } from "./agent-http";

const envelope = '{"request_id":"request-safe","data":{"value":"🌍"},"error":null}';
const encoder = new TextEncoder();
const headers = { "content-type": "application/json" };
function streamed(parts: Uint8Array[], cancel = vi.fn()) {
  return new Response(new ReadableStream<Uint8Array>({
    pull(controller) { const next = parts.shift(); if (next === undefined) controller.close(); else controller.enqueue(next); }, cancel
  }), { headers });
}

describe("Bounded Agent JSON transport", () => {
  it("preserves exact success values across split UTF-8 bytes", async () => {
    const bytes = encoder.encode(envelope);
    expect((await parseAgentEnvelope(streamed(Array.from(bytes, (byte) => new Uint8Array([byte]))))).data).toEqual({ value: "🌍" });
  });
  it("accepts exactly the byte cap and rejects one extra byte before parsing", async () => {
    const padding = " ".repeat(MAX_AGENT_JSON_BYTES - encoder.encode(envelope).byteLength);
    expect((await parseAgentEnvelope(new Response(envelope + padding))).data).toEqual({ value: "🌍" });
    await expect(parseAgentEnvelope(new Response(envelope + padding + " "))).rejects.toMatchObject({ structured: { code: "AGENT_RESPONSE_TOO_LARGE" } });
  });
  it("counts UTF-8 bytes despite a false small declared length", async () => {
    const body = JSON.stringify({ data: "🌍".repeat(MAX_AGENT_JSON_BYTES / 4), error: null });
    await expect(parseAgentEnvelope(new Response(body, { headers: { ...headers, "content-length": "12" } }))).rejects.toMatchObject({ structured: { code: "AGENT_RESPONSE_TOO_LARGE" } });
  });
  it("rejects declared oversize without awaiting a stuck cancellation", async () => {
    const cancel = vi.fn(() => new Promise<void>(() => undefined));
    const response = new Response(new ReadableStream({ cancel }), { headers: { "content-length": String(MAX_AGENT_JSON_BYTES + 1) } });
    await expect(parseAgentEnvelope(response)).rejects.toMatchObject({ structured: { code: "AGENT_RESPONSE_TOO_LARGE" } });
    expect(cancel).toHaveBeenCalledOnce();
  });
  it("abort ends a pending read even when underlying cancel never settles", async () => {
    const cancel = vi.fn(() => new Promise<void>(() => undefined));
    const response = new Response(new ReadableStream({ cancel }), { headers });
    const owner = new AbortController();
    const pending = parseAgentEnvelope(response, undefined, owner.signal);
    owner.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(cancel).toHaveBeenCalledOnce();
    expect(response.body?.locked).toBe(false);
  });
  it("does not read an already-aborted response", async () => {
    const response = streamed([encoder.encode(envelope)]);
    const owner = new AbortController(); owner.abort();
    await expect(parseAgentEnvelope(response, undefined, owner.signal)).rejects.toMatchObject({ name: "AbortError" });
  });
  it("keeps safe structured HTTP errors and hides unreadable body text", async () => {
    const response = new Response('{"request_id":"safe","data":null,"error":{"code":"DURABLE_RUN_NOT_FOUND","message":"Not found"}}', { status: 404 });
    await expect(parseAgentEnvelope(response)).rejects.toMatchObject({ status: 404, structured: { code: "DURABLE_RUN_NOT_FOUND", request_id: "safe" } });
    await expect(parseAgentEnvelope(new Response("PRIVATE_INVALID_BODY"))).rejects.toMatchObject({ structured: { code: "INVALID_RESPONSE" } });
  });
  it("rejects invalid UTF-8 and hides transport failure details", async () => {
    await expect(parseAgentEnvelope(streamed([new Uint8Array([0xff])]))).rejects.toMatchObject({ structured: { code: "AGENT_INVALID_RESPONSE" } });
    const response = new Response(new ReadableStream({ start(controller) { controller.error(new Error("PRIVATE_TRANSPORT_DETAIL")); } }));
    await expect(parseAgentEnvelope(response)).rejects.toMatchObject({ message: "The Agent response could not be verified safely. Refresh the run before taking another action." });
  });
  it("passes original approval arguments to strict decoder without redaction", async () => {
    const response = new Response('{"request_id":"safe","data":{"arguments":{"raw_output":"literal","n":9007199254740991}},"error":null}');
    const decode = vi.fn((value: unknown) => value);
    expect((await parseAgentEnvelope(response, decode)).data).toEqual({ arguments: { raw_output: "literal", n: Number.MAX_SAFE_INTEGER } });
    expect(decode).toHaveBeenCalledOnce();
  });
});
