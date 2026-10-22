import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PERSONAS } from "@/lib/auth";
import type { Language } from "@/lib/i18n";
import fixtures from "@/test/fixtures/chat-stream.json";
import { WorkbenchShell } from "./workbench-shell";

const sessionId = "session-history-recovery";
const sessionKey = "aegisrag.chat.session.tenant-demo-alpha.demo-user-employee";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((complete, fail) => { resolve = complete; reject = fail; });
  return { promise, resolve, reject };
}

function historyResponse(content = "Earlier question", id = sessionId) {
  return new Response(JSON.stringify({
    request_id: "req-history-recovery", error: null, data: { session_id: id, messages: [{
      role: "user", content, sequence_no: 1, request_id: "req-earlier",
      trace_id: "trace-earlier", created_at: "2026-10-15T00:00:00Z", citations: []
    }] }
  }));
}

function failedResponse(status: number) {
  return new Response(JSON.stringify({
    request_id: "req-history-recovery", data: null,
    error: { code: `HTTP_${status}`, message: "History read failed." }
  }), { status });
}

function renderWorkbench() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const component = (language: Language = "en") => (
    <QueryClientProvider client={client}>
      <WorkbenchShell auth={PERSONAS.employee} language={language} onLanguageChange={vi.fn()} onSignOut={vi.fn()} />
    </QueryClientProvider>
  );
  return { ...render(component()), component };
}

function typeDraft(value = "Continue the earlier question") {
  fireEvent.change(screen.getByRole("textbox", { name: "Your question" }), { target: { value } });
}

async function flush(action: () => void) {
  await act(() => { action(); return Promise.resolve(); });
}

afterEach(() => {
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("saved conversation history recovery", () => {
  it.each([
    ["network TypeError", () => Promise.reject(new TypeError("Network unavailable"))],
    ["timeout", () => Promise.reject(new DOMException("Timed out", "TimeoutError"))],
    ...[408, 429, 500, 502, 503, 504].map((status) => [`HTTP ${status}`, () => Promise.resolve(failedResponse(status))] as const)
  ] as const)("retains the saved session and blocks asking after %s", async (_label, failure) => {
    window.sessionStorage.setItem(sessionKey, sessionId);
    const fetchMock = vi.fn(failure);
    vi.stubGlobal("fetch", fetchMock);
    renderWorkbench();
    typeDraft();
    await waitFor(() => expect(screen.queryByText("Restoring conversation…")).not.toBeInTheDocument());
    expect(window.sessionStorage.getItem(sessionKey)).toBe(sessionId);
    expect(screen.getByRole("button", { name: "Retry history" })).toBeEnabled();
    expect(screen.getByText(`Current session: ${sessionId}`)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Your question" })).toHaveValue("Continue the earlier question");
    expect(screen.getByRole("textbox", { name: "Your question" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await flush(() => {});
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("status")).toHaveTextContent("Retry restoring it or start a new conversation before asking.");
  });

  it("coalesces repeated retry clicks, restores the same ID and preserves the draft for its next POST", async () => {
    window.sessionStorage.setItem(sessionKey, sessionId);
    const retry = deferred<Response>();
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("Network unavailable"))
      .mockImplementationOnce(() => retry.promise)
      .mockResolvedValueOnce(new Response(fixtures.noAnswer.replaceAll("session-fixture", sessionId)));
    vi.stubGlobal("fetch", fetchMock);
    renderWorkbench();
    const retryButton = await screen.findByRole("button", { name: "Retry history" });
    typeDraft();
    act(() => { retryButton.click(); retryButton.click(); retryButton.click(); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1]?.[0]).toBe(fetchMock.mock.calls[0]?.[0]);
    expect(fetchMock.mock.calls[1]?.[0]).toContain(`session_id=${sessionId}`);
    expect((fetchMock.mock.calls[1]?.[1] as RequestInit).method).toBe("GET");
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    await flush(() => { retry.resolve(historyResponse()); });
    expect(await screen.findByText("Earlier question")).toBeInTheDocument();
    expect(screen.getByText("History restored from backend memory")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry history" })).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Your question" })).toHaveValue("Continue the earlier question");
    expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const post = fetchMock.mock.calls[2]?.[1] as RequestInit;
    expect(post.method).toBe("POST");
    expect(JSON.parse(post.body as string)).toEqual({ query: "Continue the earlier question", session_id: sessionId });
    await waitFor(() => expect(screen.getByRole("button", { name: "Copy answer with citations" })).toBeEnabled());
  });

  it("keeps a second transient failure recoverable without an automatic retry", async () => {
    window.sessionStorage.setItem(sessionKey, sessionId);
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("Offline"))
      .mockResolvedValueOnce(failedResponse(503));
    vi.stubGlobal("fetch", fetchMock);
    renderWorkbench();
    fireEvent.click(await screen.findByRole("button", { name: "Retry history" }));
    expect(await screen.findByRole("button", { name: "Retry history" })).toBeEnabled();
    typeDraft("Edited draft after another outage");
    expect(window.sessionStorage.getItem(sessionKey)).toBe(sessionId);
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("starts a new conversation only after the explicit choice and clears its draft as before", async () => {
    window.sessionStorage.setItem(sessionKey, sessionId);
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("Offline"))
      .mockResolvedValueOnce(new Response(fixtures.noAnswer));
    vi.stubGlobal("fetch", fetchMock);
    renderWorkbench();
    const staleRetryButton = await screen.findByRole("button", { name: "Retry history" });
    typeDraft("Old draft");
    const newConversationButton = screen.getByRole("button", { name: "New conversation" });
    act(() => { newConversationButton.click(); staleRetryButton.click(); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(window.sessionStorage.getItem(sessionKey)).toBeNull();
    expect(screen.getByText("Current session: new")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Your question" })).toHaveValue("");
    expect(screen.queryByRole("button", { name: "Retry history" })).not.toBeInTheDocument();
    typeDraft("A deliberately new question");
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse((fetchMock.mock.calls[1]?.[1] as RequestInit).body as string)).toEqual({ query: "A deliberately new question" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Copy answer with citations" })).toBeEnabled());
  });

  it.each(["success", "failure"] as const)("ignores late retry %s after New conversation without unlocking its current stream", async (outcome) => {
    window.sessionStorage.setItem(sessionKey, sessionId);
    const retry = deferred<Response>();
    const current = deferred<Response>();
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("Offline"))
      .mockImplementationOnce(() => retry.promise).mockImplementationOnce(() => current.promise);
    vi.stubGlobal("fetch", fetchMock);
    renderWorkbench();
    fireEvent.click(await screen.findByRole("button", { name: "Retry history" }));
    const retrySignal = (fetchMock.mock.calls[1]?.[1] as RequestInit).signal;
    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
    expect(retrySignal?.aborted).toBe(true);
    typeDraft("New stream question");
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await flush(() => {
      if (outcome === "success") retry.resolve(historyResponse("Obsolete restored question"));
      else retry.reject(new TypeError("Late failed read"));
    });
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    expect(screen.queryByText("Obsolete restored question")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry history" })).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem(sessionKey)).toBeNull();
    await flush(() => { current.resolve(new Response(fixtures.noAnswer)); });
    await waitFor(() => expect(screen.getByRole("button", { name: "Copy answer with citations" })).toBeEnabled());
    expect(window.sessionStorage.getItem(sessionKey)).toBe("session-fixture");
  });

  it("cancels a retry on navigation and reopens the saved session without stale history overriding the newer read", async () => {
    window.sessionStorage.setItem(sessionKey, sessionId);
    const obsolete = deferred<Response>();
    const reopened = deferred<Response>();
    const reads = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>().mockRejectedValueOnce(new TypeError("Offline"))
      .mockImplementationOnce(() => obsolete.promise).mockImplementationOnce(() => reopened.promise);
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      if (url.includes("/chat/history")) return reads(input, init);
      return Promise.resolve(new Response(JSON.stringify({ request_id: "req-list", error: null, data: { items: [] } })));
    }));
    renderWorkbench();
    fireEvent.click(await screen.findByRole("button", { name: "Retry history" }));
    const retrySignal = (reads.mock.calls[1]?.[1] as RequestInit).signal;
    fireEvent.click(screen.getByRole("button", { name: /Knowledge Base/ }));
    expect(retrySignal?.aborted).toBe(true);
    expect(window.sessionStorage.getItem(sessionKey)).toBe(sessionId);
    fireEvent.click(screen.getByRole("button", { name: "AskRAG Q&A" }));
    expect(reads).toHaveBeenCalledTimes(3);
    typeDraft("Reopened draft");
    await flush(() => { obsolete.resolve(historyResponse("Obsolete read")); });
    expect(screen.queryByText("Obsolete read")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    await flush(() => { reopened.resolve(historyResponse("Freshly restored read")); });
    expect(await screen.findByText("Freshly restored read")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Your question" })).toHaveValue("Reopened draft");
    expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled();
  });

  it("cancels an unmounted retry without discarding its saved pointer", async () => {
    window.sessionStorage.setItem(sessionKey, sessionId);
    const retry = deferred<Response>();
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("Offline")).mockImplementationOnce(() => retry.promise);
    vi.stubGlobal("fetch", fetchMock);
    const view = renderWorkbench();
    fireEvent.click(await screen.findByRole("button", { name: "Retry history" }));
    const signal = (fetchMock.mock.calls[1]?.[1] as RequestInit).signal;
    view.unmount();
    expect(signal?.aborted).toBe(true);
    await flush(() => { retry.reject(new TypeError("Late failed read")); });
    expect(window.sessionStorage.getItem(sessionKey)).toBe(sessionId);
  });

  it("localizes recovery without refetching or discarding a typed draft on language changes", async () => {
    window.sessionStorage.setItem(sessionKey, sessionId);
    const retry = deferred<Response>();
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("Offline")).mockImplementationOnce(() => retry.promise);
    vi.stubGlobal("fetch", fetchMock);
    const view = renderWorkbench();
    await screen.findByRole("button", { name: "Retry history" });
    typeDraft("Keep this draft");
    view.rerender(view.component("zh"));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("textbox", { name: "你的问题" })).toHaveValue("Keep this draft");
    expect(screen.getByRole("status")).toHaveTextContent("对话历史暂时无法加载");
    fireEvent.click(screen.getByRole("button", { name: "重试恢复历史" }));
    view.rerender(view.component("en"));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await flush(() => { retry.resolve(historyResponse()); });
    expect(await screen.findByText("Earlier question")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Your question" })).toHaveValue("Keep this draft");
  });

  it.each([400, 404, 422])("keeps the existing nonretryable HTTP %s new-session fallback", async (status) => {
    window.sessionStorage.setItem(sessionKey, sessionId);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(failedResponse(status)));
    renderWorkbench();
    typeDraft();
    expect(await screen.findByText("Unable to restore this chat history. A new session will be used.")).toBeInTheDocument();
    expect(window.sessionStorage.getItem(sessionKey)).toBeNull();
    expect(screen.getByText("Current session: new")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry history" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled();
  });

  it.each(["missing messages", "mismatched session"])("keeps %s on the invalid-history fallback instead of retrying a mapping TypeError", async (malformation) => {
    window.sessionStorage.setItem(sessionKey, sessionId);
    const response = malformation === "missing messages"
      ? new Response(JSON.stringify({ request_id: "req-invalid", error: null, data: { session_id: sessionId } }))
      : historyResponse("Wrong conversation data", "another-session");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response));
    renderWorkbench();
    typeDraft();
    expect(await screen.findByText("Unable to restore this chat history. A new session will be used.")).toBeInTheDocument();
    expect(window.sessionStorage.getItem(sessionKey)).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry history" })).not.toBeInTheDocument();
    expect(screen.queryByText("Wrong conversation data")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled();
    expect(screen.getByRole("textbox", { name: "Your question" })).toHaveValue("Continue the earlier question");
  });
});
