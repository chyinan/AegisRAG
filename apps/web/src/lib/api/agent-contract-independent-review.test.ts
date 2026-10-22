import { afterEach, describe, expect, it, vi } from "vitest";
import fixtures from "@/test/fixtures/agent-lifecycle.json";
import { PERSONAS } from "@/lib/auth";
import { decodeAgentCitation, decodeAgentRun, decodeDurableAgentRun } from "./agent-contract";
import { getDurableAgent } from "./agent-client";
import { parseAgentSseEvents, readAgentSseStream } from "./agent-sse";
import type { AgentStreamEvent } from "./types";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Independent Agent contract review", () => {
  // Both values are accepted by AgentFinalAnswer and AgentCitationRef in
  // packages/agent/dto.py: Python/Pydantic string bounds count Unicode points.
  it("accepts backend-valid supplementary characters at the answer boundary", () => {
    const answer = "🌍".repeat(6001);
    const terminal = parseAgentSseEvents(fixtures.streams.success).at(-1)!;
    expect(decodeAgentRun({ ...terminal.data, final_answer: answer }).final_answer).toBe(answer);
    expect(decodeDurableAgentRun({ ...fixtures.durable.completed.data, final_answer: answer }).final_answer).toBe(answer);
  });

  it("accepts backend-valid supplementary characters in citation sources", () => {
    const source = "🌍".repeat(101);
    expect(decodeAgentCitation({ document_id: "d", version_id: "v", chunk_id: "c", source }).source).toBe(source);
  });

  it("still rejects over-limit Unicode codepoint counts", () => {
    const terminal = parseAgentSseEvents(fixtures.streams.success).at(-1)!;
    expect(() => decodeAgentRun({ ...terminal.data, final_answer: "🌍".repeat(12001) })).toThrow();
    expect(() => decodeAgentCitation({ document_id: "d", version_id: "v", chunk_id: "c", source: "🌍".repeat(201) })).toThrow();
  });

  it("preserves exact safe integer edge arguments without a redaction detour", async () => {
    const args = { input: Number.MAX_SAFE_INTEGER, small: Number.MIN_VALUE, nested: { raw_output: "literal argument" } };
    const payload = { ...fixtures.durable.awaitingApproval, data: { ...fixtures.durable.awaitingApproval.data, approval: { ...fixtures.durable.awaitingApproval.data.approval, arguments: args } } };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(payload))));
    const result = await getDurableAgent(PERSONAS.platform_admin, payload.data.run_id);
    expect(result.approval?.arguments).toEqual(args);
  });

  it("accepts bare-CR SSE framing split one byte at a time", async () => {
    const bytes = new TextEncoder().encode(fixtures.streams.success.replaceAll("\n", "\r"));
    let offset = 0;
    const response = new Response(new ReadableStream<Uint8Array>({ pull(controller) {
      if (offset === bytes.length) controller.close();
      else controller.enqueue(bytes.slice(offset, ++offset));
    } }), { headers: { "content-type": "text/event-stream" } });
    const events: AgentStreamEvent[] = [];
    for await (const event of readAgentSseStream(response)) events.push(event);
    expect(events).toHaveLength(4);
    expect(events.at(-1)?.event).toBe("final");
  });

  it("never accepts a completed final while a tool dispatch is unresolved", async () => {
    const parsed = parseAgentSseEvents(fixtures.streams.success);
    const events = [parsed[0], parsed[1], { ...parsed[3], sequence: 2 }];
    const wire = events.map((event) => `id: ${event.sequence}\nevent: ${event.event}\ndata: ${JSON.stringify(event)}\n\n`).join("");
    const accepted: AgentStreamEvent[] = [];
    const consume = async () => { for await (const event of readAgentSseStream(new Response(wire, { headers: { "content-type": "text/event-stream" } }))) {
      accepted.push(event);
    } };
    await expect(consume()).rejects.toThrow();
    expect(accepted.some((event) => event.event === "final")).toBe(false);
  });

  it("does not let an uncooperative stream cancellation hide a parser failure", async () => {
    let releaseCancel!: () => void;
    const cancellation = new Promise<void>((resolve) => { releaseCancel = resolve; });
    const cancel = vi.fn(() => cancellation);
    const body = new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(new TextEncoder().encode("event: started\nid: 0\ndata: not-json\n\n")); },
      cancel
    });
    let settled = false;
    const consume = (async () => { for await (const event of readAgentSseStream(new Response(body, { headers: { "content-type": "text/event-stream" } }))) void event; })();
    const observed = consume.then(() => { settled = true; }, () => { settled = true; });
    await new Promise((resolve) => setTimeout(resolve, 25));
    const settledBeforeCancel = settled;
    releaseCancel();
    await observed;
    expect(cancel).toHaveBeenCalledOnce();
    expect(settledBeforeCancel).toBe(true);
  });
});
