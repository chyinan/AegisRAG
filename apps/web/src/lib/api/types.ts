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
  source_display_name?: string | null;
  source?: string | null;
  source_type?: string | null;
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

export type ChatHistoryMessage = {
  role: "user" | "assistant" | "system_summary";
  content: string;
  sequence_no: number;
  request_id: string;
  trace_id: string;
  created_at: string;
  citations?: Citation[];
  no_answer?: boolean;
};

export type ChatHistoryResponse = {
  session_id: string;
  messages: ChatHistoryMessage[];
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

export type DocumentDeleteResult = {
  document_id: string;
  version_id?: string | null;
  status: string;
  deleted_versions: number;
  deleted_chunks: number;
  deleted_vectors: number;
  request_id: string;
  trace_id: string;
};

export type DocumentReviewRow = {
  document_id: string;
  version_id?: string | null;
  title?: string | null;
  source_display_name?: string | null;
  source_type?: string | null;
  status?: string | null;
  acl_summary?: string | null;
  updated_at?: string | null;
  job_id?: string | null;
};

export type DocumentReviewListResponse = {
  items: DocumentReviewRow[];
  limit?: number | null;
  next_cursor?: string | null;
};

export type AuditLogQuery = {
  user_id?: string;
  request_id?: string;
  trace_id?: string;
  action?: string;
  resource_type?: string;
  resource_id?: string;
  status?: string;
  created_at_from?: string;
  created_at_to?: string;
  limit?: number;
  include_associations?: boolean;
};

export type AuditLogAssociationSummary = {
  agent_run_id?: string | null;
  tool_call_id?: string | null;
  tool_name?: string | null;
  permission?: string | null;
  status?: string | null;
  error_code?: string | null;
  latency_ms?: number | null;
  arguments_summary?: Record<string, unknown>;
  result_summary?: Record<string, unknown>;
  steps_used?: number | null;
  tool_calls_used?: number | null;
  validation_counts?: Record<string, number>;
};

export type AuditLogSummary = {
  id: string;
  tenant_id: string;
  user_id: string;
  request_id: string;
  trace_id: string;
  action: string;
  resource_type: string;
  resource_id: string;
  status: string;
  latency_ms: number;
  error_code?: string | null;
  created_at: string;
  safe_summary?: Record<string, number | string>;
  association?: AuditLogAssociationSummary | null;
  safe_counts?: Record<string, number | string>;
};

export type AuditExplorerListResponse = {
  items: AuditLogSummary[];
  next_steps?: string[] | null;
};

export type AuditExportPayload = {
  export_id: string;
  generated_at: string;
  filter_summary: Record<string, unknown>;
  fields: string[];
  item_count: number;
  request_ids?: string[] | null;
  trace_ids?: string[] | null;
  items?: AuditLogSummary[] | null;
};

export type ReviewItemQuery = {
  item_type?: string;
  severity?: string;
  status?: string;
  request_id?: string;
  trace_id?: string;
  source_view?: string;
  created_at_from?: string;
  created_at_to?: string;
  limit?: number;
};

export type EvalCandidatePreview = {
  candidate_id: string;
  source_review_item_id: string;
  case_type: string;
  safe_identifiers?: Record<string, unknown>;
  failure_stage?: string | null;
  safe_metric_counts?: Record<string, number>;
  expected_behavior: string;
  request_id: string;
  trace_id: string;
  requires_human_confirmation: boolean;
};

export type ReviewItemSummary = {
  id: string;
  item_type: string;
  severity: string;
  status: string;
  request_id: string;
  trace_id: string;
  source_view: string;
  safe_identifiers?: Record<string, unknown>;
  safe_summary?: Record<string, unknown>;
  allowed_transitions?: string[];
  eval_candidate?: EvalCandidatePreview | null;
  created_by: string;
  tenant_id: string;
  created_at: string;
  updated_at: string;
};

export type ReviewQueueListResponse = {
  items: ReviewItemSummary[];
  next_steps?: string[] | null;
};

export type EvalEvidenceReportSummary = {
  report_filename: string;
  generated_at?: string | null;
  report_type: string;
  dataset_version?: string | null;
  dataset_name?: string | null;
  case_count: number;
  passed_count?: number | null;
  failed_count?: number | null;
  retrieval_hit_rate?: number | null;
  citation_coverage?: number | null;
  no_answer_correctness?: number | null;
  acl_isolation?: boolean | null;
  prompt_injection?: boolean | null;
  average_latency_ms?: number | null;
  decision: string;
  failed_metric_names?: string[];
  failure_stages?: string[];
};

export type EvalEvidenceReportListResponse = {
  items: EvalEvidenceReportSummary[];
  next_steps?: string[] | null;
};

export type AgentRunRequest = {
  input: string;
  max_steps?: number;
  max_tool_calls?: number;
  timeout_seconds?: number;
  metadata?: Record<string, unknown>;
};

export type AgentRunResponse = {
  agent_run_id: string;
  request_id: string;
  trace_id: string;
  tenant_id: string;
  user_id: string;
  status: string;
  termination_reason?: string | null;
  steps_used: number;
  tool_calls_used: number;
  final_answer?: string | null;
  final_citations?: Citation[];
  error_code?: string | null;
  created_at: string;
  updated_at: string;
  metadata?: Record<string, unknown>;
};
