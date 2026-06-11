"use client";

import { useMutation } from "@tanstack/react-query";
import { Gauge } from "lucide-react";
import { resolveDiagnostics } from "@/lib/api/client";
import type { DiagnosticsTimeline } from "@/lib/api/types";
import type { AuthSession } from "@/lib/auth";
import type { Language } from "@/lib/i18n";
import { text, uiText } from "@/lib/i18n";
import { CopyIdButton, SafeErrorBanner, StatusPill } from "./primitives";
import { Button } from "./ui/button";
import { CardInset } from "./ui/card";
import { Input } from "./ui/input";

export function DiagnosticsPanel({
  auth,
  language,
  requestId,
  traceId,
  canRead
}: Readonly<{
  auth: AuthSession;
  language: Language;
  requestId?: string | null;
  traceId?: string | null;
  canRead: boolean;
}>) {
  const mutation = useMutation<DiagnosticsTimeline, Error>({
    mutationFn: () => resolveDiagnostics(auth, { request_id: requestId ?? undefined, trace_id: traceId ?? undefined })
  });

  if (!canRead) {
    return (
      <CardInset>
        <StatusPill tone="danger">{text(uiText.diagnosticsRestricted, language)}</StatusPill>
        <p className="muted">{text(uiText.diagnosticsRestrictedHelp, language)}</p>
        <CopyIdButton value={requestId} label={text(uiText.copyRequestId, language)} language={language} />
      </CardInset>
    );
  }

  return (
    <CardInset>
      <div className="actions-row">
        <Gauge aria-hidden="true" />
        <strong>{text(uiText.safeRetrievalTimeline, language)}</strong>
      </div>
      <div className="two-col">
        <label>
          <span className="scope-label">request_id</span>
          <Input value={requestId ?? ""} readOnly />
        </label>
        <label>
          <span className="scope-label">trace_id</span>
          <Input value={traceId ?? ""} readOnly />
        </label>
      </div>
      <Button
        type="button"
        variant="primary"
        disabled={(requestId ?? traceId ?? "").length === 0 || mutation.isPending}
        onClick={() => mutation.mutate()}
      >
        {text(uiText.resolveDiagnostics, language)}
      </Button>
      {mutation.isError && <SafeErrorBanner message={text(uiText.diagnosticsError, language)} />}
      {mutation.data !== undefined && (
        <>
          <TimelineRow label="top_k" value={mutation.data.top_k} />
          <TimelineRow label="result_count" value={mutation.data.result_count} />
          <TimelineRow label="highest_rerank_score" value={mutation.data.highest_rerank_score} />
          <TimelineRow label="citation_count" value={mutation.data.citation_count} />
          <TimelineRow label="latency_ms" value={mutation.data.latency_ms} />
          <TimelineRow label="failure_stage" value={mutation.data.failure_stage} />
          <TimelineRow label="error_code" value={mutation.data.error_code} />
          <div>
            <span className="scope-label">next_steps</span>
            <ul>
              {(mutation.data.next_steps ?? ["No safe next steps returned."]).map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ul>
          </div>
        </>
      )}
    </CardInset>
  );
}

function TimelineRow({ label, value }: Readonly<{ label: string; value: unknown }>) {
  return (
    <div className="timeline-item">
      <span className="scope-label">{label}</span>
      <span className="id-text">{formatTimelineValue(value)}</span>
    </div>
  );
}

function formatTimelineValue(value: unknown): string {
  if (value === undefined || value === null) {
    return "-";
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}
