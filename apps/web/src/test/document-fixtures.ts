import type { DocumentVersionStatusResult, UploadDocumentResult } from "@/lib/api/types";

// Shape mirrors packages/data/dto.py, including the nullable retry/error fields.
export const uploadedDocument: UploadDocumentResult = {
  document_id: "doc-upload-1",
  version_id: "version-upload-1",
  job_id: "ingestion-job-1",
  status: "uploaded"
};

export function documentStatus(overrides: Partial<DocumentVersionStatusResult> = {}): DocumentVersionStatusResult {
  return {
    document_id: uploadedDocument.document_id,
    version_id: uploadedDocument.version_id,
    status: "parsing",
    chunk_count: 0,
    embedding_provider: null,
    embedding_model: null,
    embedding_version: null,
    embedding_dim: null,
    vector_count: null,
    index_status: null,
    job_id: uploadedDocument.job_id,
    attempt_count: 1,
    last_attempt_at: "2026-10-14T16:00:00Z",
    next_retry_at: null,
    deleted_at: null,
    error_code: null,
    error_summary: null,
    request_id: "request-status-1",
    trace_id: "trace-status-1",
    ...overrides
  };
}

export function apiResponse(data: unknown, status = 200, error: unknown = null): Response {
  return new Response(JSON.stringify({ request_id: "request-1", data, error }), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}
