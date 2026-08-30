import { afterEach, describe, expect, it, vi } from "vitest";

import { loginUser } from "./auth";

describe("tenant-bound local login", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends the explicit tenant with local credentials", async () => {
    let requestBody: unknown = null;
    vi.stubGlobal(
      "fetch",
      vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
        if (typeof init?.body !== "string") throw new Error("expected JSON request body");
        requestBody = JSON.parse(init.body) as unknown;
        return Promise.resolve(new Response(
          JSON.stringify({
            data: {
              access_token: "access-token",
              refresh_token: "refresh-token",
              token_type: "bearer",
              expires_in: 3600,
              user_id: "user-a",
              display_name: "Alice",
              tenant_id: "tenant-a",
              roles: ["reader"],
              permissions: ["document:read"]
            }
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        ));
      })
    );

    const session = await loginUser({
      tenantId: "tenant-a",
      username: "alice",
      password: "TenantA-Secret1!"
    });

    expect(requestBody).toEqual({
      tenant_id: "tenant-a",
      username: "alice",
      password: "TenantA-Secret1!"
    });
    expect(session.tenantId).toBe("tenant-a");
  });

  it("does not retry structured client authentication failures", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            error: {
              code: "AUTH_INVALID_CREDENTIALS",
              message: "Invalid username or password."
            }
          }),
          { status: 401, headers: { "Content-Type": "application/json" } }
        )
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      loginUser({
        tenantId: "tenant-a",
        username: "alice",
        password: "wrong-password"
      })
    ).rejects.toThrow("Invalid username or password.");

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
