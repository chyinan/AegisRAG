"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { FileUp, RefreshCcw } from "lucide-react";
import { useState } from "react";
import { listDocumentReview, uploadDocument } from "@/lib/api/client";
import type { DocumentReviewRow, UploadDocumentInput, UploadDocumentResult } from "@/lib/api/types";
import type { AuthSession } from "@/lib/auth";
import { hasPermission } from "@/lib/auth";
import { CopyIdButton, PermissionNotice, SafeErrorBanner, StatusPill } from "./primitives";

export function KnowledgeBasePanel({ auth }: Readonly<{ auth: AuthSession }>) {
  const canUpload = hasPermission(auth, "document:upload");
  const documents = useQuery<DocumentReviewRow[]>({
    queryKey: ["documents-review", auth.userId, auth.tenantId],
    queryFn: () => listDocumentReview(auth),
    enabled: hasPermission(auth, "document:read")
  });

  return (
    <section className="surface" aria-labelledby="knowledge-title">
      <div className="surface-header">
        <div>
          <h2 id="knowledge-title" className="surface-title">
            Knowledge Base
          </h2>
          <p className="muted">业务可读优先，工程索引状态可下钻到 Diagnostics 或现有 governance。</p>
        </div>
        <a className="secondary-button" href="/governance" target="_blank" rel="noreferrer">
          Open governance
        </a>
      </div>

      {canUpload ? <UploadForm auth={auth} /> : <PermissionNotice permission="document:upload" />}

      <div className="surface">
        <div className="surface-header">
          <strong>Documents</strong>
          <button
            type="button"
            className="secondary-button"
            onClick={() => void documents.refetch()}
            disabled={documents.isFetching}
          >
            <RefreshCcw aria-hidden="true" />
            Refresh
          </button>
        </div>
        <div className="kb-grid kb-header">
          <span>Title</span>
          <span>Source type</span>
          <span>Scope / ACL</span>
          <span>Status</span>
          <span>Updated</span>
        </div>
        <div className="kb-list">
          {documents.isError && (
            <SafeErrorBanner message="无法读取文档审阅列表。不会展示任何未授权文档名或历史片段。" />
          )}
          {documents.data?.length === 0 && (
            <div className="kb-row">
              <strong>当前授权范围没有可展示文档。</strong>
              <span className="muted">有上传权限的用户可以先导入资料；其他用户可联系知识管理员。</span>
            </div>
          )}
          {documents.data?.map((row) => <DocumentRow key={row.document_id} row={row} />)}
        </div>
      </div>
    </section>
  );
}

export function QuickImportDrawerContent({
  auth,
  onUploaded
}: Readonly<{ auth: AuthSession; onUploaded?: (result: UploadDocumentResult) => void }>) {
  if (!hasPermission(auth, "document:upload")) {
    return <PermissionNotice permission="document:upload" />;
  }
  return <UploadForm auth={auth} compact onUploaded={onUploaded} />;
}

function UploadForm({
  auth,
  compact = false,
  onUploaded
}: Readonly<{ auth: AuthSession; compact?: boolean; onUploaded?: (result: UploadDocumentResult) => void }>) {
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
      className="surface"
      onSubmit={(event) => {
        event.preventDefault();
        upload.mutate();
      }}
    >
      <div className="actions-row">
        <FileUp aria-hidden="true" />
        <strong>{compact ? "Quick import" : "Import document"}</strong>
        <StatusPill tone="index">async ingestion</StatusPill>
      </div>
      <label>
        <span className="scope-label">File</span>
        <input
          className="field"
          type="file"
          accept=".pdf,.docx,.txt,.md,.markdown,application/pdf,text/plain"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </label>
      <div className="two-col">
        <label>
          <span className="scope-label">Title</span>
          <input className="field" value={title} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <label>
          <span className="scope-label">Source type</span>
          <select
            className="select"
            value={sourceType}
            onChange={(event) => setSourceType(event.target.value as UploadDocumentInput["sourceType"])}
          >
            <option value="markdown">markdown</option>
            <option value="txt">txt</option>
            <option value="pdf">pdf</option>
            <option value="docx">docx</option>
          </select>
        </label>
      </div>
      <div className="two-col">
        <label>
          <span className="scope-label">Source reference</span>
          <input
            className="field"
            value={sourceReference}
            onChange={(event) => setSourceReference(event.target.value)}
            placeholder="kb://policy.md"
          />
        </label>
        <label>
          <span className="scope-label">ACL preset</span>
          <select
            className="select"
            value={aclPreset}
            onChange={(event) => setAclPreset(event.target.value as UploadDocumentInput["aclPreset"])}
          >
            <option value="tenant">Tenant</option>
            <option value="department">Department</option>
            <option value="private">Private</option>
          </select>
        </label>
      </div>
      <button type="submit" className="primary-button" disabled={file === null || upload.isPending}>
        <FileUp aria-hidden="true" />
        Upload and create job
      </button>
      {upload.isError && <SafeErrorBanner message="上传未完成。请检查权限、文件类型和 metadata。" />}
      {upload.data !== undefined && (
        <div className="kb-row">
          <StatusPill tone="index">status: {upload.data.status}</StatusPill>
          <span className="id-text">document_id: {upload.data.document_id}</span>
          <span className="id-text">version_id: {upload.data.version_id}</span>
          <span className="id-text">job_id: {upload.data.job_id}</span>
          <CopyIdButton value={upload.data.job_id} label="Copy job_id" />
        </div>
      )}
    </form>
  );
}

function DocumentRow({ row }: Readonly<{ row: DocumentReviewRow }>) {
  return (
    <div className="kb-row">
      <div className="kb-grid">
        <strong className="wrap">{row.title ?? row.document_id}</strong>
        <span>{row.source_type ?? "-"}</span>
        <span>{row.acl_summary ?? "backend-confirmed scope"}</span>
        <StatusPill tone={row.status === "retrieval_ready" ? "source" : "index"}>
          {row.status ?? "unknown"}
        </StatusPill>
        <span>{row.updated_at ?? "-"}</span>
      </div>
      <div className="chip-row">
        <span className="id-text">document: {row.document_id}</span>
        {row.version_id !== undefined && row.version_id !== null && (
          <span className="id-text">version: {row.version_id}</span>
        )}
        {row.job_id !== undefined && row.job_id !== null && <span className="id-text">job: {row.job_id}</span>}
      </div>
    </div>
  );
}
