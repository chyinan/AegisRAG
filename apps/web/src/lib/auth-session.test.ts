import { afterEach, describe, expect, it, vi } from "vitest";
import fixtures from "@/test/fixtures/backend-workbench.json";
import { loginUser, logoutUser, LOGOUT_TIMEOUT_MS, type AuthSession } from "./auth";
import { readLoginResponse } from "./auth-contract";

const session: AuthSession = Object.freeze({
  mode: "bearer", label: "Pilot", tenantId: "tenant-pilot", userId: "user-pilot",
  roles: ["reader"], permissions: ["retrieval:query"],
  bearerToken: "synthetic-original-access", refreshToken: "synthetic-original-refresh"
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("login expiry contract", () => {
  it.each([0, -1, NaN, Infinity, -Infinity, "3600", null])("rejects invalid expires_in %s", (expiresIn) => {
    expect(readLoginResponse({ ...fixtures.login, data: { ...fixtures.login.data, expires_in: expiresIn } })).toBeNull();
  });

  it("anchors expiry to the successful request start and freezes the in-memory session", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-14T18:00:00Z"));
    const start = Date.now();
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => {
      setTimeout(() => resolve(jsonResponse(fixtures.login)), 2000);
    }));
    vi.stubGlobal("fetch", fetchMock);
    const result = loginUser({ tenantId: "tenant-pilot", username: "pilot", password: "synthetic-password" });
    await vi.advanceTimersByTimeAsync(2000);
    const auth = await result;
    expect(auth.expiresAt).toBe(start + fixtures.login.data.expires_in * 1000);
    expect(auth.expiresAt).not.toBe(Date.now() + fixtures.login.data.expires_in * 1000);
    expect(Object.isFrozen(auth)).toBe(true);
    expect(Object.isFrozen(auth.roles)).toBe(true);
    expect(Object.isFrozen(auth.permissions)).toBe(true);
    expect(auth.roles).toEqual(fixtures.login.data.roles);
    expect(auth.permissions).toEqual(fixtures.login.data.permissions);
  });

  it.each([0, Number.MAX_VALUE])("does not retry invalid expiry contracts (%s)", async (expiresIn) => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      ...fixtures.login, data: { ...fixtures.login.data, expires_in: expiresIn }
    }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(loginUser({ tenantId: "tenant-pilot", username: "pilot", password: "synthetic-password" })).rejects.toThrow(/invalid/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("bounded server sign-out", () => {
  it("confirms only explicit backend revocation and sends the original bearer once", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(fixtures.logout));
    vi.stubGlobal("fetch", fetchMock);
    expect(await logoutUser(session)).toBe("confirmed");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/logout", expect.objectContaining({
      method: "POST", headers: { Authorization: "Bearer synthetic-original-access" }
    }));
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.body).toBeUndefined();
    expect(init.signal?.aborted).toBe(true);
  });

  it.each([
    { ...fixtures.logout, data: { revoked: false } },
    { ...fixtures.logout, data: { revoked: "true" } },
    { ...fixtures.logout, error: { code: "FAILED", message: "Denied" } },
    { data: { revoked: true } },
    { ...fixtures.logout, request_id: "" },
    null,
    []
  ])("does not claim revocation for invalid/failed response %#", async (body) => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(body));
    vi.stubGlobal("fetch", fetchMock);
    expect(await logoutUser(session)).toBe("unconfirmed");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([401, 403, 500, 503])("does not retry or claim revocation after HTTP %s", async (status) => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(fixtures.logout, status));
    vi.stubGlobal("fetch", fetchMock);
    expect(await logoutUser(session)).toBe("unconfirmed");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("reports local-only completion after a network failure without exposing the exception", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error("untrusted transport detail"));
    vi.stubGlobal("fetch", fetchMock);
    expect(await logoutUser(session)).toBe("unconfirmed");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(["headers", "body"] as const)("bounds a stalled response %s through validation", async (stage) => {
    vi.useFakeTimers();
    let signal: AbortSignal | null | undefined;
    const response = jsonResponse(fixtures.logout);
    if (stage === "body") vi.spyOn(response, "json").mockImplementation(() => new Promise<unknown>(() => undefined));
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      signal = init?.signal;
      return stage === "headers" ? new Promise<Response>(() => undefined) : Promise.resolve(response);
    });
    vi.stubGlobal("fetch", fetchMock);
    const result = logoutUser(session);
    await vi.advanceTimersByTimeAsync(LOGOUT_TIMEOUT_MS);
    expect(await result).toBe("unconfirmed");
    expect(signal?.aborted).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not send an already canceled logout or a dev-header identity", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    controller.abort();
    expect(await logoutUser(session, controller.signal)).toBe("unconfirmed");
    expect(await logoutUser({ ...session, mode: "dev_headers" })).toBe("unconfirmed");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
