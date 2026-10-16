import { authHeaders, type AuthSession } from "@/lib/auth";
import { ApiClientError, parseEnvelope } from "./client";
export type FolderCandidate = {
  document_id: string;
  version_id: string;
  source_uri: string;
};
export type FolderPlan = {
  id: string;
  connector_id: string;
  enabled: boolean;
  interval_seconds: number;
  batch_size: number;
  delete_missing: boolean;
  status: string;
  phase: string;
  cycle: number;
  imported_count: number;
  skipped_count: number;
  deleted_count: number;
  managed_count: number;
  revision: number;
  candidate_count: number;
  error_code: string | null;
  next_run_at: number;
};
export type FolderPlanInput = Pick<FolderPlan, "enabled" | "interval_seconds" | "batch_size" | "delete_missing">;
export type FolderCandidates = {
  revision: number;
  total: number;
  items: FolderCandidate[];
  next_offset: number | null;
};
export type QueueStatus = "queued" | "scheduled" | "started" | "failed" | "stopped" | "canceled";
export type QueueItem = {
  job_kind: "ingestion" | "embedding";
  job_id: string;
  document_id: string;
  version_id: string;
  queue_job_id: string;
  queue_name: string;
  queue_status: QueueStatus;
  state: string;
  domain_status: string;
  attempt_count: number;
  retries_remaining: number;
  age_seconds: number;
  last_heartbeat_at: string | null;
  error_code: string | null;
  retry_allowed: boolean;
  terminate_allowed: boolean;
};
export type QueuePage = {
  items: QueueItem[];
  next_cursor: string | null;
  scanned_count: number;
  request_id: string;
  trace_id: string;
};
export type QueueResult = {
  status: "queued" | "terminated" | "stop_requested";
  job_kind: "ingestion" | "embedding";
  job_id: string;
  queue_job_id: string;
  queue_name: string;
  attempt_count: number;
  retries_remaining: number;
  request_id: string;
  trace_id: string;
};
export type CostBreakdown = {
  operation: string;
  provider: string;
  model: string;
  price_version: string | null;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  priced_cost: string;
  unpriced_calls: number;
};
export type CostSummary = {
  currency: string;
  start: string;
  end: string;
  calls: number;
  successful_calls: number;
  failed_calls: number;
  incomplete_calls: number;
  unknown_usage_calls: number;
  unpriced_calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  priced_cost: string;
  pending_reserved_cost: string;
  cost_complete: boolean;
  success_rate: number | null;
  average_latency_ms: number | null;
  priced_cost_per_success: string | null;
  breakdown: CostBreakdown[];
};
export type BudgetSummary = {
  currency: string;
  period: string;
  limit: string | null;
  spent: string;
  reserved: string;
  remaining: string | null;
  over_budget: boolean;
};
export type SupportTicket = {
  ticket_id: string;
  tenant_id: string;
  user_id: string;
  created_by: string;
  client_reference: string;
  title: string;
  description: string;
  priority: "low" | "normal" | "high";
  status: "open" | "in_progress" | "resolved";
  revision: number;
  tool_version: string;
  created_at: string;
  updated_at: string;
};

// Each success decoder returns only the fields this surface needs. Ticket prose
// and configured-root candidate URIs are deliberate data, never generic tool output.
function invalid(): never {
  throw new ApiClientError({
    code: "OPERATIONS_INVALID_RESPONSE",
    message: "Invalid operation response. Refresh before acting."
  });
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) invalid();
  return value as Record<string, unknown>;
}
function string(value: unknown): string {
  if (typeof value !== "string") invalid();
  return value;
}
function integer(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) invalid();
  return value;
}
function number(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) invalid();
  return value;
}
function boolean(value: unknown): boolean {
  if (typeof value !== "boolean") invalid();
  return value;
}
function array<T>(value: unknown, decode: (v: unknown) => T): T[] {
  if (!Array.isArray(value)) invalid();
  return value.map(decode);
}
function nullable<T>(value: unknown, decode: (v: unknown) => T): T | null {
  return value === null ? null : decode(value);
}
function choice<T extends string>(value: unknown, choices: readonly T[]): T {
  if (!choices.includes(value as T)) invalid();
  return value as T;
}
function money(value: unknown): string {
  const result = typeof value === "number" && Number.isFinite(value) ? String(value) : string(value);
  if (!/^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(result)) invalid();
  return result; // Never round decimal amounts or replace missing values with zero.
}
function candidate(value: unknown): FolderCandidate {
  const v = object(value);
  return {
    document_id: string(v.document_id),
    version_id: string(v.version_id),
    source_uri: string(v.source_uri)
  };
}
function plan(value: unknown): FolderPlan {
  const v = object(value);
  return {
    id: string(v.id),
    connector_id: string(v.connector_id),
    enabled: boolean(v.enabled),
    interval_seconds: integer(v.interval_seconds),
    batch_size: integer(v.batch_size),
    delete_missing: boolean(v.delete_missing),
    status: string(v.status),
    phase: string(v.phase),
    cycle: integer(v.cycle),
    imported_count: integer(v.imported_count),
    skipped_count: integer(v.skipped_count),
    deleted_count: integer(v.deleted_count),
    managed_count: integer(v.managed_count),
    revision: integer(v.revision),
    candidate_count: integer(v.candidate_count),
    error_code: nullable(v.error_code, string),
    next_run_at: number(v.next_run_at)
  };
}
function candidates(value: unknown): FolderCandidates {
  const v = object(value);
  return {
    revision: integer(v.revision),
    total: integer(v.total),
    items: array(v.items, candidate),
    next_offset: nullable(v.next_offset, integer)
  };
}
function queueItem(value: unknown): QueueItem {
  const v = object(value);
  return {
    job_kind: choice(v.job_kind, ["ingestion", "embedding"]),
    job_id: string(v.job_id),
    document_id: string(v.document_id),
    version_id: string(v.version_id),
    queue_job_id: string(v.queue_job_id),
    queue_name: string(v.queue_name),
    queue_status: choice(v.queue_status, ["queued", "scheduled", "started", "failed", "stopped", "canceled"]),
    state: string(v.state),
    domain_status: string(v.domain_status),
    attempt_count: integer(v.attempt_count),
    retries_remaining: integer(v.retries_remaining),
    age_seconds: integer(v.age_seconds),
    last_heartbeat_at: nullable(v.last_heartbeat_at, string),
    error_code: nullable(v.error_code, string),
    retry_allowed: boolean(v.retry_allowed),
    terminate_allowed: boolean(v.terminate_allowed)
  };
}
function queuePage(value: unknown): QueuePage {
  const v = object(value);
  return {
    items: array(v.items, queueItem),
    next_cursor: nullable(v.next_cursor, string),
    scanned_count: integer(v.scanned_count),
    request_id: string(v.request_id),
    trace_id: string(v.trace_id)
  };
}
function queueResult(value: unknown): QueueResult {
  const v = object(value);
  return {
    status: choice(v.status, ["queued", "terminated", "stop_requested"]),
    job_kind: choice(v.job_kind, ["ingestion", "embedding"]),
    job_id: string(v.job_id),
    queue_job_id: string(v.queue_job_id),
    queue_name: string(v.queue_name),
    attempt_count: integer(v.attempt_count),
    retries_remaining: integer(v.retries_remaining),
    request_id: string(v.request_id),
    trace_id: string(v.trace_id)
  };
}
function costBreakdown(value: unknown): CostBreakdown {
  const v = object(value);
  return {
    operation: string(v.operation),
    provider: string(v.provider),
    model: string(v.model),
    price_version: nullable(v.price_version, string),
    calls: integer(v.calls),
    input_tokens: integer(v.input_tokens),
    output_tokens: integer(v.output_tokens),
    total_tokens: integer(v.total_tokens),
    priced_cost: money(v.priced_cost),
    unpriced_calls: integer(v.unpriced_calls)
  };
}
function cost(value: unknown): CostSummary {
  const v = object(value);
  return {
    currency: string(v.currency),
    start: string(v.start),
    end: string(v.end),
    calls: integer(v.calls),
    successful_calls: integer(v.successful_calls),
    failed_calls: integer(v.failed_calls),
    incomplete_calls: integer(v.incomplete_calls),
    unknown_usage_calls: integer(v.unknown_usage_calls),
    unpriced_calls: integer(v.unpriced_calls),
    input_tokens: integer(v.input_tokens),
    output_tokens: integer(v.output_tokens),
    total_tokens: integer(v.total_tokens),
    priced_cost: money(v.priced_cost),
    pending_reserved_cost: money(v.pending_reserved_cost),
    cost_complete: boolean(v.cost_complete),
    success_rate: nullable(v.success_rate, number),
    average_latency_ms: nullable(v.average_latency_ms, number),
    priced_cost_per_success: nullable(v.priced_cost_per_success, money),
    breakdown: array(v.breakdown, costBreakdown)
  };
}
function budget(value: unknown): BudgetSummary {
  const v = object(value);
  return {
    currency: string(v.currency),
    period: string(v.period),
    limit: nullable(v.limit, money),
    spent: money(v.spent),
    reserved: money(v.reserved),
    remaining: nullable(v.remaining, money),
    over_budget: boolean(v.over_budget)
  };
}
function ticket(value: unknown): SupportTicket {
  const v = object(value);
  const revision = integer(v.revision);
  if (revision < 1) invalid();
  return {
    ticket_id: string(v.ticket_id),
    tenant_id: string(v.tenant_id),
    user_id: string(v.user_id),
    created_by: string(v.created_by),
    client_reference: string(v.client_reference),
    title: string(v.title),
    description: string(v.description),
    priority: choice(v.priority, ["low", "normal", "high"]),
    status: choice(v.status, ["open", "in_progress", "resolved"]),
    revision,
    tool_version: string(v.tool_version),
    created_at: string(v.created_at),
    updated_at: string(v.updated_at)
  };
}
function ticketList(value: unknown): SupportTicket[] {
  return array(object(value).items, ticket);
}
async function request<T>(auth: AuthSession, path: string, decode: (v: unknown) => T, signal: AbortSignal, method = "GET", body?: unknown, prefix = "/api/backend"): Promise<T> {
  const response = await fetch(`${prefix}${path}`, {
    method,
    signal,
    headers: {
      ...authHeaders(auth),
      ...(body === undefined ? {} : {
        "Content-Type": "application/json"
      })
    },
    ...(body === undefined ? {} : {
      body: JSON.stringify(body)
    })
  });
  const envelope = await parseEnvelope(response, decode);
  return envelope.data as T;
}
const segment = (id: string) => encodeURIComponent(id);
export const listFolderPlans = (auth: AuthSession, signal: AbortSignal) => request(auth, "/sources/folder/sync/plans", v => array(v, plan), signal);
export const configureFolderPlan = (auth: AuthSession, input: FolderPlanInput, signal: AbortSignal) => request(auth, "/sources/folder/sync/plan", plan, signal, "PUT", {
  connector_id: "folder",
  ...input
});
export async function runFolderBatch(auth: AuthSession, id: string, signal: AbortSignal) {
  const result = await request(auth, `/sources/folder/sync/plans/${segment(id)}/batch`, plan, signal, "POST", undefined, "/api/execution");
  if (result.id !== id) invalid();
  return result;
}
export const listFolderCandidates = (auth: AuthSession, id: string, offset: number, signal: AbortSignal) => request(auth, `/sources/folder/sync/plans/${segment(id)}/candidates?offset=${offset}&limit=25`, candidates, signal);
export async function reconcileFolderCandidates(auth: AuthSession, id: string, revision: number, ids: string[], signal: AbortSignal) {
  const result = await request(auth, `/sources/folder/sync/plans/${segment(id)}/reconcile`, plan, signal, "POST", {
    expected_revision: revision,
    document_ids: ids
  }, "/api/execution");
  if (result.id !== id || result.revision <= revision) invalid();
  return result;
}
export const listQueueJobs = (auth: AuthSession, cursor: string | null, signal: AbortSignal) => request(auth, `/queue/jobs?limit=50${cursor === null ? "" : `&cursor=${encodeURIComponent(cursor)}`}`, queuePage, signal);
export async function operateQueueJob(auth: AuthSession, item: QueueItem, action: "retry" | "terminate", signal: AbortSignal) {
  const result = await request(auth, `/queue/jobs/${item.job_kind}/${segment(item.queue_job_id)}/${action}`, queueResult, signal, "POST", {
    expected_attempt_count: item.attempt_count,
    expected_queue_status: item.queue_status
  });
  if (result.queue_job_id !== item.queue_job_id || result.job_id !== item.job_id || result.job_kind !== item.job_kind || result.attempt_count !== item.attempt_count || result.retries_remaining > item.retries_remaining || (action === "retry" ? result.status !== "queued" : result.status === "queued")) invalid();
  return result;
}
export const loadCostSummary = (auth: AuthSession, start: string, end: string, signal: AbortSignal) => request(auth, `/costs/summary${start || end ? `?${new URLSearchParams({
  ...(start ? {
    start
  } : {}),
  ...(end ? {
    end
  } : {})
})}` : ""}`, cost, signal);
export const loadBudgetSummary = (auth: AuthSession, signal: AbortSignal) => request(auth, "/costs/budget", budget, signal);
export const listOwnedTickets = (auth: AuthSession, signal: AbortSignal) => request(auth, "/support-tickets?limit=50", ticketList, signal);
export const listManagedTickets = (auth: AuthSession, ticketId: string, status: string, signal: AbortSignal) => request(auth, `/support-tickets/manage?${new URLSearchParams({
  limit: "50",
  ...(ticketId ? {
    ticket_id: ticketId
  } : {}),
  ...(status ? {
    status
  } : {})
})}`, ticketList, signal);
export async function transitionTicket(auth: AuthSession, item: SupportTicket, status: "in_progress" | "resolved", signal: AbortSignal) {
  const result = await request(auth, `/support-tickets/${segment(item.ticket_id)}/status`, ticket, signal, "PATCH", {
    expected_revision: item.revision,
    status
  });
  if (result.ticket_id !== item.ticket_id || result.revision !== item.revision + 1 || result.status !== status) invalid();
  return result;
}
