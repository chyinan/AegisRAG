export type ApiEnvelope<T> = {
  request_id: string;
  data: T | null;
  error: StructuredError | null;
  metadata?: Record<string, unknown> | null;
};

export type StructuredError = {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  request_id?: string;
  trace_id?: string;
};

export type Citation = {
  document_id: string;
  version_id?: string | null;
  chunk_id: string;
  source?: string | null;
  page?: number | null;
  page_start?: number | null;
  page_end?: number | null;
  title_path?: string[] | null;
  score?: number | null;
  retrieval_method?: string | null;
};

export type ChatFinalPayload = {
  request_id?: string;
  trace_id?: string;
  session_id?: string | null;
  answer?: string;
  citations?: Citation[];
  status?: "answered" | "no_answer" | "error";
};

export type ToolEvent = {
  agent_run_id?: string;
  tool_name: string;
  status: string;
  latency_ms?: number | null;
  error_code?: string | null;
  request_id?: string | null;
  trace_id?: string | null;
  summary?: string | null;
};

export type SseEvent =
  | { type: "token"; data: { token?: string; text?: string } }
  | { type: "citation"; data: Citation | Citation[] }
  | { type: "tool_call"; data: ToolEvent }
  | { type: "tool_result"; data: ToolEvent }
  | { type: "error"; data: StructuredError }
  | { type: "final"; data: ChatFinalPayload };

export type SourceResolveRequest = {
  document_id: string;
  version_id?: string | null;
  chunk_id: string;
};

export type SourceResolveResult = {
  authorized: boolean;
  document_id?: string;
  version_id?: string | null;
  chunk_id?: string;
  title?: string | null;
  page_start?: number | null;
  page_end?: number | null;
  excerpt?: string | null;
  metadata?: Record<string, unknown> | null;
};

export type DiagnosticsResolveRequest = {
  request_id?: string;
  trace_id?: string;
};

export type DiagnosticsTimeline = {
  request_id?: string | null;
  trace_id?: string | null;
  top_k?: number | null;
  result_count?: number | null;
  highest_rerank_score?: number | null;
  citation_count?: number | null;
  latency_ms?: number | null;
  failure_stage?: string | null;
  error_code?: string | null;
  next_steps?: string[] | null;
};

export type UploadDocumentInput = {
  file: File;
  sourceType: "pdf" | "docx" | "txt" | "markdown";
  title?: string;
  sourceReference?: string;
  aclPreset: "tenant" | "department" | "private";
};

export type UploadDocumentResult = {
  document_id: string;
  version_id: string;
  job_id: string;
  status: string;
};

export type DocumentReviewRow = {
  document_id: string;
  version_id?: string | null;
  title?: string | null;
  source_type?: string | null;
  status?: string | null;
  acl_summary?: string | null;
  updated_at?: string | null;
  job_id?: string | null;
};
