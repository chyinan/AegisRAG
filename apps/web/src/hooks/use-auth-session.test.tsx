import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthSession } from "@/lib/auth";
import fixtures from "@/test/fixtures/backend-workbench.json";
import { useAuthSession } from "./use-auth-session";

function session(user = "user-old", expiresAt = Date.now() + 60_000): AuthSession {
  return Object.freeze({
    mode: "bearer", userId: user, tenantId: "tenant-pilot", label: user,
    roles: ["reader"], permissions: ["retrieval:query"],
    bearerToken: `synthetic-${user}-access`, expiresAt
  });
}

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return { client, ...renderHook(useAuthSession, { wrapper }) };
}

async function flush(action: () => void) {
  await act(() => { action(); return Promise.resolve(); });
}

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("in-memory auth session ownership", () => {
  it("expires at the server-derived deadline and immediately clears/cancels authenticated query data", async () => {
    vi.useFakeTimers();
    const { result, client } = setup();
    act(() => result.current.authenticate(session("user-old", Date.now() + 1000)));
    client.setQueryData(["documents-review", "old-scope"], { confidential: "synthetic-data" });
    let querySignal: AbortSignal | undefined;
    const pending = client.fetchQuery({ queryKey: ["pending-auth-read"], queryFn: ({ signal }) => {
      querySignal = signal;
      return new Promise<unknown>(() => undefined);
    } }).catch(() => undefined);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(result.current.auth).toBeNull();
    expect(result.current.notice).toBe("expired");
    expect(client.getQueryCache().getAll()).toHaveLength(0);
    expect(querySignal?.aborted).toBe(true);
    expect(fetchMock).not.toHaveBeenCalled();
    await pending;
  });

  it.each(["focus", "pageshow", "visibilitychange"] as const)("checks overdue sessions on %s after suspended timers", (event) => {
    vi.useFakeTimers();
    const { result } = setup();
    const start = Date.now();
    act(() => result.current.authenticate(session("user-old", start + 1000)));
    vi.setSystemTime(start + 5000);
    act(() => {
      if (event === "visibilitychange") document.dispatchEvent(new Event(event));
      else window.dispatchEvent(new Event(event));
    });
    expect(result.current.auth).toBeNull();
    expect(result.current.notice).toBe("expired");
  });

  it("does not overflow long expiry timers and rejects already elapsed sessions before rendering", async () => {
    vi.useFakeTimers();
    const { result } = setup();
    const start = Date.now();
    act(() => result.current.authenticate(session("long-lived", start + 3_000_000_000)));
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(result.current.auth?.userId).toBe("long-lived");
    act(() => result.current.authenticate(session("elapsed", start - 1)));
    expect(result.current.auth).toBeNull();
    expect(result.current.notice).toBe("expired");
  });

  it("clears auth/cache immediately, makes one logout for repeated clicks and later confirms revocation", async () => {
    let complete!: (response: Response) => void;
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => { complete = resolve; }));
    vi.stubGlobal("fetch", fetchMock);
    const { result, client } = setup();
    act(() => result.current.authenticate(session()));
    client.setQueryData(["documents-review", "old-scope"], "synthetic-data");
    act(() => { result.current.signOut(); result.current.signOut(); result.current.signOut(); });
    expect(result.current.auth).toBeNull();
    expect(result.current.notice).toBe("signout_pending");
    expect(client.getQueryCache().getAll()).toHaveLength(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await flush(() => complete(new Response(JSON.stringify(fixtures.logout))));
    expect(result.current.notice).toBe("signout_confirmed");
  });

  it("shows local-only sign-out on network failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("private network detail")));
    const { result } = setup();
    act(() => result.current.authenticate(session()));
    await flush(() => result.current.signOut());
    expect(result.current.auth).toBeNull();
    expect(result.current.notice).toBe("signout_local");
  });

  it("late old logout cannot change a newer login or its subsequent sign-out notice", async () => {
    const responses: Array<(response: Response) => void> = [];
    const signals: Array<AbortSignal | null | undefined> = [];
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      signals.push(init?.signal);
      return new Promise<Response>((resolve) => { responses.push(resolve); });
    }));
    const { result } = setup();
    act(() => result.current.authenticate(session()));
    act(() => result.current.signOut());
    const current = session("user-new");
    act(() => result.current.authenticate(current));
    expect(signals[0]?.aborted).toBe(true);
    await flush(() => responses[0](new Response(JSON.stringify(fixtures.logout))));
    expect(result.current.auth).toBe(current);
    expect(result.current.notice).toBeNull();
    act(() => result.current.signOut());
    expect(result.current.notice).toBe("signout_pending");
    await flush(() => responses[1](new Response(JSON.stringify({ ...fixtures.logout, data: { revoked: false } }))));
    expect(result.current.notice).toBe("signout_local");
  });

  it("removes old timers and aborts an in-flight logout when the page unmounts", async () => {
    vi.useFakeTimers();
    let signal: AbortSignal | null | undefined;
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      signal = init?.signal;
      return new Promise<Response>(() => undefined);
    }));
    const { result, unmount } = setup();
    act(() => result.current.authenticate(session()));
    act(() => result.current.signOut());
    unmount();
    await Promise.resolve();
    expect(signal?.aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(vi.getTimerCount()).toBe(0);
  });
});
