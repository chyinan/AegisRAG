import { authHeaders, type AuthSession } from "@/lib/auth";
import { stripForbiddenFields } from "./safety";
import { readSseStream } from "./sse";
import type {
  ApiEnvelope,
  DiagnosticsResolveRequest,
  DiagnosticsTimeline,
  DocumentReviewRow,
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
  const envelope = await getJson<DocumentReviewRow[]>("/documents/review?limit=25", auth);
  return envelope.data ?? [];
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
      message: question,
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
