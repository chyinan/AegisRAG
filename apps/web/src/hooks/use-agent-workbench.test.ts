import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PERSONAS } from "@/lib/auth";
import fixtures from "@/test/fixtures/agent-lifecycle.json";
import { useAgentWorkbench } from "./use-agent-workbench";

const key = "587629a6-65a2-4db2-8e18-c17d8190c0ac";
const otherKey = "a87629a6-65a2-4db2-8e18-c17d8190c0ac";
const runId = fixtures.durable.running.data.run_id;
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } }); }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
beforeEach(() => { vi.spyOn(crypto, "randomUUID").mockReturnValue(key); vi.spyOn(Date, "now").mockReturnValue(fixtures.clockEpochSeconds * 1000); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });

describe("startup state ownership", () => {
  it("blocks same-frame repeats and all new starts after an interrupted dispatch until explicit reconciliation", async () => {
    const response = deferred<Response>(); const fetchMock = vi.fn().mockReturnValueOnce(response.promise).mockResolvedValueOnce(json(fixtures.durable.completed)); vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useAgentWorkbench(PERSONAS.platform_admin));
    act(() => {
      void result.current.start("durable", { input: "policy" });
      void result.current.start("durable", { input: "policy" });
      result.current.discardUnresolvedStart(true); // A pending request cannot be discarded.
    });
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(result.current.state).toMatchObject({ startupKey: key, unresolvedStart: true, pending: "start" });
    act(() => {
      result.current.interrupt();
      result.current.discardUnresolvedStart(false);
      void result.current.start("durable", { input: "another" });
      void result.current.start("live", { input: "another" });
      void result.current.start("one_shot", { input: "another" });
      void result.current.read(runId);
    });
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(result.current.state).toMatchObject({ startupKey: key, unresolvedStart: true, notice: "start_unknown", pending: null });
    await act(async () => { response.resolve(json(fixtures.durable.running)); await Promise.resolve(); });
    expect(result.current.state.durable).toBeNull();
    await act(async () => { await result.current.recover(key); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result.current.state).toMatchObject({ startupKey: null, unresolvedStart: false, fresh: true, durable: { status: "completed" } });
  });

  it("never replaces an unresolved reference with an unrelated key and never retries on recovery rejection", async () => {
    const missing = { request_id: "req-not-found", data: null, error: { code: "DURABLE_RUN_NOT_FOUND", message: "Not found.", details: {} } };
    const fetchMock = vi.fn().mockRejectedValueOnce(new Error("network loss")).mockResolvedValueOnce(json(missing, 404)); vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useAgentWorkbench(PERSONAS.platform_admin));
    await act(async () => { await result.current.start("durable", { input: "policy" }); });
    await act(async () => { await result.current.recover(otherKey); });
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(result.current.state).toMatchObject({ startupKey: key, unresolvedStart: true, error: { code: "AGENT_UNRESOLVED_START" } });
    await act(async () => { await result.current.recover(key); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result.current.state).toMatchObject({ startupKey: key, unresolvedStart: true, notice: "recovery_not_found" });
    act(() => { result.current.discardUnresolvedStart(true); });
    expect(result.current.state).toMatchObject({ startupKey: null, unresolvedStart: false, notice: "start_discarded" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("invalid key validation never dispatches or creates unknown-start state", async () => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useAgentWorkbench(PERSONAS.platform_admin));
    await act(async () => { await result.current.recover("invalid"); });
    expect(result.current.state).toMatchObject({ startupKey: null, unresolvedStart: false, notice: null, error: { code: "AGENT_INVALID_STARTUP_KEY" } });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([401, 403, 422, 503])("a dispatched %i failure retains the key because status alone cannot prove non-commit", async (status) => {
    const error = { request_id: "req-denied", data: null, error: { code: "REQUEST_DENIED", message: "Rejected.", details: {} } };
    const fetchMock = vi.fn().mockResolvedValue(json(error, status)); vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useAgentWorkbench(PERSONAS.platform_admin));
    await act(async () => { await result.current.start("durable", { input: "policy" }); });
    expect(result.current.state).toMatchObject({ startupKey: key, unresolvedStart: true, notice: "start_unknown", fresh: false });
    act(() => { void result.current.start("durable", { input: "policy" }); });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("the exact disabled-start factory rejection safely clears the key before any run can be created", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(fixtures.errors.disabled, 503)); vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useAgentWorkbench(PERSONAS.platform_admin));
    await act(async () => { await result.current.start("durable", { input: "policy" }); });
    expect(result.current.state).toMatchObject({ startupKey: null, unresolvedStart: false, notice: null });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("a disabled recovery endpoint cannot prove an earlier start failed", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(fixtures.errors.disabled, 503)); vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useAgentWorkbench(PERSONAS.platform_admin));
    await act(async () => { await result.current.recover(key); });
    expect(result.current.state).toMatchObject({ startupKey: key, unresolvedStart: true, notice: "start_unknown" });
    act(() => { void result.current.start("one_shot", { input: "policy" }); });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("known nonterminal runs cannot be replaced by a key read or unrelated run ID", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(json(fixtures.durable.running))); vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useAgentWorkbench(PERSONAS.platform_admin));
    await act(async () => { await result.current.read(runId); });
    await act(async () => { await result.current.read("another-run"); await result.current.recover(key); });
    expect(fetchMock).toHaveBeenCalledOnce();
    await act(async () => { await result.current.read(runId); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("stopping a recovery read retains its reference and prevents late read output from replacing a newer explicit lookup", async () => {
    const first = deferred<Response>(); const second = deferred<Response>();
    const fetchMock = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise); vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useAgentWorkbench(PERSONAS.platform_admin));
    act(() => { void result.current.recover(key); result.current.interrupt(); void result.current.recover(key); });
    expect((fetchMock.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(true);
    await act(async () => { first.resolve(json(fixtures.durable.completed)); await Promise.resolve(); });
    expect(result.current.state).toMatchObject({ pending: "recover", durable: null, startupKey: key });
    await act(async () => { second.resolve(json(fixtures.durable.running)); await Promise.resolve(); });
    await waitFor(() => expect(result.current.state.pending).toBeNull());
    expect(result.current.state.durable?.status).toBe("running");
  });
});

describe("durable mode request lifecycle", () => {
  it("never resumes a queued background run when decide is called directly", async () => {
    const queued = { ...fixtures.durable.running, data: { ...fixtures.durable.running.data, execution_mode: "background" } };
    const fetchMock = vi.fn().mockResolvedValue(json(queued)); vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useAgentWorkbench(PERSONAS.platform_admin));
    await act(async () => { await result.current.read(runId); await result.current.decide(); });
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(result.current.state.durable?.execution_mode).toBe("background");
  });
  it("keeps a long inline active request waiting for its bounded execution budget", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const pending = deferred<Response>(); const fetchMock = vi.fn().mockReturnValue(pending.promise); vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useAgentWorkbench(PERSONAS.platform_admin));
    act(() => { void result.current.start("durable", { input: "Review", budget_mode: "active", timeout_seconds: 1800, approval_timeout_seconds: 86400 }); });
    act(() => { vi.advanceTimersByTime(135_000); });
    expect(result.current.state.pending).toBe("start");
    expect((fetchMock.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(false);
    act(() => { vi.advanceTimersByTime(1_680_000); });
    expect(result.current.state).toMatchObject({ pending: null, unresolvedStart: true, error: { code: "AGENT_REQUEST_TIMEOUT" } });
    expect((fetchMock.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(true);
    await act(async () => { pending.resolve(json(fixtures.durable.completed)); await Promise.resolve(); });
    expect(result.current.state.durable).toBeNull();
    vi.useRealTimers();
  });
  it("clears its local view when the current login changes during a read", async () => {
    const pending = deferred<Response>(); const fetchMock = vi.fn().mockReturnValue(pending.promise); vi.stubGlobal("fetch", fetchMock);
    const { result, rerender } = renderHook(({ auth }) => useAgentWorkbench(auth), { initialProps: { auth: PERSONAS.platform_admin } });
    act(() => { void result.current.read(runId); });
    rerender({ auth: { ...PERSONAS.platform_admin, department: "new-department" } });
    expect((fetchMock.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(true);
    await act(async () => { pending.resolve(json(fixtures.durable.completed)); await Promise.resolve(); });
    expect(result.current.state).toMatchObject({ durable: null, fresh: false, pending: null, startupKey: null });
  });
});
