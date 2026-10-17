"use client";

import { useState } from "react";
import { useOperatorRequest } from "@/hooks/use-operator-request";
import { authScopeKey } from "@/lib/api/auth-scope";
import { configureFolderPlan, listFolderCandidates, listFolderPlans, listQueueJobs, operateQueueJob, reconcileFolderCandidates, runFolderBatch, type FolderCandidates, type FolderPlan, type QueueItem, type QueuePage, type QueueResult } from "@/lib/api/operations-client";
import { hasPermission, type AuthSession } from "@/lib/auth";
import type { Language } from "@/lib/i18n";
import { PermissionNotice, StatusPill } from "./primitives";
import { OperationCard, OperationFeedback, OperationField, OperationInput, operationText as t } from "./operator-ui";
import { Button } from "./ui/button";
import { CardInset } from "./ui/card";
import { Select } from "./ui/select";
type Props = Readonly<{
  auth: AuthSession;
  language: Language;
  onChanged?: () => void;
}>;
export function FolderSyncPanel(props: Props) {
  return <FolderSyncContent key={authScopeKey(props.auth)} {...props} />;
}
function FolderSyncContent({
  auth,
  language,
  onChanged
}: Props) {
  const op = useOperatorRequest();
  const [plans, setPlans] = useState<FolderPlan[] | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [page, setPage] = useState<FolderCandidates | null>(null);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [confirmation, setConfirmation] = useState<{
    id: string;
    revision: number;
    ids: string[];
  } | null>(null);
  const [interval, setInterval] = useState("300");
  const [batchSize, setBatchSize] = useState("25");
  const [enabled, setEnabled] = useState(true);
  const [deleteMissing, setDeleteMissing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  if (!hasPermission(auth, "document:manage")) return <PermissionNotice permission="document:manage" language={language} />;
  const plan = plans?.find(item => item.id === selectedId);
  const validConfig = /^\d+$/.test(interval) && Number(interval) >= 30 && Number(interval) <= 86400 && /^\d+$/.test(batchSize) && Number(batchSize) >= 1 && Number(batchSize) <= 100;
  function clearCandidates() {
    setPage(null);
    setSelected([]);
    setConfirmation(null);
    setOffset(0);
  }
  function applyPlan(next: FolderPlan) {
    setPlans(current => [...(current ?? []).filter(item => item.id !== next.id), next]);
    setSelectedId(next.id);
    setInterval(String(next.interval_seconds));
    setBatchSize(String(next.batch_size));
    setEnabled(next.enabled);
    setDeleteMissing(next.delete_missing);
  }
  function refresh() {
    if (op.busy) return;
    clearCandidates();
    setPlans(null);
    setNotice(null);
    void op.execute(signal => listFolderPlans(auth, signal), items => {
      setPlans(items);
      const next = items.find(item => item.id === selectedId) ?? items[0];
      if (next) applyPlan(next);else setSelectedId("");
    });
  }
  function loadCandidates(nextOffset: number) {
    if (op.busy || !plan) return;
    clearCandidates();
    void op.execute(signal => listFolderCandidates(auth, plan.id, nextOffset, signal), next => {
      // A newly scanned revision must also be visible in the selected plan.
      setPlans(current => (current ?? []).map(item => item.id === plan.id ? {
        ...item,
        revision: next.revision,
        candidate_count: next.total
      } : item));
      setPage(next);
      setOffset(nextOffset);
    });
  }
  function reconcile() {
    if (op.busy || !confirmation || !page || !plan || !plan.delete_missing || plan.status !== "completed" || confirmation.id !== plan.id || confirmation.revision !== page.revision || confirmation.ids.join("\n") !== selected.join("\n")) return;
    const confirmed = confirmation;
    clearCandidates();
    setPlans(null);
    setNotice(null);
    void op.execute(signal => reconcileFolderCandidates(auth, confirmed.id, confirmed.revision, confirmed.ids, signal), next => {
      applyPlan(next);
      setNotice(t(language, "Selected documents soft-deleted. Reload candidates for the current revision.", "所选文档已软删除。请重新读取当前修订的候选项。"));
    }, 315_000).then(() => onChanged?.());
  }
  return <OperationCard title={t(language, "Folder synchronization", "文件夹持续同步")} help={t(language, "Uses the server-configured folder root. Scans only propose missing-file deletion candidates; removal requires selecting and confirming exact documents.", "使用服务端配置的文件夹根目录。扫描仅产生缺失文件候选项，必须选择并确认具体文档后才会软删除。 ")} permission="document:manage">
    <Button type="button" variant="secondary" disabled={op.busy} onClick={refresh}>{t(language, "Refresh sync plans", "刷新同步计划")}</Button>
    <form className="grid gap-3" onSubmit={event => {
      event.preventDefault();
      if (op.busy || !validConfig) return;
      clearCandidates();
      setPlans(null);
      setNotice(null);
      void op.execute(signal => configureFolderPlan(auth, {
        enabled,
        interval_seconds: Number(interval),
        batch_size: Number(batchSize),
        delete_missing: deleteMissing
      }, signal), applyPlan);
    }}>
      <div className="two-col"><OperationInput label={t(language, "Sync interval (seconds)", "同步间隔（秒）")} value={interval} type="number" min={30} max={86400} disabled={op.busy} onChange={setInterval} /><OperationInput label={t(language, "Sync batch size", "同步批次大小")} value={batchSize} type="number" min={1} max={100} disabled={op.busy} onChange={setBatchSize} /></div>
      <label><input type="checkbox" checked={enabled} disabled={op.busy} onChange={event => setEnabled(event.target.checked)} /> {t(language, "Enable scheduled synchronization", "启用定时同步")}</label>
      <label><input type="checkbox" checked={deleteMissing} disabled={op.busy} onChange={event => setDeleteMissing(event.target.checked)} /> {t(language, "Collect missing-file deletion candidates", "收集缺失文件删除候选项")}</label>
      <Button type="submit" disabled={op.busy || !validConfig}>{t(language, "Save configured-root sync plan", "保存配置根目录同步计划")}</Button>
    </form>
    <OperationFeedback {...op} language={language} />
    {notice && <p role="status">{notice}</p>}
    {plans?.length === 0 && <p>{t(language, "No sync plans found. Save a plan to begin.", "尚无同步计划。保存计划以开始。")}</p>}
    {plans && plans.length > 0 && <label><span className="scope-label">{t(language, "Sync plan", "同步计划")}</span><Select aria-label={t(language, "Sync plan", "同步计划")} value={selectedId} disabled={op.busy} onChange={event => {
        clearCandidates();
        setNotice(null);
        const next = plans.find(item => item.id === event.target.value);
        if (next) applyPlan(next);
      }}>{plans.map(item => <option key={item.id} value={item.id}>{item.id}</option>)}</Select></label>}
    {plan && <CardInset><div className="actions-row"><StatusPill tone={plan.error_code ? "danger" : "source"}>{plan.status} · {plan.phase}</StatusPill><span className="id-text">{plan.id}</span></div>
      <div className="two-col">{Object.entries({
          revision: plan.revision,
          cycle: plan.cycle,
          enabled: String(plan.enabled),
          interval_seconds: plan.interval_seconds,
          batch_size: plan.batch_size,
          delete_missing: String(plan.delete_missing),
          imported: plan.imported_count,
          skipped: plan.skipped_count,
          deleted: plan.deleted_count,
          managed: plan.managed_count,
          candidates: plan.candidate_count,
          error_code: plan.error_code ?? "none",
          next_run_at_unix_seconds: String(plan.next_run_at)
        }).map(([label, value]) => <OperationField key={label} label={label} value={value} />)}</div>
      <div className="actions-row"><Button type="button" disabled={op.busy} onClick={() => {
          clearCandidates();
          setPlans(null);
          setNotice(null);
          void op.execute(signal => runFolderBatch(auth, plan.id, signal), next => {
            applyPlan(next);
          }, 315_000).then(() => onChanged?.());
        }}>{t(language, "Run one sync batch", "执行一个同步批次")}</Button><Button type="button" variant="secondary" disabled={op.busy} onClick={() => loadCandidates(0)}>{t(language, "Load deletion candidates", "读取删除候选项")}</Button></div>
    </CardInset>}
    {page && <CardInset><p>{t(language, "Candidate revision", "候选项修订")} {page.revision} · {t(language, "Total", "总数")} {page.total}</p>{page.items.length === 0 && <p>{t(language, "No deletion candidates on this page.", "此页没有删除候选项。")}</p>}
      {page.items.map(item => <label key={item.document_id} className="grid gap-1"><span><input type="checkbox" aria-label={`Select ${item.document_id}`} disabled={op.busy} checked={selected.includes(item.document_id)} onChange={event => {
            setConfirmation(null);
            setSelected(current => event.target.checked ? [...current, item.document_id].sort() : current.filter(id => id !== item.document_id));
          }} /> <span className="id-text">{item.document_id} · {item.version_id}</span></span><span className="muted wrap">{item.source_uri}</span></label>)}
      <div className="actions-row"><Button type="button" variant="secondary" disabled={op.busy || offset === 0} onClick={() => loadCandidates(Math.max(0, offset - 25))}>{t(language, "Previous candidates", "上一页候选项")}</Button><Button type="button" variant="secondary" disabled={op.busy || page.next_offset === null} onClick={() => page.next_offset !== null && loadCandidates(page.next_offset)}>{t(language, "Next candidates", "下一页候选项")}</Button><Button type="button" disabled={op.busy || selected.length === 0 || !plan?.delete_missing || plan.status !== "completed"} onClick={() => setConfirmation({
          id: selectedId,
          revision: page.revision,
          ids: [...selected]
        })}>{t(language, "Review selected soft-deletions", "复核所选软删除")}</Button></div>
    </CardInset>}
    {confirmation && <CardInset role="group" aria-label={t(language, "Confirm selected soft-deletions", "确认所选软删除")}><p>{t(language, "Soft-delete exactly these documents and remove their indexed content from retrieval?", "是否软删除以下具体文档，并将其索引内容移出检索？")}</p><OperationField label="plan_id" value={confirmation.id} /><OperationField label="expected_revision" value={confirmation.revision} /><ul>{confirmation.ids.map(id => <li key={id} className="id-text">{id}</li>)}</ul><div className="actions-row"><Button type="button" variant="primary" disabled={op.busy} onClick={reconcile}>{t(language, "Confirm soft-delete selected documents", "确认软删除所选文档")}</Button><Button type="button" variant="secondary" disabled={op.busy} onClick={() => setConfirmation(null)}>{t(language, "Cancel deletion", "取消删除")}</Button></div></CardInset>}
  </OperationCard>;
}
export function QueueOperationsPanel(props: Props) {
  return <QueueOperationsContent key={authScopeKey(props.auth)} {...props} />;
}
function QueueOperationsContent({
  auth,
  language,
  onChanged
}: Props) {
  const op = useOperatorRequest();
  const [page, setPage] = useState<QueuePage | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<QueueItem | null>(null);
  const [result, setResult] = useState<QueueResult | null>(null);
  if (!hasPermission(auth, "document:manage")) return <PermissionNotice permission="document:manage" language={language} />;
  function load(nextCursor: string | null) {
    if (op.busy) return;
    setPage(null);
    setConfirmation(null);
    void op.execute(signal => listQueueJobs(auth, nextCursor, signal), next => {
      setPage(next);
      setCursor(nextCursor);
    });
  }
  function operate(item: QueueItem, action: "retry" | "terminate") {
    if (op.busy || !page?.items.some(row => row.queue_job_id === item.queue_job_id && row.job_kind === item.job_kind && row.attempt_count === item.attempt_count && row.queue_status === item.queue_status)) return;
    setPage(null);
    setConfirmation(null);
    setResult(null);
    void op.execute(signal => operateQueueJob(auth, item, action, signal), next => {
      setResult(next);
    }).then(() => onChanged?.());
  }
  return <OperationCard title={t(language, "Queue operations", "队列运维")} help={t(language, "Review failed, stale, stuck, or terminated deliveries. A retry consumes the existing RQ retry budget; it never resets domain attempts or replenishes retries.", "查看失败、过期、卡住或终止的投递。重试消耗现有 RQ 预算，不会重置领域尝试次数或补充重试次数。 ")} permission="document:manage">
    <Button type="button" variant="secondary" disabled={op.busy} onClick={() => load(null)}>{t(language, "Refresh queue jobs", "刷新队列任务")}</Button><OperationFeedback {...op} language={language} />
    {result && <CardInset role="status"><StatusPill tone={result.status === "terminated" ? "danger" : "index"}>{result.status}</StatusPill><OperationField label="queue_job_id" value={result.queue_job_id} /><OperationField label="attempt_count" value={result.attempt_count} /><OperationField label="retries_remaining" value={result.retries_remaining} /><OperationField label="request_id" value={result.request_id} /><OperationField label="trace_id" value={result.trace_id} /><p>{result.status === "stop_requested" ? t(language, "Stop requested. Termination is not confirmed; refresh to check the worker state.", "已请求停止，但尚未确认终止。请刷新以检查工作进程状态。") : t(language, "Operation confirmed by backend. Refresh the queue to load current delivery state.", "后端已确认操作。请刷新队列以读取当前投递状态。")}</p></CardInset>}
    {page && <><p className="muted">scanned_count: {page.scanned_count} · request_id: {page.request_id}</p>{page.items.length === 0 && <p>{t(language, "No attention jobs on this page.", "此页无待处理任务。")}</p>}{page.items.map(item => <CardInset key={`${item.job_kind}:${item.queue_job_id}`} as="article"><h3 className="id-text">{item.job_kind} · {item.queue_job_id}</h3><div className="two-col">{Object.entries({
            job_id: item.job_id,
            document_id: item.document_id,
            version_id: item.version_id,
            queue_name: item.queue_name,
            state: item.state,
            queue_status: item.queue_status,
            domain_status: item.domain_status,
            attempt_count: item.attempt_count,
            retries_remaining: item.retries_remaining,
            age_seconds: item.age_seconds,
            last_heartbeat_at: item.last_heartbeat_at ?? "unknown",
            error_code: item.error_code ?? "none"
          }).map(([label, value]) => <OperationField key={label} label={label} value={value} />)}</div><div className="actions-row"><Button type="button" aria-label={`Retry ${item.queue_job_id}`} disabled={op.busy || !item.retry_allowed || item.retries_remaining < 1} onClick={() => operate(item, "retry")}>{t(language, "Retry with remaining budget", "使用剩余预算重试")}</Button><Button type="button" variant="primary" aria-label={`Review termination ${item.queue_job_id}`} disabled={op.busy || !item.terminate_allowed} onClick={() => setConfirmation(item)}>{t(language, "Review termination", "复核终止")}</Button></div></CardInset>)}<div className="actions-row"><Button type="button" variant="secondary" disabled={op.busy || cursor === null} onClick={() => load(null)}>{t(language, "First queue page", "队列首页")}</Button><Button type="button" variant="secondary" disabled={op.busy || page.next_cursor === null} onClick={() => page.next_cursor !== null && load(page.next_cursor)}>{t(language, "Next queue page", "队列下一页")}</Button></div></>}
    {confirmation && <CardInset role="group" aria-label={t(language, "Confirm queue termination", "确认队列终止")}><p>{t(language, "Terminate this exact delivery? Running workers may only acknowledge a stop request.", "是否终止此具体投递？运行中的工作进程可能仅确认收到停止请求。")}</p><OperationField label="job_kind" value={confirmation.job_kind} /><OperationField label="queue_job_id" value={confirmation.queue_job_id} /><OperationField label="expected_attempt_count" value={confirmation.attempt_count} /><OperationField label="expected_queue_status" value={confirmation.queue_status} /><div className="actions-row"><Button type="button" variant="primary" disabled={op.busy} onClick={() => operate(confirmation, "terminate")}>{t(language, "Confirm terminate delivery", "确认终止投递")}</Button><Button type="button" variant="secondary" disabled={op.busy} onClick={() => setConfirmation(null)}>{t(language, "Cancel termination", "取消终止")}</Button></div></CardInset>}
  </OperationCard>;
}
