import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PERSONAS } from "@/lib/auth";
import fixtures from "@/test/fixtures/backend-workbench.json";
import stream from "@/test/fixtures/chat-stream.json";
import { WorkbenchShell } from "./workbench-shell";

const sessionKey = "aegisrag.chat.session.tenant-demo-alpha.demo-user-employee";
const draft = "Keep this follow-up question";
const response = () => new Response(JSON.stringify(fixtures.source), { headers: { "Content-Type": "application/json" } });
function deferred() {
  let resolve!: (value: Response) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<Response>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function renderWorkbench() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><WorkbenchShell auth={PERSONAS.employee}
    language="en" onLanguageChange={vi.fn()} onSignOut={vi.fn()} /></QueryClientProvider>);
}
function inspector() { return within(screen.getByRole("complementary", { name: "Evidence and diagnostics panel" })); }
function sourceButton() { return screen.getAllByRole("button", { name: /Evidence: Remote work/ })[0]; }
async function answerAndDraft() {
  fireEvent.change(screen.getByRole("textbox", { name: "Your question" }), { target: { value: "Can I work remotely?" } });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Copy answer with citations" })).toBeEnabled());
  fireEvent.change(screen.getByRole("textbox", { name: "Your question" }), { target: { value: draft } });
}
function expectConversationIntact() {
  expect(screen.getByText("Assistant").closest("article")).toHaveTextContent("Working remotely is supported");
  expect(screen.getByText("Can I work remotely?")).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Your question" })).toHaveValue(draft);
  expect(screen.getByText("Current session: session-fixture")).toBeInTheDocument();
  expect(window.sessionStorage.getItem(sessionKey)).toBe("session-fixture");
  expect(screen.getByRole("button", { name: "Copy answer with citations" })).toBeEnabled();
}
async function settle(action: () => void) {
  await act(() => { action(); return Promise.resolve(); });
}

afterEach(() => {
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("workbench citation recovery", () => {
  it("retries only the selected source while preserving the answer, session and draft", async () => {
    const retry = deferred();
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(stream.success))
      .mockRejectedValueOnce(new TypeError("Temporary source read failure"))
      .mockReturnValueOnce(retry.promise);
    vi.stubGlobal("fetch", fetchMock);
    renderWorkbench();
    await answerAndDraft();
    fireEvent.click(sourceButton());
    const button = await inspector().findByRole("button", { name: "Retry source" });
    // Re-selecting exactly the same citation still does not initiate a request.
    fireEvent.click(sourceButton());
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expectConversationIntact();
    act(() => { button.click(); button.click(); });
    expect(inspector().getByText("Re-authorizing source")).toBeInTheDocument();
    expectConversationIntact();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    await settle(() => retry.resolve(response()));
    await inspector().findByText(fixtures.source.data.text_excerpt);
    expectConversationIntact();
    expect(fetchMock.mock.calls.map((call) => call[0] as string)).toEqual([
      "/api/backend/chat/stream", "/api/backend/sources/resolve", "/api/backend/sources/resolve"
    ]);
    const firstRead = fetchMock.mock.calls[1]?.[1] as RequestInit;
    const retryRead = fetchMock.mock.calls[2]?.[1] as RequestInit;
    expect(retryRead.body).toBe(firstRead.body);
    expect(JSON.parse(retryRead.body as string)).toEqual({
      document_id: "doc-pilot", version_id: "version-pilot", chunk_id: "chunk-pilot"
    });
  });

  it("retains the Diagnostics to Evidence recovery workaround without losing the conversation", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(stream.success))
      .mockRejectedValueOnce(new TypeError("Temporary source read failure"))
      .mockResolvedValueOnce(response());
    vi.stubGlobal("fetch", fetchMock);
    renderWorkbench();
    await answerAndDraft();
    fireEvent.click(sourceButton());
    await inspector().findByRole("alert");
    fireEvent.click(sourceButton());
    expect(fetchMock).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("tab", { name: "Diagnostics" }));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expectConversationIntact();
    fireEvent.click(screen.getByRole("tab", { name: "Evidence" }));
    await inspector().findByText(fixtures.source.data.text_excerpt);
    expectConversationIntact();
    expect(fetchMock.mock.calls.map((call) => call[0] as string)).toEqual([
      "/api/backend/chat/stream", "/api/backend/sources/resolve", "/api/backend/sources/resolve"
    ]);
  });

  it.each(["resolve", "reject"] as const)("ignores a stale retry %s after inspector tab dismissal and reopening", async (outcome) => {
    const old = deferred();
    const reopened = deferred();
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(stream.success))
      .mockRejectedValueOnce(new TypeError("Transient"))
      .mockReturnValueOnce(old.promise).mockReturnValueOnce(reopened.promise);
    vi.stubGlobal("fetch", fetchMock);
    renderWorkbench();
    await answerAndDraft();
    fireEvent.click(sourceButton());
    fireEvent.click(await inspector().findByRole("button", { name: "Retry source" }));
    fireEvent.click(screen.getByRole("tab", { name: "Diagnostics" }));
    expect((fetchMock.mock.calls[2]?.[1] as RequestInit).signal?.aborted).toBe(true);
    fireEvent.click(screen.getByRole("tab", { name: "Evidence" }));
    await settle(() => outcome === "resolve" ? old.resolve(response()) : old.reject(new TypeError("Late failure")));
    expect(inspector().getByText("Re-authorizing source")).toBeInTheDocument();
    expect(inspector().queryByRole("button", { name: "Retry source" })).not.toBeInTheDocument();
    expect(inspector().queryByText(fixtures.source.data.text_excerpt)).not.toBeInTheDocument();
    expectConversationIntact();
    await settle(() => reopened.resolve(response()));
    await inspector().findByText(fixtures.source.data.text_excerpt);
    expectConversationIntact();
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it.each(["resolve", "reject"] as const)("ignores a stale retry %s after starting a new conversation", async (outcome) => {
    const retry = deferred();
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(stream.success))
      .mockRejectedValueOnce(new TypeError("Transient"))
      .mockReturnValueOnce(retry.promise);
    vi.stubGlobal("fetch", fetchMock);
    renderWorkbench();
    await answerAndDraft();
    fireEvent.click(sourceButton());
    fireEvent.click(await inspector().findByRole("button", { name: "Retry source" }));
    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
    expect((fetchMock.mock.calls[2]?.[1] as RequestInit).signal?.aborted).toBe(true);
    await settle(() => outcome === "resolve" ? retry.resolve(response()) : retry.reject(new TypeError("Late failure")));
    expect(inspector().queryByText(fixtures.source.data.text_excerpt)).not.toBeInTheDocument();
    expect(inspector().queryByRole("button", { name: "Retry source" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Working remotely is supported/)).not.toBeInTheDocument();
    expect(screen.getByText("Current session: new")).toBeInTheDocument();
    expect(window.sessionStorage.getItem(sessionKey)).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
