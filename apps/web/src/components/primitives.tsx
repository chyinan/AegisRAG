"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Clipboard,
  FileText,
  Info,
  Lock,
  ShieldAlert
} from "lucide-react";
import { useState } from "react";
import type { Citation, ToolEvent } from "@/lib/api/types";

export function ScopeBadge({ label }: Readonly<{ label: string }>) {
  return (
    <span className="scope-badge">
      <Lock aria-hidden="true" />
      <span className="wrap">{label}</span>
    </span>
  );
}

export function CitationChip({
  citation,
  onOpen
}: Readonly<{ citation: Citation; onOpen: (citation: Citation) => void }>) {
  const label = citationLabel(citation);
  return (
    <button
      type="button"
      className="chip"
      onClick={() => onOpen(citation)}
      aria-label={`Open evidence for ${label}`}
    >
      <FileText aria-hidden="true" />
      <span className="wrap">{label}</span>
    </button>
  );
}

export function StatusPill({
  tone = "neutral",
  children
}: Readonly<{ tone?: "neutral" | "source" | "index" | "danger"; children: React.ReactNode }>) {
  const Icon = tone === "danger" ? ShieldAlert : tone === "index" ? Info : CheckCircle2;
  return (
    <span className={`status-pill ${tone}`}>
      <Icon aria-hidden="true" />
      <span>{children}</span>
    </span>
  );
}

export function CopyIdButton({
  value,
  label = "Copy ID",
  disabled = false
}: Readonly<{ value: string | null | undefined; label?: string; disabled?: boolean }>) {
  const [copied, setCopied] = useState(false);
  const canCopy = !disabled && value !== undefined && value !== null && value.length > 0;

  async function copy() {
    if (!canCopy) {
      return;
    }
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }

  return (
    <button type="button" className="secondary-button" onClick={() => void copy()} disabled={!canCopy}>
      <Clipboard aria-hidden="true" />
      {copied ? "Copied" : label}
    </button>
  );
}

export function SafeErrorBanner({
  code,
  message,
  requestId
}: Readonly<{ code?: string; message: string; requestId?: string | null }>) {
  return (
    <div className="safe-banner" role="alert">
      <strong>{code ?? "SAFE_ERROR"}</strong>
      <span>{message}</span>
      {requestId !== undefined && requestId !== null && (
        <span className="id-text">request_id: {requestId}</span>
      )}
    </div>
  );
}

export function NoAnswerPanel({
  requestId,
  onDiagnostics
}: Readonly<{ requestId?: string | null; onDiagnostics: () => void }>) {
  return (
    <div className="no-answer">
      <strong>无法从当前授权资料确认。</strong>
      <span>你可以查看检索范围、上传补充资料，或把 request_id 发给管理员排查。</span>
      <div className="actions-row">
        <CopyIdButton value={requestId} label="Copy request_id" />
        <button type="button" className="secondary-button" onClick={onDiagnostics}>
          查看诊断范围
        </button>
      </div>
    </div>
  );
}

export function ToolEventRow({ event }: Readonly<{ event: ToolEvent }>) {
  return (
    <div className="tool-row">
      <div className="actions-row">
        <StatusPill tone={event.status === "error" ? "danger" : "neutral"}>
          {event.tool_name} · {event.status}
        </StatusPill>
        {event.latency_ms !== undefined && event.latency_ms !== null && (
          <span className="mono">{event.latency_ms}ms</span>
        )}
      </div>
      <span className="muted">{event.summary ?? "受控工具事件摘要，未展示 raw arguments/output。"}</span>
      <div className="chip-row">
        {event.agent_run_id !== undefined && <span className="id-text">run: {event.agent_run_id}</span>}
        {event.request_id !== undefined && event.request_id !== null && (
          <span className="id-text">request: {event.request_id}</span>
        )}
        {event.error_code !== undefined && event.error_code !== null && (
          <span className="id-text">error: {event.error_code}</span>
        )}
      </div>
    </div>
  );
}

export function citationLabel(citation: Citation): string {
  const title = citation.title_path?.join(" / ") ?? citation.source ?? citation.document_id;
  const version = citation.version_id !== undefined && citation.version_id !== null ? ` · ${citation.version_id}` : "";
  const page =
    citation.page !== undefined && citation.page !== null
      ? ` · p${citation.page}`
      : citation.page_start !== undefined && citation.page_start !== null
        ? ` · p${citation.page_start}`
        : "";
  return `${title}${version}${page}`;
}

export function PermissionNotice({ permission }: Readonly<{ permission: string }>) {
  return (
    <div className="surface">
      <div className="actions-row">
        <AlertTriangle aria-hidden="true" />
        <strong>需要权限</strong>
      </div>
      <p className="muted">当前身份缺少 {permission}，界面不会展示未授权资源是否存在。</p>
    </div>
  );
}
