"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Citation } from "@/lib/api/types";
import type { Language } from "@/lib/i18n";
import { text, uiText } from "@/lib/i18n";
import { citationLabel } from "./primitives";

const CITATION_TOKEN = /\s*\[cite-([A-Za-z0-9_-]+)\]/g;

export function AnswerMarkdown({
  answer,
  citations,
  onOpenCitation,
  language = "en"
}: Readonly<{
  answer: string;
  citations: Citation[];
  onOpenCitation: (citation: Citation) => void;
  language?: Language;
}>) {
  const markdown = normalizeAnswerMarkdown(answer, citations.length);

  return (
    <div className="answer-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        allowedElements={[
          "a",
          "blockquote",
          "br",
          "code",
          "em",
          "h1",
          "h2",
          "h3",
          "li",
          "ol",
          "p",
          "pre",
          "strong",
          "table",
          "tbody",
          "td",
          "th",
          "thead",
          "tr",
          "ul"
        ]}
        urlTransform={(url) => url}
        components={{
          a: ({ href, children }) => {
            const citation = citationFromHref(href, citations);
            if (citation !== null) {
              const label = citationLabel(citation);
              return (
                <button
                  type="button"
                  className="inline-citation"
                  onClick={() => onOpenCitation(citation)}
                  aria-label={`${text(uiText.evidence, language)}: ${label}`}
                  title={label}
                >
                  {children}
                </button>
              );
            }

            const safeHref = safeExternalHref(href);
            if (safeHref === null) {
              return <span>{children}</span>;
            }

            return (
              <a href={safeHref} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          }
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}

export function normalizeAnswerMarkdown(answer: string, citationCount: number): string {
  const citationIndexes = new Map<string, number>();
  const normalized = answer.replace(CITATION_TOKEN, (_match, citationId: string) => {
    let index = citationIndexes.get(citationId);
    if (index === undefined) {
      index = citationIndexes.size;
      citationIndexes.set(citationId, index);
    }
    if (index >= citationCount) {
      return "";
    }
    return ` [${index + 1}](citation:${index})`;
  });
  return normalized.trim();
}

function citationFromHref(href: string | undefined, citations: Citation[]): Citation | null {
  if (href === undefined || !href.startsWith("citation:")) {
    return null;
  }
  const index = Number.parseInt(href.slice("citation:".length), 10);
  if (!Number.isInteger(index) || index < 0 || index >= citations.length) {
    return null;
  }
  return citations[index] ?? null;
}

function safeExternalHref(href: string | undefined): string | null {
  if (href === undefined) {
    return null;
  }
  if (href.startsWith("#") || href.startsWith("/") || href.startsWith("https://") || href.startsWith("http://")) {
    return href;
  }
  return null;
}
