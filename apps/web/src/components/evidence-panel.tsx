"use client";

import { useMutation } from "@tanstack/react-query";
import { SearchCheck } from "lucide-react";
import { useEffect } from "react";
import { ApiClientError, resolveSource } from "@/lib/api/client";
import { safeErrorMessage } from "@/lib/api/safety";
import type { Citation, SourceResolveResult } from "@/lib/api/types";
import type { AuthSession } from "@/lib/auth";
import type { Language } from "@/lib/i18n";
import { text, uiText } from "@/lib/i18n";
import { CopyIdButton, SafeErrorBanner, StatusPill, citationLabel } from "./primitives";
import { CardInset } from "./ui/card";

export function EvidencePanel({
  auth,
  citation,
  language,
  shouldResolve = true
}: Readonly<{ auth: AuthSession; citation: Citation | null; language: Language; shouldResolve?: boolean }>) {
  const mutation = useMutation<SourceResolveResult, Error, Citation>({
    mutationFn: (selected) =>
      resolveSource(auth, {
        document_id: selected.document_id,
        version_id: selected.version_id,
        chunk_id: selected.chunk_id
      })
  });

  useEffect(() => {
    mutation.reset();
    if (citation !== null && shouldResolve) {
      mutation.mutate(citation);
    }
  }, [citation?.document_id, citation?.version_id, citation?.chunk_id, shouldResolve]);

  if (citation === null) {
    return (
      <CardInset>
        <StatusPill tone="source">{text(uiText.citationReady, language)}</StatusPill>
        <p className="muted">{text(uiText.citationReadyHelp, language)}</p>
      </CardInset>
    );
  }

  if (!shouldResolve) {
    return <SourceSummaryPanel citation={citation} language={language} />;
  }

  if (mutation.isIdle || mutation.isPending) {
    return (
      <CardInset>
        <div className="actions-row">
          <SearchCheck aria-hidden="true" />
          <strong>{text(uiText.resolvingSource, language)}</strong>
        </div>
        <p className="muted">{citationLabel(citation)}</p>
      </CardInset>
    );
  }

  if (mutation.isError) {
    if (isSourceUnavailableError(mutation.error)) {
      return <SourceUnavailablePanel citation={citation} language={language} />;
    }
    return <SafeErrorBanner message={safeErrorMessage(errorCode(mutation.error))} />;
  }

  const source = mutation.data;
  if (source?.authorized === false) {
    return <SourceUnavailablePanel citation={citation} language={language} />;
  }

  return (
    <CardInset>
      <div className="actions-row">
        <StatusPill tone="source">{text(uiText.authorizedExcerpt, language)}</StatusPill>
        <CopyIdButton value={source?.chunk_id ?? citation.chunk_id} label={text(uiText.copyChunkId, language)} language={language} />
      </div>
      <h3 className="panel-title">{sourceTitle(source, citation)}</h3>
      <p className="muted">
        page {source?.page_start ?? citation.page_start ?? "-"} - {source?.page_end ?? citation.page_end ?? "-"}
      </p>
      <p className="wrap">{sourceExcerpt(source) ?? text(uiText.noExcerpt, language)}</p>
      <div className="chip-row">
        <span className="id-text">document: {source?.document_id ?? citation.document_id}</span>
        <span className="id-text">version: {source?.version_id ?? citation.version_id ?? "-"}</span>
      </div>
    </CardInset>
  );
}

function SourceSummaryPanel({
  citation,
  language
}: Readonly<{ citation: Citation; language: Language }>) {
  return (
    <CardInset>
      <div className="actions-row">
        <StatusPill tone="source">{text(uiText.citationReady, language)}</StatusPill>
        <CopyIdButton value={citation.chunk_id} label={text(uiText.copyChunkId, language)} language={language} />
      </div>
      <h3 className="panel-title">{citationLabel(citation)}</h3>
      <p className="muted">{text(uiText.citationReadyHelp, language)}</p>
      <div className="chip-row">
        <span className="id-text">document: {citation.document_id}</span>
        <span className="id-text">version: {citation.version_id ?? "-"}</span>
      </div>
    </CardInset>
  );
}

function SourceUnavailablePanel({
  citation,
  language
}: Readonly<{ citation: Citation; language: Language }>) {
  return (
    <CardInset className="bg-[var(--index-soft)] text-[var(--index)] shadow-[inset_0_0_0_1px_rgb(194_106_18_/_0.16)]">
      <div className="actions-row">
        <StatusPill tone="index">{text(uiText.sourceUnavailable, language)}</StatusPill>
        <CopyIdButton value={citation.chunk_id} label={text(uiText.copyChunkId, language)} language={language} />
      </div>
      <p>{text(uiText.sourceNotAvailable, language)}</p>
      <div className="chip-row">
        <span className="id-text">document: {citation.document_id}</span>
        <span className="id-text">version: {citation.version_id ?? "-"}</span>
      </div>
    </CardInset>
  );
}

function isSourceUnavailableError(error: Error): boolean {
  const code = errorCode(error);
  return code === "SOURCE_ACCESS_DENIED" || code === "SOURCE_REFERENCE_INVALID" || code === "HTTP_404";
}

function errorCode(error: Error): string | undefined {
  return error instanceof ApiClientError ? error.structured.code : undefined;
}

function sourceTitle(source: SourceResolveResult | undefined, citation: Citation): string {
  const withBackendFields = source as SourceResolveResult & { source_display_name?: string | null };
  return source?.title ?? withBackendFields?.source_display_name ?? citationLabel(citation);
}

function sourceExcerpt(source: SourceResolveResult | undefined): string | null | undefined {
  const withBackendFields = source as SourceResolveResult & { text_excerpt?: string | null };
  return source?.excerpt ?? withBackendFields?.text_excerpt;
}
