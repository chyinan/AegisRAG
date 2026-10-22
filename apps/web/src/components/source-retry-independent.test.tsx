import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EvidencePanel } from "@/components/evidence-panel";
import { authHeaders, PERSONAS } from "@/lib/auth";
import type { Citation } from "@/lib/api/types";
import fixtures from "@/test/fixtures/backend-workbench.json";

const citation: Citation = { document_id: "doc-pilot", version_id: "version-pilot", chunk_id: "chunk-pilot" };
const nextCitation: Citation = { ...citation, chunk_id: "chunk-next" };
const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { "Content-Type": "application/json" }
});
const success = (source = citation, excerpt = "Current excerpt") => response({
  ...fixtures.source,
  data: { ...fixtures.source.data, ...source, text_excerpt: excerpt }
});
const failed = (status: number, code = "UPSTREAM_FAILURE") => response({
  request_id: "req-independent-source", data: null,
  error: { code, message: "Internal transport detail must not be displayed." }
}, status);
function deferred() {
  let resolve!: (value: Response) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<Response>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
const panel = (source: Citation | null = citation, language: "en" | "zh" = "en", shouldResolve = true) =>
  <EvidencePanel auth={PERSONAS.employee} citation={source} language={language} shouldResolve={shouldResolve} />;
async function flush(action: () => void) { await act(async () => { action(); await Promise.resolve(); }); }
const retry = () => screen.getByRole("button", { name: "Retry source" });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("independent source retry controls", () => {
  it("regression: explicitly retries one unchanged source and guards repeated clicks before render", async () => {
    const pending = deferred();
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("offline"))
      .mockReturnValueOnce(pending.promise);
    vi.stubGlobal("fetch", fetchMock);
    render(panel());
    const button = await screen.findByRole("button", { name: "Retry source" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    act(() => { button.click(); button.click(); button.click(); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button", { name: "Retry source" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    const first = fetchMock.mock.calls[0] as [string, RequestInit];
    const second = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(first[0]).toBe("/api/backend/sources/resolve");
    expect(second[0]).toBe(first[0]);
    expect(second[1].method).toBe("POST");
    expect(second[1].body).toBe(first[1].body);
    expect(typeof second[1].body).toBe("string");
    expect(JSON.parse(second[1].body as string)).toEqual(citation);
    expect(second[1].headers).toEqual({ "Content-Type": "application/json", ...authHeaders(PERSONAS.employee) });
    expect(first[1].signal?.aborted).toBe(true);
    expect(second[1].signal?.aborted).toBe(false);
    // Detached controls must not start another request while the live one is pending.
    act(() => { button.click(); button.click(); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await flush(() => pending.resolve(success()));
    expect(screen.getByText("Current excerpt")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("locale and equivalent citation rerenders do not retry, including while pending", async () => {
    const pending = deferred();
    const fetchMock = vi.fn().mockResolvedValueOnce(failed(503)).mockReturnValueOnce(pending.promise);
    vi.stubGlobal("fetch", fetchMock);
    const view = render(panel());
    await screen.findByRole("button", { name: "Retry source" });
    view.rerender(panel({ ...citation }, "zh"));
    expect(screen.getByRole("button", { name: "重试来源" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "重试来源" }));
    view.rerender(panel({ ...citation }, "en"));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await flush(() => pending.resolve(success()));
    expect(screen.getByText("Current excerpt")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("requires a fresh manual action after another transient failure, retaining its safe request ID", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(failed(503)).mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(success());
    vi.stubGlobal("fetch", fetchMock);
    render(panel());
    await screen.findByRole("button", { name: "Retry source" });
    expect(screen.getByRole("alert")).toHaveTextContent("req-independent-source");
    expect(screen.queryByText("Internal transport detail must not be displayed.")).not.toBeInTheDocument();
    fireEvent.click(retry());
    await screen.findByRole("button", { name: "Retry source" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    fireEvent.click(retry());
    expect(await screen.findByText("Current excerpt")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it.each([408, 429, 500, 503, 599])("offers only manual retry for HTTP %s", async (status) => {
    const fetchMock = vi.fn().mockResolvedValueOnce(failed(status));
    vi.stubGlobal("fetch", fetchMock);
    render(panel());
    expect(await screen.findByRole("button", { name: "Retry source" })).toBeEnabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([400, 401, 403, 404, 409, 422])("does not reinterpret HTTP %s as retryable", async (status) => {
    const fetchMock = vi.fn().mockResolvedValueOnce(failed(status));
    vi.stubGlobal("fetch", fetchMock);
    render(panel());
    await screen.findByRole("alert");
    expect(screen.queryByRole("button", { name: "Retry source" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("retains source-unavailable precedence even when its structured code accompanies 503", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(failed(503, "SOURCE_REFERENCE_INVALID"));
    vi.stubGlobal("fetch", fetchMock);
    render(panel());
    await screen.findByText("Source unavailable");
    expect(screen.queryByRole("button", { name: "Retry source" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(["wrong version", "null data", "malformed envelope", "unreadable successful body"])(
    "does not retry %s", async (kind) => {
      let result: Response;
      if (kind === "wrong version") result = success({ ...citation, version_id: "older-version" });
      else if (kind === "null data") result = response({ ...fixtures.source, data: null });
      else if (kind === "malformed envelope") result = response({ unexpected: true });
      else {
        result = new Response("unused", { status: 200 });
        vi.spyOn(result, "json").mockRejectedValueOnce(new TypeError("interrupted successful body"));
      }
      const fetchMock = vi.fn().mockResolvedValueOnce(result);
      vi.stubGlobal("fetch", fetchMock);
      render(panel());
      if (kind === "wrong version") await screen.findByText("Source unavailable");
      else await screen.findByRole("alert");
      expect(screen.queryByRole("button", { name: "Retry source" })).not.toBeInTheDocument();
      expect(fetchMock).toHaveBeenCalledTimes(1);
    }
  );

  it("retains an actually propagated TimeoutError as a manual-retry failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValueOnce(new DOMException("timed out", "TimeoutError")));
    render(panel());
    expect(await screen.findByRole("button", { name: "Retry source" })).toBeEnabled();
  });

  it("offers manual retry for an Error-named TimeoutError propagated during body reading", async () => {
    const body = new Response("unused", { status: 200 });
    const timeout = new Error("body interrupted");
    timeout.name = "TimeoutError";
    vi.spyOn(body, "json").mockRejectedValueOnce(timeout);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(body));
    render(panel());
    expect(await screen.findByRole("button", { name: "Retry source" })).toBeEnabled();
  });

  it("retains the generic parser's jsdom DOMException-body INVALID_RESPONSE distinction", async () => {
    const body = new Response("unused", { status: 200 });
    const timeout = new DOMException("body interrupted", "TimeoutError");
    // This existing jsdom realm distinction is why this body error is not propagated.
    expect(timeout instanceof Error).toBe(false);
    vi.spyOn(body, "json").mockRejectedValueOnce(timeout);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(body));
    render(panel());
    expect(await screen.findByRole("alert")).toHaveTextContent("INVALID_RESPONSE");
    expect(screen.queryByRole("button", { name: "Retry source" })).not.toBeInTheDocument();
  });

  it.each(["success", "rejection"])("late retry %s cannot replace or unlock another source request", async (late) => {
    const old = deferred();
    const current = deferred();
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("offline"))
      .mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
    vi.stubGlobal("fetch", fetchMock);
    const view = render(panel());
    await screen.findByRole("button", { name: "Retry source" });
    fireEvent.click(retry());
    view.rerender(panel(nextCitation));
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect((fetchMock.mock.calls[1][1] as RequestInit).signal?.aborted).toBe(true);
    expect((fetchMock.mock.calls[2][1] as RequestInit).signal?.aborted).toBe(false);
    await flush(() => late === "success" ? old.resolve(success(citation, "Obsolete excerpt")) : old.reject(new TypeError("obsolete")));
    expect(screen.queryByText("Obsolete excerpt")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry source" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    await flush(() => current.resolve(success(nextCitation, "New selected excerpt")));
    expect(screen.getByText("New selected excerpt")).toBeInTheDocument();
  });

  it.each(["summary", "clear", "unmount"])("cancels pending retry on %s and ignores its late completion", async (action) => {
    const pending = deferred();
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("offline"))
      .mockReturnValueOnce(pending.promise).mockResolvedValueOnce(success(citation, "Fresh reopened excerpt"));
    vi.stubGlobal("fetch", fetchMock);
    const view = render(panel());
    await screen.findByRole("button", { name: "Retry source" });
    fireEvent.click(retry());
    if (action === "summary") view.rerender(panel(citation, "en", false));
    else if (action === "clear") view.rerender(panel(null));
    else view.unmount();
    expect((fetchMock.mock.calls[1][1] as RequestInit).signal?.aborted).toBe(true);
    await flush(() => pending.resolve(success(citation, "Obsolete excerpt")));
    expect(screen.queryByText("Obsolete excerpt")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    if (action !== "unmount") {
      view.rerender(panel());
      expect(await screen.findByText("Fresh reopened excerpt")).toBeInTheDocument();
      expect(fetchMock).toHaveBeenCalledTimes(3);
    }
  });
});
