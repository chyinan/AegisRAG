import type { Citation } from "./api/types";

const CITATION_TOKEN = /[ \t]*\[(cite-[A-Za-z0-9_-]+)\]/g;
const CITATION_ID = /^cite-[0-9a-f]{16}$/;

/** Match backend identities, never the order in which the model mentions sources. */
export function normalizeAnswerMarkdown(answer: string, citations: readonly Citation[]): string {
  const references = citationReferences(citations);
  return answer.replace(CITATION_TOKEN, (_match, id: string) => {
    const reference = references.get(id);
    // Legacy/missing/unknown/ambiguous IDs must never select an arbitrary source.
    return reference ? ` [${reference.index + 1}](citation:${id})` : "";
  }).trim();
}

export function citationFromHref(href: string | undefined, citations: readonly Citation[]): Citation | null {
  if (!href?.startsWith("citation:")) return null;
  const id = href.slice("citation:".length);
  if (!CITATION_ID.test(id)) return null;
  return citationReferences(citations).get(id)?.citation ?? null;
}

/** Keep each copied marker tied to its full source tuple, including sibling chunks. */
export function answerWithCitations(message: { text: string; citations: readonly Citation[] }): string {
  const references = citationReferences(message.citations);
  const seen = new Set<string>();
  const lines: string[] = [];
  for (const citation of message.citations) {
    const key = JSON.stringify([citation.document_id, citation.version_id ?? null, citation.chunk_id]);
    if (seen.has(key)) continue;
    seen.add(key);
    const id = citation.citation_id;
    // Unbound older citations remain available without invented numbered references.
    const marker = id && references.has(id) ? `[${id}]` : "-";
    const label = citation.title_path?.filter((part) => part.trim()).join(" / ") ||
      citation.source_display_name?.trim() || citation.document_id;
    const page = citation.page ?? citation.page_start;
    lines.push(`${marker} ${label} (document: ${citation.document_id}; version: ${citation.version_id ?? "unknown"}; chunk: ${citation.chunk_id}${page == null ? "" : `; page: ${page}`})`);
  }
  return lines.length ? [message.text, "", "Citations:", ...lines].join("\n") : message.text;
}

function citationReferences(citations: readonly Citation[]) {
  const references = new Map<string, { citation: Citation; index: number }>();
  const ambiguous = new Set<string>();
  citations.forEach((citation, index) => {
    const id = citation.citation_id;
    if (!id || !CITATION_ID.test(id) || ambiguous.has(id)) return;
    const existing = references.get(id)?.citation;
    if (existing && (existing.document_id !== citation.document_id ||
        existing.version_id !== citation.version_id || existing.chunk_id !== citation.chunk_id)) {
      references.delete(id);
      ambiguous.add(id);
    } else if (!existing) {
      references.set(id, { citation, index });
    }
  });
  return references;
}
