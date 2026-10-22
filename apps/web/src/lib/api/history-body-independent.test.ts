import { afterEach, describe, expect, it, vi } from "vitest";
import { PERSONAS } from "@/lib/auth";
import { ApiClientError, loadChatHistory, parseEnvelope } from "./client";

const id = "session-independent-body";

function bodyFailure(error: Error, status = 200) {
  const response = new Response(JSON.stringify({
    request_id: "req-independent-body", error: null, data: { session_id: id, messages: [] }
  }), { status });
  vi.spyOn(response, "json").mockRejectedValueOnce(error);
  return response;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("independent history-only body read recovery boundary", () => {
  it("preserves a successful history response body TypeError as the exact transport error", async () => {
    const error = new TypeError("Successful response connection terminated");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(bodyFailure(error)));
    await expect(loadChatHistory(PERSONAS.employee, id)).rejects.toBe(error);
  });

  it("keeps the public parser default contract unchanged for a successful body TypeError", async () => {
    const error = new TypeError("Generic body connection terminated");
    const rejection = parseEnvelope(bodyFailure(error));
    await expect(rejection).rejects.toBeInstanceOf(ApiClientError);
    await expect(rejection).rejects.toMatchObject({ status: 200, structured: { code: "INVALID_RESPONSE" } });
  });

  it.each([400, 404, 422])("preserves nonretryable HTTP %s classification when its body fails with TypeError", async (status) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(bodyFailure(new TypeError("Error body connection terminated"), status)));
    await expect(loadChatHistory(PERSONAS.employee, id)).rejects.toMatchObject({
      name: "ApiClientError", status, structured: { code: `HTTP_${status}` }
    });
  });

  it.each([408, 429, 500, 503])("preserves retryable HTTP %s classification when its body fails with TypeError", async (status) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(bodyFailure(new TypeError("Error body connection terminated"), status)));
    await expect(loadChatHistory(PERSONAS.employee, id)).rejects.toMatchObject({
      name: "ApiClientError", status, structured: { code: `HTTP_${status}` }
    });
  });

  it("does not relabel invalid JSON as transient transport failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(bodyFailure(new SyntaxError("Invalid JSON"))));
    await expect(loadChatHistory(PERSONAS.employee, id)).rejects.toMatchObject({
      name: "ApiClientError", status: 200, structured: { code: "INVALID_RESPONSE" }
    });
  });
});
