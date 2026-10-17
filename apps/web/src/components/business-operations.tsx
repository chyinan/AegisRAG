"use client";

import { useState } from "react";
import { useOperatorRequest } from "@/hooks/use-operator-request";
import { authScopeKey } from "@/lib/api/auth-scope";
import { listManagedTickets, listOwnedTickets, loadBudgetSummary, loadCostSummary, transitionTicket, type BudgetSummary, type CostSummary, type SupportTicket } from "@/lib/api/operations-client";
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
}>;
export function CostPanel(props: Props) {
  return <CostContent key={authScopeKey(props.auth)} {...props} />;
}
function CostContent({
  auth,
  language
}: Props) {
  const op = useOperatorRequest();
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [data, setData] = useState<{
    summary: CostSummary;
    budget: BudgetSummary;
  } | null>(null);
  if (!hasPermission(auth, "cost:read") && !hasPermission(auth, "audit:read")) return <PermissionNotice permission="cost:read / audit:read" language={language} />;
  const validDates = (!start || /^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(start) && Number.isFinite(Date.parse(start))) && (!end || /^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(end) && Number.isFinite(Date.parse(end))) && (!start || !end || Date.parse(start) < Date.parse(end));
  return <OperationCard title={t(language, "Cost and budget", "成本与预算")} help={t(language, "Model-call accounting with configured versioned prices. Partial, unknown usage and active holds remain visible; cost per success is not answer cost.", "使用配置的版本化价格统计模型调用。展示不完整数据、未知用量和活跃预留，单次成功调用成本不等同于回答成本。 ")} permission="cost:read / audit:read">
    <form className="grid gap-3" onSubmit={event => {
      event.preventDefault();
      if (op.busy || !validDates) return;
      setData(null);
      void op.execute(async signal => {
        const [summary, budget] = await Promise.all([loadCostSummary(auth, start, end, signal), loadBudgetSummary(auth, signal)]);
        return {
          summary,
          budget
        };
      }, setData);
    }}><div className="two-col"><OperationInput label={t(language, "Cost start (ISO with timezone)", "成本开始（含时区 ISO）")} value={start} disabled={op.busy} onChange={setStart} /><OperationInput label={t(language, "Cost end (ISO with timezone)", "成本结束（含时区 ISO）")} value={end} disabled={op.busy} onChange={setEnd} /></div><p className="muted">{t(language, "Leave dates empty for the backend default window. Example: 2026-10-01T00:00:00Z. Budget uses its own displayed period.", "留空使用后端默认时间范围。示例：2026-10-01T00:00:00Z。预算使用下方独立显示的周期。 ")}</p>{!validDates && <p role="alert">{t(language, "Use valid timezone-aware ISO dates with start before end.", "请使用含时区的有效 ISO 日期，开始时间须早于结束时间。 ")}</p>}<Button type="submit" disabled={op.busy || !validDates}>{t(language, "Refresh costs and budget", "刷新成本与预算")}</Button></form>
    <OperationFeedback {...op} language={language} />
    {data && <><CardInset><h3>{t(language, "Cost summary", "成本汇总")}</h3><StatusPill tone={data.summary.cost_complete ? "source" : "danger"}>{data.summary.cost_complete ? t(language, "Complete cost coverage", "成本统计完整") : t(language, "Partial cost coverage", "成本统计不完整")}</StatusPill><p>{t(language, "Unknown usage and unpriced calls are not zero-cost calls. Priced cost is only the known portion when coverage is partial.", "未知用量和未定价调用不代表零成本。当统计不完整时，已定价成本仅是已知部分。 ")}</p><div className="two-col">{Object.entries({
            currency: data.summary.currency,
            start: data.summary.start,
            end: data.summary.end,
            calls: data.summary.calls,
            successful_calls: data.summary.successful_calls,
            failed_calls: data.summary.failed_calls,
            incomplete_calls: data.summary.incomplete_calls,
            unknown_usage_calls: data.summary.unknown_usage_calls,
            unpriced_calls: data.summary.unpriced_calls,
            input_tokens: data.summary.input_tokens,
            output_tokens: data.summary.output_tokens,
            total_tokens: data.summary.total_tokens,
            priced_cost: data.summary.priced_cost,
            pending_reserved_cost: data.summary.pending_reserved_cost,
            success_rate: data.summary.success_rate,
            average_latency_ms: data.summary.average_latency_ms,
            priced_cost_per_success: data.summary.priced_cost_per_success
          }).map(([label, value]) => <OperationField key={label} label={label} value={value} />)}</div></CardInset>
      <CardInset><h3>{t(language, "Budget and holds", "预算与预留")}</h3><StatusPill tone={data.budget.over_budget ? "danger" : "index"}>{data.budget.limit === null ? t(language, "No budget limit configured", "尚未配置预算上限") : data.budget.over_budget ? t(language, "Over budget", "超出预算") : t(language, "Within configured budget", "在配置预算范围内")}</StatusPill><div className="two-col">{Object.entries({
            currency: data.budget.currency,
            period: data.budget.period,
            limit: data.budget.limit ?? t(language, "Not configured", "未配置"),
            spent: data.budget.spent,
            reserved: data.budget.reserved,
            remaining: data.budget.remaining ?? t(language, "Not configured", "未配置")
          }).map(([label, value]) => <OperationField key={label} label={label} value={value} />)}</div></CardInset>
      <section aria-label={t(language, "Cost breakdown", "成本明细")} className="grid gap-3"><h3>{t(language, "Operation / model / price version", "操作 / 模型 / 价格版本")}</h3>{data.summary.breakdown.length === 0 && <p>{t(language, "No model calls in this window.", "此时间范围内无模型调用。 ")}</p>}{data.summary.breakdown.map((row, index) => <CardInset as="article" key={`${row.operation}:${row.provider}:${row.model}:${row.price_version}:${index}`}><div className="two-col">{Object.entries(row).map(([label, value]) => <OperationField key={label} label={label} value={value} />)}</div></CardInset>)}</section></>}
  </OperationCard>;
}
export function SupportTicketsPanel(props: Props) {
  return <SupportTicketsContent key={authScopeKey(props.auth)} {...props} />;
}
function SupportTicketsContent({
  auth,
  language
}: Props) {
  const op = useOperatorRequest();
  const canRead = hasPermission(auth, "support:ticket:read");
  const canManage = hasPermission(auth, "support:ticket:manage");
  const [mode, setMode] = useState<"owned" | "manage">(canRead ? "owned" : "manage");
  const [ticketId, setTicketId] = useState("");
  const [status, setStatus] = useState("");
  const [tickets, setTickets] = useState<SupportTicket[] | null>(null);
  const [confirmation, setConfirmation] = useState<SupportTicket | null>(null);
  const [updated, setUpdated] = useState<SupportTicket | null>(null);
  if (!canRead && !canManage) return <PermissionNotice permission="support:ticket:read / support:ticket:manage" language={language} />;
  const validId = !ticketId || /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(ticketId);
  function load() {
    if (op.busy || mode === "manage" && (!canManage || !validId) || mode === "owned" && !canRead) return;
    setTickets(null);
    setConfirmation(null);
    setUpdated(null);
    void op.execute(signal => mode === "manage" ? listManagedTickets(auth, ticketId, status, signal) : listOwnedTickets(auth, signal), setTickets);
  }
  function transition() {
    if (op.busy || !canManage || !confirmation || !tickets?.some(item => item.ticket_id === confirmation.ticket_id && item.revision === confirmation.revision && item.status === confirmation.status)) return;
    const item = confirmation;
    const next = item.status === "open" ? "in_progress" : item.status === "in_progress" ? "resolved" : null;
    if (next === null) return;
    setTickets(null);
    setConfirmation(null);
    setUpdated(null);
    void op.execute(signal => transitionTicket(auth, item, next, signal), result => {
      setUpdated(result);
      setTickets([result]);
    });
  }
  return <OperationCard title={t(language, "Support tickets", "支持工单")} help={t(language, "View stored ticket content and process the current revision. Create tickets through the durable Agent approval workflow using create_support_ticket.", "查看已存储工单内容并处理当前修订。通过持久化 Agent 审批流程使用 create_support_ticket 创建工单。 ")} permission={canManage ? "support:ticket:manage" : "support:ticket:read"}>
    <label><span className="scope-label">{t(language, "Ticket view", "工单视图")}</span><Select aria-label={t(language, "Ticket view", "工单视图")} value={mode} disabled={op.busy} onChange={event => {
        setMode(event.target.value as "owned" | "manage");
        setTickets(null);
        setConfirmation(null);
        setUpdated(null);
      }}>
      {canRead && <option value="owned">{t(language, "My tickets", "我的工单")}</option>}{canManage && <option value="manage">{t(language, "Tenant ticket management", "租户工单管理")}</option>}
    </Select></label>
    {mode === "manage" && <div className="two-col"><OperationInput label={t(language, "Exact ticket ID (optional UUID)", "精确工单 ID（可选 UUID）")} value={ticketId} disabled={op.busy} onChange={value => {
        setTicketId(value.trim());
        setTickets(null);
        setConfirmation(null);
        setUpdated(null);
      }} /><label><span className="scope-label">{t(language, "Ticket status filter", "工单状态筛选")}</span><Select aria-label={t(language, "Ticket status filter", "工单状态筛选")} value={status} disabled={op.busy} onChange={event => {
          setStatus(event.target.value);
          setTickets(null);
          setConfirmation(null);
          setUpdated(null);
        }}><option value="">{t(language, "Any status", "全部状态")}</option><option value="open">open</option><option value="in_progress">in_progress</option><option value="resolved">resolved</option></Select></label></div>}
    <Button type="button" disabled={op.busy || mode === "manage" && !validId} onClick={load}>{t(language, "Refresh tickets", "刷新工单")}</Button><p className="muted">{t(language, "Shows up to 50 tickets. Use the exact ID filter to find a specific managed ticket.", "最多展示 50 个工单。使用精确 ID 筛选查找具体管理工单。 ")}</p><OperationFeedback {...op} language={language} />
    {updated && <p role="status">{t(language, "Status updated", "状态已更新")}: {updated.ticket_id} · {updated.status} · revision {updated.revision}</p>}
    {tickets && tickets.length === 0 && <p>{t(language, "No tickets found in this view.", "此视图未找到工单。 ")}</p>}
    {tickets?.map(item => <CardInset as="article" key={item.ticket_id}><h3 className="wrap">{item.title}</h3><p className="whitespace-pre-wrap break-words">{item.description}</p><StatusPill tone={item.status === "resolved" ? "source" : "index"}>{item.status}</StatusPill><div className="two-col">{Object.entries({
          ticket_id: item.ticket_id,
          revision: item.revision,
          priority: item.priority,
          user_id: item.user_id,
          created_by: item.created_by,
          tool_version: item.tool_version,
          created_at: item.created_at,
          updated_at: item.updated_at
        }).map(([label, value]) => <OperationField key={label} label={label} value={value} />)}</div>{canManage && item.status !== "resolved" && <Button type="button" disabled={op.busy} aria-label={`Review status ${item.ticket_id}`} onClick={() => setConfirmation(item)}>{item.status === "open" ? t(language, "Review start processing", "复核开始处理") : t(language, "Review resolution", "复核解决")}</Button>}</CardInset>)}
    {confirmation && <CardInset role="group" aria-label={t(language, "Confirm ticket transition", "确认工单状态变更")}><OperationField label="ticket_id" value={confirmation.ticket_id} /><OperationField label="expected_revision" value={confirmation.revision} /><OperationField label="current_status" value={confirmation.status} /><OperationField label="next_status" value={confirmation.status === "open" ? "in_progress" : "resolved"} /><div className="actions-row"><Button type="button" disabled={op.busy} onClick={transition}>{t(language, "Confirm ticket status change", "确认工单状态变更")}</Button><Button type="button" variant="secondary" disabled={op.busy} onClick={() => setConfirmation(null)}>{t(language, "Cancel status change", "取消状态变更")}</Button></div></CardInset>}
  </OperationCard>;
}
