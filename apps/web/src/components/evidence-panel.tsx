"use client";

import { SearchCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiClientError, resolveSource } from "@/lib/api/client";
import { safeErrorMessage } from "@/lib/api/safety";
import type { Citation, SourceResolveResult } from "@/lib/api/types";
import type { AuthSession } from "@/lib/auth";
import type { Language } from "@/lib/i18n";
import { text, uiText } from "@/lib/i18n";
import { CopyIdButton, SafeErrorBanner, StatusPill, citationLabel } from "./primitives";
import { Button } from "./ui/button";
import { CardInset } from "./ui/card";

export function EvidencePanel({
  auth,
  citation,
  language,
  shouldResolve = true
}: Readonly<{ auth: AuthSession; citation: Citation | null; language: Language; shouldResolve?: boolean }>) {

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

  return <ResolvedEvidencePanel
    key={JSON.stringify([citation.document_id, citation.version_id, citation.chunk_id])}
    auth={auth} citation={citation} language={language}
  />;
}

function ResolvedEvidencePanel({ auth, citation, language }: Readonly<{
  auth: AuthSession; citation: Citation; language: Language;
}>) {
  const documentId = citation.document_id;
  const versionId = citation.version_id;
  const chunkId = citation.chunk_id;
  const [attempt, setAttempt] = useState(0);
  const pendingRef = useRef(true);
  const [resolution, setResolution] = useState<{
    attempt: number;
    auth: AuthSession;
    documentId: string;
    versionId: string | null | undefined;
    chunkId: string;
    source?: SourceResolveResult;
    error?: Error;
  } | null>(null);

  useEffect(() => {
    if (documentId === undefined || chunkId === undefined) return;
    const controller = new AbortController();
    let current = true;
    pendingRef.current = true;
    void resolveSource(auth, {
      document_id: documentId,
      version_id: versionId,
      chunk_id: chunkId
    }, controller.signal).then((source) => {
      if (current) {
        pendingRef.current = false;
        setResolution({ attempt, auth, documentId, versionId, chunkId, source });
      }
    }).catch((error: unknown) => {
      if (current && !controller.signal.aborted) {
        pendingRef.current = false;
        setResolution({ attempt, auth, documentId, versionId, chunkId,
          error: error instanceof Error || error instanceof DOMException ? error : new Error("Source resolution failed.") });
      }
    });
    return () => {
      current = false;
      controller.abort();
    };
  }, [attempt, auth, documentId, versionId, chunkId]);

  // Never display an earlier identity's or citation's excerpt, including the
  // render before effect cleanup and when an aborted transport resolves late.
  const current = resolution?.attempt === attempt && resolution.auth === auth && resolution.documentId === documentId &&
    resolution.versionId === versionId && resolution.chunkId === chunkId ? resolution : null;

  if (current === null) {
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

  if (current.error !== undefined) {
    if (isSourceUnavailableError(current.error)) {
      return <SourceUnavailablePanel citation={citation} language={language} />;
    }
    return (
      <>
        <SafeErrorBanner code={errorCode(current.error)} message={safeErrorMessage(errorCode(current.error))}
          requestId={current.error instanceof ApiClientError ? current.error.structured.request_id : undefined} />
        {isRetryableSourceError(current.error) && (
          <div className="actions-row">
            <Button type="button" variant="secondary" onClick={() => {
              // Close the repeated-click window before React replaces the error with loading.
              if (pendingRef.current) return;
              pendingRef.current = true;
              setAttempt((value) => value + 1);
            }}>
              {text(uiText.retrySource, language)}
            </Button>
          </div>
        )}
      </>
    );
  }

  const source = current.source;
  if (source === undefined || source.document_id !== documentId || source.version_id !== versionId || source.chunk_id !== chunkId) {
    return <SourceUnavailablePanel citation={citation} language={language} />;
  }

  return (
    <CardInset>
      <div className="actions-row">
        <StatusPill tone="source">{text(uiText.authorizedExcerpt, language)}</StatusPill>
        <CopyIdButton value={source?.chunk_id ?? citation.chunk_id} label={text(uiText.copyChunkId, language)} language={language} />
      </div>
      <h3 className="panel-title">{source.source_display_name}</h3>
      <p className="muted">
        page {source?.page_start ?? citation.page_start ?? "-"} - {source?.page_end ?? citation.page_end ?? "-"}
      </p>
      <p className="wrap">{source.text_excerpt || text(uiText.noExcerpt, language)}</p>
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

function isRetryableSourceError(error: Error): boolean {
  return error instanceof ApiClientError
    ? error.status === 408 || error.status === 429 || (error.status !== undefined && error.status >= 500 && error.status < 600)
    : error instanceof TypeError || error.name === "TimeoutError";
}

function errorCode(error: Error): string | undefined {
  return error instanceof ApiClientError ? error.structured.code : undefined;
}

