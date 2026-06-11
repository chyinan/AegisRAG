import { authHeaders, type AuthSession } from "@/lib/auth";
import { stripForbiddenFields } from "./safety";
import { readSseStream } from "./sse";
import type {
  ApiEnvelope,
  AuditExplorerListResponse,
  AuditExportPayload,
  AuditLogQuery,
  AgentRunRequest,
  AgentRunResponse,
  ChatHistoryResponse,
  DiagnosticsResolveRequest,
  DiagnosticsTimeline,
  DocumentDeleteResult,
  DocumentReviewListResponse,
  DocumentReviewRow,
  EvalEvidenceReportListResponse,
  ReviewItemQuery,
  ReviewQueueListResponse,
  SseEvent,
  SourceResolveRequest,
  SourceResolveResult,
  StructuredError,
  UploadDocumentInput,
  UploadDocumentResult
} from "./types";

const API_PREFIX = "/api/backend";

export class ApiClientError extends Error {
  readonly structured: StructuredError;

  constructor(error: StructuredError) {
    super(error.message);
    this.name = "ApiClientError";
    this.structured = error;
  }
}

export async function postJson<T>(
  path: string,
  auth: AuthSession,
  body: Record<string, unknown>
): Promise<ApiEnvelope<T>> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(auth)
    },
    body: JSON.stringify(body)
  });

  return parseEnvelope<T>(response);
}

export async function getJson<T>(path: string, auth: AuthSession): Promise<ApiEnvelope<T>> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    method: "GET",
    headers: {
      ...authHeaders(auth)
    }
  });

  return parseEnvelope<T>(response);
}

export async function deleteJson<T>(path: string, auth: AuthSession): Promise<ApiEnvelope<T>> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    method: "DELETE",
    headers: {
      ...authHeaders(auth)
    }
  });

  return parseEnvelope<T>(response);
}

export async function resolveSource(
  auth: AuthSession,
  request: SourceResolveRequest
): Promise<SourceResolveResult> {
  const envelope = await postJson<SourceResolveResult>("/sources/resolve", auth, {
    document_id: request.document_id,
    version_id: request.version_id,
    chunk_id: request.chunk_id
  });
  if (envelope.data === null) {
    throw new ApiClientError(
      envelope.error ?? {
        code: "SOURCE_RESOLVE_EMPTY",
        message: "Source resolve returned no data.",
        request_id: envelope.request_id
      }
    );
  }
  return envelope.data;
}

export async function resolveDiagnostics(
  auth: AuthSession,
  request: DiagnosticsResolveRequest
): Promise<DiagnosticsTimeline> {
  const envelope = await postJson<DiagnosticsTimeline>("/diagnostics/resolve", auth, request);
  if (envelope.data === null) {
    throw new ApiClientError(
      envelope.error ?? {
        code: "DIAGNOSTICS_EMPTY",
        message: "Diagnostics returned no data.",
        request_id: envelope.request_id
      }
    );
  }
  return envelope.data;
}

export async function uploadDocument(
  auth: AuthSession,
  input: UploadDocumentInput
): Promise<UploadDocumentResult> {
  const acl = aclForPreset(input.aclPreset);
  const formData = new FormData();
  formData.append("file", input.file);
  formData.append("source_type", input.sourceType);
  if (input.title !== undefined && input.title.trim().length > 0) {
    formData.append("title", input.title.trim());
  }
  if (input.sourceReference !== undefined && input.sourceReference.trim().length > 0) {
    formData.append("source_uri", input.sourceReference.trim());
  }
  formData.append("acl", JSON.stringify(acl));

  const response = await fetch(`${API_PREFIX}/upload`, {
    method: "POST",
    headers: {
      ...authHeaders(auth)
    },
    body: formData
  });
  const envelope = await parseEnvelope<UploadDocumentResult>(response);
  if (envelope.data === null) {
    throw new ApiClientError(
      envelope.error ?? {
        code: "UPLOAD_EMPTY",
        message: "Upload returned no data.",
        request_id: envelope.request_id
      }
    );
  }
  return envelope.data;
}

export async function listDocumentReview(auth: AuthSession): Promise<DocumentReviewRow[]> {
  const envelope = await getJson<DocumentReviewRow[] | DocumentReviewListResponse>(
    "/documents/review?limit=25",
    auth
  );
  return normalizeDocumentReviewRows(envelope.data);
}

export async function deleteDocument(auth: AuthSession, documentId: string): Promise<DocumentDeleteResult> {
  const envelope = await deleteJson<DocumentDeleteResult>(
    `/documents/${encodeURIComponent(documentId)}`,
    auth
  );
  if (envelope.data === null) {
    throw new ApiClientError(
      envelope.error ?? {
        code: "DOCUMENT_DELETE_EMPTY",
        message: "Document delete returned no data.",
        request_id: envelope.request_id
      }
    );
  }
  return envelope.data;
}

export async function listAuditLogs(auth: AuthSession, query: AuditLogQuery): Promise<AuditExplorerListResponse> {
  const envelope = await getJson<AuditExplorerListResponse>(buildAuditLogsPath(query), auth);
  return envelope.data ?? { items: [], next_steps: [] };
}

export async function exportAuditLogs(auth: AuthSession, query: AuditLogQuery): Promise<AuditExportPayload> {
  const envelope = await postJson<AuditExportPayload>("/audit/export", auth, auditQueryPayload(query, 200));
  if (envelope.data === null) {
    throw new ApiClientError(
      envelope.error ?? {
        code: "AUDIT_EXPORT_EMPTY",
        message: "Audit export returned no data.",
        request_id: envelope.request_id
      }
    );
  }
  return envelope.data;
}

export function buildAuditLogsPath(query: AuditLogQuery): string {
  const params = new URLSearchParams();
  const payload = auditQueryPayload(query, 50);
  for (const [key, value] of Object.entries(payload)) {
    params.set(key, String(value));
  }
  return `/audit/logs?${params.toString()}`;
}

export async function listReviewItems(auth: AuthSession, query: ReviewItemQuery): Promise<ReviewQueueListResponse> {
  const envelope = await getJson<ReviewQueueListResponse>(buildReviewItemsPath(query), auth);
  return envelope.data ?? { items: [], next_steps: [] };
}

export function buildReviewItemsPath(query: ReviewItemQuery): string {
  const params = new URLSearchParams();
  const payload = reviewQueryPayload(query);
  for (const [key, value] of Object.entries(payload)) {
    params.set(key, String(value));
  }
  return `/review/items?${params.toString()}`;
}

export async function listEvalReports(auth: AuthSession, limit = 20): Promise<EvalEvidenceReportListResponse> {
  const params = new URLSearchParams({ limit: String(clamp(limit, 1, 100, 20)) });
  const envelope = await getJson<EvalEvidenceReportListResponse>(`/eval/reports?${params.toString()}`, auth);
  return envelope.data ?? { items: [], next_steps: [] };
}

export async function runAgent(auth: AuthSession, request: AgentRunRequest): Promise<AgentRunResponse> {
  const envelope = await postJson<AgentRunResponse>("/agent/run", auth, agentRunPayload(request));
  if (envelope.data === null) {
    throw new ApiClientError(
      envelope.error ?? {
        code: "AGENT_RUN_EMPTY",
        message: "Agent run returned no data.",
        request_id: envelope.request_id
      }
    );
  }
  return envelope.data;
}

export async function loadChatHistory(
  auth: AuthSession,
  sessionId: string,
  limit = 50
): Promise<ChatHistoryResponse> {
  const params = new URLSearchParams({
    session_id: sessionId,
    limit: String(clamp(limit, 1, 100, 50))
  });
  const envelope = await getJson<ChatHistoryResponse>(`/chat/history?${params.toString()}`, auth);
  if (envelope.data === null) {
    throw new ApiClientError(
      envelope.error ?? {
        code: "CHAT_HISTORY_EMPTY",
        message: "Chat history returned no data.",
        request_id: envelope.request_id
      }
    );
  }
  return envelope.data;
}

export async function* streamChat(
  auth: AuthSession,
  question: string,
  sessionId?: string | null
): AsyncGenerator<SseEvent> {
  const response = await fetch(`${API_PREFIX}/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(auth)
    },
    body: JSON.stringify({
      query: question,
      session_id: sessionId ?? undefined
    })
  });

  if (!response.ok) {
    const envelope = await parseEnvelope<unknown>(response);
    throw new ApiClientError(
      envelope.error ?? {
        code: "CHAT_STREAM_FAILED",
        message: "Chat stream failed.",
        request_id: envelope.request_id
      }
    );
  }

  yield* readSseStream(response);
}

async function parseEnvelope<T>(response: Response): Promise<ApiEnvelope<T>> {
  const raw = (await response.json()) as unknown;
  const cleaned = stripForbiddenFields(raw) as ApiEnvelope<T>;

  if (!response.ok || cleaned.error !== null) {
    if (cleaned.error !== null) {
      throw new ApiClientError(cleaned.error);
    }
    throw new ApiClientError({
      code: `HTTP_${response.status}`,
      message: response.statusText || "Backend request failed.",
      request_id: cleaned.request_id
    });
  }

  return cleaned;
}

function aclForPreset(preset: UploadDocumentInput["aclPreset"]): Record<string, unknown> {
  if (preset === "department") {
    return { visibility: "department" };
  }
  if (preset === "private") {
    return { visibility: "private" };
  }
  return { visibility: "tenant" };
}

function normalizeDocumentReviewRows(
  data: DocumentReviewRow[] | DocumentReviewListResponse | null
): DocumentReviewRow[] {
  const rows = Array.isArray(data) ? data : data?.items;
  if (!Array.isArray(rows)) {
    return [];
  }
  return rows.map((row) => ({
    ...row,
    title: row.title ?? row.source_display_name ?? row.document_id
  }));
}

function auditQueryPayload(query: AuditLogQuery, defaultLimit: number): Record<string, string | number | boolean> {
  const payload: Record<string, string | number | boolean> = {
    limit: clampAuditLimit(query.limit, defaultLimit),
    include_associations: query.include_associations ?? true
  };
  for (const key of [
    "user_id",
    "request_id",
    "trace_id",
    "action",
    "resource_type",
    "resource_id",
    "status",
    "created_at_from",
    "created_at_to"
  ] as const) {
    const value = query[key];
    if (typeof value === "string" && value.trim().length > 0) {
      payload[key] = value.trim();
    }
  }
  return payload;
}

function clampAuditLimit(limit: number | undefined, fallback: number): number {
  if (limit === undefined || !Number.isFinite(limit)) {
    return fallback;
  }
  return Math.min(Math.max(Math.trunc(limit), 1), 200);
}

function reviewQueryPayload(query: ReviewItemQuery): Record<string, string | number> {
  const payload: Record<string, string | number> = {
    limit: clamp(query.limit, 1, 100, 50)
  };
  for (const key of [
    "item_type",
    "severity",
    "status",
    "request_id",
    "trace_id",
    "source_view",
    "created_at_from",
    "created_at_to"
  ] as const) {
    const value = query[key];
    if (typeof value === "string" && value.trim().length > 0) {
      payload[key] = value.trim();
    }
  }
  return payload;
}

function agentRunPayload(request: AgentRunRequest): Record<string, unknown> {
  return {
    input: request.input.trim(),
    max_steps: clamp(request.max_steps, 1, 20, 8),
    max_tool_calls: clamp(request.max_tool_calls, 0, 20, 4),
    timeout_seconds: clamp(request.timeout_seconds, 1, 120, 30),
    metadata: request.metadata ?? { surface: "main_workbench" }
  };
}

function clamp(value: number | undefined, min: number, max: number, fallback: number): number {
  if (value === undefined || !Number.isFinite(value)) {
    return fallback;
  }
  return Math.min(Math.max(Math.trunc(value), min), max);
}
