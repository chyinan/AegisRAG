"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileUp, RefreshCcw, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { authScopeKey, documentReviewKey, documentStatusKey } from "@/lib/api/auth-scope";
import { deleteDocument, listDocumentReview, uploadDocument } from "@/lib/api/client";
import type { DocumentReviewRow, UploadDocumentInput, UploadDocumentResult } from "@/lib/api/types";
import type { AuthSession } from "@/lib/auth";
import { hasPermission } from "@/lib/auth";
import type { Language } from "@/lib/i18n";
import { text, uiText } from "@/lib/i18n";
import { PermissionNotice, SafeErrorBanner, StatusPill } from "./primitives";
import { Button } from "./ui/button";
import { Card, CardHeader, CardInset } from "./ui/card";
import { Input } from "./ui/input";
import { Select } from "./ui/select";
import { UploadStatusCard } from "./upload-status";

export function KnowledgeBasePanel({ auth, language }: Readonly<{ auth: AuthSession; language: Language }>) {
  return <KnowledgeBaseContent key={authScopeKey(auth)} auth={auth} language={language} />;
}

function KnowledgeBaseContent({ auth, language }: Readonly<{ auth: AuthSession; language: Language }>) {
  const queryClient = useQueryClient();
  const canUpload = hasPermission(auth, "document:upload");
  const canDelete = hasPermission(auth, "document:manage");
  // This is the management review endpoint, not the retrieval read surface.
  const canRead = hasPermission(auth, "document:manage");
  const documents = useQuery<DocumentReviewRow[]>({
    queryKey: documentReviewKey(auth),
    queryFn: ({ signal }) => listDocumentReview(auth, signal),
    enabled: canRead
  });
  const documentRows = Array.isArray(documents.data) ? documents.data : [];
  const deleteMutation = useMutation({
    mutationFn: (documentId: string) => deleteDocument(auth, documentId),
    // Capture this panel's scope and the mutation's document ID. Invalidating
    // the version prefix refreshes mounted terminal cards and marks inactive
    // versions stale, without touching another document or identity's cache.
    onSuccess: async (_result, documentId) => {
      const queryKey = documentStatusKey(auth, documentId);
      // invalidateQueries only cancels a refetch when cached data exists.
      // Also cancel initial reads so a pre-delete snapshot cannot win the race.
      await queryClient.cancelQueries({ queryKey });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: documentReviewKey(auth), exact: true }),
        queryClient.invalidateQueries({ queryKey })
      ]);
    }
  });

  return (
    <Card aria-labelledby="knowledge-title">
      <CardHeader>
        <div>
          <h2 id="knowledge-title" className="surface-title">
            {text(uiText.knowledgeTitle, language)}
          </h2>
          <p className="muted">{text(uiText.knowledgeHelp, language)}</p>
        </div>
        <Button asChild variant="secondary">
          <a href="/governance" target="_blank" rel="noreferrer">
            {text(uiText.openGovernance, language)}
          </a>
        </Button>
      </CardHeader>

      {canUpload ? <UploadForm auth={auth} language={language} /> : <PermissionNotice permission="document:upload" language={language} />}

      <CardInset className="overflow-x-auto" role="region" aria-label={text(uiText.documents, language)} tabIndex={0}>
        <CardHeader>
          <strong>{text(uiText.documents, language)}</strong>
          <Button
            type="button"
            variant="secondary"
            onClick={() => void documents.refetch()}
            disabled={!canRead || documents.isFetching}
          >
            <RefreshCcw aria-hidden="true" />
            {text(uiText.refresh, language)}
          </Button>
        </CardHeader>
        <div className="kb-grid kb-header">
          <span>{text(uiText.title, language)}</span>
          <span>{text(uiText.sourceType, language)}</span>
          <span>{text(uiText.scopeAcl, language)}</span>
          <span>{text(uiText.status, language)}</span>
          <span>{text(uiText.updated, language)}</span>
          <span>{text(uiText.actions, language)}</span>
        </div>
        <div className="kb-list">
          {!canRead && <PermissionNotice permission="document:manage" language={language} />}
          {canRead && documents.isPending && (
            <p role="status">{language === "zh" ? "正在读取文档…" : "Loading documents…"}</p>
          )}
          {canRead && documents.isError && (
            <SafeErrorBanner message={text(uiText.documentListError, language)} />
          )}
          {deleteMutation.isError && (
            <SafeErrorBanner message={text(uiText.deleteDocumentError, language)} />
          )}
          {canRead && documents.isSuccess && documentRows.length === 0 && (
            <CardInset>
              <strong>{text(uiText.noVisibleDocuments, language)}</strong>
              <span className="muted">{text(uiText.noVisibleDocumentsHelp, language)}</span>
            </CardInset>
          )}
          {canRead && !documents.isError && documentRows.map((row) => (
            <DocumentRow
              key={row.document_id}
              row={row}
              canDelete={canDelete}
              isDeleting={deleteMutation.isPending && deleteMutation.variables === row.document_id}
              language={language}
              onDelete={(documentId) => {
                const confirmed = window.confirm(text(uiText.deleteDocumentConfirm, language));
                if (confirmed) {
                  deleteMutation.mutate(documentId);
                }
              }}
            />
          ))}
        </div>
      </CardInset>
    </Card>
  );
}

export function QuickImportDrawerContent({
  auth,
  onUploaded,
  language
}: Readonly<{ auth: AuthSession; language: Language; onUploaded?: (result: UploadDocumentResult) => void }>) {
  if (!hasPermission(auth, "document:upload")) {
    return <PermissionNotice permission="document:upload" language={language} />;
  }
  return <UploadForm key={authScopeKey(auth)} auth={auth} language={language} compact onUploaded={onUploaded} />;
}

function UploadForm({
  auth,
  language,
  compact = false,
  onUploaded
}: Readonly<{ auth: AuthSession; language: Language; compact?: boolean; onUploaded?: (result: UploadDocumentResult) => void }>) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [sourceType, setSourceType] = useState<UploadDocumentInput["sourceType"]>("markdown");
  const [sourceReference, setSourceReference] = useState("");
  const [aclPreset, setAclPreset] = useState<UploadDocumentInput["aclPreset"]>("tenant");

  const queryClient = useQueryClient();
  const inFlight = useRef<AbortController | null>(null);
  useEffect(() => () => inFlight.current?.abort(), []);
  const upload = useMutation({
    mutationFn: ({ input, controller }: { input: UploadDocumentInput; controller: AbortController }) =>
      uploadDocument(auth, input, controller.signal),
    onSuccess: (result, { controller }) => {
      if (!controller.signal.aborted) {
        void queryClient.invalidateQueries({ queryKey: documentReviewKey(auth), exact: true });
        onUploaded?.(result);
      }
    },
    onSettled: (_result, _error, { controller }) => {
      if (inFlight.current === controller) inFlight.current = null;
    }
  });

  return (
    <form
      className="grid min-w-0 gap-2.5 rounded-lg bg-white/95 p-4 shadow-[var(--shadow-soft)]"
      onSubmit={(event) => {
        event.preventDefault();
        if (file === null || inFlight.current !== null) return;
        const controller = new AbortController();
        inFlight.current = controller;
        upload.mutate({ input: { file, sourceType, title, sourceReference, aclPreset }, controller });
      }}
    >
      <div className="actions-row">
        <FileUp aria-hidden="true" />
        <strong>{compact ? text(uiText.quickImport, language) : text(uiText.importDocument, language)}</strong>
        <StatusPill tone="index">{text(uiText.asyncIngestion, language)}</StatusPill>
      </div>
      <label>
        <span className="scope-label">{text(uiText.file, language)}</span>
        <span className="file-picker-control">
          <span className="file-picker-action">
            <FileUp aria-hidden="true" />
            {text(uiText.chooseFile, language)}
          </span>
          <span className={file === null ? "file-picker-name muted" : "file-picker-name"}>
            {file === null
              ? text(uiText.noFileSelected, language)
              : `${text(uiText.selectedFile, language)} ${file.name}`}
          </span>
        </span>
        <Input
          className="sr-only"
          type="file"
          disabled={upload.isPending}
          accept=".pdf,.docx,.txt,.md,.markdown,application/pdf,text/plain"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </label>
      <div className="two-col">
        <label>
          <span className="scope-label">{text(uiText.title, language)}</span>
          <Input disabled={upload.isPending} value={title} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <label>
          <span className="scope-label">{text(uiText.sourceType, language)}</span>
          <Select
            value={sourceType}
            disabled={upload.isPending}
            onChange={(event) => setSourceType(event.target.value as UploadDocumentInput["sourceType"])}
          >
            <option value="markdown">markdown</option>
            <option value="txt">txt</option>
            <option value="pdf">pdf</option>
            <option value="docx">docx</option>
          </Select>
        </label>
      </div>
      <div className="two-col">
        <label>
          <span className="scope-label">{text(uiText.sourceReference, language)}</span>
          <Input
            value={sourceReference}
            disabled={upload.isPending}
            onChange={(event) => setSourceReference(event.target.value)}
            placeholder="kb://policy.md"
          />
        </label>
        <label>
          <span className="scope-label">{text(uiText.aclPreset, language)}</span>
          <Select
            value={aclPreset}
            disabled={upload.isPending}
            onChange={(event) => setAclPreset(event.target.value as UploadDocumentInput["aclPreset"])}
          >
            <option value="tenant">Tenant</option>
            <option value="department" disabled>
              {language === "zh" ? "Department（暂不支持）" : "Department (unavailable)"}
            </option>
            <option value="private">Private</option>
          </Select>
        </label>
      </div>
      <p className="muted">
        {language === "zh"
          ? "暂不支持 Department 范围。请明确选择 Tenant 或 Private；不会自动扩大访问范围。"
          : "Department scope is unavailable. Choose Tenant or Private explicitly; access is never broadened automatically."}
      </p>
      <Button type="submit" variant="primary" disabled={file === null || upload.isPending}>
        <FileUp aria-hidden="true" />
        {upload.isPending ? (language === "zh" ? "正在上传…" : "Uploading…") : text(uiText.uploadAndCreateJob, language)}
      </Button>
      {upload.isError && <SafeErrorBanner message={text(uiText.uploadError, language)} />}
      {upload.data !== undefined && <UploadStatusCard auth={auth} result={upload.data} language={language} />}
    </form>
  );
}

function DocumentRow({
  row,
  canDelete,
  isDeleting,
  language,
  onDelete
}: Readonly<{
  row: DocumentReviewRow;
  canDelete: boolean;
  isDeleting: boolean;
  language: Language;
  onDelete: (documentId: string) => void;
}>) {
  return (
    <CardInset>
      <div className="kb-grid">
        <strong className="wrap">{row.title ?? row.document_id}</strong>
        <span>{row.source_type ?? "-"}</span>
        <span>{row.acl_summary ?? "backend-confirmed scope"}</span>
        <StatusPill tone={row.status === "retrieval_ready" ? "source" : "index"}>
          {row.status ?? "unknown"}
        </StatusPill>
        <time className="kb-date" dateTime={row.updated_at ?? undefined}>
          {formatDocumentTimestamp(row.updated_at)}
        </time>
        <div className="kb-actions">
          {canDelete && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="text-[var(--danger)] hover:bg-[var(--danger-soft)] hover:text-[var(--danger)]"
              onClick={() => onDelete(row.document_id)}
              disabled={isDeleting}
              aria-label={`${text(uiText.deleteDocument, language)}: ${row.title ?? row.document_id}`}
              title={text(uiText.deleteDocument, language)}
            >
              <Trash2 aria-hidden="true" />
            </Button>
          )}
        </div>
      </div>
      <div className="chip-row">
        <span className="id-text">document: {row.document_id}</span>
        {row.version_id !== undefined && row.version_id !== null && (
          <span className="id-text">version: {row.version_id}</span>
        )}
        {row.job_id !== undefined && row.job_id !== null && <span className="id-text">job: {row.job_id}</span>}
      </div>
    </CardInset>
  );
}

function formatDocumentTimestamp(value: string | null | undefined): string {
  if (value === null || value === undefined || value.trim().length === 0) {
    return "-";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value.length > 16 ? value.slice(0, 16).replace("T", " ") : value;
  }
  const year = parsed.getUTCFullYear();
  const month = padDatePart(parsed.getUTCMonth() + 1);
  const day = padDatePart(parsed.getUTCDate());
  const hour = padDatePart(parsed.getUTCHours());
  const minute = padDatePart(parsed.getUTCMinutes());
  return `${year}-${month}-${day} ${hour}:${minute}`;
}

function padDatePart(value: number): string {
  return String(value).padStart(2, "0");
}
