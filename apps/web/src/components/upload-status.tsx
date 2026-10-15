"use client";

import { documentStatusPollDelay, useDocumentStatus } from "@/hooks/use-document-status";
import type { UploadDocumentResult } from "@/lib/api/types";
import type { AuthSession } from "@/lib/auth";
import type { Language } from "@/lib/i18n";
import { text } from "@/lib/i18n";
import { CopyIdButton, SafeErrorBanner, StatusPill } from "./primitives";
import { Button } from "./ui/button";
import { CardInset } from "./ui/card";

const copy = {
  queued: { en: "Queued", zh: "已排队" },
  processing: { en: "Processing", zh: "处理中" },
  ready: { en: "Ready for retrieval", zh: "可用于检索" },
  retryable: { en: "Retryable failure", zh: "可重试失败" },
  terminal: { en: "Processing stopped", zh: "处理已停止" },
  deleted: { en: "Deleted", zh: "已删除" },
  unknown: { en: "Status not recognized", zh: "状态未知" },
  loading: { en: "Checking indexing status…", zh: "正在读取索引状态…" },
  polling: { en: "Indexing status updates automatically while this view is open.", zh: "此视图打开时会自动更新索引状态。" },
  attempts: { en: "Attempts", zh: "已尝试次数" },
  retryAt: { en: "Next retry", zh: "下次重试" },
  retryableHelp: { en: "The backend marked this failure retryable. This view checks for updates; it does not start a retry.", zh: "后端将此失败标记为可重试。此视图只检查状态，不会发起重试。" },
  terminalHelp: { en: "Automatic checks have stopped. Share the IDs with a knowledge manager to investigate.", zh: "已停止自动检查。可将下方 ID 提供给知识管理员排查。" },
  permission: { en: "Live indexing status requires document:manage permission. The upload was accepted.", zh: "上传已接收；实时索引状态需要 document:manage 权限。" },
  unavailable: { en: "Status could not be verified. Automatic checks are paused; refresh status to check again.", zh: "无法确认当前状态，已暂停自动检查。可刷新状态后重新检查。" },
  refreshing: { en: "Retrying the status check…", zh: "正在重新检查状态…" },
  refresh: { en: "Refresh status", zh: "刷新状态" },
  noJob: { en: "No new job", zh: "未创建新任务" },
  safeSummary: { en: "Safe error summary", zh: "安全错误摘要" }
} as const;

function statusLabel(status: string, language: Language): string {
  if (status === "queued" || status === "uploaded") return text(copy.queued, language);
  if (status === "retrieval_ready") return text(copy.ready, language);
  if (status === "failed_retryable") return text(copy.retryable, language);
  if (status === "failed_terminal") return text(copy.terminal, language);
  if (status === "deleted") return text(copy.deleted, language);
  if (["processing", "parsing", "parsed", "chunking", "chunked", "embedding", "indexing"].includes(status)) {
    return text(copy.processing, language);
  }
  return text(copy.unknown, language);
}

function safeCode(value: unknown): string | null {
  return typeof value === "string" && /^[A-Z][A-Z0-9_]{0,79}$/.test(value) ? value : null;
}

export function UploadStatusCard({ auth, result, language }: Readonly<{
  auth: AuthSession;
  result: UploadDocumentResult;
  language: Language;
}>) {
  const statusQuery = useDocumentStatus(auth, result);
  const data = statusQuery.data;
  const status = data?.deleted_at != null ? "deleted" : data?.status ?? result.status;
  const jobId = data?.job_id ?? result.job_id;
  const terminal = status === "failed_terminal" || status === "deleted";
  const failure = terminal || status === "failed_retryable";
  const errorCode = safeCode(data?.error_code);
  // The status DTO's safe summary currently contains only error_code. Do not
  // stringify arbitrary provider exceptions or newly added backend fields.
  const summaryCode = safeCode(data?.error_summary?.error_code);

  return (
    <CardInset aria-label={language === "zh" ? "上传索引状态" : "Upload indexing status"} aria-live="polite">
      <div className="actions-row">
        <StatusPill tone={failure ? "danger" : status === "retrieval_ready" ? "source" : "index"}>
          {statusLabel(status, language)}
        </StatusPill>
        <span className="id-text">status: {status}</span>
      </div>
      <div className="chip-row">
        <span className="id-text">document_id: {result.document_id}</span>
        <span className="id-text">version_id: {result.version_id}</span>
        <span className="id-text">job_id: {jobId ?? text(copy.noJob, language)}</span>
      </div>
      {jobId != null && <CopyIdButton value={jobId} label="Copy job_id" language={language} />}
      {!statusQuery.canReadStatus && <p className="muted">{text(copy.permission, language)}</p>}
      {statusQuery.canReadStatus && statusQuery.isPending && <p role="status">{text(copy.loading, language)}</p>}
      {statusQuery.failureCount > 0 && statusQuery.isFetching && <p role="status">{text(copy.refreshing, language)}</p>}
      {data?.attempt_count != null && <span>{text(copy.attempts, language)}: {data.attempt_count}</span>}
      {data?.next_retry_at != null && (
        <span>{text(copy.retryAt, language)}: <time dateTime={data.next_retry_at}>{data.next_retry_at}</time></span>
      )}
      {status === "failed_retryable" && <p>{text(copy.retryableHelp, language)}</p>}
      {terminal && <p>{text(copy.terminalHelp, language)}</p>}
      {errorCode !== null && <span className="id-text">error_code: {errorCode}</span>}
      {summaryCode !== null && <span>{text(copy.safeSummary, language)}: {summaryCode}</span>}
      {data?.request_id && <span className="id-text">request_id: {data.request_id}</span>}
      {data?.trace_id && <span className="id-text">trace_id: {data.trace_id}</span>}
      {statusQuery.isError && <SafeErrorBanner code="DOCUMENT_STATUS_UNAVAILABLE" message={text(copy.unavailable, language)} />}
      {statusQuery.canReadStatus && documentStatusPollDelay(data) !== false && !statusQuery.isError && !statusQuery.isPending && (
        <span className="muted">{text(copy.polling, language)}</span>
      )}
      {statusQuery.canReadStatus && statusQuery.isError && (
        <Button type="button" variant="secondary" disabled={statusQuery.isFetching} onClick={() => void statusQuery.refetch()}>
          {text(copy.refresh, language)}
        </Button>
      )}
    </CardInset>
  );
}
