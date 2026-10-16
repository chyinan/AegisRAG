import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkbenchShell } from "@/components/workbench-shell";
import { PERSONAS } from "@/lib/auth";
import source from "@/test/fixtures/backend-workbench.json";
import stream from "@/test/fixtures/chat-stream.json";

const sessionKey = "aegisrag.chat.session.tenant-demo-alpha.demo-user-employee";
const sourceResponse = (excerpt = source.source.data.text_excerpt) => new Response(JSON.stringify({
  ...source.source, data: { ...source.source.data, text_excerpt: excerpt }
}));
function deferred() {
  let resolve!: (response: Response) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<Response>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function renderWorkbench() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const element = (language: "en" | "zh" = "en") =>
    <QueryClientProvider client={client}><WorkbenchShell auth={PERSONAS.employee} language={language}
      onLanguageChange={vi.fn()} onSignOut={vi.fn()} /></QueryClientProvider>;
  return { ...render(element()), element };
}
const inspector = () => within(screen.getByRole("complementary", { name: "Evidence and diagnostics panel" }));
async function answeredWorkbench() {
  const view = renderWorkbench();
  fireEvent.change(screen.getByRole("textbox", { name: "Your question" }), { target: { value: "What is our remote work policy?" } });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Copy answer with citations" })).toBeEnabled());
  fireEvent.change(screen.getByRole("textbox", { name: "Your question" }), { target: { value: "Unsent follow-up remains here" } });
  return view;
}
function assertAnswerIntact() {
  expect(screen.getByText("Assistant").closest("article")).toHaveTextContent("Working remotely is supported");
  expect(screen.getByRole("textbox", { name: "Your question" })).toHaveValue("Unsent follow-up remains here");
  expect(screen.getByText("Current session: session-fixture")).toBeInTheDocument();
  expect(window.sessionStorage.getItem(sessionKey)).toBe("session-fixture");
  expect(screen.getByRole("button", { name: "Copy answer with citations" })).toBeEnabled();
}
const openCitation = () => fireEvent.click(screen.getAllByRole("button", { name: /Evidence: Remote work/ })[0]);
async function flush(action: () => void) { await act(async () => { action(); await Promise.resolve(); }); }
afterEach(() => { cleanup(); window.sessionStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("independent workbench source recovery", () => {
  it("manual recovery preserves answer, draft and session and invokes only source resolution", async () => {
    const pending = deferred();
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(stream.success))
      .mockRejectedValueOnce(new TypeError("temporary")).mockReturnValueOnce(pending.promise);
    vi.stubGlobal("fetch", fetchMock);
    const view = await answeredWorkbench();
    openCitation();
    const button = await inspector().findByRole("button", { name: "Retry source" });
    openCitation();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    assertAnswerIntact();
    act(() => { button.click(); button.click(); button.click(); });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    assertAnswerIntact();
    view.rerender(view.element("zh"));
    expect(fetchMock).toHaveBeenCalledTimes(3);
    view.rerender(view.element());
    await flush(() => pending.resolve(sourceResponse()));
    expect(inspector().getByText(source.source.data.text_excerpt)).toBeInTheDocument();
    assertAnswerIntact();
    expect(fetchMock.mock.calls.map(([url]) => url as string)).toEqual([
      "/api/backend/chat/stream", "/api/backend/sources/resolve", "/api/backend/sources/resolve"
    ]);
    expect((fetchMock.mock.calls[2][1] as RequestInit).body).toBe((fetchMock.mock.calls[1][1] as RequestInit).body);
  });

  it("existing tab-remount workaround preserves the conversation and does not fetch diagnostics", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(stream.success))
      .mockRejectedValueOnce(new TypeError("temporary")).mockResolvedValueOnce(sourceResponse());
    vi.stubGlobal("fetch", fetchMock);
    await answeredWorkbench();
    openCitation();
    await inspector().findByRole("alert");
    openCitation();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("tab", { name: "Diagnostics" }));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    assertAnswerIntact();
    fireEvent.click(screen.getByRole("tab", { name: "Evidence" }));
    await inspector().findByText(source.source.data.text_excerpt);
    assertAnswerIntact();
    expect(fetchMock.mock.calls.map(([url]) => url as string)).toEqual([
      "/api/backend/chat/stream", "/api/backend/sources/resolve", "/api/backend/sources/resolve"
    ]);
  });

  it.each(["success", "error"])("late retry %s cannot overwrite a successfully reopened inspector", async (outcome) => {
    const old = deferred();
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(stream.success))
      .mockRejectedValueOnce(new TypeError("temporary")).mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce(sourceResponse("Fresh inspector excerpt"));
    vi.stubGlobal("fetch", fetchMock);
    await answeredWorkbench();
    openCitation();
    fireEvent.click(await inspector().findByRole("button", { name: "Retry source" }));
    fireEvent.click(screen.getByRole("tab", { name: "Diagnostics" }));
    expect((fetchMock.mock.calls[2][1] as RequestInit).signal?.aborted).toBe(true);
    fireEvent.click(screen.getByRole("tab", { name: "Evidence" }));
    await inspector().findByText("Fresh inspector excerpt");
    await flush(() => outcome === "success" ? old.resolve(sourceResponse("Stale inspector excerpt")) : old.reject(new TypeError("late failure")));
    expect(inspector().getByText("Fresh inspector excerpt")).toBeInTheDocument();
    expect(inspector().queryByText("Stale inspector excerpt")).not.toBeInTheDocument();
    expect(inspector().queryByRole("alert")).not.toBeInTheDocument();
    expect(inspector().queryByRole("button", { name: "Retry source" })).not.toBeInTheDocument();
    assertAnswerIntact();
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });
});
