import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PERSONAS, type AuthSession } from "@/lib/auth";
import fixtures from "@/test/fixtures/agent-lifecycle.json";
import { AgentPanel } from "./agent-panel";

const initialAuth: AuthSession = { ...PERSONAS.platform_admin, tenantId: "tenant-pilot", userId: "user-pilot" };
function json(value: unknown) { return new Response(JSON.stringify(value), { headers: { "content-type": "application/json" } }); }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
function renderAgent() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const component = (auth = initialAuth) => <QueryClientProvider client={client}><AgentPanel auth={auth} language="en" canRun /></QueryClientProvider>;
  return { ...render(component()), component };
}
function recover() {
  fireEvent.change(screen.getByRole("combobox", { name: "Execution mode" }), { target: { value: "durable" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Durable run ID" }), { target: { value: fixtures.durable.awaitingApproval.data.run_id } });
  fireEvent.click(screen.getByRole("button", { name: "Read run" }));
}
async function resolveRead(pending: ReturnType<typeof deferred<Response>>, value: unknown) {
  await act(async () => { pending.resolve(json(value)); await Promise.resolve(); });
}
beforeEach(() => { vi.spyOn(Date, "now").mockReturnValue(fixtures.clockEpochSeconds * 1000); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Independent Agent action-ownership review", () => {
  it("auth replacement during approval pre-read cannot dispatch the old approval", async () => {
    const pending = deferred<Response>();
    const fetchMock = vi.fn().mockResolvedValueOnce(json(fixtures.durable.awaitingApproval)).mockReturnValueOnce(pending.promise);
    vi.stubGlobal("fetch", fetchMock);
    const view = renderAgent(); recover();
    fireEvent.click(await screen.findByRole("button", { name: "Approve exact action" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    view.rerender(view.component({ ...initialAuth, userId: "replacement-user" }));
    await resolveRead(pending, fixtures.durable.awaitingApproval);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect((fetchMock.mock.calls[1][1] as RequestInit).signal?.aborted).toBe(true);
    expect(screen.queryByRole("button", { name: "Approve exact action" })).not.toBeInTheDocument();
  });

  it("stopping the approval pre-read prevents dispatch even if the read later succeeds", async () => {
    const pending = deferred<Response>();
    const fetchMock = vi.fn().mockResolvedValueOnce(json(fixtures.durable.awaitingApproval)).mockReturnValueOnce(pending.promise);
    vi.stubGlobal("fetch", fetchMock); renderAgent(); recover();
    fireEvent.click(await screen.findByRole("button", { name: "Approve exact action" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "Stop waiting" }));
    await resolveRead(pending, fixtures.durable.awaitingApproval);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "Approve exact action" })).toBeDisabled();
  });

  it("an approval expiring while its pre-read is in flight cannot be submitted", async () => {
    const pending = deferred<Response>();
    const fetchMock = vi.fn().mockResolvedValueOnce(json(fixtures.durable.awaitingApproval)).mockReturnValueOnce(pending.promise);
    vi.stubGlobal("fetch", fetchMock); renderAgent(); recover();
    fireEvent.click(await screen.findByRole("button", { name: "Approve exact action" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    vi.spyOn(Date, "now").mockReturnValue(fixtures.durable.awaitingApproval.data.deadline_at * 1000);
    await resolveRead(pending, fixtures.durable.awaitingApproval);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    // The visual clock ticks each second; the synchronous action guard must
    // already refuse another click during that short rendering interval.
    fireEvent.click(screen.getByRole("button", { name: "Approve exact action" }));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve exact action" })).toBeDisabled(), { timeout: 2000 });
    expect(screen.getAllByText(/original deadline or approval has expired/i).length).toBeGreaterThan(0);
  });

  it("same-revision argument drift cannot inherit the prior user approval", async () => {
    const changed = { ...fixtures.durable.awaitingApproval, data: { ...fixtures.durable.awaitingApproval.data, approval: { ...fixtures.durable.awaitingApproval.data.approval, arguments: { query: "Changed action", top_k: 4 } } } };
    const fetchMock = vi.fn().mockResolvedValueOnce(json(fixtures.durable.awaitingApproval)).mockResolvedValueOnce(json(changed));
    vi.stubGlobal("fetch", fetchMock); renderAgent(); recover();
    fireEvent.click(await screen.findByRole("button", { name: "Approve exact action" }));
    expect(await screen.findByText(/server state changed after you reviewed it/i)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText("Exact tool arguments")).toHaveTextContent("Changed action");
  });

  it("late cancellation after Stop waiting is not shown as a cancellation receipt", async () => {
    const pending = deferred<Response>();
    const fetchMock = vi.fn().mockResolvedValueOnce(json(fixtures.durable.running)).mockReturnValueOnce(pending.promise);
    vi.stubGlobal("fetch", fetchMock); renderAgent(); recover();
    fireEvent.click(await screen.findByRole("button", { name: "Cancel durable run" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "Stop waiting" }));
    await resolveRead(pending, fixtures.durable.cancelled);
    expect(screen.queryByText("The server confirmed the durable run is cancelled.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel durable run" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("a discarded old startup response cannot erase a newer unresolved reference", async () => {
    const oldKey = "587629a6-65a2-4db2-8e18-c17d8190c0ac";
    const newKey = "a87629a6-65a2-4db2-8e18-c17d8190c0ac";
    vi.spyOn(crypto, "randomUUID").mockReturnValueOnce(oldKey).mockReturnValueOnce(newKey);
    const older = deferred<Response>(); const newer = deferred<Response>();
    const fetchMock = vi.fn().mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
    vi.stubGlobal("fetch", fetchMock); renderAgent();
    fireEvent.change(screen.getByRole("combobox", { name: "Execution mode" }), { target: { value: "durable" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Agent input" }), { target: { value: "Explicit new run" } });
    fireEvent.click(screen.getByRole("button", { name: "Run agent" }));
    fireEvent.click(screen.getByRole("button", { name: "Stop waiting" }));
    fireEvent.click(screen.getByRole("checkbox", { name: /server effects remain unresolved/ }));
    fireEvent.click(screen.getByRole("button", { name: "Discard local startup reference" }));
    fireEvent.click(screen.getByRole("button", { name: "Run agent" }));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await resolveRead(older, fixtures.durable.completed);
    expect(screen.queryByText("Remote work requires approval.")).not.toBeInTheDocument();
    expect(screen.getByText(newKey)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Read by retained startup key" })).toBeDisabled();
    await resolveRead(newer, fixtures.durable.awaitingApproval);
    expect(await screen.findByRole("button", { name: "Approve exact action" })).toBeEnabled();
    expect(screen.queryByRole("region", { name: "Startup recovery key" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
