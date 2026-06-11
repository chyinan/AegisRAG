"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { FileUp, RefreshCcw, Trash2 } from "lucide-react";
import { useState } from "react";
import { deleteDocument, listDocumentReview, uploadDocument } from "@/lib/api/client";
import type { DocumentReviewRow, UploadDocumentInput, UploadDocumentResult } from "@/lib/api/types";
import type { AuthSession } from "@/lib/auth";
import { hasPermission } from "@/lib/auth";
import type { Language } from "@/lib/i18n";
import { text, uiText } from "@/lib/i18n";
import { CopyIdButton, PermissionNotice, SafeErrorBanner, StatusPill } from "./primitives";
import { Button } from "./ui/button";
import { Card, CardHeader, CardInset } from "./ui/card";
import { Input } from "./ui/input";
import { Select } from "./ui/select";

export function KnowledgeBasePanel({ auth, language }: Readonly<{ auth: AuthSession; language: Language }>) {
  const canUpload = hasPermission(auth, "document:upload");
  const canDelete = hasPermission(auth, "document:manage");
  const documents = useQuery<DocumentReviewRow[]>({
    queryKey: ["documents-review", auth.userId, auth.tenantId],
    queryFn: () => listDocumentReview(auth),
    enabled: hasPermission(auth, "document:read")
  });
  const documentRows = Array.isArray(documents.data) ? documents.data : [];
  const deleteMutation = useMutation({
    mutationFn: (documentId: string) => deleteDocument(auth, documentId),
    onSuccess: () => {
      void documents.refetch();
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

      <CardInset>
        <CardHeader>
          <strong>{text(uiText.documents, language)}</strong>
          <Button
            type="button"
            variant="secondary"
            onClick={() => void documents.refetch()}
            disabled={documents.isFetching}
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
          {documents.isError && (
            <SafeErrorBanner message={text(uiText.documentListError, language)} />
          )}
          {deleteMutation.isError && (
            <SafeErrorBanner message={text(uiText.deleteDocumentError, language)} />
          )}
          {documentRows.length === 0 && (
            <CardInset>
              <strong>{text(uiText.noVisibleDocuments, language)}</strong>
              <span className="muted">{text(uiText.noVisibleDocumentsHelp, language)}</span>
            </CardInset>
          )}
          {documentRows.map((row) => (
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
  return <UploadForm auth={auth} language={language} compact onUploaded={onUploaded} />;
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

  const upload = useMutation<UploadDocumentResult, Error>({
    mutationFn: () => {
      if (file === null) {
        throw new Error("File is required.");
      }
      return uploadDocument(auth, {
        file,
        sourceType,
        title,
        sourceReference,
        aclPreset
      });
    },
    onSuccess: (result) => {
      onUploaded?.(result);
    }
  });

  return (
    <form
      className="grid min-w-0 gap-2.5 rounded-lg bg-white/95 p-4 shadow-[var(--shadow-soft)]"
      onSubmit={(event) => {
        event.preventDefault();
        upload.mutate();
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
          accept=".pdf,.docx,.txt,.md,.markdown,application/pdf,text/plain"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </label>
      <div className="two-col">
        <label>
          <span className="scope-label">{text(uiText.title, language)}</span>
          <Input value={title} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <label>
          <span className="scope-label">{text(uiText.sourceType, language)}</span>
          <Select
            value={sourceType}
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
            onChange={(event) => setSourceReference(event.target.value)}
            placeholder="kb://policy.md"
          />
        </label>
        <label>
          <span className="scope-label">{text(uiText.aclPreset, language)}</span>
          <Select
            value={aclPreset}
            onChange={(event) => setAclPreset(event.target.value as UploadDocumentInput["aclPreset"])}
          >
            <option value="tenant">Tenant</option>
            <option value="department">Department</option>
            <option value="private">Private</option>
          </Select>
        </label>
      </div>
      <Button type="submit" variant="primary" disabled={file === null || upload.isPending}>
        <FileUp aria-hidden="true" />
        {text(uiText.uploadAndCreateJob, language)}
      </Button>
      {upload.isError && <SafeErrorBanner message={text(uiText.uploadError, language)} />}
      {upload.data !== undefined && (
        <CardInset>
          <StatusPill tone="index">status: {upload.data.status}</StatusPill>
          <span className="id-text">document_id: {upload.data.document_id}</span>
          <span className="id-text">version_id: {upload.data.version_id}</span>
          <span className="id-text">job_id: {upload.data.job_id}</span>
          <CopyIdButton value={upload.data.job_id} label="Copy job_id" language={language} />
        </CardInset>
      )}
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
