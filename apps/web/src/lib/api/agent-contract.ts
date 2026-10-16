import type { AgentApprovalRequest, AgentCitationRef, AgentRunResponse, DurableAgentRun, JsonValue, StructuredError } from "./types";

/** Protocol errors deliberately never include rejected payloads or parser diagnostics. */
export class AgentProtocolError extends Error {
  readonly structured: StructuredError;
  constructor(code = "AGENT_INVALID_RESPONSE") {
    super("The Agent response could not be verified safely. Refresh the run before taking another action.");
    this.name = "AgentProtocolError";
    this.structured = { code, message: this.message };
  }
}

export function agentRecord(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new AgentProtocolError();
  return value as Record<string, unknown>;
}
/** Match backend Python/Pydantic limits: Unicode points, not UTF-16 units. */
function withinStringLimit(value: string, max: number): boolean {
  if (value.length <= max) return true;
  let points = 0;
  for (const character of value) {
    void character;
    if (++points > max) return false;
  }
  return true;
}
export function agentString(value: unknown, max = 128): string {
  if (typeof value !== "string" || value.trim().length === 0 || !withinStringLimit(value, max)) throw new AgentProtocolError();
  return value;
}
export function agentNullableString(value: unknown, max = 128): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "string" || !withinStringLimit(value, max)) throw new AgentProtocolError();
  return value;
}
export function agentInteger(value: unknown, max = Number.MAX_SAFE_INTEGER): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0 || value > max) throw new AgentProtocolError();
  return value;
}
export function agentTimestamp(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0 || value > 8.64e12) throw new AgentProtocolError();
  return value;
}
export function agentErrorCode(value: unknown): string | null {
  const code = agentNullableString(value, 80);
  if (code !== null && !/^[A-Z][A-Z0-9_]{0,79}$/.test(code)) throw new AgentProtocolError();
  return code;
}
export function agentToolName(value: unknown): string {
  const name = agentString(value);
  if (!/^[a-z][a-z0-9_]*$/.test(name)) throw new AgentProtocolError();
  return name;
}

export function decodeAgentCitation(raw: unknown): AgentCitationRef {
  const value = agentRecord(raw);
  const identifier = (item: unknown) => {
    const id = agentString(item);
    if (!/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(id)) throw new AgentProtocolError();
    return id;
  };
  const page = (item: unknown) => {
    if (item === undefined || item === null) return null;
    const parsed = agentInteger(item);
    if (parsed < 1) throw new AgentProtocolError();
    return parsed;
  };
  const start = page(value.page_start);
  const end = page(value.page_end);
  if (start !== null && end !== null && start > end) throw new AgentProtocolError();
  return {
    document_id: identifier(value.document_id), version_id: identifier(value.version_id), chunk_id: identifier(value.chunk_id),
    source: agentNullableString(value.source, 200), page_start: start, page_end: end,
    tool_name: value.tool_name == null ? null : agentToolName(value.tool_name),
    observation_index: value.observation_index == null ? null : agentInteger(value.observation_index)
  };
}

function finalFields(value: Record<string, unknown>, completed: boolean) {
  const answer = agentNullableString(value.final_answer, 12_000);
  const rawCitations: unknown = value.final_citations ?? [];
  if (!Array.isArray(rawCitations) || rawCitations.length > 50) throw new AgentProtocolError();
  if (!completed && (answer !== null || rawCitations.length !== 0)) throw new AgentProtocolError();
  return { final_answer: answer, final_citations: rawCitations.map(decodeAgentCitation) };
}

export function decodeAgentRun(raw: unknown): AgentRunResponse {
  const value = agentRecord(raw);
  const status = value.status;
  if (status !== "completed" && status !== "stopped" && status !== "failed") throw new AgentProtocolError();
  return {
    agent_run_id: agentString(value.agent_run_id), request_id: agentString(value.request_id), trace_id: agentString(value.trace_id),
    tenant_id: agentString(value.tenant_id), user_id: agentString(value.user_id), status,
    steps_used: agentInteger(value.steps_used, 20), tool_calls_used: agentInteger(value.tool_calls_used, 20),
    termination_reason: agentNullableString(value.termination_reason), error_code: agentErrorCode(value.error_code),
    created_at: agentString(value.created_at), updated_at: agentString(value.updated_at),
    ...finalFields(value, status === "completed")
  };
}

const SECRET_KEYS = ["password", "secret", "token", "api_key", "apikey", "authorization", "cookie", "credential", "private_key"];
const SECRET_VALUES = ["-----begin", "bearer ", "password=", "api_key=", "secret=", "access_token="];
function exactJson(value: unknown, depth = 0): JsonValue {
  if (depth > 24) throw new AgentProtocolError();
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "string") {
    if (SECRET_VALUES.some((marker) => value.toLowerCase().includes(marker))) throw new AgentProtocolError();
    return value;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    if ((Number.isInteger(value) && !Number.isSafeInteger(value)) || Object.is(value, -0)) throw new AgentProtocolError();
    return value;
  }
  if (Array.isArray(value)) return value.map((item: unknown) => exactJson(item, depth + 1));
  const record = agentRecord(value);
  return Object.fromEntries(Object.entries(record).map(([key, item]) => {
    const normalized = key.toLowerCase().replaceAll("-", "_");
    if (["__proto__", "prototype", "constructor"].includes(normalized) || SECRET_KEYS.some((part) => normalized.includes(part))) {
      throw new AgentProtocolError();
    }
    return [key, exactJson(item, depth + 1)];
  }));
}

function decodeApproval(raw: unknown): AgentApprovalRequest {
  const value = agentRecord(raw);
  const rawArguments = agentRecord(value.arguments);
  if (new TextEncoder().encode(JSON.stringify(rawArguments)).length > 16_384) throw new AgentProtocolError();
  const argumentsValue = exactJson(rawArguments) as Record<string, JsonValue>;
  const digest = (item: unknown) => {
    const parsed = agentString(item, 64);
    if (!/^[a-f0-9]{64}$/.test(parsed)) throw new AgentProtocolError();
    return parsed;
  };
  return {
    approval_id: agentString(value.approval_id, 36), tool_name: agentToolName(value.tool_name), arguments: argumentsValue,
    action_digest: digest(value.action_digest), definition_digest: digest(value.definition_digest),
    expires_at: agentTimestamp(value.expires_at)
  };
}

export function decodeDurableAgentRun(raw: unknown): DurableAgentRun {
  const value = agentRecord(raw);
  const { status, backend } = value;
  if (value.schema_version !== 1 || (backend !== "native" && backend !== "langgraph")) throw new AgentProtocolError();
  if (status !== "running" && status !== "awaiting_approval" && status !== "completed" && status !== "failed" &&
      status !== "stopped" && status !== "cancelled" && status !== "intervention_required") throw new AgentProtocolError();
  const approval = value.approval == null ? null : decodeApproval(value.approval);
  if ((status === "awaiting_approval") !== (approval !== null)) throw new AgentProtocolError();
  const deadline = agentTimestamp(value.deadline_at);
  if (approval !== null && approval.expires_at > deadline) throw new AgentProtocolError();
  return {
    run_id: agentString(value.run_id), schema_version: 1, status, revision: agentInteger(value.revision), backend,
    steps_used: agentInteger(value.steps_used, 20), tool_calls_used: agentInteger(value.tool_calls_used, 20), deadline_at: deadline,
    approval, ...finalFields(value, status === "completed"), error_code: agentErrorCode(value.error_code),
    termination_reason: agentNullableString(value.termination_reason)
  };
}
