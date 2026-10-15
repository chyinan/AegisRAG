import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/lib/api/client";
import { PERSONAS } from "@/lib/auth";
import { apiResponse, documentStatus, uploadedDocument } from "@/test/document-fixtures";
import { documentStatusPollDelay, shouldRetryStatusFetch, useDocumentStatus } from "./use-document-status";

const clients: QueryClient[] = [];
function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { gcTime: 0 } } });
  clients.push(client);
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}
async function advance(ms = 1) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}
beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("document indexing polling", () => {
  it("continues retryable lifecycle failures but stops terminal states", () => {
    expect(documentStatusPollDelay(documentStatus({ status: "failed_retryable" }))).toBe(2_000);
    expect(documentStatusPollDelay(documentStatus({ status: "failed_retryable", next_retry_at: "2026-10-14T16:01:00Z" }), Date.parse("2026-10-14T16:00:00Z"))).toBe(30_000);
    for (const status of ["retrieval_ready", "failed_terminal", "deleted", "unknown"]) {
      expect(documentStatusPollDelay(documentStatus({ status }))).toBe(false);
    }
    expect(documentStatusPollDelay(documentStatus({ deleted_at: "2026-10-14T16:00:00Z" }))).toBe(false);
  });

  it("bounds retries to transient network and HTTP failures", () => {
    expect(shouldRetryStatusFetch(0, new TypeError("network failed"))).toBe(true);
    for (const status of [408, 429, 500, 503]) {
      expect(shouldRetryStatusFetch(0, new ApiClientError({ code: "UNAVAILABLE", message: "safe" }, status))).toBe(true);
    }
    for (const status of [400, 401, 403, 404, 422]) {
      expect(shouldRetryStatusFetch(0, new ApiClientError({ code: "DENIED", message: "safe" }, status))).toBe(false);
    }
    expect(shouldRetryStatusFetch(3, new TypeError("network failed"))).toBe(false);
    expect(shouldRetryStatusFetch(0, new DOMException("aborted", "AbortError"))).toBe(false);
  });

  it("follows parsing, retryable failure, and retrieval readiness, then stops", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(apiResponse(documentStatus()))
      .mockResolvedValueOnce(apiResponse(documentStatus({ status: "failed_retryable", attempt_count: 2, error_code: "EMBEDDING_RATE_LIMITED", error_summary: { error_code: "EMBEDDING_RATE_LIMITED" } })))
      .mockResolvedValue(apiResponse(documentStatus({ status: "retrieval_ready", chunk_count: 4, vector_count: 4 })));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useDocumentStatus(PERSONAS.platform_admin, uploadedDocument), { wrapper: wrapper() });
    await advance();
    expect(result.current.data?.status).toBe("parsing");
    await advance(2_000);
    expect(result.current.data?.status).toBe("failed_retryable");
    await advance(2_000);
    expect(result.current.data?.status).toBe("retrieval_ready");
    await advance(60_000);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it.each(["failed_terminal", "deleted"])("stops polling a backend %s result", async (status) => {
    const fetchMock = vi.fn().mockResolvedValue(apiResponse(documentStatus({
      status, attempt_count: 3, error_code: "INGESTION_UNSUPPORTED_FORMAT", error_summary: { error_code: "INGESTION_UNSUPPORTED_FORMAT" }
    })));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useDocumentStatus(PERSONAS.platform_admin, uploadedDocument), { wrapper: wrapper() });
    await advance(60_000);
    expect(result.current.data?.status).toBe(status);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("recovers from a transient fetch error without restarting ingestion", async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("network failed"))
      .mockResolvedValue(apiResponse(documentStatus({ status: "retrieval_ready" })));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useDocumentStatus(PERSONAS.platform_admin, uploadedDocument), { wrapper: wrapper() });
    await advance(1_010);
    expect(result.current.data?.status).toBe("retrieval_ready");
    expect(result.current.isError).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.every((call) => (call[1] as RequestInit).method === "GET")).toBe(true);
  });

  it("backs off transient status-fetch errors and stops after the retry budget", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network failed"));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useDocumentStatus(PERSONAS.platform_admin, uploadedDocument), { wrapper: wrapper() });
    await advance();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await advance(1_000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await advance(2_000);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    await advance(4_000);
    expect(fetchMock).toHaveBeenCalledTimes(4);
    await advance(60_000);
    expect(result.current.isError).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("times out a stalled status request and retries the read safely", async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
        const signal = init?.signal;
        signal?.addEventListener("abort", () => reject(new DOMException("Timed out", "TimeoutError")), { once: true });
      }))
      .mockResolvedValue(apiResponse(documentStatus({ status: "retrieval_ready" })));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useDocumentStatus(PERSONAS.platform_admin, uploadedDocument), { wrapper: wrapper() });
    await advance(14_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(result.current.isFetching).toBe(true);
    await advance(2_010);
    expect(result.current.data?.status).toBe("retrieval_ready");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("allows an explicit refresh after the transient retry budget is exhausted", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network failed"));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useDocumentStatus(PERSONAS.platform_admin, uploadedDocument), { wrapper: wrapper() });
    await advance(8_000);
    expect(result.current.isError).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(4);
    fetchMock.mockResolvedValue(apiResponse(documentStatus({ status: "retrieval_ready" })));
    await act(async () => { await result.current.refetch(); });
    await advance();
    expect(result.current.isError).toBe(false);
    expect(result.current.data?.status).toBe("retrieval_ready");
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  it("does not retry a denied status fetch", async () => {
    const fetchMock = vi.fn().mockResolvedValue(apiResponse(null, 403, { code: "DOCUMENT_MANAGE_FORBIDDEN", message: "Denied" }));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useDocumentStatus(PERSONAS.platform_admin, uploadedDocument), { wrapper: wrapper() });
    await advance(60_000);
    expect(result.current.isError).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("never polls without the backend's document:manage permission", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useDocumentStatus(PERSONAS.employee, uploadedDocument), { wrapper: wrapper() });
    await advance(60_000);
    expect(result.current.canReadStatus).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("aborts an in-flight request on unmount and discards a late response", async () => {
    let signal: AbortSignal | null | undefined;
    let resolveRequest: (response: Response) => void = () => undefined;
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      signal = init?.signal;
      return new Promise<Response>((resolve) => { resolveRequest = resolve; });
    });
    vi.stubGlobal("fetch", fetchMock);
    const { unmount } = renderHook(() => useDocumentStatus(PERSONAS.platform_admin, uploadedDocument), { wrapper: wrapper() });
    await advance();
    unmount();
    expect(signal?.aborted).toBe(true);
    resolveRequest(apiResponse(documentStatus()));
    await advance(60_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("cannot show a stale response after the identity changes", async () => {
    let resolveOld: (response: Response) => void = () => undefined;
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveOld = resolve; }))
      .mockResolvedValue(apiResponse(documentStatus({ status: "retrieval_ready", request_id: "new-identity-request" })));
    vi.stubGlobal("fetch", fetchMock);
    const { result, rerender } = renderHook(({ auth }) => useDocumentStatus(auth, uploadedDocument), {
      wrapper: wrapper(), initialProps: { auth: PERSONAS.platform_admin }
    });
    await advance();
    rerender({ auth: { ...PERSONAS.platform_admin, userId: "other-user" } });
    await advance();
    expect(result.current.data?.request_id).toBe("new-identity-request");
    resolveOld(apiResponse(documentStatus({ request_id: "old-identity-request" })));
    await advance();
    expect(result.current.data?.request_id).toBe("new-identity-request");
  });
});
