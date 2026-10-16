import { describe, expect, it } from "vitest";
import fixtures from "@/test/fixtures/agent-lifecycle.json";
import { AgentProtocolError, decodeAgentRun, decodeDurableAgentRun } from "./agent-contract";
import { parseAgentSseEvents } from "./agent-sse";

describe("Agent response contracts", () => {
  it("decodes actual backend durable DTO envelopes and minimal Agent citation references", () => {
    for (const fixture of Object.values(fixtures.durable)) expect(decodeDurableAgentRun(fixture.data).run_id).toBe(fixture.data.run_id);
    const completed = decodeDurableAgentRun(fixtures.durable.completed.data);
    expect(completed.final_citations[0]).toMatchObject({ document_id: "doc-pilot", version_id: "version-pilot", chunk_id: "chunk-pilot", source: "Pilot handbook.md", observation_index: 0 });
    expect(completed.final_citations[0]).not.toHaveProperty("source_display_name");
    const event = parseAgentSseEvents(fixtures.streams.success).at(-1)!;
    expect(event.event).toBe("final");
    expect(decodeAgentRun(event.data).final_answer).toContain("Remote work 🌍");
  });

  it("preserves exact nonsecret approval arguments including fields the generic redactor would remove", () => {
    const argumentsValue = { query: "Exact <script>alert(1)</script> 🌍", source_uri: "logical-source", prompt: { unchanged: true }, nested: [1, null, "value"] };
    const raw = { ...fixtures.durable.awaitingApproval.data, approval: { ...fixtures.durable.awaitingApproval.data.approval, arguments: argumentsValue } };
    expect(decodeDurableAgentRun(raw).approval?.arguments).toEqual(argumentsValue);
  });

  it.each([
    { password: "hidden" }, { nested: { access_token: "hidden" } }, { query: "bearer hidden" },
    { apiKey: "visible" }, { large: 9007199254740992 }, { minusZero: -0 }, { value: Infinity }
  ])("rejects unsafe or inexact approval arguments instead of silently redacting: %j", (argumentsValue) => {
    const raw = { ...fixtures.durable.awaitingApproval.data, approval: { ...fixtures.durable.awaitingApproval.data.approval, arguments: argumentsValue } };
    expect(() => decodeDurableAgentRun(raw)).toThrow(AgentProtocolError);
  });

  it.each([
    { schema_version: 2 }, { revision: -1 }, { steps_used: 21 }, { tool_calls_used: 0.1 }, { deadline_at: NaN },
    { status: "new_state" }, { approval: null }, { final_answer: "unvalidated" }, { final_citations: [{}] }
  ])("fails closed on an invalid durable state: %j", (patch) => {
    expect(() => decodeDurableAgentRun({ ...fixtures.durable.awaitingApproval.data, ...patch })).toThrow(AgentProtocolError);
  });

  it("never includes rejected private payloads in exceptions", () => {
    try { decodeDurableAgentRun({ ...fixtures.durable.awaitingApproval.data, approval: { ...fixtures.durable.awaitingApproval.data.approval, arguments: { password: "do-not-leak-this" } } }); }
    catch (error) { expect(String(error)).not.toContain("do-not-leak-this"); }
  });

  it("rejects nonterminal one-shot results and unvalidated final answers", () => {
    const event = parseAgentSseEvents(fixtures.streams.success).at(-1)!;
    expect(() => decodeAgentRun({ ...event.data, status: "running" })).toThrow();
    expect(() => decodeAgentRun({ ...event.data, status: "failed" })).toThrow();
  });
});
