import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Citation } from "@/lib/api/types";
import { PERSONAS } from "@/lib/auth";
import fixtures from "@/test/fixtures/backend-workbench.json";
import { EvidencePanel } from "./evidence-panel";

const citation: Citation = {
  document_id: "doc-pilot", version_id: "version-pilot", chunk_id: "chunk-pilot"
};
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" }
});
const failure = (status: number) => response({
  data: null, error: { code: "SOURCE_READ_FAILED", message: "Private upstream details" },
  request_id: "req-source-retry"
}, status);
function deferred() {
  let resolve!: (value: Response) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<Response>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function panel(nextCitation: Citation | null = citation, language: "en" | "zh" = "en", shouldResolve = true) {
  return <EvidencePanel auth={PERSONAS.employee} citation={nextCitation} language={language} shouldResolve={shouldResolve} />;
}
async function settle(action: () => void) {
  await act(() => { action(); return Promise.resolve(); });
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("manual source retry", () => {
  it("manually retries a transport failure with the exact same citation", async () => {
    const retry = deferred();
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("Private upstream details"))
      .mockReturnValueOnce(retry.promise);
    vi.stubGlobal("fetch", fetchMock);
    render(panel());
    const button = await screen.findByRole("button", { name: "Retry source" });
    expect(screen.getByRole("alert")).not.toHaveTextContent("Private upstream details");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fireEvent.click(button);
    expect(screen.getByText("Re-authorizing source")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry source" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const first = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const second = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(fetchMock.mock.calls.map((call) => call[0] as string)).toEqual([
      "/api/backend/sources/resolve", "/api/backend/sources/resolve"
    ]);
    expect(second.body).toBe(JSON.stringify(citation));
    expect(second.body).toBe(first.body);
    expect(second.headers).toEqual(first.headers);
    expect(second.method).toBe("POST");
    expect(second.signal).not.toBe(first.signal);
    expect(first.signal?.aborted).toBe(true);
    await settle(() => retry.resolve(response(fixtures.source)));
    expect(await screen.findByText(fixtures.source.data.text_excerpt)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("guards repeated retry clicks synchronously before the loading render", async () => {
    const retry = deferred();
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("Transient"))
      .mockReturnValueOnce(retry.promise);
    vi.stubGlobal("fetch", fetchMock);
    render(panel());
    const button = await screen.findByRole("button", { name: "Retry source" });
    act(() => { button.click(); button.click(); button.click(); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button", { name: "Retry source" })).not.toBeInTheDocument();
    await settle(() => retry.resolve(response(fixtures.source)));
    expect(await screen.findByText(fixtures.source.data.text_excerpt)).toBeInTheDocument();
  });

  it("allows another explicit retry after the previous retry fails", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(failure(503))
      .mockRejectedValueOnce(new TypeError("Still unavailable"))
      .mockResolvedValueOnce(response(fixtures.source));
    vi.stubGlobal("fetch", fetchMock);
    render(panel());
    fireEvent.click(await screen.findByRole("button", { name: "Retry source" }));
    const button = await screen.findByRole("button", { name: "Retry source" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    fireEvent.click(button);
    expect(await screen.findByText(fixtures.source.data.text_excerpt)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it.each([408, 429, 500, 503, 599])("offers manual recovery for HTTP %s without exposing raw details", async (status) => {
    const fetchMock = vi.fn().mockResolvedValueOnce(failure(status));
    vi.stubGlobal("fetch", fetchMock);
    render(panel());
    expect(await screen.findByRole("button", { name: "Retry source" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("req-source-retry");
    expect(screen.getByRole("alert")).not.toHaveTextContent("Private upstream details");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each(["fetch", "body"] as const)("offers retry for a cooperative TimeoutError from %s", async (stage) => {
    // The existing body parser propagates Error instances named TimeoutError.
    // Fetch rejects directly, so its DOMException reaches the component unchanged.
    const error = stage === "fetch"
      ? new DOMException("Private upstream details", "TimeoutError")
      : Object.assign(new Error("Private upstream details"), { name: "TimeoutError" });
    const timedOut = response(fixtures.source);
    if (stage === "body") vi.spyOn(timedOut, "json").mockRejectedValueOnce(error);
    const fetchMock = stage === "fetch" ? vi.fn().mockRejectedValueOnce(error) : vi.fn().mockResolvedValueOnce(timedOut);
    vi.stubGlobal("fetch", fetchMock);
    render(panel());
    expect(await screen.findByRole("button", { name: "Retry source" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).not.toHaveTextContent("Private upstream details");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("keeps a successful-body DOMException outside Error nonretryable under the existing parser", async () => {
    const error = new DOMException("Private body timeout", "TimeoutError");
    // jsdom's DOMException is outside the Error realm checked by the generic parser.
    expect(error).not.toBeInstanceOf(Error);
    const result = response(fixtures.source);
    vi.spyOn(result, "json").mockRejectedValueOnce(error);
    const fetchMock = vi.fn().mockResolvedValueOnce(result);
    vi.stubGlobal("fetch", fetchMock);
    render(panel());
    expect(await screen.findByRole("alert")).toHaveTextContent("INVALID_RESPONSE");
    expect(screen.getByRole("alert")).not.toHaveTextContent("Private body timeout");
    expect(screen.queryByRole("button", { name: "Retry source" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([400, 409, 422])("does not retry permanent HTTP %s failures", async (status) => {
    const fetchMock = vi.fn().mockResolvedValueOnce(failure(status));
    vi.stubGlobal("fetch", fetchMock);
    render(panel());
    await screen.findByRole("alert");
    expect(screen.queryByRole("button", { name: "Retry source" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("keeps a missing source unavailable without retry", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response("Not found", { status: 404 })));
    render(panel());
    await screen.findByText("Source unavailable");
    expect(screen.queryByRole("button", { name: "Retry source" })).not.toBeInTheDocument();
  });

  it.each(["invalid-json", "empty-data", "mismatched-source", "interrupted-body"] as const)(
    "does not reinterpret a successful %s response as transient", async (kind) => {
      let result: Response;
      if (kind === "invalid-json") result = new Response("not json");
      else if (kind === "empty-data") result = response({ data: null, error: null, request_id: "req-empty" });
      else if (kind === "mismatched-source") result = response({
        ...fixtures.source, data: { ...fixtures.source.data, chunk_id: "different-chunk" }
      });
      else {
        result = response(fixtures.source);
        vi.spyOn(result, "json").mockRejectedValueOnce(new TypeError("Private interrupted body"));
      }
      const fetchMock = vi.fn().mockResolvedValueOnce(result);
      vi.stubGlobal("fetch", fetchMock);
      render(panel());
      if (kind === "mismatched-source") await screen.findByText("Source unavailable");
      else await screen.findByRole("alert");
      if (kind === "interrupted-body") expect(screen.getByRole("alert")).toHaveTextContent("INVALID_RESPONSE");
      expect(screen.queryByRole("button", { name: "Retry source" })).not.toBeInTheDocument();
      expect(screen.queryByText(fixtures.source.data.text_excerpt)).not.toBeInTheDocument();
      expect(fetchMock).toHaveBeenCalledTimes(1);
    }
  );

  it("localizes the action without retrying on language or equivalent citation rerenders", async () => {
    const retry = deferred();
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("Transient"))
      .mockReturnValueOnce(retry.promise);
    vi.stubGlobal("fetch", fetchMock);
    const view = render(panel());
    await screen.findByRole("button", { name: "Retry source" });
    view.rerender(panel({ ...citation }, "zh"));
    expect(screen.getByRole("button", { name: "重试来源" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "重试来源" }));
    expect(screen.getByText("正在重新授权来源")).toBeInTheDocument();
    view.rerender(panel({ ...citation }, "en"));
    expect(screen.getByText("Re-authorizing source")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await settle(() => retry.resolve(response(fixtures.source)));
    await screen.findByText(fixtures.source.data.text_excerpt);
    view.rerender(panel({ ...citation }, "zh"));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([null, undefined])("preserves an absent version (%s) across the manual retry", async (versionId) => {
    const selected = { ...citation, version_id: versionId };
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("Transient"))
      .mockResolvedValueOnce(response({ ...fixtures.source, data: { ...fixtures.source.data, version_id: versionId } }));
    vi.stubGlobal("fetch", fetchMock);
    render(panel(selected));
    fireEvent.click(await screen.findByRole("button", { name: "Retry source" }));
    await screen.findByText(fixtures.source.data.text_excerpt);
    const bodies = fetchMock.mock.calls.map((call) => (call[1] as RequestInit).body);
    expect(bodies).toEqual([JSON.stringify(selected), JSON.stringify(selected)]);
  });

  it.each(["resolve", "reject"] as const)("ignores a late %s after changing citation during retry", async (outcome) => {
    const old = deferred();
    const current = deferred();
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("Transient"))
      .mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
    vi.stubGlobal("fetch", fetchMock);
    const view = render(panel());
    fireEvent.click(await screen.findByRole("button", { name: "Retry source" }));
    view.rerender(panel({ ...citation, chunk_id: "current-chunk" }));
    expect((fetchMock.mock.calls[1]?.[1] as RequestInit).signal?.aborted).toBe(true);
    await settle(() => outcome === "resolve" ? old.resolve(response(fixtures.source)) : old.reject(new TypeError("Late failure")));
    expect(screen.getByText("Re-authorizing source")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry source" })).not.toBeInTheDocument();
    expect(screen.queryByText(fixtures.source.data.text_excerpt)).not.toBeInTheDocument();
    await settle(() => current.resolve(response({
      ...fixtures.source, data: { ...fixtures.source.data, chunk_id: "current-chunk", text_excerpt: "Current excerpt" }
    })));
    await screen.findByText("Current excerpt");
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it.each(["null", "summary", "unmount"] as const)("cancels retry on %s and ignores its late result", async (navigation) => {
    const pending = deferred();
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("Transient"))
      .mockReturnValueOnce(pending.promise);
    vi.stubGlobal("fetch", fetchMock);
    const view = render(panel());
    fireEvent.click(await screen.findByRole("button", { name: "Retry source" }));
    if (navigation === "unmount") view.unmount();
    else view.rerender(panel(navigation === "null" ? null : citation, "en", navigation !== "summary"));
    expect((fetchMock.mock.calls[1]?.[1] as RequestInit).signal?.aborted).toBe(true);
    await settle(() => pending.resolve(response(fixtures.source)));
    expect(screen.queryByText(fixtures.source.data.text_excerpt)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry source" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
