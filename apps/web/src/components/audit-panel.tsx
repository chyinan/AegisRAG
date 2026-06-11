"use client";

import { useMutation } from "@tanstack/react-query";
import { Download, Search, ShieldCheck } from "lucide-react";
import type React from "react";
import { useMemo, useState } from "react";
import { exportAuditLogs, listAuditLogs } from "@/lib/api/client";
import type { AuditExplorerListResponse, AuditExportPayload, AuditLogQuery, AuditLogSummary } from "@/lib/api/types";
import type { AuthSession } from "@/lib/auth";
import type { Language } from "@/lib/i18n";
import { text, uiText } from "@/lib/i18n";
import { CopyIdButton, PermissionNotice, SafeErrorBanner, StatusPill } from "./primitives";
import { Button } from "./ui/button";
import { Card, CardHeader, CardInset } from "./ui/card";
import { Input } from "./ui/input";
import { Select } from "./ui/select";

type AuditFilters = {
  user_id: string;
  request_id: string;
  trace_id: string;
  action: string;
  resource_type: string;
  resource_id: string;
  status: string;
  created_at_from: string;
  created_at_to: string;
  limit: string;
};

const DEFAULT_FILTERS: AuditFilters = {
  user_id: "",
  request_id: "",
  trace_id: "",
  action: "",
  resource_type: "",
  resource_id: "",
  status: "",
  created_at_from: "",
  created_at_to: "",
  limit: "50"
};

export function AuditPanel({
  auth,
  language,
  canRead
}: Readonly<{ auth: AuthSession; language: Language; canRead: boolean }>) {
  const [filters, setFilters] = useState<AuditFilters>(DEFAULT_FILTERS);
  const [exportPayload, setExportPayload] = useState<AuditExportPayload | null>(null);
  const query = useMemo(() => filtersToQuery(filters), [filters]);

  const logs = useMutation<AuditExplorerListResponse, Error, AuditLogQuery>({
    mutationFn: (nextQuery) => listAuditLogs(auth, nextQuery),
    onSuccess: () => setExportPayload(null)
  });
  const auditExport = useMutation<AuditExportPayload, Error, AuditLogQuery>({
    mutationFn: (nextQuery) => exportAuditLogs(auth, nextQuery),
    onSuccess: setExportPayload
  });

  if (!canRead) {
    return <PermissionNotice permission="audit:read" language={language} />;
  }

  return (
    <Card as="section" aria-labelledby="audit-explorer-title">
      <CardHeader>
        <div>
          <h2 id="audit-explorer-title" className="surface-title">
            {text(uiText.auditExplorerTitle, language)}
          </h2>
          <p className="muted">{text(uiText.auditExplorerHelp, language)}</p>
        </div>
        <StatusPill tone="source">audit:read</StatusPill>
      </CardHeader>

      <CardInset className="bg-[#f7fbfa] shadow-[inset_3px_0_0_var(--source),inset_0_0_0_1px_rgb(20_122_103_/_0.12)]">
        <div className="actions-row">
          <ShieldCheck aria-hidden="true" />
          <strong>{text(uiText.backendFactsOnly, language)}</strong>
        </div>
        <span className="muted">{text(uiText.auditSafeBoundary, language)}</span>
      </CardInset>

      <form
        className="grid gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          logs.mutate(query);
        }}
      >
        <span className="scope-label">{text(uiText.auditFilters, language)}</span>
        <div className="two-col">
          <AuditInput label="user_id" value={filters.user_id} onChange={(value) => setFilter(setFilters, "user_id", value)} />
          <AuditInput
            label="request_id"
            value={filters.request_id}
            onChange={(value) => setFilter(setFilters, "request_id", value)}
          />
          <AuditInput
            label="trace_id"
            value={filters.trace_id}
            onChange={(value) => setFilter(setFilters, "trace_id", value)}
          />
          <AuditInput label="action" value={filters.action} onChange={(value) => setFilter(setFilters, "action", value)} />
          <AuditInput
            label="resource_type"
            value={filters.resource_type}
            onChange={(value) => setFilter(setFilters, "resource_type", value)}
          />
          <AuditInput
            label="resource_id"
            value={filters.resource_id}
            onChange={(value) => setFilter(setFilters, "resource_id", value)}
          />
          <label>
            <span className="scope-label">status</span>
            <Select value={filters.status} onChange={(event) => setFilter(setFilters, "status", event.target.value)}>
              <option value="">any</option>
              <option value="success">success</option>
              <option value="failure">failure</option>
              <option value="denied">denied</option>
            </Select>
          </label>
          <AuditInput
            label="limit"
            value={filters.limit}
            inputMode="numeric"
            onChange={(value) => setFilter(setFilters, "limit", value)}
          />
          <AuditInput
            label={text(uiText.createdAtFrom, language)}
            value={filters.created_at_from}
            placeholder={text(uiText.dateTimeFilterPlaceholder, language)}
            onChange={(value) => setFilter(setFilters, "created_at_from", value)}
          />
          <AuditInput
            label={text(uiText.createdAtTo, language)}
            value={filters.created_at_to}
            placeholder={text(uiText.dateTimeFilterPlaceholder, language)}
            onChange={(value) => setFilter(setFilters, "created_at_to", value)}
          />
        </div>
        <div className="actions-row">
          <Button type="submit" variant="primary" disabled={logs.isPending}>
            <Search aria-hidden="true" />
            {text(uiText.searchLogs, language)}
          </Button>
          <Button type="button" variant="secondary" disabled={auditExport.isPending} onClick={() => auditExport.mutate(query)}>
            <Download aria-hidden="true" />
            {text(uiText.prepareExport, language)}
          </Button>
          <CopyIdButton
            value={exportPayload === null ? "" : JSON.stringify(exportPayload, null, 2)}
            label={text(uiText.copyExportJson, language)}
            disabled={exportPayload === null}
            language={language}
          />
        </div>
      </form>

      {(logs.isError || auditExport.isError) && <SafeErrorBanner message={text(uiText.auditError, language)} />}

      {exportPayload !== null && (
        <CardInset>
          <StatusPill tone="index">{text(uiText.auditExportReady, language)}</StatusPill>
          <span className="id-text">export_id: {exportPayload.export_id}</span>
          <span className="id-text">item_count: {exportPayload.item_count}</span>
        </CardInset>
      )}

      {logs.data !== undefined && (
        <section className="grid gap-3" aria-labelledby="audit-results-title">
          <h3 id="audit-results-title" className="surface-title text-base">
            {text(uiText.auditResults, language)}
          </h3>
          {logs.data.items.length === 0 ? (
            <CardInset>
              <span className="muted">{text(uiText.noAuditRecords, language)}</span>
            </CardInset>
          ) : (
            logs.data.items.map((item) => <AuditLogRow key={item.id} item={item} language={language} />)
          )}
          {(logs.data.next_steps ?? []).length > 0 && (
            <CardInset>
              <span className="scope-label">next_steps</span>
              <ul>
                {(logs.data.next_steps ?? []).map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ul>
            </CardInset>
          )}
        </section>
      )}
    </Card>
  );
}

function AuditInput({
  label,
  value,
  onChange,
  type = "text",
  inputMode,
  placeholder
}: Readonly<{
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  inputMode?: React.HTMLAttributes<HTMLInputElement>["inputMode"];
  placeholder?: string;
}>) {
  return (
    <label>
      <span className="scope-label">{label}</span>
      <Input
        aria-label={label}
        type={type}
        inputMode={inputMode}
        placeholder={placeholder}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function AuditLogRow({ item, language }: Readonly<{ item: AuditLogSummary; language: Language }>) {
  return (
    <CardInset as="article">
      <div className="actions-row">
        <StatusPill tone={item.status === "success" ? "source" : item.status === "denied" ? "danger" : "index"}>
          {item.action} · {item.status}
        </StatusPill>
        <span className="mono">{formatValue(item.latency_ms)}ms</span>
      </div>
      <div className="two-col">
        <Field label="audit_id" value={item.id} />
        <Field label="tenant_id" value={item.tenant_id} />
        <Field label="user_id" value={item.user_id} />
        <Field label="request_id" value={item.request_id} />
        <Field label="trace_id" value={item.trace_id} />
        <Field label="resource" value={`${item.resource_type}:${item.resource_id}`} />
        <Field label="created_at" value={item.created_at} />
        <Field label="error_code" value={item.error_code} />
      </div>
      <SafeMap label="safe_summary" value={item.safe_summary} />
      <SafeMap label="safe_counts" value={item.safe_counts} />
      {item.association !== undefined && item.association !== null && (
        <div>
          <span className="scope-label">{text(uiText.auditAssociations, language)}</span>
          <div className="chip-row">
            <span className="id-text">tool: {item.association.tool_name ?? "-"}</span>
            <span className="id-text">permission: {item.association.permission ?? "-"}</span>
            <span className="id-text">status: {item.association.status ?? "-"}</span>
            <span className="id-text">latency_ms: {formatValue(item.association.latency_ms)}</span>
          </div>
        </div>
      )}
    </CardInset>
  );
}

function Field({ label, value }: Readonly<{ label: string; value: unknown }>) {
  return (
    <div>
      <span className="scope-label">{label}</span>
      <span className="id-text wrap">{formatValue(value)}</span>
    </div>
  );
}

function SafeMap({ label, value }: Readonly<{ label: string; value: Record<string, number | string> | undefined }>) {
  const entries = Object.entries(value ?? {});
  if (entries.length === 0) {
    return null;
  }
  return (
    <div>
      <span className="scope-label">{label}</span>
      <div className="chip-row">
        {entries.map(([key, entryValue]) => (
          <span key={key} className="id-text">
            {key}: {entryValue}
          </span>
        ))}
      </div>
    </div>
  );
}

function setFilter(
  setFilters: React.Dispatch<React.SetStateAction<AuditFilters>>,
  key: keyof AuditFilters,
  value: string
) {
  setFilters((current) => ({ ...current, [key]: value }));
}

function filtersToQuery(filters: AuditFilters): AuditLogQuery {
  return {
    user_id: filters.user_id,
    request_id: filters.request_id,
    trace_id: filters.trace_id,
    action: filters.action,
    resource_type: filters.resource_type,
    resource_id: filters.resource_id,
    status: filters.status,
    created_at_from: normalizeDateTimeFilter(filters.created_at_from),
    created_at_to: normalizeDateTimeFilter(filters.created_at_to),
    limit: Number.parseInt(filters.limit, 10),
    include_associations: true
  };
}

function normalizeDateTimeFilter(value: string): string {
  const trimmed = value.trim();
  if (trimmed.length === 0) {
    return "";
  }
  return trimmed.includes("T") ? trimmed : trimmed.replace(" ", "T");
}

function formatValue(value: unknown): string {
  if (value === undefined || value === null || value === "") {
    return "-";
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}
