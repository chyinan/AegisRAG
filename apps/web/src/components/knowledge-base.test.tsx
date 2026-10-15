import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PERSONAS } from "@/lib/auth";
import { apiResponse, documentStatus, uploadedDocument } from "@/test/document-fixtures";
import { KnowledgeBasePanel, QuickImportDrawerContent } from "./knowledge-base";
import { UploadStatusCard } from "./upload-status";

const clients: QueryClient[] = [];
function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  clients.push(client);
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}
function requestUrl(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
}
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.unstubAllGlobals();
});

describe("upload and indexing journey", () => {
  it("disables Department explicitly rather than changing its scope", () => {
    render(<QuickImportDrawerContent auth={PERSONAS.platform_admin} language="en" />, { wrapper: wrapper() });
    expect(screen.getByRole("option", { name: "Department (unavailable)" })).toBeDisabled();
    expect(screen.getByText(/access is never broadened automatically/i)).toBeInTheDocument();
  });

  it("distinguishes a pending list, a true empty result, and a fetch failure", async () => {
    let resolveList: (response: Response) => void = () => undefined;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { resolveList = resolve; })));
    const view = render(<KnowledgeBasePanel auth={PERSONAS.platform_admin} language="en" />, { wrapper: wrapper() });
    expect(screen.getByText("Loading documents…")).toBeInTheDocument();
    expect(screen.queryByText("No documents are visible in the current authorized scope.")).not.toBeInTheDocument();
    resolveList(apiResponse({ items: [], limit: 25, next_cursor: null, request_id: "request-1", trace_id: "trace-1" }));
    expect(await screen.findByText("No documents are visible in the current authorized scope.")).toBeInTheDocument();
    view.unmount();

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiResponse(null, 403, { code: "FORBIDDEN", message: "Denied" })));
    render(<KnowledgeBasePanel auth={PERSONAS.platform_admin} language="en" />, { wrapper: wrapper() });
    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to load the document review list.");
    expect(screen.queryByText("No documents are visible in the current authorized scope.")).not.toBeInTheDocument();
    expect(screen.queryByText("Loading documents…")).not.toBeInTheDocument();
  });

  it.each([{ permissions: [] }, { permissions: ["document:read"] }])("requires management permission for the review list (%j)", ({ permissions }) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<KnowledgeBasePanel auth={{ ...PERSONAS.employee, permissions }} language="en" />, { wrapper: wrapper() });
    expect(screen.getByText(/document:manage/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeDisabled();
    expect(screen.queryByText("No documents are visible in the current authorized scope.")).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("allows the review list with document:manage even without document:read", async () => {
    const fetchMock = vi.fn().mockResolvedValue(apiResponse({ items: [], limit: 25, next_cursor: null, request_id: "request-list", trace_id: "trace-list" }));
    vi.stubGlobal("fetch", fetchMock);
    render(<KnowledgeBasePanel auth={{ ...PERSONAS.employee, permissions: ["document:manage"] }} language="en" />, { wrapper: wrapper() });
    expect(await screen.findByText("No documents are visible in the current authorized scope.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(["knowledge", "quick"] as const)("refreshes the list and tracks the exact returned version after the %s upload", async (entrypoint) => {
    const user = userEvent.setup();
    let accepted = false;
    let reviewReads = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url.endsWith("/upload")) {
        accepted = true;
        return Promise.resolve(apiResponse(uploadedDocument));
      }
      if (url.endsWith("/status")) return Promise.resolve(apiResponse(documentStatus({ status: "retrieval_ready", chunk_count: 3, vector_count: 3 })));
      reviewReads += 1;
      return Promise.resolve(apiResponse({
        items: accepted ? [{
          document_id: uploadedDocument.document_id,
          version_id: uploadedDocument.version_id,
          source_display_name: "Uploaded policy",
          source_type: "markdown",
          status: "retrieval_ready",
          created_by: "demo-user-platform-admin",
          chunk_count: 3,
          request_id: "request-list",
          trace_id: "trace-list"
        }] : [], limit: 25, next_cursor: null, request_id: "request-list", trace_id: "trace-list"
      }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<>
      <KnowledgeBasePanel auth={PERSONAS.platform_admin} language="en" />
      {entrypoint === "quick" && <QuickImportDrawerContent auth={PERSONAS.platform_admin} language="en" />}
    </>, { wrapper: wrapper() });
    await screen.findByText("No documents are visible in the current authorized scope.");
    const buttons = screen.getAllByRole("button", { name: "Upload and create job" });
    const button = entrypoint === "quick" ? buttons[1] : buttons[0];
    const form = button.closest("form");
    if (form === null) throw new Error("Upload form not found");
    await user.upload(within(form).getByLabelText(/File/), new File(["# Policy"], "policy.md", { type: "text/markdown" }));
    await user.click(button);
    expect(await screen.findByText("Uploaded policy")).toBeInTheDocument();
    expect(await screen.findByText("Ready for retrieval")).toBeInTheDocument();
    expect(screen.getByText(`document_id: ${uploadedDocument.document_id}`)).toBeInTheDocument();
    expect(screen.getByText(`version_id: ${uploadedDocument.version_id}`)).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => requestUrl(input).endsWith(`/documents/${uploadedDocument.document_id}/versions/${uploadedDocument.version_id}/status`))).toBe(true);
    expect(reviewReads).toBeGreaterThanOrEqual(2);
  });

  it("guards rapid repeated submits and discards an upload completed after identity change", async () => {
    const user = userEvent.setup();
    let resolveUpload: (response: Response) => void = () => undefined;
    let signal: AbortSignal | null | undefined;
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      signal = init?.signal;
      return new Promise<Response>((resolve) => { resolveUpload = resolve; });
    });
    vi.stubGlobal("fetch", fetchMock);
    const onUploaded = vi.fn();
    const view = render(<QuickImportDrawerContent auth={PERSONAS.platform_admin} onUploaded={onUploaded} language="en" />, { wrapper: wrapper() });
    await user.upload(screen.getByLabelText(/File/), new File(["# Policy"], "policy.md", { type: "text/markdown" }));
    const form = screen.getByRole("button", { name: "Upload and create job" }).closest("form");
    if (form === null) throw new Error("Upload form not found");
    fireEvent.submit(form);
    fireEvent.submit(form);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    view.rerender(<QuickImportDrawerContent auth={{ ...PERSONAS.platform_admin, userId: "different-user" }} onUploaded={onUploaded} language="en" />);
    expect(signal?.aborted).toBe(true);
    await act(async () => { resolveUpload(apiResponse(uploadedDocument)); await Promise.resolve(); });
    expect(onUploaded).not.toHaveBeenCalled();
    expect(screen.queryByText(`document_id: ${uploadedDocument.document_id}`)).not.toBeInTheDocument();
  });

  it("renders retry attempts and the backend safe error summary without raw details", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiResponse(documentStatus({
      status: "failed_retryable", attempt_count: 2, next_retry_at: "2026-10-14T16:05:00Z", error_code: "EMBEDDING_RATE_LIMITED",
      error_summary: { error_code: "EMBEDDING_RATE_LIMITED", message: "private provider body", raw_output: "private raw output" }
    }))));
    render(<UploadStatusCard auth={PERSONAS.platform_admin} result={uploadedDocument} language="en" />, { wrapper: wrapper() });
    expect(await screen.findByText("Retryable failure")).toBeInTheDocument();
    expect(screen.getByText("Attempts: 2")).toBeInTheDocument();
    expect(screen.getByText("2026-10-14T16:05:00Z")).toBeInTheDocument();
    expect(screen.getByText("error_code: EMBEDDING_RATE_LIMITED")).toBeInTheDocument();
    expect(screen.getByText("Safe error summary: EMBEDDING_RATE_LIMITED")).toBeInTheDocument();
    expect(screen.queryByText(/private provider|private raw/)).not.toBeInTheDocument();
  });

  it("keeps accepted upload IDs visible when status access is unavailable", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<UploadStatusCard auth={PERSONAS.employee} result={uploadedDocument} language="en" />, { wrapper: wrapper() });
    expect(screen.getByText(/Live indexing status requires document:manage/)).toBeInTheDocument();
    expect(screen.getByText(`document_id: ${uploadedDocument.document_id}`)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
