"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Citation } from "@/lib/api/types";
import { citationFromHref, normalizeAnswerMarkdown } from "@/lib/citation-markers";
import type { Language } from "@/lib/i18n";
import { text, uiText } from "@/lib/i18n";
import { citationLabel } from "./primitives";

export { normalizeAnswerMarkdown } from "@/lib/citation-markers";

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
  const markdown = normalizeAnswerMarkdown(answer, citations);

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

function safeExternalHref(href: string | undefined): string | null {
  if (href === undefined) {
    return null;
  }
  if (href.startsWith("#") || href.startsWith("/") || href.startsWith("https://") || href.startsWith("http://")) {
    return href;
  }
  return null;
}
