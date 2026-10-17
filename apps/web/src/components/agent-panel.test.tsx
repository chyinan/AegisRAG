import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PERSONAS, type AuthSession } from "@/lib/auth";
import fixtures from "@/test/fixtures/agent-lifecycle.json";
import { MAX_AGENT_JSON_BYTES } from "@/lib/api/agent-http";
import { parseAgentSseEvents } from "@/lib/api/agent-sse";
import { AgentPanel } from "./agent-panel";
import { WorkbenchShell } from "./workbench-shell";

const startupKey = "587629a6-65a2-4db2-8e18-c17d8190c0ac";
const runId = fixtures.durable.awaitingApproval.data.run_id;
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } }); }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
function renderAgent(auth: AuthSession = { ...PERSONAS.platform_admin, tenantId: "tenant-pilot", userId: "user-pilot" }, canRun = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const component = (identity = auth, permitted = canRun) => <QueryClientProvider client={client}><AgentPanel auth={identity} language="en" canRun={permitted} /></QueryClientProvider>;
  return { ...render(component()), component };
}
function choose(mode: string) { fireEvent.change(screen.getByRole("combobox", { name: "Execution mode" }), { target: { value: mode } }); }
function start(mode = "durable") {
  choose(mode);
  fireEvent.change(screen.getByRole("textbox", { name: "Agent input" }), { target: { value: "Find the remote-work policy" } });
  fireEvent.click(screen.getByRole("button", { name: "Run agent" }));
}
function recover(id = runId) {
  choose("durable");
  fireEvent.change(screen.getByRole("textbox", { name: "Durable run ID" }), { target: { value: id } });
  fireEvent.click(screen.getByRole("button", { name: "Read run" }));
}
function pendingStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const cancel = vi.fn();
  const response = new Response(new ReadableStream<Uint8Array>({ start(value) { controller = value; }, cancel }), { headers: { "content-type": "text/event-stream" } });
  return { response, cancel, push: (wire: string) => controller.enqueue(new TextEncoder().encode(wire)), close: () => controller.close() };
}
async function flush(action: () => void) { await act(async () => { action(); await Promise.resolve(); }); }

beforeEach(() => { vi.spyOn(Date, "now").mockReturnValue(fixtures.clockEpochSeconds * 1000); vi.spyOn(crypto, "randomUUID").mockReturnValue(startupKey); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); sessionStorage.clear(); localStorage.clear(); });

describe("Agent workbench journey", () => {
  it("preserves the one-shot default and permission gate", async () => {
    const last = parseAgentSseEvents(fixtures.streams.success).at(-1)!;
    const fetchMock = vi.fn().mockResolvedValue(json({ request_id: last.request_id, data: last.data, error: null }));
    vi.stubGlobal("fetch", fetchMock);
    const view = renderAgent();
    expect(screen.getByRole("combobox", { name: "Execution mode" })).toHaveValue("one_shot");
    start("one_shot");
    expect(await screen.findByText(/Remote work 🌍 requires approval/)).toBeInTheDocument();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/execution/agent/run");
    expect(screen.getByRole("region", { name: "Validated source references" })).toHaveTextContent("Pilot handbook.md");
    view.rerender(view.component(PERSONAS.employee, false));
    expect(screen.queryByRole("combobox", { name: "Execution mode" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Remote work 🌍/)).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("renders live tool progress before the final answer and never persists private data", async () => {
    const stream = pendingStream(); vi.stubGlobal("fetch", vi.fn().mockResolvedValue(stream.response));
    const storage = vi.spyOn(Storage.prototype, "setItem");
    renderAgent(); start("live");
    await flush(() => stream.push(fixtures.streams.incomplete));
    expect(await screen.findByText("rag_search · started")).toBeInTheDocument();
    expect(screen.getByText("rag_search · success")).toBeInTheDocument();
    expect(screen.queryByText(/Remote work 🌍 requires approval/)).not.toBeInTheDocument();
    const finalWire = fixtures.streams.success.slice(fixtures.streams.incomplete.length);
    await flush(() => stream.push(finalWire));
    expect(await screen.findByText(/Remote work 🌍 requires approval/)).toBeInTheDocument();
    expect(stream.cancel).toHaveBeenCalledOnce();
    expect(storage).not.toHaveBeenCalled();
  });

  it("reports incomplete streams without inventing a final answer", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(fixtures.streams.incomplete, { headers: { "content-type": "text/event-stream" } })));
    renderAgent(); start("live");
    expect(await screen.findByRole("alert")).toHaveTextContent("AGENT_STREAM_INCOMPLETE");
    expect(screen.queryByRole("region", { name: "Validated source references" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run agent" })).toBeEnabled();
  });

  it("local stream abort requests disconnect cancellation without claiming durable confirmation", async () => {
    const stream = pendingStream(); const fetchMock = vi.fn().mockResolvedValue(stream.response); vi.stubGlobal("fetch", fetchMock);
    renderAgent(); start("live");
    await flush(() => stream.push(fixtures.streams.incomplete));
    fireEvent.click(screen.getByRole("button", { name: "Stop waiting" }));
    expect(await screen.findByText(/Server-side cancellation persistence is not confirmed/)).toBeInTheDocument();
    expect((fetchMock.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(true);
    expect(stream.cancel).toHaveBeenCalledOnce();
    expect(screen.queryByText(/server confirmed the durable run is cancelled/i)).not.toBeInTheDocument();
  });

  it("shows exact approval, deadline and consumed budgets, then reads before sending the explicit decision", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json(fixtures.durable.awaitingApproval)).mockResolvedValueOnce(json(fixtures.durable.awaitingApproval)).mockResolvedValueOnce(json(fixtures.durable.completed));
    vi.stubGlobal("fetch", fetchMock);
    renderAgent(); start();
    expect(await screen.findByRole("heading", { name: "Review the exact requested action" })).toBeInTheDocument();
    expect(JSON.parse(screen.getByLabelText("Exact tool arguments").textContent ?? "")).toEqual({ query: "Remote-work policy 🌍", top_k: 3 });
    expect(screen.getByText("Original deadline (UTC)")).toBeInTheDocument();
    expect(screen.getByText("Decisions consumed")).toBeInTheDocument();
    expect(screen.getByText(fixtures.durable.awaitingApproval.data.approval.action_digest)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Approve exact action" }));
    expect(await screen.findByText("Remote work requires approval.")).toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([url]) => url as string)).toEqual(["/api/execution/agent/durable", `/api/backend/agent/durable/${runId}`, `/api/execution/agent/durable/${runId}/resume`]);
    expect(JSON.parse((fetchMock.mock.calls[2][1] as RequestInit).body as string)).toEqual({ expected_revision: 2, approval_id: fixtures.durable.awaitingApproval.data.approval.approval_id, approve: true });
    expect(screen.queryByRole("button", { name: "Resume run" })).not.toBeInTheDocument();
  });

  it("a changed approval requires another explicit review and never reuses the old decision", async () => {
    const changed = { ...fixtures.durable.awaitingApproval, data: { ...fixtures.durable.awaitingApproval.data, revision: 3, approval: { ...fixtures.durable.awaitingApproval.data.approval, arguments: { query: "Changed policy", top_k: 2 } } } };
    const fetchMock = vi.fn().mockResolvedValueOnce(json(fixtures.durable.awaitingApproval)).mockResolvedValueOnce(json(changed));
    vi.stubGlobal("fetch", fetchMock); renderAgent(); start();
    fireEvent.click(await screen.findByRole("button", { name: "Approve exact action" }));
    expect(await screen.findByText(/server state changed after you reviewed it/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Exact tool arguments")).toHaveTextContent("Changed policy");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "Approve exact action" })).toBeEnabled();
  });

  it("a stale-revision conflict disables old approval until an explicit refresh", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json(fixtures.durable.awaitingApproval)).mockResolvedValueOnce(json(fixtures.durable.awaitingApproval)).mockResolvedValueOnce(json(fixtures.errors.conflict, 409)).mockResolvedValueOnce(json(fixtures.durable.awaitingApproval));
    vi.stubGlobal("fetch", fetchMock); renderAgent(); start();
    fireEvent.click(await screen.findByRole("button", { name: "Approve exact action" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("DURABLE_RESUME_CONFLICT");
    expect(screen.getByRole("button", { name: "Approve exact action" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    fireEvent.click(screen.getByRole("button", { name: "Refresh run" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve exact action" })).toBeEnabled());
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("reject sends an explicit false approval with the current revision and confirms only server cancellation", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json(fixtures.durable.awaitingApproval)).mockResolvedValueOnce(json(fixtures.durable.awaitingApproval)).mockResolvedValueOnce(json(fixtures.durable.cancelled));
    vi.stubGlobal("fetch", fetchMock); renderAgent(); start();
    fireEvent.click(await screen.findByRole("button", { name: "Reject and cancel run" }));
    expect(await screen.findByText("The server confirmed the durable run is cancelled.")).toBeInTheDocument();
    expect(JSON.parse((fetchMock.mock.calls[2][1] as RequestInit).body as string)).toEqual({ expected_revision: 2, approval_id: fixtures.durable.awaitingApproval.data.approval.approval_id, approve: false });
  });

  it("cancels a known run by exact revision without interpreting local abort as cancellation", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json(fixtures.durable.running)).mockResolvedValueOnce(json(fixtures.durable.cancelled));
    vi.stubGlobal("fetch", fetchMock); renderAgent(); recover();
    fireEvent.click(await screen.findByRole("button", { name: "Cancel durable run" }));
    expect(await screen.findByText("The server confirmed the durable run is cancelled.")).toBeInTheDocument();
    expect(fetchMock.mock.calls[1][0]).toBe(`/api/backend/agent/durable/${runId}/cancel`);
    expect(JSON.parse((fetchMock.mock.calls[1][1] as RequestInit).body as string)).toEqual({ expected_revision: 2 });
  });

  it("reads existing running state without resuming until asked, then retains consumed budgets", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json(fixtures.durable.running)).mockResolvedValueOnce(json(fixtures.durable.running)).mockResolvedValueOnce(json(fixtures.durable.completed));
    vi.stubGlobal("fetch", fetchMock); renderAgent(); recover();
    const resume = await screen.findByRole("button", { name: "Resume run" });
    expect(fetchMock).toHaveBeenCalledOnce();
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("GET");
    fireEvent.click(resume);
    expect(await screen.findByText("Remote work requires approval.")).toBeInTheDocument();
    expect(JSON.parse((fetchMock.mock.calls[2][1] as RequestInit).body as string)).toEqual({ expected_revision: 2 });
  });

  it.each(["completed", "cancelled", "interventionRequired"] as const)("terminal %s cannot resume", async (key) => {
    const fetchMock = vi.fn().mockResolvedValue(json(fixtures.durable[key])); vi.stubGlobal("fetch", fetchMock);
    renderAgent(); recover();
    await screen.findByRole("button", { name: "Refresh run" });
    expect(screen.queryByRole("button", { name: "Resume run" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel durable run" })).not.toBeInTheDocument();
    if (key === "interventionRequired") expect(screen.getByRole("alert")).toHaveTextContent("operator must reconcile external state");
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("expired approval is shown but cannot be approved or resumed", async () => {
    vi.spyOn(Date, "now").mockReturnValue(fixtures.durable.awaitingApproval.data.deadline_at * 1000);
    const fetchMock = vi.fn().mockResolvedValue(json(fixtures.durable.awaitingApproval)); vi.stubGlobal("fetch", fetchMock);
    renderAgent(); recover();
    expect(await screen.findByRole("button", { name: "Approve exact action" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("deadline or approval has expired");
    fireEvent.click(screen.getByRole("button", { name: "Approve exact action" }));
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("disabled durable mode gives operator guidance without changing server settings", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(fixtures.errors.disabled, 503)); vi.stubGlobal("fetch", fetchMock);
    renderAgent(); start();
    expect(await screen.findByRole("alert")).toHaveTextContent("Durable Agent is disabled on this server");
    expect(screen.queryByText(/start outcome is unknown/)).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Execution mode" })).toBeEnabled();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("losing the initial start never retries, fabricates a run ID, or claims cancellation", async () => {
    const request = deferred<Response>(); const fetchMock = vi.fn().mockReturnValue(request.promise); vi.stubGlobal("fetch", fetchMock);
    renderAgent(); start();
    fireEvent.click(screen.getByRole("button", { name: "Stop waiting" }));
    expect(screen.getByText(/start outcome is unknown and no run ID was received/)).toBeInTheDocument();
    await flush(() => request.resolve(json(fixtures.durable.completed)));
    expect(screen.queryByText("Remote work requires approval.")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy run ID" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("same-frame repeated starts and approvals dispatch only once", async () => {
    const request = deferred<Response>(); const fetchMock = vi.fn().mockReturnValueOnce(request.promise).mockResolvedValueOnce(json(fixtures.durable.awaitingApproval)).mockResolvedValueOnce(json(fixtures.durable.completed));
    vi.stubGlobal("fetch", fetchMock); renderAgent();
    choose("durable"); fireEvent.change(screen.getByRole("textbox", { name: "Agent input" }), { target: { value: "policy" } });
    const button = screen.getByRole("button", { name: "Run agent" });
    act(() => { fireEvent.click(button); fireEvent.click(button); });
    expect(fetchMock).toHaveBeenCalledOnce();
    await flush(() => request.resolve(json(fixtures.durable.awaitingApproval)));
    const approve = screen.getByRole("button", { name: "Approve exact action" });
    act(() => { fireEvent.click(approve); fireEvent.click(approve); });
    expect(await screen.findByText("Remote work requires approval.")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("auth replacement aborts old work and ignores late responses", async () => {
    const request = deferred<Response>(); const fetchMock = vi.fn().mockReturnValue(request.promise); vi.stubGlobal("fetch", fetchMock);
    const oldAuth: AuthSession = { ...PERSONAS.platform_admin, mode: "bearer", bearerToken: "old-private-token" };
    const view = renderAgent(oldAuth); start();
    view.rerender(view.component({ ...oldAuth, bearerToken: "new-private-token" }));
    expect((fetchMock.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(true);
    expect(screen.getByRole("textbox", { name: "Agent input" })).toHaveValue("");
    await flush(() => request.resolve(json(fixtures.durable.awaitingApproval)));
    expect(screen.queryByText("Review the exact requested action")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/old-private-token|new-private-token/);
  });

  it("navigation unmount closes a live reader and returning does not reconnect", async () => {
    const stream = pendingStream(); const fetchMock = vi.fn().mockResolvedValue(stream.response); vi.stubGlobal("fetch", fetchMock);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><WorkbenchShell auth={{ ...PERSONAS.platform_admin, userId: "agent-lifecycle-user" }} language="en" onLanguageChange={vi.fn()} onSignOut={vi.fn()} /></QueryClientProvider>);
    fireEvent.click(screen.getByRole("button", { name: /Agent Runs/ })); start("live");
    await flush(() => stream.push(fixtures.streams.incomplete));
    fireEvent.click(screen.getByRole("button", { name: /^AskRAG/ }));
    await waitFor(() => expect(stream.cancel).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: /Agent Runs/ }));
    expect(screen.queryByText("rag_search · started")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("renders bilingual lifecycle controls and safe escaped approval text", async () => {
    const fixture = { ...fixtures.durable.awaitingApproval, data: { ...fixtures.durable.awaitingApproval.data, approval: { ...fixtures.durable.awaitingApproval.data.approval, arguments: { query: "<img src=x onerror=alert(1)>" } } } };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(fixture)));
    render(<AgentPanel auth={PERSONAS.platform_admin} language="zh" canRun />);
    fireEvent.change(screen.getByRole("combobox", { name: "执行模式" }), { target: { value: "durable" } });
    fireEvent.change(screen.getByRole("textbox", { name: "持久化运行 ID" }), { target: { value: runId } });
    fireEvent.click(screen.getByRole("button", { name: "读取运行" }));
    expect(await screen.findByRole("button", { name: "批准此精确操作" })).toBeEnabled();
    const view = screen.getByLabelText("精确工具参数");
    expect(view).toHaveTextContent("<img src=x onerror=alert(1)>");
    expect(within(view).queryByRole("img")).not.toBeInTheDocument();
  });

  it("refuses invalid budgets and blank input before dispatch", () => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock); renderAgent();
    expect(screen.getByRole("button", { name: "Run agent" })).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Agent input" }), { target: { value: "policy" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "max_steps" }), { target: { value: "21" } });
    expect(screen.getByRole("button", { name: "Run agent" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Run agent" }));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("stopping an in-flight approval invalidates the view and ignores a late completed mutation", async () => {
    const mutation = deferred<Response>();
    const fetchMock = vi.fn().mockResolvedValueOnce(json(fixtures.durable.awaitingApproval)).mockResolvedValueOnce(json(fixtures.durable.awaitingApproval)).mockReturnValueOnce(mutation.promise);
    vi.stubGlobal("fetch", fetchMock); renderAgent(); start();
    fireEvent.click(await screen.findByRole("button", { name: "Approve exact action" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    fireEvent.click(screen.getByRole("button", { name: "Stop waiting" }));
    expect(screen.getByRole("button", { name: "Approve exact action" })).toBeDisabled();
    expect(screen.getByText(/Refresh this run before deciding/)).toBeInTheDocument();
    expect((fetchMock.mock.calls[2][1] as RequestInit).signal?.aborted).toBe(true);
    await flush(() => mutation.resolve(json(fixtures.durable.completed)));
    expect(screen.queryByText("Remote work requires approval.")).not.toBeInTheDocument();
  });

  it("failed refresh withdraws a prior durable final answer instead of leaving it freshly authorized", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json(fixtures.durable.completed)).mockResolvedValueOnce(json(fixtures.errors.expired, 410));
    vi.stubGlobal("fetch", fetchMock); renderAgent(); recover();
    expect(await screen.findByText("Remote work requires approval.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Refresh run" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(fixtures.errors.expired.error.code);
    expect(screen.queryByText("Remote work requires approval.")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Validated source references" })).not.toBeInTheDocument();
  });

  it("an approval expires while on screen without renewing its budget", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const fetchMock = vi.fn().mockResolvedValue(json(fixtures.durable.awaitingApproval)); vi.stubGlobal("fetch", fetchMock);
    renderAgent(); recover();
    expect(await screen.findByRole("button", { name: "Approve exact action" })).toBeEnabled();
    // Flush the response-render effects before advancing the display clock.
    await act(async () => { await Promise.resolve(); });
    vi.spyOn(Date, "now").mockReturnValue(fixtures.durable.awaitingApproval.data.deadline_at * 1000);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.getByRole("button", { name: "Approve exact action" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("deadline or approval has expired");
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("the client wait deadline aborts a lost start without retrying or claiming a durable result", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const request = deferred<Response>(); const fetchMock = vi.fn().mockReturnValue(request.promise); vi.stubGlobal("fetch", fetchMock);
    renderAgent(); start();
    act(() => { vi.advanceTimersByTime(135_000); });
    expect(screen.getByRole("alert")).toHaveTextContent("AGENT_REQUEST_TIMEOUT");
    expect(screen.getByText(/start outcome is unknown and no run ID was received/)).toBeInTheDocument();
    expect((fetchMock.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(true);
    await flush(() => request.resolve(json(fixtures.durable.completed)));
    expect(screen.queryByText("Remote work requires approval.")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledOnce();
  });


  it.each(["limitBeforeDispatch", "repeatBeforeDispatch"] as const)("shows legitimate %s termination instead of a protocol error", async (key) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(fixtures.streams[key], { headers: { "content-type": "text/event-stream" } })));
    renderAgent(); start("live");
    expect(await screen.findByText(/^Stopped ·/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Validated source references" })).not.toBeInTheDocument();
  });

});


describe("durable startup recovery journey", () => {
  it("shows a copyable pre-dispatch key, retains it after local abort, and explicitly reads without replaying start", async () => {
    const initial = deferred<Response>();
    const fetchMock = vi.fn().mockReturnValueOnce(initial.promise).mockResolvedValueOnce(json(fixtures.durable.awaitingApproval));
    vi.stubGlobal("fetch", fetchMock);
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const clipboard = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: clipboard }, configurable: true });
    renderAgent(); start();
    expect(screen.getByRole("region", { name: "Startup recovery key" })).toHaveTextContent(startupKey);
    expect(screen.getByRole("button", { name: "Copy startup key" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Read by retained startup key" })).toBeDisabled();
    expect(JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)).toHaveProperty("idempotency_key", startupKey);
    expect(clipboard).not.toHaveBeenCalled();
    await flush(() => fireEvent.click(screen.getByRole("button", { name: "Copy startup key" })));
    expect(clipboard).toHaveBeenCalledWith(startupKey);
    fireEvent.click(screen.getByRole("button", { name: "Stop waiting" }));
    expect(screen.getByText(startupKey)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run agent" })).toBeDisabled();
    expect(screen.getByRole("combobox", { name: "Execution mode" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "Durable run ID" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Read by retained startup key" })).toBeEnabled();
    await flush(() => initial.resolve(json(fixtures.durable.completed)));
    expect(screen.queryByText("Remote work requires approval.")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Read by retained startup key" }));
    expect(await screen.findByRole("heading", { name: "Review the exact requested action" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([url]) => url as string)).toEqual(["/api/execution/agent/durable", "/api/backend/agent/durable/recover"]);
    expect(JSON.parse((fetchMock.mock.calls[1][1] as RequestInit).body as string)).toEqual({ idempotency_key: startupKey });
    expect(screen.queryByRole("region", { name: "Startup recovery key" })).not.toBeInTheDocument();
    expect(screen.getByText("Original deadline (UTC)").parentElement).toHaveTextContent(new Date(fixtures.durable.awaitingApproval.data.deadline_at * 1000).toISOString());
    expect(storage).not.toHaveBeenCalled();
    expect(window.location.href).not.toContain(startupKey);
  });

  it("a 404 retains the key and uncertainty until a later explicit read finds the committed result", async () => {
    const missing = { request_id: "req-missing", data: null, error: { code: "DURABLE_RUN_NOT_FOUND", message: "Not found.", details: {} } };
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("Lost transport"))
      .mockResolvedValueOnce(json(missing, 404)).mockResolvedValueOnce(json(fixtures.durable.completed));
    vi.stubGlobal("fetch", fetchMock); renderAgent(); start();
    expect(await screen.findByText(/start outcome is unknown/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Read by retained startup key" }));
    expect(await screen.findByText(/404 does not prove the start failed/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run agent" })).toBeDisabled();
    expect(screen.getByText(startupKey)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "Read by retained startup key" }));
    expect(await screen.findByText("Remote work requires approval.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run agent" })).toBeEnabled();
    expect(fetchMock.mock.calls.map(([url]) => url as string)).toEqual(["/api/execution/agent/durable", "/api/backend/agent/durable/recover", "/api/backend/agent/durable/recover"]);
  });

  it("requires a clear local discard acknowledgment before allowing a deliberate new start with a fresh key", async () => {
    const nextKey = "a87629a6-65a2-4db2-8e18-c17d8190c0ac";
    vi.spyOn(crypto, "randomUUID").mockReturnValueOnce(startupKey).mockReturnValueOnce(nextKey);
    const next = deferred<Response>();
    const fetchMock = vi.fn().mockRejectedValueOnce(new Error("lost")).mockReturnValueOnce(next.promise); vi.stubGlobal("fetch", fetchMock);
    renderAgent(); start();
    expect(await screen.findByText(/start outcome is unknown/)).toBeInTheDocument();
    const discard = screen.getByRole("button", { name: "Discard local startup reference" });
    expect(discard).toBeDisabled(); fireEvent.click(discard);
    expect(screen.getByRole("button", { name: "Run agent" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: /server effects remain unresolved/ }));
    fireEvent.click(discard);
    expect(screen.getByText(/No server cancellation was requested or confirmed/)).toBeInTheDocument();
    expect(screen.queryByText(startupKey)).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Run agent" }));
    expect(screen.getByText(nextKey)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(JSON.parse((fetchMock.mock.calls[1][1] as RequestInit).body as string)).toHaveProperty("idempotency_key", nextKey);
    await flush(() => next.resolve(json(fixtures.durable.completed)));
  });

  it("reads a manually saved key only once, then still freshly compares the exact approval", async () => {
    const recovery = deferred<Response>();
    const fetchMock = vi.fn().mockReturnValueOnce(recovery.promise).mockResolvedValueOnce(json(fixtures.durable.awaitingApproval)).mockResolvedValueOnce(json(fixtures.durable.completed));
    vi.stubGlobal("fetch", fetchMock); renderAgent(); choose("durable");
    fireEvent.change(screen.getByRole("textbox", { name: "Saved startup recovery key (UUID4)" }), { target: { value: startupKey.toUpperCase() } });
    const button = screen.getByRole("button", { name: "Read by saved startup key" });
    act(() => { fireEvent.click(button); fireEvent.click(button); });
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(screen.getByRole("textbox", { name: "Saved startup recovery key (UUID4)" })).toHaveValue("");
    await flush(() => recovery.resolve(json(fixtures.durable.awaitingApproval)));
    expect(screen.getByRole("button", { name: "Approve exact action" })).toBeEnabled();
    expect(fetchMock).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Approve exact action" }));
    expect(await screen.findByText("Remote work requires approval.")).toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([url]) => url as string)).toEqual(["/api/backend/agent/durable/recover", `/api/backend/agent/durable/${runId}`, `/api/execution/agent/durable/${runId}/resume`]);
    expect(JSON.parse((fetchMock.mock.calls[2][1] as RequestInit).body as string)).toEqual({ expected_revision: 2, approval_id: fixtures.durable.awaitingApproval.data.approval.approval_id, approve: true });
  });

  it.each(["not-a-key", startupKey.replace("4db2", "5db2"), startupKey.replace("8e18", "7e18")])("rejects malformed saved key %s without dispatch", (key) => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock); renderAgent(); choose("durable");
    fireEvent.change(screen.getByRole("textbox", { name: "Saved startup recovery key (UUID4)" }), { target: { value: key } });
    const button = screen.getByRole("button", { name: "Read by saved startup key" });
    expect(button).toBeDisabled(); fireEvent.click(button); expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not dispatch a durable start if secure key generation is unavailable", async () => {
    vi.stubGlobal("crypto", {});
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock); renderAgent(); start();
    expect(await screen.findByRole("alert")).toHaveTextContent("No durable start was sent");
    expect(screen.queryByText(/start outcome is unknown/)).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Execution mode" })).toBeEnabled();
    expect(screen.queryByRole("region", { name: "Startup recovery key" })).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("uses the 12-second read budget for recovery and retains its key after timeout", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const response = deferred<Response>(); const fetchMock = vi.fn().mockReturnValue(response.promise); vi.stubGlobal("fetch", fetchMock);
    renderAgent(); choose("durable");
    fireEvent.change(screen.getByRole("textbox", { name: "Saved startup recovery key (UUID4)" }), { target: { value: startupKey } });
    fireEvent.click(screen.getByRole("button", { name: "Read by saved startup key" }));
    act(() => { vi.advanceTimersByTime(11_999); });
    expect((fetchMock.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(false);
    act(() => { vi.advanceTimersByTime(1); });
    expect(screen.getByRole("alert")).toHaveTextContent("AGENT_REQUEST_TIMEOUT");
    expect((fetchMock.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(true);
    expect(screen.getByText(startupKey)).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Execution mode" })).toBeDisabled();
    await flush(() => response.resolve(json(fixtures.durable.completed)));
    expect(screen.queryByText("Remote work requires approval.")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it.each(["token", "tenant", "permissions", "revoked"] as const)("%s change clears private key and aborts key recovery before late data can display", async (change) => {
    const response = deferred<Response>(); const fetchMock = vi.fn().mockReturnValue(response.promise); vi.stubGlobal("fetch", fetchMock);
    const auth: AuthSession = { ...PERSONAS.platform_admin, mode: "bearer", tenantId: "tenant-pilot", bearerToken: "old-token" };
    const view = renderAgent(auth); choose("durable");
    fireEvent.change(screen.getByRole("textbox", { name: "Saved startup recovery key (UUID4)" }), { target: { value: startupKey } });
    fireEvent.click(screen.getByRole("button", { name: "Read by saved startup key" }));
    expect(screen.getByText(startupKey)).toBeInTheDocument();
    const nextAuth = change === "token" ? { ...auth, bearerToken: "new-token" } : change === "tenant" ? { ...auth, tenantId: "other" } : { ...auth, permissions: ["agent:run"] };
    view.rerender(view.component(nextAuth, change !== "revoked"));
    expect((fetchMock.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(true);
    expect(screen.queryByText(startupKey)).not.toBeInTheDocument();
    await flush(() => response.resolve(json(fixtures.durable.completed)));
    expect(screen.queryByText("Remote work requires approval.")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("unmount aborts key recovery and remount never restores keys or reissues a request", async () => {
    const response = deferred<Response>(); const fetchMock = vi.fn().mockReturnValue(response.promise); vi.stubGlobal("fetch", fetchMock);
    const view = renderAgent(); choose("durable");
    fireEvent.change(screen.getByRole("textbox", { name: "Saved startup recovery key (UUID4)" }), { target: { value: startupKey } });
    fireEvent.click(screen.getByRole("button", { name: "Read by saved startup key" }));
    view.unmount();
    expect((fetchMock.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(true);
    renderAgent(); choose("durable");
    expect(screen.getByRole("textbox", { name: "Saved startup recovery key (UUID4)" })).toHaveValue("");
    await flush(() => response.resolve(json(fixtures.durable.completed)));
    expect(screen.queryByText(startupKey)).not.toBeInTheDocument();
    expect(screen.queryByText("Remote work requires approval.")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});


it.each(["oversized", "malformed"] as const)("retains startup recovery after an unverifiable %s first response", async (kind) => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(kind === "oversized" ? " ".repeat(MAX_AGENT_JSON_BYTES + 1) : "{private malformed"));
  vi.stubGlobal("fetch", fetchMock); renderAgent(); start();
  expect(await screen.findByRole("alert")).toHaveTextContent(kind === "oversized" ? "AGENT_RESPONSE_TOO_LARGE" : "INVALID_RESPONSE");
  expect(screen.getByText(startupKey)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Run agent" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Read by retained startup key" })).toBeEnabled();
  expect(document.body.textContent).not.toContain("private malformed");
  expect(fetchMock).toHaveBeenCalledOnce();
});


it("a disabled recovery endpoint keeps the prior startup reference instead of implying another mode is immediately safe", async () => {
  const fetchMock = vi.fn().mockResolvedValue(json(fixtures.errors.disabled, 503)); vi.stubGlobal("fetch", fetchMock);
  renderAgent(); choose("durable");
  fireEvent.change(screen.getByRole("textbox", { name: "Saved startup recovery key (UUID4)" }), { target: { value: startupKey } });
  fireEvent.click(screen.getByRole("button", { name: "Read by saved startup key" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("This cannot reconcile an earlier start");
  expect(screen.getByText(startupKey)).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Execution mode" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Discard local startup reference" })).toBeDisabled();
  expect(fetchMock).toHaveBeenCalledOnce();
});
