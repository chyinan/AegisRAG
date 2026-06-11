"use client";

import { useMutation } from "@tanstack/react-query";
import { Bot, FileCheck2, ListFilter, Play, ShieldCheck, SlidersHorizontal } from "lucide-react";
import type React from "react";
import { useMemo, useState } from "react";
import { listEvalReports, listReviewItems, runAgent } from "@/lib/api/client";
import type {
  AgentRunResponse,
  EvalEvidenceReportListResponse,
  EvalEvidenceReportSummary,
  ReviewItemQuery,
  ReviewItemSummary,
  ReviewQueueListResponse
} from "@/lib/api/types";
import type { AuthSession } from "@/lib/auth";
import type { Language } from "@/lib/i18n";
import { text, uiText } from "@/lib/i18n";
import { CopyIdButton, PermissionNotice, SafeErrorBanner, StatusPill } from "./primitives";
import { Button } from "./ui/button";
import { Card, CardHeader, CardInset } from "./ui/card";
import { Input } from "./ui/input";
import { Select } from "./ui/select";
import { Textarea } from "./ui/textarea";

type ReviewFilters = {
  request_id: string;
  trace_id: string;
  item_type: string;
  severity: string;
  status: string;
  source_view: string;
  limit: string;
};

const DEFAULT_REVIEW_FILTERS: ReviewFilters = {
  request_id: "",
  trace_id: "",
  item_type: "",
  severity: "",
  status: "",
  source_view: "",
  limit: "50"
};

export function ReviewPanel({
  auth,
  language,
  canRead
}: Readonly<{ auth: AuthSession; language: Language; canRead: boolean }>) {
  const [filters, setFilters] = useState<ReviewFilters>(DEFAULT_REVIEW_FILTERS);
  const query = useMemo(() => reviewFiltersToQuery(filters), [filters]);
  const review = useMutation<ReviewQueueListResponse, Error, ReviewItemQuery>({
    mutationFn: (nextQuery) => listReviewItems(auth, nextQuery)
  });

  if (!canRead) {
    return <PermissionNotice permission="review:read" language={language} />;
  }

  return (
    <Card as="section" aria-labelledby="review-queue-title">
      <CardHeader>
        <div>
          <h2 id="review-queue-title" className="surface-title">
            {text(uiText.reviewQueueTitle, language)}
          </h2>
          <p className="muted">{text(uiText.reviewQueueHelp, language)}</p>
        </div>
        <StatusPill tone="source">review:read</StatusPill>
      </CardHeader>
      <CardInset className="bg-[#f7fbfa] shadow-[inset_3px_0_0_var(--source),inset_0_0_0_1px_rgb(20_122_103_/_0.12)]">
        <div className="actions-row">
          <ShieldCheck aria-hidden="true" />
          <strong>{text(uiText.backendFactsOnly, language)}</strong>
        </div>
        <span className="muted">{text(uiText.backendFactsCopy, language)}</span>
      </CardInset>
      <form
        className="grid gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          review.mutate(query);
        }}
      >
        <span className="scope-label">{text(uiText.auditFilters, language)}</span>
        <div className="two-col">
          <LabeledInput label="request_id" value={filters.request_id} onChange={(value) => setReviewFilter(setFilters, "request_id", value)} />
          <LabeledInput label="trace_id" value={filters.trace_id} onChange={(value) => setReviewFilter(setFilters, "trace_id", value)} />
          <LabeledInput label="item_type" value={filters.item_type} onChange={(value) => setReviewFilter(setFilters, "item_type", value)} />
          <label>
            <span className="scope-label">severity</span>
            <Select value={filters.severity} onChange={(event) => setReviewFilter(setFilters, "severity", event.target.value)}>
              <option value="">any</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
              <option value="critical">critical</option>
            </Select>
          </label>
          <label>
            <span className="scope-label">status</span>
            <Select value={filters.status} onChange={(event) => setReviewFilter(setFilters, "status", event.target.value)}>
              <option value="">any</option>
              <option value="open">open</option>
              <option value="accepted">accepted</option>
              <option value="rejected">rejected</option>
              <option value="needs_followup">needs_followup</option>
              <option value="converted_to_eval_case">converted_to_eval_case</option>
            </Select>
          </label>
          <LabeledInput label="source_view" value={filters.source_view} onChange={(value) => setReviewFilter(setFilters, "source_view", value)} />
          <LabeledInput label="limit" value={filters.limit} inputMode="numeric" onChange={(value) => setReviewFilter(setFilters, "limit", value)} />
        </div>
        <Button type="submit" variant="primary" disabled={review.isPending}>
          <ListFilter aria-hidden="true" />
          {text(uiText.loadReviewItems, language)}
        </Button>
      </form>
      {review.isError && <SafeErrorBanner message={text(uiText.reviewError, language)} />}
      {review.data !== undefined && (
        <ResultList
          emptyText={text(uiText.noReviewItems, language)}
          nextSteps={review.data.next_steps}
          items={review.data.items}
          render={(item) => <ReviewItemRow key={item.id} item={item} />}
        />
      )}
    </Card>
  );
}

export function EvalPanel({
  auth,
  language,
  canRead
}: Readonly<{ auth: AuthSession; language: Language; canRead: boolean }>) {
  const [limit, setLimit] = useState("20");
  const reports = useMutation<EvalEvidenceReportListResponse, Error, number>({
    mutationFn: (nextLimit) => listEvalReports(auth, nextLimit)
  });

  if (!canRead) {
    return <PermissionNotice permission="eval:read" language={language} />;
  }

  return (
    <Card as="section" aria-labelledby="eval-evidence-title">
      <CardHeader>
        <div>
          <h2 id="eval-evidence-title" className="surface-title">
            {text(uiText.evalEvidenceTitle, language)}
          </h2>
          <p className="muted">{text(uiText.evalEvidenceHelp, language)}</p>
        </div>
        <StatusPill tone="source">eval:read</StatusPill>
      </CardHeader>
      <div className="two-col">
        <LabeledInput label="limit" value={limit} inputMode="numeric" onChange={setLimit} />
      </div>
      <Button type="button" variant="primary" disabled={reports.isPending} onClick={() => reports.mutate(Number.parseInt(limit, 10))}>
        <FileCheck2 aria-hidden="true" />
        {text(uiText.loadEvalReports, language)}
      </Button>
      {reports.isError && <SafeErrorBanner message={text(uiText.evalError, language)} />}
      {reports.data !== undefined && (
        <ResultList
          emptyText={text(uiText.noEvalReports, language)}
          nextSteps={reports.data.next_steps}
          items={reports.data.items}
          render={(item) => <EvalReportRow key={item.report_filename} item={item} />}
        />
      )}
    </Card>
  );
}

export function AgentPanel({
  auth,
  language,
  canRun
}: Readonly<{ auth: AuthSession; language: Language; canRun: boolean }>) {
  const [input, setInput] = useState("");
  const [maxSteps, setMaxSteps] = useState("8");
  const [maxToolCalls, setMaxToolCalls] = useState("4");
  const [timeoutSeconds, setTimeoutSeconds] = useState("30");
  const agent = useMutation<AgentRunResponse, Error>({
    mutationFn: () =>
      runAgent(auth, {
        input,
        max_steps: Number.parseInt(maxSteps, 10),
        max_tool_calls: Number.parseInt(maxToolCalls, 10),
        timeout_seconds: Number.parseInt(timeoutSeconds, 10),
        metadata: { surface: "main_workbench" }
      })
  });

  if (!canRun) {
    return <PermissionNotice permission="agent:run" language={language} />;
  }

  return (
    <Card as="section" aria-labelledby="agent-console-title">
      <CardHeader>
        <div>
          <h2 id="agent-console-title" className="surface-title">
            {text(uiText.agentConsoleTitle, language)}
          </h2>
          <p className="muted">{text(uiText.agentConsoleHelp, language)}</p>
        </div>
        <StatusPill tone="index">agent:run</StatusPill>
      </CardHeader>
      <label>
        <span className="scope-label">{text(uiText.agentInput, language)}</span>
        <Textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder={text(uiText.agentInputPlaceholder, language)}
        />
      </label>
      <div className="two-col">
        <LabeledInput label="max_steps" value={maxSteps} inputMode="numeric" onChange={setMaxSteps} />
        <LabeledInput label="max_tool_calls" value={maxToolCalls} inputMode="numeric" onChange={setMaxToolCalls} />
        <LabeledInput label="timeout_seconds" value={timeoutSeconds} inputMode="numeric" onChange={setTimeoutSeconds} />
      </div>
      <Button type="button" variant="primary" disabled={agent.isPending || input.trim().length === 0} onClick={() => agent.mutate()}>
        <Play aria-hidden="true" />
        {text(uiText.runAgent, language)}
      </Button>
      {agent.isError && <SafeErrorBanner message={text(uiText.agentError, language)} />}
      {agent.data !== undefined && <AgentRunResult result={agent.data} language={language} />}
    </Card>
  );
}

export function SettingsPanel({ auth, language }: Readonly<{ auth: AuthSession; language: Language }>) {
  return (
    <Card as="section" aria-labelledby="identity-boundaries-title">
      <CardHeader>
        <div>
          <h2 id="identity-boundaries-title" className="surface-title">
            {text(uiText.identityBoundariesTitle, language)}
          </h2>
          <p className="muted">{text(uiText.identityBoundariesHelp, language)}</p>
        </div>
        <StatusPill tone="source">admin:settings</StatusPill>
      </CardHeader>
      <CardInset className="bg-[#f7fbfa] shadow-[inset_3px_0_0_var(--source),inset_0_0_0_1px_rgb(20_122_103_/_0.12)]">
        <div className="actions-row">
          <SlidersHorizontal aria-hidden="true" />
          <strong>{text(uiText.noPermissionExpansion, language)}</strong>
        </div>
      </CardInset>
      <div className="two-col">
        <SafeField label="mode" value={auth.mode} />
        <SafeField label="tenant_id" value={auth.tenantId ?? "JWT"} />
        <SafeField label="user_id" value={auth.userId ?? "JWT subject"} />
        <SafeField label="department" value={auth.department ?? "-"} />
      </div>
      <CardInset>
        <span className="scope-label">{text(uiText.currentRoles, language)}</span>
        <div className="chip-row">
          {auth.roles.map((role) => (
            <span key={role} className="id-text">{role}</span>
          ))}
        </div>
      </CardInset>
      <CardInset>
        <span className="scope-label">{text(uiText.currentPermissions, language)}</span>
        <div className="chip-row">
          {auth.permissions.map((permission) => (
            <span key={permission} className="id-text">{permission}</span>
          ))}
        </div>
      </CardInset>
    </Card>
  );
}

function ReviewItemRow({ item }: Readonly<{ item: ReviewItemSummary }>) {
  return (
    <CardInset as="article">
      <div className="actions-row">
        <StatusPill tone={item.severity === "critical" || item.severity === "high" ? "danger" : "index"}>
          {item.item_type} · {item.status}
        </StatusPill>
        <span className="id-text">{item.severity}</span>
      </div>
      <div className="two-col">
        <SafeField label="review_id" value={item.id} />
        <SafeField label="source_view" value={item.source_view} />
        <SafeField label="request_id" value={item.request_id} />
        <SafeField label="trace_id" value={item.trace_id} />
        <SafeField label="created_by" value={item.created_by} />
        <SafeField label="updated_at" value={item.updated_at} />
      </div>
      <SafeMap label="safe_identifiers" value={item.safe_identifiers} />
      <SafeMap label="safe_summary" value={item.safe_summary} />
    </CardInset>
  );
}

function EvalReportRow({ item }: Readonly<{ item: EvalEvidenceReportSummary }>) {
  return (
    <CardInset as="article">
      <div className="actions-row">
        <StatusPill tone={item.decision === "pass" || item.decision === "passed" ? "source" : "index"}>
          {item.report_type} · {item.decision}
        </StatusPill>
        <span className="id-text">{item.report_filename}</span>
      </div>
      <div className="two-col">
        <SafeField label="dataset" value={item.dataset_name} />
        <SafeField label="generated_at" value={item.generated_at} />
        <SafeField label="cases" value={item.case_count} />
        <SafeField label="passed" value={item.passed_count} />
        <SafeField label="failed" value={item.failed_count} />
        <SafeField label="retrieval_hit_rate" value={item.retrieval_hit_rate} />
        <SafeField label="citation_coverage" value={item.citation_coverage} />
        <SafeField label="avg_latency_ms" value={item.average_latency_ms} />
      </div>
    </CardInset>
  );
}

function AgentRunResult({ result, language }: Readonly<{ result: AgentRunResponse; language: Language }>) {
  return (
    <CardInset>
      <div className="actions-row">
        <Bot aria-hidden="true" />
        <StatusPill tone={result.status === "completed" ? "source" : "index"}>
          {result.status} · {result.termination_reason ?? "run"}
        </StatusPill>
      </div>
      <div className="two-col">
        <SafeField label="agent_run_id" value={result.agent_run_id} />
        <SafeField label="request_id" value={result.request_id} />
        <SafeField label="trace_id" value={result.trace_id} />
        <SafeField label="steps_used" value={result.steps_used} />
        <SafeField label="tool_calls_used" value={result.tool_calls_used} />
        <SafeField label="error_code" value={result.error_code} />
      </div>
      {result.final_answer !== undefined && result.final_answer !== null && <p>{result.final_answer}</p>}
      <div className="actions-row">
        <CopyIdButton value={result.request_id} label={text(uiText.copyRequestId, language)} language={language} />
      </div>
    </CardInset>
  );
}

function ResultList<T>({
  items,
  emptyText,
  nextSteps,
  render
}: Readonly<{ items: T[]; emptyText: string; nextSteps?: string[] | null; render: (item: T) => React.ReactNode }>) {
  return (
    <section className="grid gap-3">
      {items.length === 0 ? <CardInset><span className="muted">{emptyText}</span></CardInset> : items.map(render)}
      {(nextSteps ?? []).length > 0 && (
        <CardInset>
          <span className="scope-label">next_steps</span>
          <ul>
            {(nextSteps ?? []).map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ul>
        </CardInset>
      )}
    </section>
  );
}

function LabeledInput({
  label,
  value,
  onChange,
  inputMode
}: Readonly<{
  label: string;
  value: string;
  onChange: (value: string) => void;
  inputMode?: React.HTMLAttributes<HTMLInputElement>["inputMode"];
}>) {
  return (
    <label>
      <span className="scope-label">{label}</span>
      <Input aria-label={label} inputMode={inputMode} value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function SafeField({ label, value }: Readonly<{ label: string; value: unknown }>) {
  return (
    <div>
      <span className="scope-label">{label}</span>
      <span className="id-text wrap">{formatValue(value)}</span>
    </div>
  );
}

function SafeMap({ label, value }: Readonly<{ label: string; value: Record<string, unknown> | undefined }>) {
  const entries = Object.entries(value ?? {});
  if (entries.length === 0) {
    return null;
  }
  return (
    <div>
      <span className="scope-label">{label}</span>
      <div className="chip-row">
        {entries.map(([key, item]) => (
          <span key={key} className="id-text">{key}: {formatValue(item)}</span>
        ))}
      </div>
    </div>
  );
}

function setReviewFilter(
  setFilters: React.Dispatch<React.SetStateAction<ReviewFilters>>,
  key: keyof ReviewFilters,
  value: string
) {
  setFilters((current) => ({ ...current, [key]: value }));
}

function reviewFiltersToQuery(filters: ReviewFilters): ReviewItemQuery {
  return {
    request_id: filters.request_id,
    trace_id: filters.trace_id,
    item_type: filters.item_type,
    severity: filters.severity,
    status: filters.status,
    source_view: filters.source_view,
    limit: Number.parseInt(filters.limit, 10)
  };
}

function formatValue(value: unknown): string {
  if (value === undefined || value === null || value === "") {
    return "-";
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.map((item) => formatValue(item)).join(", ");
  }
  return JSON.stringify(value);
}
