import { afterEach, describe, expect, it, vi } from "vitest";
import { PERSONAS } from "@/lib/auth";
import { apiResponse, documentStatus } from "@/test/document-fixtures";
import { authScopeKey } from "./auth-scope";
import { getDocumentVersionStatus, listDocumentReview, uploadDocument } from "./client";
import type { UploadDocumentInput } from "./types";

afterEach(() => vi.unstubAllGlobals());

describe("document API contracts", () => {
  it.each(["tenant", "private"] as const)("sends the explicit %s ACL without widening access", async (aclPreset) => {
    const fetchMock = vi.fn().mockResolvedValue(apiResponse({ document_id: "doc", version_id: "v1", job_id: "job", status: "uploaded" }));
    vi.stubGlobal("fetch", fetchMock);
    await uploadDocument(PERSONAS.platform_admin, { file: new File(["text"], "note.txt"), sourceType: "txt", aclPreset });
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect((init.body as FormData).get("acl")).toBe(JSON.stringify({ visibility: aclPreset }));
  });

  it("rejects unsupported department and unknown ACLs before sending a request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    for (const preset of ["department", "restricted", "bogus"]) {
      await expect(uploadDocument(PERSONAS.platform_admin, {
        file: new File(["text"], "note.txt"), sourceType: "txt", aclPreset: preset as UploadDocumentInput["aclPreset"]
      })).rejects.toMatchObject({ structured: { code: "UPLOAD_ACL_UNSUPPORTED" } });
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("uses the exact encoded document/version status route and forwards cancellation", async () => {
    const fetchMock = vi.fn().mockResolvedValue(apiResponse(documentStatus({ document_id: "doc/a", version_id: "v 1" })));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    const result = await getDocumentVersionStatus(PERSONAS.platform_admin, "doc/a", "v 1", controller.signal);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/backend/documents/doc%2Fa/versions/v%201/status");
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).signal).toBeInstanceOf(AbortSignal);
    expect(result.attempt_count).toBe(1);
  });

  it("refuses a mismatched status response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiResponse(documentStatus({ version_id: "different-version" }))));
    await expect(getDocumentVersionStatus(PERSONAS.platform_admin, "doc-upload-1", "version-upload-1"))
      .rejects.toMatchObject({ structured: { code: "DOCUMENT_STATUS_INVALID" } });
  });

  it("does not treat an invalid document list as an empty list", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiResponse(null)));
    await expect(listDocumentReview(PERSONAS.platform_admin)).rejects.toMatchObject({ structured: { code: "DOCUMENT_REVIEW_INVALID" } });
  });

  it("preserves HTTP status for safe transient-error retry classification", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiResponse(null, 503, { code: "SERVICE_UNAVAILABLE", message: "Unavailable" })));
    await expect(listDocumentReview(PERSONAS.platform_admin)).rejects.toMatchObject({ status: 503 });
  });

  it("scopes caches to all authorization inputs without storing bearer credentials in keys", () => {
    const session = { ...PERSONAS.platform_admin, mode: "bearer" as const, bearerToken: "secret-credential" };
    const key = authScopeKey(session);
    expect(authScopeKey(session)).toBe(key);
    expect(key).not.toContain("secret-credential");
    expect(authScopeKey({ ...session, bearerToken: "another-secret" })).not.toBe(key);
    expect(authScopeKey({ ...PERSONAS.platform_admin, permissions: [] })).not.toBe(authScopeKey(PERSONAS.platform_admin));
    expect(authScopeKey({ ...PERSONAS.platform_admin, department: "other" })).not.toBe(authScopeKey(PERSONAS.platform_admin));
  });
});


it("keeps the actual API envelope request ID on failed HTTP requests", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    request_id: "req-envelope", data: null,
    error: { code: "DOCUMENT_READ_FORBIDDEN", message: "Not allowed.", details: {} }
  }), { status: 403, headers: { "Content-Type": "application/json", "X-Trace-ID": "trace-header" } })));
  await expect(listDocumentReview(PERSONAS.platform_admin)).rejects.toMatchObject({
    status: 403, structured: { request_id: "req-envelope", trace_id: "trace-header" }
  });
});
