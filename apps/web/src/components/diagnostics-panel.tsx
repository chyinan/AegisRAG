"use client";

import { useMutation } from "@tanstack/react-query";
import { Gauge } from "lucide-react";
import { resolveDiagnostics } from "@/lib/api/client";
import type { DiagnosticsTimeline } from "@/lib/api/types";
import type { AuthSession } from "@/lib/auth";
import { CopyIdButton, SafeErrorBanner, StatusPill } from "./primitives";

export function DiagnosticsPanel({
  auth,
  requestId,
  traceId,
  canRead
}: Readonly<{ auth: AuthSession; requestId?: string | null; traceId?: string | null; canRead: boolean }>) {
  const mutation = useMutation<DiagnosticsTimeline, Error>({
    mutationFn: () => resolveDiagnostics(auth, { request_id: requestId ?? undefined, trace_id: traceId ?? undefined })
  });

  if (!canRead) {
    return (
      <div className="evidence-body">
        <StatusPill tone="danger">Diagnostics restricted</StatusPill>
        <p className="muted">当前身份缺少 diagnostics:read。可复制 request_id 给有权限的工程或管理员排查。</p>
        <CopyIdButton value={requestId} label="Copy request_id" />
      </div>
    );
  }

  return (
    <div className="timeline">
      <div className="actions-row">
        <Gauge aria-hidden="true" />
        <strong>Safe retrieval timeline</strong>
      </div>
      <div className="two-col">
        <label>
          <span className="scope-label">request_id</span>
          <input className="field" value={requestId ?? ""} readOnly />
        </label>
        <label>
          <span className="scope-label">trace_id</span>
          <input className="field" value={traceId ?? ""} readOnly />
        </label>
      </div>
      <button
        type="button"
        className="primary-button"
        disabled={(requestId ?? traceId ?? "").length === 0 || mutation.isPending}
        onClick={() => mutation.mutate()}
      >
        Resolve diagnostics
      </button>
      {mutation.isError && <SafeErrorBanner message="无法解析诊断摘要；不会显示 raw query、prompt 或 chunk content。" />}
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
    </div>
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
