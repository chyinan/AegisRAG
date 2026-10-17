import { afterEach, describe, expect, it, vi } from "vitest";
import { PERSONAS } from "@/lib/auth";
import fixtures from "@/test/fixtures/agent-lifecycle.json";
import { cancelDurableAgent, getDurableAgent, recoverDurableAgent, resumeDurableAgent, runAgentOnce, startDurableAgent, streamAgent } from "./agent-client";
import { MAX_AGENT_JSON_BYTES } from "./agent-http";
import { parseAgentSseEvents } from "./agent-sse";
import type { AgentStreamEvent, DurableAgentResumeRequest, DurableAgentStartRequest } from "./types";

const auth = { ...PERSONAS.platform_admin, tenantId: "tenant-pilot", userId: "user-pilot" };
const startupKey = "587629a6-65a2-4db2-8e18-c17d8190c0ac";
const id = fixtures.durable.awaitingApproval.data.run_id;
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } }); }
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Agent API requests", () => {
  it("keeps one-shot default body bounds and forwards ownership abort", async () => {
    const event = parseAgentSseEvents(fixtures.streams.success).at(-1)!;
    const fetchMock = vi.fn().mockResolvedValue(json({ request_id: "req-agent-fixture", data: event.data, error: null }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    await runAgentOnce(auth, { input: "  policy  ", max_steps: 99, max_tool_calls: -1, timeout_seconds: 999, idempotency_key: startupKey } as DurableAgentStartRequest, controller.signal);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/execution/agent/run");
    expect(init.signal).toBe(controller.signal);
    expect(JSON.parse(init.body as string)).toEqual({ input: "policy", max_steps: 20, max_tool_calls: 0, timeout_seconds: 120, metadata: { surface: "main_workbench" } });
  });

  it("streams via the Agent POST wire, without replay or alternate identity fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(fixtures.streams.success, { headers: { "content-type": "text/event-stream" } }));
    vi.stubGlobal("fetch", fetchMock);
    const events: AgentStreamEvent[] = [];
    for await (const event of streamAgent(auth, { input: "policy", idempotency_key: startupKey } as DurableAgentStartRequest)) events.push(event);
    expect(events).toHaveLength(4);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/execution/agent/run/stream");
    expect(init.method).toBe("POST");
    expect(init.headers).not.toHaveProperty("Last-Event-ID");
    expect(JSON.parse(init.body as string)).not.toHaveProperty("tenant_id");
    expect(JSON.parse(init.body as string)).not.toHaveProperty("idempotency_key");
  });

  it("sends a whitelisted durable start request with explicit tool approvals", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(fixtures.durable.awaitingApproval));
    vi.stubGlobal("fetch", fetchMock);
    const result = await startDurableAgent(auth, { input: "policy", approval_tools: ["rag_search"], tenant_id: "spoofed", permissions: ["*"] } as DurableAgentStartRequest);
    expect(result.approval?.arguments).toEqual({ query: "Remote-work policy 🌍", top_k: 3 });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/execution/agent/durable");
    expect(init.cache).toBe("no-store");
    expect(JSON.parse(init.body as string)).toEqual({ input: "policy", max_steps: 8, max_tool_calls: 4, timeout_seconds: 30, metadata: { surface: "main_workbench" }, approval_tools: ["rag_search"] });
  });

  it("sends a keyed start and owner-authenticated read with body-only keys and no-store", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(json(fixtures.durable.awaitingApproval)));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    await startDurableAgent(auth, { input: "policy", idempotency_key: startupKey });
    await recoverDurableAgent(auth, startupKey.toUpperCase(), controller.signal);
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)).toHaveProperty("idempotency_key", startupKey);
    expect(url).toBe("/api/backend/agent/durable/recover");
    expect(init).toMatchObject({ method: "POST", cache: "no-store", signal: controller.signal });
    expect(JSON.parse(init.body as string)).toEqual({ idempotency_key: startupKey });
    expect(init.headers).toMatchObject({ "Content-Type": "application/json", "X-Tenant-ID": auth.tenantId, "X-User-ID": auth.userId });
    expect(fetchMock.mock.calls.every(([path]) => !String(path).includes(startupKey))).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each(["", "not-a-key", "587629a6-65a2-5db2-8e18-c17d8190c0ac", "587629a6-65a2-4db2-7e18-c17d8190c0ac", "../escape", ` ${startupKey}`, `${startupKey}?owner=other`])("rejects invalid startup key %s before any request", (key) => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    expect(() => startDurableAgent(auth, { input: "policy", idempotency_key: key })).toThrow();
    expect(() => recoverDurableAgent(auth, key)).toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not retry or change owner context when a key read is not found", async () => {
    const missing = { request_id: "req-missing", data: null, error: { code: "DURABLE_RUN_NOT_FOUND", message: "Run not found.", details: {} } };
    const fetchMock = vi.fn().mockResolvedValue(json(missing, 404)); vi.stubGlobal("fetch", fetchMock);
    const owner = { ...auth, mode: "bearer" as const, bearerToken: "current-owner-token" };
    await expect(recoverDurableAgent(owner, startupKey)).rejects.toMatchObject({ status: 404, structured: { code: "DURABLE_RUN_NOT_FOUND" } });
    expect((fetchMock.mock.calls[0][1] as RequestInit).headers).toEqual({ "Content-Type": "application/json", Authorization: "Bearer current-owner-token" });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("preserves every approval argument from the raw success envelope", async () => {
    const args = { prompt: "must remain visible", source_uri: "logical-resource", nested: { raw_output: "literal argument" } };
    const fixture = { ...fixtures.durable.awaitingApproval, data: { ...fixtures.durable.awaitingApproval.data, approval: { ...fixtures.durable.awaitingApproval.data.approval, arguments: args } } };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(fixture)));
    expect((await getDurableAgent(auth, id)).approval?.arguments).toEqual(args);
  });

  it("uses exact revisions and paired approval fields without echoing tool args or identity", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(json(fixtures.durable.completed)));
    vi.stubGlobal("fetch", fetchMock);
    const approvalId = fixtures.durable.awaitingApproval.data.approval.approval_id;
    await resumeDurableAgent(auth, id, { expected_revision: 2, approval_id: approvalId, approve: true, arguments: { injected: true } } as DurableAgentResumeRequest);
    await cancelDurableAgent(auth, id, 5);
    expect(fetchMock.mock.calls[0][0]).toBe(`/api/execution/agent/durable/${id}/resume`);
    expect(JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)).toEqual({ expected_revision: 2, approval_id: approvalId, approve: true });
    expect(JSON.parse((fetchMock.mock.calls[1][1] as RequestInit).body as string)).toEqual({ expected_revision: 5 });
  });

  it.each(["../escape", "run?admin=true", "https://other/run", "", "run/other"])("rejects an unsafe recovery identifier %s before fetch", (runId) => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    expect(() => getDurableAgent(auth, runId)).toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects duplicate approval tools and incomplete approval decisions before fetch", () => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    expect(() => startDurableAgent(auth, { input: "x", approval_tools: ["rag_search", "rag_search"] })).toThrow();
    expect(() => resumeDurableAgent(auth, id, { expected_revision: 2, approve: true })).toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fails closed on wrong-run results, unknown schema, malformed JSON, and secret-shaped approval", async () => {
    const variants = [
      { ...fixtures.durable.completed, data: { ...fixtures.durable.completed.data, run_id: "other-run" } },
      { ...fixtures.durable.completed, data: { ...fixtures.durable.completed.data, schema_version: 9 } },
      { ...fixtures.durable.awaitingApproval, data: { ...fixtures.durable.awaitingApproval.data, approval: { ...fixtures.durable.awaitingApproval.data.approval, arguments: { access_token: "secret" } } } }
    ];
    for (const fixture of variants) {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(fixture)));
      await expect(getDurableAgent(auth, id)).rejects.toThrow();
    }
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>provider output</html>")));
    await expect(getDurableAgent(auth, id)).rejects.toMatchObject({ structured: { code: "INVALID_RESPONSE" } });
  });

  it.each([["disabled", 503], ["conflict", 409], ["staleApproval", 409], ["expired", 410]] as const)("preserves safe structured %s rejection without retrying", async (key, status) => {
    const fetchMock = vi.fn().mockResolvedValue(json(fixtures.errors[key], status)); vi.stubGlobal("fetch", fetchMock);
    await expect(getDurableAgent(auth, id)).rejects.toMatchObject({ status, structured: { code: fixtures.errors[key].error.code } });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("rejects one-shot and live final owners that disagree with available auth claims", async () => {
    const terminal = parseAgentSseEvents(fixtures.streams.success).at(-1)!;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ request_id: terminal.request_id, data: terminal.data, error: null })));
    await expect(runAgentOnce({ ...auth, userId: "different-user" }, { input: "policy" })).rejects.toMatchObject({ structured: { code: "AGENT_RESPONSE_OWNER_MISMATCH" } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(fixtures.streams.success, { headers: { "content-type": "text/event-stream" } })));
    const events: AgentStreamEvent[] = [];
    const consume = async () => { for await (const event of streamAgent({ ...auth, tenantId: "other-tenant" }, { input: "policy" })) events.push(event); };
    await expect(consume()).rejects.toMatchObject({ structured: { code: "AGENT_RESPONSE_OWNER_MISMATCH" } });
    expect(events.some((event) => event.event === "final")).toBe(false);
  });

});


describe("bounded Agent endpoint integration", () => {
  it.each(["one-shot", "start", "recover", "read", "resume", "cancel", "stream-error"] as const)("caps %s response bytes before parsing JSON", async (endpoint) => {
    const response = new Response(" ".repeat(MAX_AGENT_JSON_BYTES + 1), { status: endpoint === "stream-error" ? 500 : 200 });
    const fetchMock = vi.fn().mockResolvedValue(response); vi.stubGlobal("fetch", fetchMock);
    const consume = async () => {
      switch (endpoint) {
        case "one-shot": return runAgentOnce(auth, { input: "policy" });
        case "start": return startDurableAgent(auth, { input: "policy", idempotency_key: startupKey });
        case "recover": return recoverDurableAgent(auth, startupKey);
        case "read": return getDurableAgent(auth, id);
        case "resume": return resumeDurableAgent(auth, id, { expected_revision: 2 });
        case "cancel": return cancelDurableAgent(auth, id, 2);
        case "stream-error": for await (const event of streamAgent(auth, { input: "policy" })) void event;
      }
    };
    await expect(consume()).rejects.toMatchObject({ structured: { code: "AGENT_RESPONSE_TOO_LARGE" } });
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(response.body?.locked).toBe(false);
  });

  it.each(["one-shot", "recover", "stream-error"] as const)("forwards ownership abort to a stalled %s JSON body without waiting for transport cancellation", async (endpoint) => {
    const cancel = vi.fn(() => new Promise<void>(() => undefined));
    const response = new Response(new ReadableStream({ cancel }), { status: endpoint === "stream-error" ? 500 : 200 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
    const owner = new AbortController();
    const consume = async () => {
      if (endpoint === "one-shot") return runAgentOnce(auth, { input: "policy" }, owner.signal);
      if (endpoint === "recover") return recoverDurableAgent(auth, startupKey, owner.signal);
      for await (const event of streamAgent(auth, { input: "policy" }, owner.signal)) void event;
    };
    const pending = consume();
    await Promise.resolve(); // Let fetch deliver its headers before aborting body ownership.
    owner.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(cancel).toHaveBeenCalledOnce();
    expect(response.body?.locked).toBe(false);
  });

  it("keeps the existing empty one-shot error instead of inventing a result", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ request_id: "empty-run", data: null, error: null })));
    await expect(runAgentOnce(auth, { input: "policy" })).rejects.toMatchObject({ structured: { code: "AGENT_RUN_EMPTY", request_id: "empty-run" } });
  });
});
