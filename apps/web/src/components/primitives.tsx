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
import type { Language } from "@/lib/i18n";
import { text, uiText } from "@/lib/i18n";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardInset } from "./ui/card";

export function ScopeBadge({ label }: Readonly<{ label: string }>) {
  return (
    <Badge variant="scope" className="scope-badge">
      <Lock aria-hidden="true" />
      <span className="wrap">{label}</span>
    </Badge>
  );
}

export function CitationChip({
  citation,
  onOpen,
  language = "en",
  label,
  count = 1
}: Readonly<{
  citation: Citation;
  onOpen: (citation: Citation) => void;
  language?: Language;
  label?: string;
  count?: number;
}>) {
  const displayLabel = label ?? citationLabel(citation);
  return (
    <Button
      type="button"
      variant="ghost"
      className="citation-chip min-h-7 rounded-full bg-[var(--source-soft)] px-2.5 py-1 text-xs font-semibold text-[var(--source)] hover:bg-[#d7eee8]"
      onClick={() => onOpen(citation)}
      aria-label={`${text(uiText.evidence, language)}: ${displayLabel}${count > 1 ? ` x${count}` : ""}`}
    >
      <FileText aria-hidden="true" />
      <span className="wrap">{displayLabel}</span>
      {count > 1 && <span className="citation-count">x{count}</span>}
    </Button>
  );
}

export function StatusPill({
  tone = "neutral",
  children
}: Readonly<{ tone?: "neutral" | "source" | "index" | "danger"; children: React.ReactNode }>) {
  const Icon = tone === "danger" ? ShieldAlert : tone === "index" ? Info : CheckCircle2;
  return (
    <Badge variant={tone}>
      <Icon aria-hidden="true" />
      <span>{children}</span>
    </Badge>
  );
}

export function CopyIdButton({
  value,
  label = "Copy ID",
  disabled = false,
  language = "en"
}: Readonly<{ value: string | null | undefined; label?: string; disabled?: boolean; language?: Language }>) {
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
    <Button type="button" variant="secondary" onClick={() => void copy()} disabled={!canCopy}>
      <Clipboard aria-hidden="true" />
      {copied ? text(uiText.copied, language) : label}
    </Button>
  );
}

export function SafeErrorBanner({
  code,
  message,
  requestId
}: Readonly<{ code?: string; message: string; requestId?: string | null }>) {
  return (
    <CardInset
      role="alert"
      className="bg-[var(--danger-soft)] text-[var(--danger)] shadow-[inset_0_0_0_1px_rgb(180_35_24_/_0.12)]"
    >
      <strong>{code ?? text(uiText.safeError, "en")}</strong>
      <span>{message}</span>
      {requestId !== undefined && requestId !== null && (
        <span className="id-text">request_id: {requestId}</span>
      )}
    </CardInset>
  );
}

export function NoAnswerPanel({
  requestId,
  onDiagnostics,
  language = "en"
}: Readonly<{ requestId?: string | null; onDiagnostics: () => void; language?: Language }>) {
  return (
    <CardInset className="bg-[var(--index-soft)] text-[var(--index)] shadow-[inset_0_0_0_1px_rgb(194_106_18_/_0.16)]">
      <strong>{text(uiText.noAnswerTitle, language)}</strong>
      <span>{text(uiText.noAnswerHelp, language)}</span>
      <div className="actions-row">
        <CopyIdButton value={requestId} label={text(uiText.copyRequestId, language)} language={language} />
        <Button type="button" variant="secondary" onClick={onDiagnostics}>
          {text(uiText.viewDiagnostics, language)}
        </Button>
      </div>
    </CardInset>
  );
}

export function ToolEventRow({ event, language = "en" }: Readonly<{ event: ToolEvent; language?: Language }>) {
  return (
    <CardInset>
      <div className="actions-row">
        <StatusPill tone={event.status === "error" ? "danger" : "neutral"}>
          {event.tool_name} · {event.status}
        </StatusPill>
        {event.latency_ms !== undefined && event.latency_ms !== null && (
          <span className="mono">{event.latency_ms}ms</span>
        )}
      </div>
      <span className="muted">{event.summary ?? text(uiText.toolEventDefault, language)}</span>
      <div className="chip-row">
        {event.agent_run_id !== undefined && <span className="id-text">run: {event.agent_run_id}</span>}
        {event.request_id !== undefined && event.request_id !== null && (
          <span className="id-text">request: {event.request_id}</span>
        )}
        {event.error_code !== undefined && event.error_code !== null && (
          <span className="id-text">error: {event.error_code}</span>
        )}
      </div>
    </CardInset>
  );
}

export function citationLabel(citation: Citation): string {
  const title = citationSourceLabel(citation);
  const version = citation.version_id !== undefined && citation.version_id !== null ? ` · ${citation.version_id}` : "";
  const page =
    citation.page !== undefined && citation.page !== null
      ? ` · p${citation.page}`
      : citation.page_start !== undefined && citation.page_start !== null
        ? ` · p${citation.page_start}`
        : "";
  return `${title}${version}${page}`;
}

export function citationSourceLabel(citation: Citation): string {
  return citation.title_path?.join(" / ") ?? citation.source_display_name ?? citation.source ?? citation.document_id;
}

export function PermissionNotice({ permission, language = "en" }: Readonly<{ permission: string; language?: Language }>) {
  return (
    <Card>
      <div className="actions-row">
        <AlertTriangle aria-hidden="true" />
        <strong>{text(uiText.permissionRequired, language)}</strong>
      </div>
      <p className="muted">
        {text(uiText.permissionRequiredHelp, language)} ({permission})
      </p>
    </Card>
  );
}
