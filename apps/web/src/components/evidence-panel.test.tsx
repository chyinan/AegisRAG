import { act, render, screen, waitFor } from "@testing-library/react";
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
function deferred() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => { resolve = done; });
  return { promise, resolve };
}

afterEach(() => vi.unstubAllGlobals());

describe("source resolution lifecycle", () => {
  it("renders the real backend source DTO", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(fixtures.source)));
    render(<EvidencePanel auth={PERSONAS.employee} citation={citation} language="en" />);
    expect(await screen.findByText(fixtures.source.data.text_excerpt)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Pilot handbook.md" })).toBeInTheDocument();
  });

  it("aborts the old citation and never renders its late excerpt", async () => {
    const old = deferred();
    const fetchMock = vi.fn().mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce(response(fixtures.sourceDenied, 404));
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<EvidencePanel auth={PERSONAS.employee} citation={citation} language="en" />);
    const newCitation = { ...citation, chunk_id: "deleted-chunk" };
    view.rerender(<EvidencePanel auth={PERSONAS.employee} citation={newCitation} language="en" />);
    await screen.findByText("Source unavailable");
    await act(async () => { old.resolve(response(fixtures.source)); await old.promise; });
    expect(screen.queryByText(fixtures.source.data.text_excerpt)).not.toBeInTheDocument();
    const options = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(options.signal?.aborted).toBe(true);
  });

  it("hides a previous identity's already loaded excerpt before denial resolves", async () => {
    const denied = deferred();
    const fetchMock = vi.fn().mockResolvedValueOnce(response(fixtures.source)).mockReturnValueOnce(denied.promise);
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<EvidencePanel auth={PERSONAS.employee} citation={citation} language="en" />);
    await screen.findByText(fixtures.source.data.text_excerpt);
    view.rerender(<EvidencePanel auth={PERSONAS.auditor} citation={citation} language="en" />);
    expect(screen.queryByText(fixtures.source.data.text_excerpt)).not.toBeInTheDocument();
    await act(async () => { denied.resolve(response(fixtures.sourceDenied, 404)); await denied.promise; });
    await screen.findByText("Source unavailable");
  });

  it("does not trust a successful response for a different version", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      ...fixtures.source, data: { ...fixtures.source.data, version_id: "old-version" }
    })));
    render(<EvidencePanel auth={PERSONAS.employee} citation={citation} language="en" />);
    await screen.findByText("Source unavailable");
    expect(screen.queryByText(fixtures.source.data.text_excerpt)).not.toBeInTheDocument();
  });

  it("rechecks authorization when reopening evidence after a summary", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response(fixtures.source))
      .mockResolvedValueOnce(response(fixtures.sourceDenied, 404));
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<EvidencePanel auth={PERSONAS.employee} citation={citation} language="en" />);
    await screen.findByText(fixtures.source.data.text_excerpt);
    view.rerender(<EvidencePanel auth={PERSONAS.employee} citation={citation} language="en" shouldResolve={false} />);
    view.rerender(<EvidencePanel auth={PERSONAS.employee} citation={citation} language="en" />);
    expect(screen.queryByText(fixtures.source.data.text_excerpt)).not.toBeInTheDocument();
    await screen.findByText("Source unavailable");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });
});
