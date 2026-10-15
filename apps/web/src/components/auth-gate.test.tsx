import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthGate } from "./auth-gate";

describe("AuthGate tenant login", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("requires a tenant before submitting local credentials", async () => {
    const onAuthenticated = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(new Response(
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
        ))
      )
    );
    render(
      <AuthGate
        language="en"
        onLanguageChange={vi.fn()}
        onAuthenticated={onAuthenticated}
      />
    );

    const submit = screen.getByRole("button", { name: /sign in/i });
    await userEvent.type(screen.getByLabelText("Username"), "alice");
    await userEvent.type(screen.getByLabelText("Password"), "TenantA-Secret1!");
    expect(submit).toBeDisabled();

    await userEvent.type(screen.getByLabelText("Tenant ID"), "tenant-a");
    await userEvent.click(submit);

    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledTimes(1));
    expect(onAuthenticated.mock.calls[0]?.[0]).toMatchObject({ tenantId: "tenant-a" });
  });
});


it("does not offer a pasted-token flow without backend identity hydration", () => {
  render(<AuthGate language="en" onLanguageChange={vi.fn()} onAuthenticated={vi.fn()} />);
  expect(screen.queryByLabelText("Bearer token")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Continue with JWT" })).not.toBeInTheDocument();
  expect(screen.getByText(/pasted JWT sign-in is unavailable/)).toBeInTheDocument();
});
