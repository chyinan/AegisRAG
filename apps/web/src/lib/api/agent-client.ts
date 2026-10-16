import { authHeaders, type AuthSession } from "@/lib/auth";
import { ApiClientError, agentRunPayload } from "./client";
import { parseAgentEnvelope } from "./agent-http";
import { AgentProtocolError, agentInteger, decodeAgentRun, decodeDurableAgentRun } from "./agent-contract";
import { readAgentSseStream } from "./agent-sse";
import { durableStartupKey } from "./agent-startup-key";
import type { AgentRunRequest, AgentRunResponse, AgentStreamEvent, DurableAgentResumeRequest, DurableAgentRun, DurableAgentStartRequest } from "./types";

const API_PREFIX = "/api/backend";
const EXECUTION_PREFIX = "/api/execution";

export async function runAgentOnce(auth: AuthSession, request: AgentRunRequest, signal?: AbortSignal) {
  const response = await fetch(`${EXECUTION_PREFIX}/agent/run`, {
    method: "POST", signal, headers: { "Content-Type": "application/json", ...authHeaders(auth) },
    body: JSON.stringify(agentRunPayload(request))
  });
  const envelope = await parseAgentEnvelope(response, (value) => value === null ? null : decodeAgentRun(value), signal);
  if (envelope.data === null) {
    throw new ApiClientError({ code: "AGENT_RUN_EMPTY", message: "Agent run returned no data.", request_id: envelope.request_id });
  }
  return ownedAgentResult(auth, envelope.data);
}

export async function* streamAgent(auth: AuthSession, request: AgentRunRequest, signal?: AbortSignal): AsyncGenerator<AgentStreamEvent> {
  const response = await fetch(`${EXECUTION_PREFIX}/agent/run/stream`, {
    method: "POST", signal, headers: { "Content-Type": "application/json", ...authHeaders(auth) },
    body: JSON.stringify(agentRunPayload(request))
  });
  if (!response.ok) {
    const envelope = await parseAgentEnvelope<unknown>(response, undefined, signal);
    throw new ApiClientError({ code: "AGENT_STREAM_FAILED", message: "Agent stream failed.", request_id: envelope.request_id }, response.status);
  }
  for await (const event of readAgentSseStream(response, signal)) {
    if (event.event === "final") ownedAgentResult(auth, event.data);
    yield event;
  }
}

function ownedAgentResult(auth: AuthSession, result: AgentRunResponse): AgentRunResponse {
  if ((auth.tenantId !== undefined && result.tenant_id !== auth.tenantId) ||
      (auth.userId !== undefined && result.user_id !== auth.userId)) {
    throw new AgentProtocolError("AGENT_RESPONSE_OWNER_MISMATCH");
  }
  return result;
}

async function durableRequest(auth: AuthSession, path: string, body?: Record<string, unknown>, signal?: AbortSignal, expectedRunId?: string, execution = false): Promise<DurableAgentRun> {
  const response = await fetch(`${execution ? EXECUTION_PREFIX : API_PREFIX}${path}`, {
    method: body === undefined ? "GET" : "POST", signal, cache: "no-store",
    headers: { ...(body === undefined ? {} : { "Content-Type": "application/json" }), ...authHeaders(auth) },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const envelope = await parseAgentEnvelope(response, decodeDurableAgentRun, signal);
  if (envelope.data === null || (expectedRunId !== undefined && envelope.data.run_id !== expectedRunId)) throw new AgentProtocolError();
  return envelope.data;
}

function runPath(runId: string): string {
  // Opaque path segment only: never allow a caller-controlled URL or query string.
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(runId)) throw new AgentProtocolError("AGENT_INVALID_RUN_ID");
  return `/agent/durable/${encodeURIComponent(runId)}`;
}

export function startDurableAgent(auth: AuthSession, request: DurableAgentStartRequest, signal?: AbortSignal) {
  const budgetMode = request.budget_mode ?? "wall_clock";
  const executionMode = request.execution_mode ?? "inline";
  const timeout = request.timeout_seconds;
  const approvalTimeout = request.approval_timeout_seconds;
  if ((budgetMode !== "wall_clock" && budgetMode !== "active") || (executionMode !== "inline" && executionMode !== "background") ||
      (timeout !== undefined && (!Number.isFinite(timeout) || timeout <= 0 || timeout > (budgetMode === "active" ? 3600 : 120))) ||
      (approvalTimeout !== undefined && (budgetMode !== "active" || !Number.isFinite(approvalTimeout) || approvalTimeout <= 0 || approvalTimeout > 604800))) {
    throw new AgentProtocolError("AGENT_INVALID_DURABLE_OPTIONS");
  }
  const tools = request.approval_tools ?? [];
  if (tools.length > 20 || new Set(tools).size !== tools.length || tools.some((name) => !/^[a-z][a-z0-9_]*$/.test(name))) {
    throw new AgentProtocolError("AGENT_INVALID_APPROVAL_TOOLS");
  }
  const key = request.idempotency_key === undefined ? {} : { idempotency_key: durableStartupKey(request.idempotency_key) };
  return durableRequest(auth, "/agent/durable", {
    ...agentRunPayload(request),
    // The shared one-shot payload intentionally retains its 120-second cap.
    ...(timeout === undefined ? {} : { timeout_seconds: timeout }),
    ...(request.budget_mode === undefined ? {} : { budget_mode: budgetMode }),
    ...(request.execution_mode === undefined ? {} : { execution_mode: executionMode }),
    ...(approvalTimeout === undefined ? {} : { approval_timeout_seconds: approvalTimeout }),
    approval_tools: tools, ...key
  }, signal, undefined, true);
}
/** A POST transport for a read, so a private key never enters URL/access-log paths. */
export function recoverDurableAgent(auth: AuthSession, key: string, signal?: AbortSignal) {
  return durableRequest(auth, "/agent/durable/recover", { idempotency_key: durableStartupKey(key) }, signal);
}
export function getDurableAgent(auth: AuthSession, runId: string, signal?: AbortSignal) {
  return durableRequest(auth, runPath(runId), undefined, signal, runId);
}
export function resumeDurableAgent(auth: AuthSession, runId: string, request: DurableAgentResumeRequest, signal?: AbortSignal) {
  const body: Record<string, unknown> = { expected_revision: agentInteger(request.expected_revision) };
  if ((request.approval_id === undefined) !== (request.approve === undefined)) throw new AgentProtocolError("AGENT_INVALID_APPROVAL");
  if (request.approval_id !== undefined) {
    if (request.approval_id.length < 1 || request.approval_id.length > 36 || typeof request.approve !== "boolean") throw new AgentProtocolError();
    body.approval_id = request.approval_id;
    body.approve = request.approve;
  }
  return durableRequest(auth, `${runPath(runId)}/resume`, body, signal, runId, true);
}
export function cancelDurableAgent(auth: AuthSession, runId: string, expectedRevision: number, signal?: AbortSignal) {
  return durableRequest(auth, `${runPath(runId)}/cancel`, { expected_revision: agentInteger(expectedRevision) }, signal, runId);
}
