import { StrictMode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PERSONAS } from "@/lib/auth";
import fixtures from "@/test/fixtures/chat-stream.json";
import { WorkbenchShell } from "./workbench-shell";

const sessionId = "session-independent-recovery";
const sessionKey = "aegisrag.chat.session.tenant-demo-alpha.demo-user-employee";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((complete, fail) => { resolve = complete; reject = fail; });
  return { promise, resolve, reject };
}

function historyResponse(content = "Independent saved question") {
  return new Response(JSON.stringify({
    request_id: "req-independent-history", error: null,
    data: { session_id: sessionId, messages: [{
      role: "user", content, sequence_no: 1, request_id: "req-saved",
      trace_id: "trace-saved", created_at: "2026-10-15T00:00:00Z", citations: []
    }] }
  }));
}

function renderWorkbench(strict = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const shell = <QueryClientProvider client={client}>
    <WorkbenchShell auth={PERSONAS.employee} language="en" onLanguageChange={vi.fn()} onSignOut={vi.fn()} />
  </QueryClientProvider>;
  return render(strict ? <StrictMode>{shell}</StrictMode> : shell);
}

function draft(value = "Retain my current draft") {
  fireEvent.change(screen.getByRole("textbox", { name: "Your question" }), { target: { value } });
}

async function flush(action: () => void) {
  await act(() => { action(); return Promise.resolve(); });
}

afterEach(() => {
  window.sessionStorage.clear();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("independent chat-history recovery edge cases", () => {
  it("keeps an interrupted successful response body retryable and preserves its draft and pointer", async () => {
    window.sessionStorage.setItem(sessionKey, sessionId);
    const response = historyResponse();
    vi.spyOn(response, "json").mockRejectedValueOnce(new TypeError("Body connection terminated"));
    const fetchMock = vi.fn().mockResolvedValueOnce(response).mockResolvedValueOnce(historyResponse());
    vi.stubGlobal("fetch", fetchMock);
    renderWorkbench();
    draft();
    const retry = await screen.findByRole("button", { name: "Retry history" });
    expect(window.sessionStorage.getItem(sessionKey)).toBe(sessionId);
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "Your question" })).toHaveValue("Retain my current draft");
    expect(fetchMock).toHaveBeenCalledOnce();
    fireEvent.click(retry);
    expect(await screen.findByText("Independent saved question")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled();
    expect(window.sessionStorage.getItem(sessionKey)).toBe(sessionId);
  });

  it("keeps invalid JSON permanent after a retry rather than mistaking it for a transport failure", async () => {
    window.sessionStorage.setItem(sessionKey, sessionId);
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("Offline"))
      .mockResolvedValueOnce(new Response("{not JSON"));
    vi.stubGlobal("fetch", fetchMock);
    renderWorkbench();
    draft();
    fireEvent.click(await screen.findByRole("button", { name: "Retry history" }));
    expect(await screen.findByText("Unable to restore this chat history. A new session will be used.")).toBeInTheDocument();
    expect(window.sessionStorage.getItem(sessionKey)).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry history" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled();
    expect(screen.getByRole("textbox", { name: "Your question" })).toHaveValue("Retain my current draft");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each(["invalid DTO", "permanent HTTP"] as const)("cannot erase a completed new conversation when an obsolete retry resolves with %s", async (outcome) => {
    window.sessionStorage.setItem(sessionKey, sessionId);
    const obsolete = deferred<Response>();
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("Offline"))
      .mockImplementationOnce(() => obsolete.promise)
      .mockResolvedValueOnce(new Response(fixtures.noAnswer));
    vi.stubGlobal("fetch", fetchMock);
    renderWorkbench();
    fireEvent.click(await screen.findByRole("button", { name: "Retry history" }));
    const obsoleteSignal = (fetchMock.mock.calls[1]?.[1] as RequestInit).signal;
    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
    expect(obsoleteSignal?.aborted).toBe(true);
    draft("Explicit new conversation");
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(window.sessionStorage.getItem(sessionKey)).toBe("session-fixture"));
    draft("Next new-conversation turn");
    await flush(() => obsolete.resolve(new Response(JSON.stringify({
      request_id: "req-obsolete", error: outcome === "permanent HTTP" ? { code: "NOT_FOUND", message: "Unavailable" } : null,
      data: outcome === "invalid DTO" ? { session_id: sessionId } : null
    }), { status: outcome === "permanent HTTP" ? 404 : 200 })));
    expect(window.sessionStorage.getItem(sessionKey)).toBe("session-fixture");
    expect(screen.getByText("Current session: session-fixture")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled();
    expect(screen.getByRole("textbox", { name: "Your question" })).toHaveValue("Next new-conversation turn");
    expect(screen.queryByText("Unable to restore this chat history. A new session will be used.")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("preserves current-read ownership through Strict Mode cleanup and a late aborted read", async () => {
    window.sessionStorage.setItem(sessionKey, sessionId);
    const obsolete = deferred<Response>();
    const current = deferred<Response>();
    const fetchMock = vi.fn().mockImplementationOnce(() => obsolete.promise)
      .mockImplementationOnce(() => current.promise);
    vi.stubGlobal("fetch", fetchMock);
    renderWorkbench(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).signal?.aborted).toBe(true);
    expect((fetchMock.mock.calls[1]?.[1] as RequestInit).signal?.aborted).toBe(false);
    draft();
    await flush(() => obsolete.reject(new TypeError("Aborted old fetch settled later")));
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    expect(window.sessionStorage.getItem(sessionKey)).toBe(sessionId);
    expect(screen.getByRole("status")).toHaveTextContent("Restoring conversation");
    await flush(() => current.resolve(historyResponse()));
    expect(await screen.findByText("Independent saved question")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled();
    expect(screen.getByRole("textbox", { name: "Your question" })).toHaveValue("Retain my current draft");
  });

  it("surfaces the real cooperative read deadline as explicit recovery without dropping the saved pointer", async () => {
    vi.useFakeTimers();
    window.sessionStorage.setItem(sessionKey, sessionId);
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      const signal = init?.signal as AbortSignal;
      signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    }));
    vi.stubGlobal("fetch", fetchMock);
    renderWorkbench();
    draft();
    await act(async () => { await vi.advanceTimersByTimeAsync(14_999); });
    expect(screen.queryByRole("button", { name: "Retry history" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(screen.getByRole("button", { name: "Retry history" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    expect(window.sessionStorage.getItem(sessionKey)).toBe(sessionId);
    expect(screen.getByRole("textbox", { name: "Your question" })).toHaveValue("Retain my current draft");
    expect(fetchMock).toHaveBeenCalledOnce();
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Retry history" })).toBeEnabled();
    expect(window.sessionStorage.getItem(sessionKey)).toBe(sessionId);
  });
});
