import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { authScopeKey } from "@/lib/api/auth-scope";
import { PERSONAS, type AuthSession } from "@/lib/auth";
import { apiResponse, documentStatus, uploadedDocument } from "@/test/document-fixtures";
import { KnowledgeBasePanel } from "./knowledge-base";
import { UploadStatusCard } from "./upload-status";

const clients: QueryClient[] = [];
const auth = PERSONAS.platform_admin;
const documentId = uploadedDocument.document_id;
const statusKey = (session: AuthSession, versionId = uploadedDocument.version_id, id = documentId) =>
  ["document-version-status", authScopeKey(session), id, versionId];
const list = {
  items: [{ document_id: documentId, version_id: uploadedDocument.version_id, source_display_name: "Uploaded policy", status: "retrieval_ready" }],
  limit: 25, next_cursor: null, request_id: "request-list", trace_id: "trace-list"
};
const deleted = {
  document_id: documentId, status: "deleted", deleted_versions: 3, deleted_chunks: 3, deleted_vectors: 3,
  request_id: "request-delete", trace_id: "trace-delete"
};

function createClient() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
  clients.push(client);
  return client;
}

function seedReady(client: QueryClient, session: AuthSession, versionId = uploadedDocument.version_id, id = documentId) {
  client.setQueryData(statusKey(session, versionId, id), documentStatus({ document_id: id, version_id: versionId, status: "retrieval_ready" }));
}

function requestUrl(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
}

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("document deletion status cache contract", () => {
  it("refreshes all mounted versions and shared cards, invalidates inactive versions, and isolates document/auth scopes", async () => {
    const client = createClient();
    const otherAuth = { ...auth, userId: "another-user" };
    const otherTenant = { ...auth, tenantId: "another-tenant" };
    const bearerAuth: AuthSession = { ...auth, mode: "bearer", bearerToken: "synthetic-token" };
    const replacedBearerAuth = { ...bearerAuth };
    for (const version of [uploadedDocument.version_id, "previous-version", "inactive-version"]) seedReady(client, auth, version);
    for (const session of [otherAuth, otherTenant, bearerAuth, replacedBearerAuth]) seedReady(client, session);
    seedReady(client, auth, uploadedDocument.version_id, `${documentId}-other`);
    let isDeleted = false;
    const statusReads: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (init?.method === "DELETE") {
        isDeleted = true;
        return Promise.resolve(apiResponse(deleted));
      }
      if (url.endsWith("/status")) {
        statusReads.push(url);
        const versionId = url.split("/versions/")[1].split("/")[0];
        return Promise.resolve(apiResponse(documentStatus({ version_id: versionId, status: "deleted", deleted_at: "2026-10-15T02:00:00Z" })));
      }
      return Promise.resolve(apiResponse(isDeleted ? { ...list, items: [] } : list));
    }));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const contents = (showInactive = false) => <QueryClientProvider client={client}>
      <KnowledgeBasePanel auth={auth} language="en" />
      {/* The top-level and drawer cards share the same version query. */}
      <UploadStatusCard auth={auth} result={uploadedDocument} language="en" />
      <UploadStatusCard auth={auth} result={uploadedDocument} language="en" />
      <UploadStatusCard auth={auth} result={{ ...uploadedDocument, version_id: "previous-version" }} language="en" />
      <UploadStatusCard auth={auth} result={{ ...uploadedDocument, document_id: `${documentId}-other` }} language="en" />
      {showInactive && <UploadStatusCard auth={auth} result={{ ...uploadedDocument, version_id: "inactive-version" }} language="en" />}
    </QueryClientProvider>;
    const view = render(contents());
    await screen.findByRole("button", { name: "Delete document: Uploaded policy" }, { timeout: 5_000 });
    expect(screen.getAllByText("Ready for retrieval")).toHaveLength(4);
    expect(statusReads).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Delete document: Uploaded policy" }));
    await waitFor(() => expect(screen.getAllByText("Deleted", { exact: true })).toHaveLength(3));
    expect(await screen.findByText("No documents are visible in the current authorized scope.")).toBeInTheDocument();
    expect(statusReads).toHaveLength(2);
    expect(client.getQueryState(statusKey(auth, "inactive-version"))?.isInvalidated).toBe(true);
    for (const session of [otherAuth, otherTenant, bearerAuth, replacedBearerAuth]) {
      expect(client.getQueryState(statusKey(session))?.isInvalidated).toBe(false);
      expect(client.getQueryData(statusKey(session))).toMatchObject({ status: "retrieval_ready" });
    }
    expect(client.getQueryState(statusKey(auth, uploadedDocument.version_id, `${documentId}-other`))?.isInvalidated).toBe(false);
    expect(screen.getAllByText("Ready for retrieval")).toHaveLength(1);

    // A terminal cache invalidated while inactive must be rechecked on remount.
    view.rerender(contents(true));
    await waitFor(() => expect(screen.getAllByText("Deleted", { exact: true })).toHaveLength(4));
    expect(statusReads).toHaveLength(3);
    expect(client.getQueryState(statusKey(auth, "inactive-version"))?.isInvalidated).toBe(false);
  });

  it("does not invalidate a ready card when deletion fails", async () => {
    const client = createClient();
    seedReady(client, auth);
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => Promise.resolve(init?.method === "DELETE"
      ? apiResponse(null, 500, { code: "DOCUMENT_DELETE_FAILED", message: "safe" })
      : apiResponse(list)));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<QueryClientProvider client={client}>
      <KnowledgeBasePanel auth={auth} language="en" />
      <UploadStatusCard auth={auth} result={uploadedDocument} language="en" />
    </QueryClientProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "Delete document: Uploaded policy" }, { timeout: 5_000 }));
    await screen.findByRole("alert");
    expect(screen.getByText("Ready for retrieval")).toBeInTheDocument();
    expect(client.getQueryState(statusKey(auth))?.isInvalidated).toBe(false);
    expect(fetchMock.mock.calls.some(([input]) => requestUrl(input).endsWith("/status"))).toBe(false);
  });

  it.each([true, false])("cancels an older read so its late response cannot overwrite deletion (cached: %s)", async (hasCachedStatus) => {
    const client = createClient();
    if (hasCachedStatus) seedReady(client, auth);
    let resolveOldRead: (response: Response) => void = () => undefined;
    let oldSignal: AbortSignal | null | undefined;
    let statusReads = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "DELETE") return Promise.resolve(apiResponse(deleted));
      if (requestUrl(input).endsWith("/status")) {
        statusReads += 1;
        if (statusReads === 1) {
          oldSignal = init?.signal;
          return new Promise<Response>((resolve) => { resolveOldRead = resolve; });
        }
        return Promise.resolve(apiResponse(documentStatus({ status: "deleted", deleted_at: "2026-10-15T02:00:00Z" })));
      }
      return Promise.resolve(apiResponse(list));
    }));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<QueryClientProvider client={client}>
      <KnowledgeBasePanel auth={auth} language="en" />
      <UploadStatusCard auth={auth} result={uploadedDocument} language="en" />
    </QueryClientProvider>);
    await screen.findByRole("button", { name: "Delete document: Uploaded policy" }, { timeout: 5_000 });
    const oldRead = hasCachedStatus ? client.refetchQueries({ queryKey: statusKey(auth), exact: true }) : Promise.resolve();
    await waitFor(() => expect(statusReads).toBe(1));
    fireEvent.click(screen.getByRole("button", { name: "Delete document: Uploaded policy" }));
    expect(await screen.findByText("Deleted", { exact: true })).toBeInTheDocument();
    expect(oldSignal?.aborted).toBe(true);
    await act(async () => {
      resolveOldRead(apiResponse(documentStatus({ status: "retrieval_ready" })));
      await oldRead;
    });
    expect(screen.queryByText("Ready for retrieval")).not.toBeInTheDocument();
    expect(client.getQueryData(statusKey(auth))).toMatchObject({ status: "deleted" });
    expect(statusReads).toBe(2);
  });

  it("keeps a late delete completion in its original bearer scope after identity replacement", async () => {
    const client = createClient();
    const oldAuth: AuthSession = { ...auth, mode: "bearer", bearerToken: "synthetic-old-token" };
    const newAuth = { ...oldAuth, bearerToken: "synthetic-new-token" };
    seedReady(client, oldAuth);
    seedReady(client, newAuth);
    let resolveDelete: (response: Response) => void = () => undefined;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => init?.method === "DELETE"
      ? new Promise<Response>((resolve) => { resolveDelete = resolve; })
      : Promise.resolve(apiResponse(requestUrl(input).endsWith("/status") ? documentStatus({ status: "retrieval_ready" }) : list)));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const contents = (session: AuthSession) => <QueryClientProvider client={client}>
      <KnowledgeBasePanel auth={session} language="en" />
      <UploadStatusCard auth={session} result={uploadedDocument} language="en" />
    </QueryClientProvider>;
    const view = render(contents(oldAuth));
    fireEvent.click(await screen.findByRole("button", { name: "Delete document: Uploaded policy" }, { timeout: 5_000 }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(true));
    view.rerender(contents(newAuth));
    await screen.findByRole("button", { name: "Delete document: Uploaded policy" }, { timeout: 5_000 });
    await waitFor(() => expect(client.getQueryState(statusKey(newAuth))?.fetchStatus).toBe("idle"));
    const statusReadsBeforeCompletion = fetchMock.mock.calls.filter(([input]) => requestUrl(input).endsWith("/status")).length;
    await act(async () => { resolveDelete(apiResponse(deleted)); await Promise.resolve(); });
    await waitFor(() => expect(client.getQueryState(statusKey(oldAuth))?.isInvalidated).toBe(true));
    expect(client.getQueryState(statusKey(newAuth))?.isInvalidated).toBe(false);
    expect(screen.getByText("Ready for retrieval")).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([input]) => requestUrl(input).endsWith("/status"))).toHaveLength(statusReadsBeforeCompletion);
  });
});
