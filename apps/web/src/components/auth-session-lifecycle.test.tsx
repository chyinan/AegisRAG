import { act, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import Home from "../../app/page";
import fixtures from "@/test/fixtures/backend-workbench.json";
import streams from "@/test/fixtures/chat-stream.json";

async function flush(action: () => void): Promise<void> {
  await act(() => { action(); return Promise.resolve(); });
}

function renderHome() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  return { client, ...render(<QueryClientProvider client={client}><Home /></QueryClientProvider>) };
}

async function signIn() {
  fireEvent.change(screen.getByLabelText("Tenant ID"), { target: { value: "tenant-pilot" } });
  fireEvent.change(screen.getByLabelText("Username"), { target: { value: "pilot" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "synthetic-password" } });
  await flush(() => fireEvent.click(screen.getByRole("button", { name: "Sign In" })));
}

async function ask() {
  fireEvent.change(screen.getByRole("textbox", { name: "Your question" }), { target: { value: "Remote work?" } });
  await flush(() => fireEvent.click(screen.getByRole("button", { name: "Ask" })));
}

function streamResponse(cancel: () => void): Response {
  return new Response(new ReadableStream<Uint8Array>({
    start(controller) { controller.enqueue(new TextEncoder().encode(streams.tokensOnly)); },
    cancel
  }));
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

describe("Home authenticated session boundary", () => {
  it("unmounts and aborts an active answer at expiry, clears cache and shows a re-login notice", async () => {
    vi.useFakeTimers();
    const canceled = vi.fn();
    let streamSignal: AbortSignal | null | undefined;
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      if (url === "/api/auth/login") return Promise.resolve(new Response(JSON.stringify({
        ...fixtures.login, data: { ...fixtures.login.data, expires_in: 1 }
      })));
      if (url.endsWith("/chat/stream")) {
        streamSignal = init?.signal;
        return Promise.resolve(streamResponse(canceled));
      }
      throw new Error("Unexpected request in expiry test");
    });
    vi.stubGlobal("fetch", fetchMock);
    const { client } = renderHome();
    await signIn();
    expect(screen.getByTestId("workbench-shell")).toBeInTheDocument();
    await ask();
    client.setQueryData(["authenticated-synthetic-query"], { title: "Private test data" });
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.queryByTestId("workbench-shell")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Your session expired. Sign in again to continue.");
    expect(streamSignal?.aborted).toBe(true);
    expect(canceled).toHaveBeenCalledOnce();
    expect(client.getQueryCache().getAll()).toHaveLength(0);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(storageWrite).not.toHaveBeenCalled();
  });

  it("signs out locally before server completion and cannot let a late old result affect a new identity", async () => {
    let logoutComplete!: (response: Response) => void;
    let logoutSignal: AbortSignal | null | undefined;
    let streamSignal: AbortSignal | null | undefined;
    let logins = 0;
    const canceled = vi.fn();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      if (url === "/api/auth/login") {
        logins += 1;
        return Promise.resolve(new Response(JSON.stringify({ ...fixtures.login, data: {
          ...fixtures.login.data, user_id: `user-${logins}`, display_name: `Pilot ${logins}`,
          access_token: `synthetic-access-${logins}`
        } })));
      }
      if (url.endsWith("/chat/stream")) {
        streamSignal = init?.signal;
        return Promise.resolve(streamResponse(canceled));
      }
      if (url === "/api/auth/logout") {
        logoutSignal = init?.signal;
        expect(init?.headers).toEqual({ Authorization: "Bearer synthetic-access-1" });
        return new Promise<Response>((resolve) => { logoutComplete = resolve; });
      }
      throw new Error("Unexpected request in sign-out test");
    });
    vi.stubGlobal("fetch", fetchMock);
    const { client } = renderHome();
    await signIn();
    await ask();
    client.setQueryData(["old-auth-query"], "Private test data");
    await flush(() => fireEvent.click(screen.getByRole("button", { name: "Sign out" })));
    expect(screen.getByRole("status")).toHaveTextContent("Signed out on this device. Confirming server sign-out");
    expect(screen.queryByTestId("workbench-shell")).not.toBeInTheDocument();
    expect(streamSignal?.aborted).toBe(true);
    expect(canceled).toHaveBeenCalledOnce();
    expect(client.getQueryCache().getAll()).toHaveLength(0);
    await signIn();
    expect(logoutSignal?.aborted).toBe(true);
    expect(screen.getByText("Pilot 2")).toBeInTheDocument();
    await flush(() => logoutComplete(new Response(JSON.stringify(fixtures.logout))));
    expect(screen.getByText("Pilot 2")).toBeInTheDocument();
    expect(screen.queryByText(/server confirmed this session was revoked/)).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });
});
