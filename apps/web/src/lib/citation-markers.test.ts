import { describe, expect, it } from "vitest";
import fixtures from "@/test/fixtures/chat-stream.json";
import type { Citation } from "./api/types";
import { parseSseEvents } from "./api/sse";
import { dedupeCitations, historyMessageToChatMessage } from "./chat-stream-state";
import { answerWithCitations, citationFromHref, normalizeAnswerMarkdown } from "./citation-markers";

function reversed() {
  const [event] = parseSseEvents(fixtures.reversedCitations);
  if (event.type !== "final") throw new Error("Expected generated final fixture");
  return event.data;
}

describe("exact citation marker binding", () => {
  it("binds actual backend prompt IDs despite reversed order, unused sources, and repeated mentions", () => {
    const { answer, citations } = reversed();
    expect(citations.map((citation) => citation.chunk_id)).toEqual(["chunk-pilot", "chunk-unused", "chunk-approval"]);
    const markdown = normalizeAnswerMarkdown(answer, citations);
    expect(markdown).toBe("Approval is required [3](citation:cite-6ce867535b64626c). Remote work is supported [1](citation:cite-a3b7d982cee7ed64). Ask for approval [3](citation:cite-6ce867535b64626c).");
    const hrefs = [...markdown.matchAll(/\]\((citation:[^)]+)\)/g)].map((match) => match[1]);
    expect(hrefs.map((href) => citationFromHref(href, citations)?.chunk_id)).toEqual(["chunk-approval", "chunk-pilot", "chunk-approval"]);
    expect(citationFromHref(hrefs[0], [...citations].reverse())?.chunk_id).toBe("chunk-approval");
  });

  it("cannot map unknown, malformed, positional, or missing legacy IDs to a source", () => {
    const { citations } = reversed();
    expect(normalizeAnswerMarkdown("A [cite-ffffffffffffffff] B [cite-hidden]", citations)).toBe("A B");
    const legacy = citations.map((citation) => ({ ...citation, citation_id: undefined }));
    expect(normalizeAnswerMarkdown("A [cite-6ce867535b64626c]", legacy)).toBe("A");
    for (const href of [undefined, "citation:0", "citation:0junk", "citation:cite-6ce867535b64626cjunk", "citation:cite-ffffffffffffffff"]) {
      expect(citationFromHref(href, citations)).toBeNull();
    }
    expect(normalizeAnswerMarkdown("A [1]", citations)).toBe("A [1]");
  });

  it("preserves paragraph boundaries while hiding unmatched markers", () => {
    expect(normalizeAnswerMarkdown("A\n\n[cite-hidden]\nB", [])).toBe("A\n\n\nB");
  });

  it("keeps stable IDs through citation deduplication and history restoration", () => {
    const { answer, citations } = reversed();
    const history = historyMessageToChatMessage({
      role: "assistant", sequence_no: 2, content: answer, citations: [...citations, ...citations],
      request_id: "req-history", trace_id: "trace-history", created_at: "2026-10-14T00:00:00Z"
    });
    expect(history.citations).toEqual(citations);
    expect(normalizeAnswerMarkdown(history.text, history.citations)).toBe(normalizeAnswerMarkdown(answer, citations));
    const version = { ...citations[0], version_id: "version-next", citation_id: "cite-1111111111111111" };
    const chunk = { ...citations[0], chunk_id: "chunk-next", citation_id: "cite-2222222222222222" };
    expect(dedupeCitations([citations[0], version, chunk, citations[0]])).toEqual([citations[0], version, chunk]);
    expect(citationFromHref(`citation:${version.citation_id}`, [citations[0], version])).toBe(version);
  });

  it("rejects an ambiguous marker shared by different document/version/chunk identities", () => {
    const { citations } = reversed();
    const original = citations[0];
    const collision: Citation = { ...original, version_id: "different-version" };
    const duplicates = [original, { ...original }];
    expect(citationFromHref(`citation:${original.citation_id}`, duplicates)).toBe(original);
    const conflicting = [...duplicates, collision, original];
    expect(citationFromHref(`citation:${original.citation_id}`, conflicting)).toBeNull();
    expect(normalizeAnswerMarkdown(`A [${original.citation_id}]`, conflicting)).toBe("A");
  });

  it("copies one exact-ID source entry per chunk instead of inventing grouped-document numbers", () => {
    const { answer, citations } = reversed();
    const copied = answerWithCitations({ text: answer, citations: [...citations, citations[0]] });
    expect(copied.split("\n\n")[0]).toBe(answer);
    expect(copied.split("\n").slice(3)).toEqual([
      "[cite-a3b7d982cee7ed64] Remote work (document: doc-pilot; version: version-pilot; chunk: chunk-pilot)",
      "[cite-3687ab275dc00c4e] Remote work (document: doc-pilot; version: version-pilot; chunk: chunk-unused)",
      "[cite-6ce867535b64626c] Remote work (document: doc-pilot; version: version-pilot; chunk: chunk-approval)"
    ]);
    const legacy = citations.map((citation) => ({ ...citation, citation_id: undefined }));
    expect(answerWithCitations({ text: "Old [1].", citations: legacy })).not.toContain("\n[1]");
  });
});
