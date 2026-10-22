// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { createServer } from "node:http";
import { EXECUTION_PROXY_BUDGET_MS as BUDGET, EXECUTION_PROXY_MAX_REQUEST_BYTES, EXECUTION_PROXY_MAX_RESPONSE_BYTES, proxyExecution } from "./execution-proxy";

const encoder = new TextEncoder();
const base = "http://api.test:8000";
function request(path: string[], body?: unknown, signal?: AbortSignal): Request {
  return new Request(`http://workbench.test/api/execution/${path.join("/")}`, {
    method: "POST", signal,
    headers: { "content-type": "application/json", authorization: "Bearer fixture", "x-request-id": "request-fixture", "x-trace-id": "trace-fixture" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) })
  });
}
function state(overrides: Record<string, unknown> = {}) {
  return Response.json({ request_id: "request-fixture", data: { run_id: "run-1", execution_mode: "inline", budget_mode: "wall_clock", ...overrides }, error: null });
}
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe("Scoped Workbench execution transport", () => {
  it.each([
    ["agent", "run"], ["agent", "run", "stream"], ["agent", "durable"],
    ["sources", "folder", "sync", "plans", "plan-1", "batch"],
    ["sources", "folder", "sync", "plans", "plan-1", "reconcile"]
  ])("forwards the named POST %j with unchanged status/body", async (...path) => {
    const transport = vi.fn<typeof fetch>().mockResolvedValue(new Response('{"data":null,"error":{"code":"FIXTURE_CONFLICT"}}', {
      status: 409, headers: { "content-type": "application/json", "x-request-id": "upstream-request", "retry-after": "3" }
    }));
    const response = await proxyExecution(request(path, { input: "fixture" }), path, { apiBaseUrl: base, fetch: transport });
    expect(response.status).toBe(409);
    expect(response.headers.get("x-request-id")).toBe("upstream-request");
    expect(response.headers.get("retry-after")).toBe("3");
    expect(await response.json()).toEqual({ data: null, error: { code: "FIXTURE_CONFLICT" } });
    expect(transport.mock.calls[0][0]).toEqual(new URL(`${base}/${path.join("/")}`));
    expect(transport.mock.calls[0][1]).toMatchObject({ method: "POST", cache: "no-store", redirect: "manual" });
  });

  it("keeps ordinary reads and other operations out of the execution route", async () => {
    const transport = vi.fn<typeof fetch>();
    const response = await proxyExecution(request(["queue", "jobs"]), ["queue", "jobs"], { fetch: transport });
    expect(response.status).toBe(404);
    const method = await proxyExecution(new Request("http://workbench.test/api/execution/agent/run"), ["agent", "run"], { fetch: transport });
    expect(method.status).toBe(405);
    expect(method.headers.get("allow")).toBe("POST");
    expect(transport).not.toHaveBeenCalled();
  });

  it("preserves the app authentication and correlation header contract", async () => {
    const input = request(["agent", "run"], { input: "fixture" });
    const expected = { "x-user-id": "fixture-user", "x-tenant-id": "fixture-tenant", "x-roles": "employee", "x-department": "platform", "x-permissions": "agent:run", traceparent: "00-fixture" };
    for (const [name, value] of Object.entries(expected)) input.headers.set(name, value);
    const transport = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ data: {} }));
    const response = await proxyExecution(input, ["agent", "run"], { apiBaseUrl: base, fetch: transport });
    await response.text();
    const forwarded = new Headers(transport.mock.calls[0][1]?.headers);
    expect(forwarded.get("authorization")).toBe("Bearer fixture");
    expect(forwarded.get("x-request-id")).toBe("request-fixture");
    expect(forwarded.get("x-trace-id")).toBe("trace-fixture");
    for (const [name, value] of Object.entries(expected)) expect(forwarded.get(name)).toBe(value);
  });

  it("forwards an SSE chunk before completion and cleans up only after EOF", async () => {
    vi.useFakeTimers();
    let upstream!: ReadableStreamDefaultController<Uint8Array>;
    const input = request(["agent", "run", "stream"], { input: "fixture" });
    const removeListener = vi.spyOn(input.signal, "removeEventListener");
    const transport = vi.fn<typeof fetch>().mockResolvedValue(new Response(new ReadableStream<Uint8Array>({ start(controller) { upstream = controller; } }), { headers: { "content-type": "text/event-stream" } }));
    const response = await proxyExecution(input, ["agent", "run", "stream"], { fetch: transport });
    const reader = response.body!.getReader();
    upstream.enqueue(encoder.encode("event: token\ndata: fixture\n\n"));
    expect(new TextDecoder().decode((await reader.read()).value)).toBe("event: token\ndata: fixture\n\n");
    expect(vi.getTimerCount()).toBe(1);
    upstream.close();
    expect((await reader.read()).done).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
    expect(removeListener).toHaveBeenCalledWith("abort", expect.any(Function));
  });

  it.each([
    { name: "ordinary one-shot", path: ["agent", "run"], body: { input: "fixture" }, budget: BUDGET.agent },
    { name: "ordinary live stream", path: ["agent", "run", "stream"], body: { input: "fixture" }, budget: BUDGET.agent },
    { name: "background submission", path: ["agent", "durable"], body: { input: "fixture", execution_mode: "background", budget_mode: "active", timeout_seconds: 3600 }, budget: BUDGET.background },
    { name: "ordinary durable inline", path: ["agent", "durable"], body: { input: "fixture" }, budget: BUDGET.agent },
    { name: "explicit legacy active inline", path: ["agent", "durable"], body: { input: "fixture", budget_mode: "active", timeout_seconds: 3600 }, budget: BUDGET.maximumInline },
    { name: "folder batch", path: ["sources", "folder", "sync", "plans", "plan-1", "batch"], budget: BUDGET.folder },
    { name: "folder reconcile", path: ["sources", "folder", "sync", "plans", "plan-1", "reconcile"], body: { expected_revision: 1, document_ids: [] }, budget: BUDGET.folder }
  ])("enforces the finite $name deadline before headers", async ({ path, body, budget }) => {
    vi.useFakeTimers();
    const transport = vi.fn<typeof fetch>().mockImplementation(() => new Promise<Response>(() => undefined));
    const pending = proxyExecution(request(path, body), path, { fetch: transport });
    await vi.advanceTimersByTimeAsync(0);
    expect(transport).toHaveBeenCalledOnce();
    const upstreamSignal = transport.mock.calls[0][1]?.signal;
    await vi.advanceTimersByTimeAsync(budget - 1);
    expect(upstreamSignal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect((await pending).status).toBe(504);
    expect(upstreamSignal?.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([
    { stateOptions: { execution_mode: "background", budget_mode: "active", active_seconds_remaining: 3600 }, budget: BUDGET.background },
    { stateOptions: { budget_mode: "active", active_seconds_remaining: 250 }, budget: 265_000 },
    { stateOptions: {}, budget: BUDGET.agent }
  ])("reads the current run before selecting the resume deadline $budget", async ({ stateOptions, budget }) => {
    vi.useFakeTimers();
    const path = ["agent", "durable", "run-1", "resume"];
    const body = { expected_revision: 4, approval_id: "approval-1", approve: true };
    const transport = vi.fn<typeof fetch>().mockResolvedValueOnce(state(stateOptions)).mockImplementationOnce(() => new Promise<Response>(() => undefined));
    const pending = proxyExecution(request(path, body), path, { apiBaseUrl: base, fetch: transport });
    await vi.advanceTimersByTimeAsync(0);
    expect(transport).toHaveBeenCalledTimes(2);
    expect(transport.mock.calls[0][0]).toEqual(new URL(`${base}/agent/durable/run-1`));
    expect(transport.mock.calls[0][1]).toMatchObject({ method: "GET", cache: "no-store", redirect: "manual" });
    expect(new Headers(transport.mock.calls[0][1]?.headers).get("authorization")).toBe("Bearer fixture");
    expect(JSON.parse(new TextDecoder().decode(transport.mock.calls[1][1]?.body as Uint8Array))).toEqual(body);
    await vi.advanceTimersByTimeAsync(budget);
    expect((await pending).status).toBe(504);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("returns a failed current-state read unchanged without submitting resume", async () => {
    const path = ["agent", "durable", "run-1", "resume"];
    const payload = { data: null, error: { code: "DURABLE_RUN_NOT_FOUND" } };
    const transport = vi.fn<typeof fetch>().mockResolvedValue(Response.json(payload, { status: 404 }));
    const response = await proxyExecution(request(path, { expected_revision: 1 }), path, { fetch: transport });
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual(payload);
    expect(transport).toHaveBeenCalledOnce();
  });

  it("bounds the current-state read independently of a long execution budget", async () => {
    vi.useFakeTimers();
    const path = ["agent", "durable", "run-1", "resume"];
    const transport = vi.fn<typeof fetch>().mockImplementation(() => new Promise<Response>(() => undefined));
    const pending = proxyExecution(request(path, { expected_revision: 1 }), path, { fetch: transport });
    await vi.advanceTimersByTimeAsync(BUDGET.stateRead);
    expect((await pending).status).toBe(504);
    expect(transport).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps the deadline alive after headers while a body is stalled", async () => {
    vi.useFakeTimers();
    const cancel = vi.fn(() => new Promise<void>(() => undefined));
    const transport = vi.fn<typeof fetch>().mockResolvedValue(new Response(new ReadableStream<Uint8Array>({ cancel })));
    const response = await proxyExecution(request(["agent", "run"], { input: "fixture" }), ["agent", "run"], { fetch: transport });
    const body = response.text();
    const rejected = expect(body).rejects.toMatchObject({ name: "AbortError" });
    await vi.advanceTimersByTimeAsync(BUDGET.agent);
    await rejected;
    expect(cancel).toHaveBeenCalledOnce();
    expect(transport.mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("propagates downstream cancellation and releases timers/listeners", async () => {
    vi.useFakeTimers();
    const cancel = vi.fn(() => new Promise<void>(() => undefined));
    const input = request(["agent", "run", "stream"], { input: "fixture" });
    const removeListener = vi.spyOn(input.signal, "removeEventListener");
    const transport = vi.fn<typeof fetch>().mockResolvedValue(new Response(new ReadableStream<Uint8Array>({ cancel })));
    const response = await proxyExecution(input, ["agent", "run", "stream"], { fetch: transport });
    await response.body!.cancel("viewer closed");
    expect(cancel).toHaveBeenCalledOnce();
    expect(transport.mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(removeListener).toHaveBeenCalledWith("abort", expect.any(Function));
    expect(vi.getTimerCount()).toBe(0);
  });

  it("propagates incoming abort before headers and clears its deadline", async () => {
    vi.useFakeTimers();
    const incoming = new AbortController();
    const transport = vi.fn<typeof fetch>().mockImplementation(() => new Promise<Response>(() => undefined));
    const pending = proxyExecution(request(["agent", "run"], { input: "fixture" }, incoming.signal), ["agent", "run"], { fetch: transport });
    await vi.advanceTimersByTimeAsync(0);
    incoming.abort();
    expect((await pending).status).toBe(499);
    expect(transport.mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("bounds a stalled incoming body without contacting the API", async () => {
    vi.useFakeTimers();
    const cancel = vi.fn(() => new Promise<void>(() => undefined));
    const input = new Request("http://workbench.test/api/execution/agent/run", {
      method: "POST", body: new ReadableStream<Uint8Array>({ cancel }), duplex: "half"
    } as RequestInit & { duplex: "half" });
    const transport = vi.fn<typeof fetch>();
    const pending = proxyExecution(input, ["agent", "run"], { fetch: transport });
    await vi.advanceTimersByTimeAsync(BUDGET.requestBody);
    expect((await pending).status).toBe(504);
    expect(cancel).toHaveBeenCalledOnce();
    expect(transport).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("bounds request and forwarded response bytes", async () => {
    const transport = vi.fn<typeof fetch>().mockResolvedValue(new Response(new Uint8Array(EXECUTION_PROXY_MAX_RESPONSE_BYTES + 1)));
    const oversized = new Request("http://workbench.test/api/execution/agent/run", { method: "POST", body: "x".repeat(EXECUTION_PROXY_MAX_REQUEST_BYTES + 1) });
    expect((await proxyExecution(oversized, ["agent", "run"], { fetch: transport })).status).toBe(413);
    expect(transport).not.toHaveBeenCalled();
    const response = await proxyExecution(request(["agent", "run"], { input: "fixture" }), ["agent", "run"], { fetch: transport });
    await expect(response.text()).rejects.toThrow("Execution proxy response could not complete.");
    expect(transport.mock.calls[0][1]?.signal?.aborted).toBe(true);
  });

  it("cleans up an empty successful response without waiting for a deadline", async () => {
    vi.useFakeTimers();
    const response = await proxyExecution(request(["agent", "run"], { input: "fixture" }), ["agent", "run"], {
      fetch: vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }))
    });
    expect(response.status).toBe(204);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("uses the owned Node transport against a local HTTP fixture", async () => {
    const seen: { method: string | undefined; path: string | undefined; authorization: string | undefined; body: string }[] = [];
    const server = createServer((incoming, outgoing) => {
      const chunks: Buffer[] = [];
      incoming.on("data", (chunk: Buffer) => { chunks.push(chunk); });
      incoming.once("end", () => {
        seen.push({ method: incoming.method, path: incoming.url, authorization: incoming.headers.authorization, body: Buffer.concat(chunks).toString("utf8") });
        outgoing.writeHead(409, { "content-type": "application/json", "x-request-id": "local-fixture" });
        outgoing.end('{"data":null,"error":{"code":"LOCAL_FIXTURE"}}');
      });
    });
    await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
    try {
      const address = server.address();
      if (address === null || typeof address === "string") throw new Error("Local HTTP fixture has no port.");
      const response = await proxyExecution(request(["agent", "run"], { input: "fixture" }), ["agent", "run"], {
        apiBaseUrl: `http://127.0.0.1:${address.port}`
      });
      expect(response.status).toBe(409);
      expect(response.headers.get("x-request-id")).toBe("local-fixture");
      expect(await response.json()).toEqual({ data: null, error: { code: "LOCAL_FIXTURE" } });
      expect(seen).toEqual([{ method: "POST", path: "/agent/run", authorization: "Bearer fixture", body: '{"input":"fixture"}' }]);
    } finally {
      server.closeAllConnections();
      await new Promise<void>((resolve, reject) => { server.close((error) => { if (error) reject(error); else resolve(); }); });
    }
  });

  it("closes the owned HTTP stream when its downstream reader cancels", async () => {
    let closed!: () => void;
    const connectionClosed = new Promise<void>((resolve) => { closed = resolve; });
    const server = createServer((incoming, outgoing) => {
      incoming.resume();
      outgoing.once("close", closed);
      outgoing.writeHead(200, { "content-type": "text/event-stream" });
      outgoing.write("event: token\ndata: fixture\n\n");
    });
    await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
    try {
      const address = server.address();
      if (address === null || typeof address === "string") throw new Error("Local HTTP fixture has no port.");
      const response = await proxyExecution(request(["agent", "run", "stream"], { input: "fixture" }), ["agent", "run", "stream"], {
        apiBaseUrl: `http://127.0.0.1:${address.port}`
      });
      const reader = response.body!.getReader();
      expect(new TextDecoder().decode((await reader.read()).value)).toBe("event: token\ndata: fixture\n\n");
      await reader.cancel();
      await connectionClosed;
    } finally {
      server.closeAllConnections();
      await new Promise<void>((resolve, reject) => { server.close((error) => { if (error) reject(error); else resolve(); }); });
    }
  });
});
