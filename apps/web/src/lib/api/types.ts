// pattern: Functional Core

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
  /** Backend-derived prompt marker identity; absent on older responses. */
  citation_id?: string | null;
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

/** Wire contract from packages/rag/streaming.py, validated before UI use. */
export type StreamContext = { request_id: string; trace_id: string };

export type ChatFinalPayload = StreamContext & {
  session_id?: string | null;
  answer: string;
  citations: Citation[];
  status: "success" | "error";
  no_answer: boolean;
  metadata?: { error_code?: string };
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
  error_code?: string | null;
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
  | { type: "token"; data: StreamContext & { index: number; delta: string } }
  | { type: "citation"; data: StreamContext & { citation: Citation } }
  | { type: "tool_call"; data: StreamContext & ToolEvent }
  | { type: "tool_result"; data: StreamContext & ToolEvent }
  | { type: "error"; data: StreamContext & StructuredError & { terminal: boolean } }
  | { type: "final"; data: ChatFinalPayload };

export type SourceResolveRequest = {
  document_id: string;
  version_id?: string | null;
  chunk_id: string;
};

// The source endpoint returns this DTO only after backend authorization succeeds.
export type SourceResolveResult = {
  request_id: string;
  trace_id: string;
  document_id: string;
  version_id: string;
  chunk_id: string;
  source_display_name: string;
  source_type: string;
  page_start?: number | null;
  page_end?: number | null;
  title_path: string[];
  text_excerpt: string;
  excerpt_char_count: number;
  token_count: number;
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
  aclPreset: "tenant" | "private";
};

export type UploadDocumentResult = {
  document_id: string;
  version_id: string;
  job_id: string | null;
  status: string;
};

export type DocumentVersionStatusResult = {
  document_id: string;
  version_id: string;
  status: string;
  chunk_count: number;
  embedding_provider?: string | null;
  embedding_model?: string | null;
  embedding_version?: string | null;
  embedding_dim?: number | null;
  vector_count?: number | null;
  index_status?: string | null;
  job_id?: string | null;
  attempt_count?: number | null;
  last_attempt_at?: string | null;
  next_retry_at?: string | null;
  deleted_at?: string | null;
  error_code?: string | null;
  error_summary?: Record<string, unknown> | null;
  request_id: string;
  trace_id: string;
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
  p95_latency_ms?: number | null;
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

export type AgentCitationRef = {
  document_id: string;
  version_id: string;
  chunk_id: string;
  source?: string | null;
  page_start?: number | null;
  page_end?: number | null;
  tool_name?: string | null;
  observation_index?: number | null;
};

export type AgentRunResponse = {
  agent_run_id: string;
  request_id: string;
  trace_id: string;
  tenant_id: string;
  user_id: string;
  status: "completed" | "stopped" | "failed";
  termination_reason?: string | null;
  steps_used: number;
  tool_calls_used: number;
  final_answer?: string | null;
  final_citations?: AgentCitationRef[];
  error_code?: string | null;
  created_at: string;
  updated_at: string;
  metadata?: Record<string, unknown>;
};


export type AgentExecutionMode = "one_shot" | "live" | "durable";

export type DurableAgentStatus = "running" | "awaiting_approval" | "completed" | "failed" | "stopped" | "cancelled" | "intervention_required";
export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export type AgentApprovalRequest = {
  approval_id: string;
  tool_name: string;
  arguments: Record<string, JsonValue>;
  action_digest: string;
  definition_digest: string;
  expires_at: number;
};

export type DurableAgentRun = {
  run_id: string;
  schema_version: 1;
  status: DurableAgentStatus;
  revision: number;
  backend: "native" | "langgraph";
  steps_used: number;
  tool_calls_used: number;
  deadline_at: number;
  approval: AgentApprovalRequest | null;
  final_answer: string | null;
  final_citations: AgentCitationRef[];
  error_code: string | null;
  termination_reason: string | null;
};

export type DurableAgentStartRequest = AgentRunRequest & { approval_tools?: string[]; idempotency_key?: string };
export type DurableAgentResumeRequest = {
  expected_revision: number;
  approval_id?: string;
  approve?: boolean;
};

type AgentStreamContext = {
  sequence: number;
  request_id: string;
  trace_id: string;
  agent_run_id: string | null;
};
export type AgentStreamEvent = AgentStreamContext & (
  | { event: "started"; data: { status: "running" } }
  | { event: "tool_call" | "tool_result"; data: {
      tool_call_id: string;
      tool_name: string;
      status: "started" | "success" | "error";
      metadata: { latency_ms?: number; error_code?: string };
    } }
  | { event: "error"; data: { code: string; terminal: true } }
  | { event: "final"; data: AgentRunResponse }
);
