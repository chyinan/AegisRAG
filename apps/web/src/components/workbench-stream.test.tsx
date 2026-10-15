import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSession } from "@/lib/auth";
import type { Language } from "@/lib/i18n";
import { PERSONAS } from "@/lib/auth";
import fixtures from "@/test/fixtures/chat-stream.json";
import { WorkbenchShell } from "./workbench-shell";

const sessionKey = "aegisrag.chat.session.tenant-demo-alpha.demo-user-employee";

function pendingStream() {
  let controller: ReadableStreamDefaultController<Uint8Array>;
  const cancelled = vi.fn();
  const response = new Response(new ReadableStream<Uint8Array>({
    start(value) { controller = value; },
    cancel: cancelled
  }), { headers: { "content-type": "text/event-stream" } });
  return {
    response, cancelled,
    push: (wire: string) => controller.enqueue(new TextEncoder().encode(wire)),
    close: () => controller.close()
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => { resolve = complete; });
  return { promise, resolve };
}

function renderWorkbench(auth: AuthSession = PERSONAS.employee) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const props = { auth, language: "en" as Language, onLanguageChange: vi.fn(), onSignOut: vi.fn() };
  const component = (next = props) => <QueryClientProvider client={client}><WorkbenchShell {...next} /></QueryClientProvider>;
  return { ...render(component()), component, props };
}

async function ask(question = "Can I work remotely?") {
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox", { name: "Your question" }), question);
  await user.click(screen.getByRole("button", { name: "Ask" }));
}

// Resolve the producer's queued microtasks inside React's async act boundary.
async function flush(action: () => void): Promise<void> {
  await act(() => { action(); return Promise.resolve(); });
}

function copyButton() { return screen.getByRole("button", { name: "Copy answer with citations" }); }

afterEach(() => {
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("workbench stream lifecycle", () => {
  it("renders backend deltas while copy is locked, then replaces with final citations", async () => {
    const stream = pendingStream();
    const fetchMock = vi.fn().mockResolvedValue(stream.response);
    vi.stubGlobal("fetch", fetchMock);
    renderWorkbench();
    await ask();
    await flush(() => { stream.push(fixtures.tokensOnly); });
    expect(await screen.findByText("Working remotely 🌍 is supported.")).toBeInTheDocument();
    expect(copyButton()).toBeDisabled();
    await flush(() => { stream.push(fixtures.success.slice(fixtures.tokensOnly.length)); });
    expect(await screen.findByText(/Working remotely is supported/)).toBeInTheDocument();
    expect(copyButton()).toBeEnabled();
    expect(screen.getAllByRole("button", { name: /Evidence: Remote work/ })).toHaveLength(2);
    expect(window.sessionStorage.getItem(sessionKey)).toBe("session-fixture");
    expect(stream.cancelled).toHaveBeenCalledOnce();
  });

  it.each(["error", "failedFinal"] as const)("keeps %s HTTP-200 responses visibly failed and uncopyable", async (fixture) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(fixtures[fixture])));
    renderWorkbench();
    await ask();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("RAG_QUERY_FAILED");
    expect(alert).toHaveTextContent("req-stream-fixture");
    expect(copyButton()).toBeDisabled();
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Your question" })).toHaveValue("Can I work remotely?"));
    expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled();
    expect(screen.queryByText("No-answer is a valid result")).not.toBeInTheDocument();
  });

  it("distinguishes successful no-answer from generation failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(fixtures.noAnswer)));
    renderWorkbench();
    await ask();
    await waitFor(() => expect(copyButton()).toBeEnabled());
    expect(screen.getByRole("button", { name: "View diagnostics" })).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("returns a truncated response to a recoverable state and preserves the question", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(fixtures.tokensOnly)));
    renderWorkbench();
    await ask();
    expect(await screen.findByRole("alert")).toHaveTextContent("CHAT_STREAM_INCOMPLETE");
    expect(screen.getByRole("alert")).toHaveTextContent("req-stream-fixture");
    expect(copyButton()).toBeDisabled();
    await waitFor(() => expect(screen.getByRole("button", { name: "Ask" })).toBeEnabled());
  });

  it("guards repeated submit synchronously before React commits a disabled button", async () => {
    const stream = pendingStream();
    const fetchMock = vi.fn().mockResolvedValue(stream.response);
    vi.stubGlobal("fetch", fetchMock);
    renderWorkbench();
    fireEvent.change(screen.getByRole("textbox", { name: "Your question" }), { target: { value: "Question" } });
    const button = screen.getByRole("button", { name: "Ask" });
    act(() => { button.click(); button.click(); button.click(); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await flush(() => { stream.push(fixtures.noAnswer); });
  });

  it("aborts on navigation and ignores a late response from the previous conversation", async () => {
    const response = deferred<Response>();
    let signal: AbortSignal | null | undefined;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      if (!url.includes("/chat/stream")) {
        return Promise.resolve(new Response(JSON.stringify({ request_id: "req-list", error: null, data: { items: [] } })));
      }
      signal = init?.signal;
      return response.promise;
    }));
    renderWorkbench();
    await ask();
    await userEvent.click(screen.getByRole("button", { name: /Knowledge Base/ }));
    expect(signal?.aborted).toBe(true);
    await flush(() => { response.resolve(new Response(fixtures.success)); });
    expect(window.sessionStorage.getItem(sessionKey)).toBeNull();
    expect(screen.queryByText(/Working remotely is supported/)).not.toBeInTheDocument();
  });

  it("cancels a pending stream on unmount and auth-scope change", async () => {
    const first = pendingStream();
    const second = pendingStream();
    const signals: Array<AbortSignal | null | undefined> = [];
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      signals.push(init?.signal);
      return Promise.resolve(signals.length === 1 ? first.response : second.response);
    }));
    const view = renderWorkbench();
    await ask();
    view.rerender(view.component({ ...view.props, auth: { ...PERSONAS.employee, userId: "other-user" } }));
    expect(signals[0]?.aborted).toBe(true);
    expect(screen.queryByText("Can I work remotely?")).not.toBeInTheDocument();
    await ask("A new identity question");
    view.unmount();
    expect(signals[1]?.aborted).toBe(true);
    await waitFor(() => expect(second.cancelled).toHaveBeenCalledOnce());
  });

  it("new conversation cancels a delayed fetch; late old finally cannot unlock the new request", async () => {
    const old = deferred<Response>();
    const current = pendingStream();
    const signals: Array<AbortSignal | null | undefined> = [];
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      signals.push(init?.signal);
      return signals.length === 1 ? old.promise : Promise.resolve(current.response);
    }));
    renderWorkbench();
    await ask("Old question");
    await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
    expect(signals[0]?.aborted).toBe(true);
    await ask("Current question");
    await flush(() => { old.resolve(new Response(fixtures.success)); });
    expect(screen.queryByText("Old question")).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem(sessionKey)).toBeNull();
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    expect(copyButton()).toBeDisabled();
    await flush(() => { current.push(fixtures.noAnswer.replaceAll("session-fixture", "session-current")); });
    await waitFor(() => expect(copyButton()).toBeEnabled());
    expect(window.sessionStorage.getItem(sessionKey)).toBe("session-current");
  });

  it("ignores late history after new conversation and does not restore again on language change", async () => {
    window.sessionStorage.setItem(sessionKey, "session-old");
    const old = deferred<Response>();
    const current = pendingStream();
    const signals: Array<AbortSignal | null | undefined> = [];
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      signals.push(init?.signal);
      return signals.length === 1 ? old.promise : Promise.resolve(current.response);
    });
    vi.stubGlobal("fetch", fetchMock);
    const view = renderWorkbench();
    expect(await screen.findByRole("status")).toHaveTextContent("Restoring conversation");
    await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
    expect(signals[0]?.aborted).toBe(true);
    await ask("Current question");
    await flush(() => { old.resolve(new Response(JSON.stringify({
      request_id: "req-old", error: null, data: { session_id: "session-old", messages: [{
        role: "user", content: "Obsolete history", sequence_no: 1, request_id: "req-old", trace_id: "trace-old", created_at: "2026-10-14T00:00:00Z"
      }] }
    }))); });
    expect(screen.queryByText("Obsolete history")).not.toBeInTheDocument();
    expect(screen.getByText("Current question")).toBeInTheDocument();
    await flush(() => { current.push(fixtures.noAnswer); });
    view.rerender(view.component({ ...view.props, language: "zh" }));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Current question")).toBeInTheDocument();
  });

  it("keeps successful answers usable when browser session storage is unavailable", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("Blocked", "SecurityError"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("Blocked", "SecurityError"); });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(fixtures.noAnswer)));
    renderWorkbench();
    await ask();
    await waitFor(() => expect(copyButton()).toBeEnabled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText(/Current session: session-fixture/)).toBeInTheDocument();
  });

  it("never restores a shared fallback session when bearer identity claims are unavailable", () => {
    window.sessionStorage.setItem("aegisrag.chat.session.jwt-tenant.jwt-user", "someone-elses-session");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderWorkbench({ ...PERSONAS.employee, mode: "bearer", userId: undefined, tenantId: undefined, bearerToken: "synthetic-test-credential" });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText(/Current session: new/)).toBeInTheDocument();
  });

  it("filters system summaries and restores failed history as an error without copying", async () => {
    window.sessionStorage.setItem(sessionKey, "session-old");
    const base = { request_id: "req-old", trace_id: "trace-old", created_at: "2026-10-14T00:00:00Z", citations: [] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      request_id: "req-old", error: null, data: { session_id: "session-old", messages: [
        { ...base, role: "system_summary", content: "Private summary", sequence_no: 1 },
        { ...base, role: "assistant", content: "stream_error:RAG_QUERY_FAILED", sequence_no: 2, no_answer: true, error_code: "RAG_QUERY_FAILED" }
      ] }
    }))));
    renderWorkbench();
    expect(await screen.findByRole("alert")).toHaveTextContent("RAG_QUERY_FAILED");
    expect(screen.queryByText("Private summary")).not.toBeInTheDocument();
    expect(screen.queryByText("stream_error:RAG_QUERY_FAILED")).not.toBeInTheDocument();
    expect(copyButton()).toBeDisabled();
    expect(within(screen.getByRole("alert")).getByText(/req-old/)).toBeInTheDocument();
  });
});
