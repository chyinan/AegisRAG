import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PERSONAS, type AuthSession } from "@/lib/auth";
import fixtures from "@/test/fixtures/agent-lifecycle.json";
import { AgentPanel } from "./agent-panel";

const now = fixtures.clockEpochSeconds;
const key = "587629a6-65a2-4db2-8e18-c17d8190c0ac";
const runId = fixtures.durable.running.data.run_id;
const auth: AuthSession = { ...PERSONAS.platform_admin, tenantId: "tenant-pilot", userId: "user-pilot" };
const activeFields = { budget_mode: "active", execution_mode: "background", active_seconds_used: 12, active_seconds_remaining: 1788, deadline_at: now - 60 };
const queued = { ...fixtures.durable.running, data: { ...fixtures.durable.running.data, ...activeFields } };
const approval = { ...fixtures.durable.awaitingApproval, data: { ...fixtures.durable.awaitingApproval.data, ...activeFields,
  approval: { ...fixtures.durable.awaitingApproval.data.approval, expires_at: now + 86400 } } };
const completed = { ...fixtures.durable.completed, data: { ...fixtures.durable.completed.data, ...activeFields,
  active_seconds_used: 14, active_seconds_remaining: 1786 } };
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } }); }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
function renderAgent() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const component = (current = auth) => <QueryClientProvider client={client}><AgentPanel auth={current} language="en" canRun /></QueryClientProvider>;
  return { ...render(component()), component };
}
function choose(name: string, value: string) { fireEvent.change(screen.getByRole("combobox", { name }), { target: { value } }); }
function durableMode() { choose("Execution mode", "durable"); }
function input() { fireEvent.change(screen.getByRole("textbox", { name: "Agent input" }), { target: { value: "Review the support case" } }); }
function read() {
  durableMode();
  fireEvent.change(screen.getByRole("textbox", { name: "Durable run ID" }), { target: { value: runId } });
  fireEvent.click(screen.getByRole("button", { name: "Read run" }));
}
beforeEach(() => { vi.spyOn(Date, "now").mockReturnValue(now * 1000); vi.spyOn(crypto, "randomUUID").mockReturnValue(key); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });

describe("durable budget and worker workbench", () => {
  it("keeps wall-clock inline defaults and makes ticket approval mandatory", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(fixtures.durable.completed)); vi.stubGlobal("fetch", fetchMock);
    renderAgent(); durableMode(); input();
    expect(screen.getByRole("combobox", { name: "Durable budget" })).toHaveValue("wall_clock");
    expect(screen.getByRole("combobox", { name: "Durable dispatch" })).toHaveValue("inline");
    expect(screen.getByRole("checkbox", { name: "create_support_ticket, when configured (approval always required)" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "create_support_ticket, when configured (approval always required)" })).toBeDisabled();
    act(() => { const button = screen.getByRole("button", { name: "Run agent" }); fireEvent.click(button); fireEvent.click(button); });
    expect(await screen.findByText("Remote work requires approval.")).toBeInTheDocument();
    const body: unknown = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    expect(body).not.toHaveProperty("budget_mode"); expect(body).not.toHaveProperty("execution_mode");
    expect(body).toMatchObject({ approval_tools: ["calculator", "rag_search"] });
  });

  it("starts an opt-in active background run and only reads queued progress", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json(queued)).mockResolvedValueOnce(json(approval))
      .mockResolvedValueOnce(json(approval)).mockResolvedValueOnce(json(queued)).mockResolvedValueOnce(json(completed));
    vi.stubGlobal("fetch", fetchMock); renderAgent(); durableMode();
    choose("Durable budget", "active"); choose("Durable dispatch", "background"); input();
    fireEvent.change(screen.getByRole("spinbutton", { name: "timeout_seconds" }), { target: { value: "1800" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "approval_timeout_seconds" }), { target: { value: "86400" } });
    expect(screen.getByText(/Selecting this option does not configure or start a worker/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Run agent" }));
    expect(await screen.findByText(/Queued or running in a background worker/)).toBeInTheDocument();
    expect(JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)).toMatchObject({ budget_mode: "active", execution_mode: "background", timeout_seconds: 1800, approval_timeout_seconds: 86400 });
    expect(screen.queryByRole("button", { name: "Resume run" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Refresh run" }));
    expect(await screen.findByRole("button", { name: "Approve exact action" })).toBeEnabled();
    expect(screen.getByText("Execution window checkpoint (UTC)")).toBeInTheDocument();
    expect(screen.getByText("Active seconds consumed (checkpointed)").parentElement).toHaveTextContent("12");
    expect(screen.getByText("Approval expiry (UTC)").parentElement).toHaveTextContent(new Date((now + 86400) * 1000).toISOString());
    fireEvent.click(screen.getByRole("button", { name: "Approve exact action" }));
    expect(await screen.findByText(/Queued or running in a background worker/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(JSON.parse((fetchMock.mock.calls[3][1] as RequestInit).body as string)).toEqual({ expected_revision: 2, approval_id: approval.data.approval.approval_id, approve: true });
    expect(screen.queryByRole("button", { name: "Resume run" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Refresh run" }));
    expect(await screen.findByText("Remote work requires approval.")).toBeInTheDocument();
    expect(screen.getByText("Active seconds consumed (checkpointed)").parentElement).toHaveTextContent("14");
    expect(screen.getByText("Active seconds remaining (checkpointed)").parentElement).toHaveTextContent("1786");
    expect(fetchMock.mock.calls.filter(([, init]) => (init as RequestInit).method === "POST")).toHaveLength(2);
  });

  it("validates the independent limits before dispatch and restores wall-clock limits", () => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock); renderAgent(); durableMode(); input();
    choose("Durable budget", "active");
    const run = screen.getByRole("button", { name: "Run agent" });
    fireEvent.change(screen.getByRole("spinbutton", { name: "timeout_seconds" }), { target: { value: "3600" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "approval_timeout_seconds" }), { target: { value: "604800" } });
    expect(run).toBeEnabled();
    fireEvent.change(screen.getByRole("spinbutton", { name: "approval_timeout_seconds" }), { target: { value: "604801" } });
    expect(run).toBeDisabled();
    fireEvent.change(screen.getByRole("spinbutton", { name: "approval_timeout_seconds" }), { target: { value: "3600" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "timeout_seconds" }), { target: { value: "3601" } });
    expect(run).toBeDisabled();
    choose("Durable budget", "wall_clock");
    expect(screen.queryByRole("spinbutton", { name: "approval_timeout_seconds" })).not.toBeInTheDocument();
    expect(run).toBeDisabled(); expect(fetchMock).not.toHaveBeenCalled();
  });

  it("exposes an unconfigured background rejection and permits a deliberate inline start", async () => {
    const error = { request_id: "req-background-unconfigured", data: null, error: { code: "DURABLE_BACKGROUND_UNCONFIGURED", message: "Unavailable", details: {} } };
    const fetchMock = vi.fn().mockResolvedValueOnce(json(error, 503)).mockResolvedValueOnce(json(fixtures.durable.completed));
    vi.stubGlobal("fetch", fetchMock); renderAgent(); durableMode(); choose("Durable dispatch", "background"); input();
    fireEvent.click(screen.getByRole("button", { name: "Run agent" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Background dispatch is not configured");
    expect(screen.queryByRole("region", { name: "Startup recovery key" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledOnce();
    choose("Durable dispatch", "inline"); fireEvent.click(screen.getByRole("button", { name: "Run agent" }));
    expect(await screen.findByText("Remote work requires approval.")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("reads API-created modes and blocks repeated refresh and decisions while in flight", async () => {
    const refreshing = deferred<Response>(); const reviewing = deferred<Response>();
    const fetchMock = vi.fn().mockResolvedValueOnce(json(queued)).mockReturnValueOnce(refreshing.promise)
      .mockReturnValueOnce(reviewing.promise).mockResolvedValueOnce(json(queued));
    vi.stubGlobal("fetch", fetchMock); renderAgent(); read();
    expect(await screen.findByText(/Queued or running in a background worker/)).toBeInTheDocument();
    const refresh = screen.getByRole("button", { name: "Refresh run" });
    act(() => { fireEvent.click(refresh); fireEvent.click(refresh); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(async () => { refreshing.resolve(json(approval)); await Promise.resolve(); });
    const approve = screen.getByRole("button", { name: "Approve exact action" });
    act(() => { fireEvent.click(approve); fireEvent.click(approve); });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    await act(async () => { reviewing.resolve(json(approval)); await Promise.resolve(); });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    expect(screen.queryByRole("button", { name: "Resume run" })).not.toBeInTheDocument();
  });

  it("clears the current view and aborts a background refresh when the login changes", async () => {
    const refresh = deferred<Response>();
    const fetchMock = vi.fn().mockResolvedValueOnce(json(queued)).mockReturnValueOnce(refresh.promise);
    vi.stubGlobal("fetch", fetchMock); const view = renderAgent(); read();
    fireEvent.click(await screen.findByRole("button", { name: "Refresh run" }));
    view.rerender(view.component({ ...auth, label: "Refreshed login", mode: "bearer", bearerToken: "replacement-login" }));
    expect((fetchMock.mock.calls[1][1] as RequestInit).signal?.aborted).toBe(true);
    await act(async () => { refresh.resolve(json(completed)); await Promise.resolve(); });
    expect(screen.queryByText("Remote work requires approval.")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Last verified server state" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("expires a long approval at its own TTL while preserving checkpointed active counters", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const fetchMock = vi.fn().mockResolvedValue(json(approval)); vi.stubGlobal("fetch", fetchMock);
    renderAgent(); read();
    expect(await screen.findByRole("button", { name: "Approve exact action" })).toBeEnabled();
    await act(async () => { await Promise.resolve(); });
    vi.spyOn(Date, "now").mockReturnValue(approval.data.approval.expires_at * 1000);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve exact action" })).toBeDisabled());
    expect(screen.getByRole("alert")).toHaveTextContent("active execution budget is exhausted or this approval has expired");
    expect(screen.getByText("Active seconds remaining (checkpointed)").parentElement).toHaveTextContent("1788");
    fireEvent.click(screen.getByRole("button", { name: "Approve exact action" }));
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("shows configured ticket arguments for an explicit decision without dispatching on read", async () => {
    const ticket = { ...approval, data: { ...approval.data, approval: { ...approval.data.approval, tool_name: "create_support_ticket", arguments: { title: "Review case", description: "Reproduce the reported issue", priority: "normal" } } } };
    const fetchMock = vi.fn().mockResolvedValueOnce(json(ticket)).mockResolvedValueOnce(json(ticket)).mockResolvedValueOnce(json(fixtures.durable.cancelled));
    vi.stubGlobal("fetch", fetchMock); renderAgent(); read();
    expect(await screen.findByText("create_support_ticket")).toBeInTheDocument();
    expect(JSON.parse(screen.getByLabelText("Exact tool arguments").textContent ?? "")).toEqual(ticket.data.approval.arguments);
    expect(fetchMock).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Reject and cancel run" }));
    expect(await screen.findByText("The server confirmed the durable run is cancelled.")).toBeInTheDocument();
    expect(JSON.parse((fetchMock.mock.calls[2][1] as RequestInit).body as string)).toEqual({ expected_revision: 2, approval_id: ticket.data.approval.approval_id, approve: false });
  });
});
