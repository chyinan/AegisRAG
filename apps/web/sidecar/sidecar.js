(function () {
  "use strict";

  const CITATION_INPUT_FIELDS = [
    "document_id",
    "version_id",
    "chunk_id",
    "page_start",
    "page_end",
    "request_id",
    "citation_ref",
  ];

  const SAFE_SOURCE_FIELDS = [
    "source_display_name",
    "source_type",
    "document_id",
    "version_id",
    "chunk_id",
    "page_start",
    "page_end",
    "title_path",
    "text_excerpt",
    "excerpt_char_count",
    "token_count",
    "retrieval_method",
    "score",
    "request_id",
    "trace_id",
  ];

  const SOURCE_EVIDENCE_MAX_ITEMS = 20;

  const SOURCE_EVIDENCE_REFERENCE_FIELDS = [
    "document_id",
    "version_id",
    "chunk_id",
    "page_start",
    "page_end",
    "request_id",
    "citation_ref",
  ];

  const SOURCE_RESOLVE_BODY_FIELDS = [
    "document_id",
    "version_id",
    "chunk_id",
    "page_start",
    "page_end",
    "request_id",
    "citation_ref",
  ];

  const SAFE_SOURCE_EVIDENCE_FIELDS = [
    "authorization_status",
    "source_display_name",
    "source_type",
    "document_id",
    "version_id",
    "chunk_id",
    "page_start",
    "page_end",
    "title_path",
    "text_excerpt",
    "excerpt_char_count",
    "token_count",
    "retrieval_method",
    "score",
    "request_id",
    "trace_id",
    "metadata",
  ];

  const SAFE_SOURCE_EVIDENCE_SUMMARY_FIELDS = [
    "authorization_status",
    "source_display_name",
    "source_type",
    "document_id",
    "version_id",
    "chunk_id",
    "page_start",
    "page_end",
    "title_path",
    "retrieval_method",
    "score",
    "request_id",
    "trace_id",
  ];

  const SAFE_SOURCE_EVIDENCE_FAILURE_FIELDS = [
    "request_id",
    "trace_id",
    "failure_stage",
    "error_code",
    "next_step",
  ];

  const SAFE_SOURCE_EVIDENCE_METADATA_FIELDS = [
    "chunk_index",
    "sequence",
    "parent_chunk_id",
    "child_chunk_ids",
    "neighbor_prev_chunk_id",
    "neighbor_next_chunk_id",
  ];

  const SAFE_STATUS_FIELDS = [
    "status",
    "chunk_count",
    "embedding_provider",
    "embedding_model",
    "embedding_version",
    "embedding_dim",
    "vector_count",
    "index_status",
    "job_id",
    "attempt_count",
    "last_attempt_at",
    "next_retry_at",
    "error_code",
    "error_summary",
    "request_id",
    "trace_id",
  ];

  const SAFE_DOCUMENT_REVIEW_FIELDS = [
    "document_id",
    "version_id",
    "source_display_name",
    "source_type",
    "status",
    "created_by",
    "created_at",
    "updated_at",
    "chunk_count",
    "embedding_provider",
    "embedding_model",
    "embedding_version",
    "embedding_dim",
    "vector_count",
    "index_status",
    "error_code",
    "error_summary",
    "request_id",
    "trace_id",
  ];

  const SAFE_DOCUMENT_REVIEW_DETAIL_FIELDS = [
    "document_id",
    "version_id",
    "source_display_name",
    "source_type",
    "status",
    "created_by",
    "created_at",
    "updated_at",
    "chunk_count",
    "embedding_provider",
    "embedding_model",
    "embedding_version",
    "embedding_dim",
    "vector_count",
    "index_status",
    "job_id",
    "attempt_count",
    "last_attempt_at",
    "next_retry_at",
    "deleted_at",
    "error_code",
    "error_summary",
    "request_id",
    "trace_id",
  ];

  const SAFE_DOCUMENT_REVIEW_LIFECYCLE_FIELDS = [
    "status",
    "label",
    "description",
    "position",
    "tone",
    "is_current",
    "is_failure",
    "is_known",
  ];

  const SAFE_DIAGNOSTICS_SUMMARY_FIELDS = [
    "tenant_id",
    "user_id",
    "request_id",
    "trace_id",
    "action",
    "status",
    "top_k",
    "result_count",
    "highest_rerank_score",
    "citation_count",
    "context_item_count",
    "context_source_count",
    "generation_provider",
    "generation_model",
    "generation_version",
    "prompt_token_count",
    "completion_token_count",
    "total_token_count",
    "event_count",
    "latency_ms",
    "failure_stage",
    "error_code",
  ];

  const SAFE_DIAGNOSTICS_STAGE_FIELDS = [
    "name",
    "status",
    "latency_ms",
    "error_code",
    "counts",
  ];

  const SAFE_DIAGNOSTICS_TIMELINE_FIELDS = SAFE_DIAGNOSTICS_STAGE_FIELDS;

  const SAFE_DIAGNOSTICS_COUNT_FIELDS = [
    "top_k",
    "result_count",
    "dense_top_k",
    "sparse_top_k",
    "dense_input_count",
    "sparse_input_count",
    "deduped_count",
    "filtered_count",
    "threshold",
    "threshold_decision",
    "input_count",
    "output_count",
    "highest_score",
    "model_candidate_count",
    "metadata_filter_count",
    "acl_filter",
    "tenant_filter",
    "context_item_count",
    "context_source_count",
    "packed_chunk_count",
    "citation_count",
    "prompt_token_count",
    "completion_token_count",
    "total_token_count",
    "event_count",
  ];

  const SAFE_DIAGNOSTICS_REPORT_FIELDS = [
    "lookup",
    "summary",
    "stages",
    "next_steps",
    "generated_at",
  ];

  const SAFE_EVAL_REPORT_SUMMARY_FIELDS = [
    "report_filename",
    "generated_at",
    "report_type",
    "dataset_version",
    "dataset_name",
    "case_count",
    "passed_count",
    "failed_count",
    "retrieval_hit_rate",
    "citation_coverage",
    "no_answer_correctness",
    "acl_isolation",
    "prompt_injection",
    "average_latency_ms",
    "decision",
    "failed_metric_names",
    "failure_stages",
  ];

  const SAFE_EVAL_CASE_FIELDS = [
    "case_id",
    "failure_stage",
    "matched_documents",
    "matched_chunks",
    "matched_citations",
    "retrieval_result_count",
    "context_item_count",
    "citation_count",
    "unsupported_count",
    "forged_reference_count",
    "prompt_risk_count",
    "request_id",
    "trace_id",
    "top_k",
    "latency_ms",
    "generation",
  ];

  const SAFE_EVAL_GENERATION_FIELDS = [
    "provider",
    "model",
    "version",
    "finish_reason",
    "error_code",
    "token_usage",
  ];

  const SAFE_EVAL_GATE_FIELDS = [
    "metric",
    "threshold_name",
    "passed",
    "expected",
    "actual",
  ];

  const SAFE_EVAL_REPORT_EXPORT_FIELDS = [
    "summary",
    "failed_cases",
    "gate_metrics",
    "next_steps",
  ];

  const SAFE_AUDIT_LOG_FIELDS = [
    "id",
    "tenant_id",
    "user_id",
    "request_id",
    "trace_id",
    "action",
    "resource_type",
    "resource_id",
    "status",
    "latency_ms",
    "error_code",
    "created_at",
    "safe_summary",
    "association",
    "safe_counts",
  ];

  const SAFE_AUDIT_ASSOCIATION_FIELDS = [
    "agent_run_id",
    "tool_call_id",
    "tool_name",
    "permission",
    "status",
    "error_code",
    "latency_ms",
    "arguments_summary",
    "result_summary",
    "steps_used",
    "tool_calls_used",
    "validation_counts",
  ];

  const SAFE_TOOL_EVENT_FIELDS = [
    "event",
    "agent_run_id",
    "tool_call_id",
    "tool_name",
    "status",
    "latency_ms",
    "error_code",
    "request_id",
    "trace_id",
    "next_step",
    "audit_ref",
    "review_ref",
  ];

  const SAFE_TOOL_EVENT_METADATA_FIELDS = [
    "tool_event",
    "tool_events",
    "tool_event_summary",
  ];

  const SAFE_AUDIT_EXPORT_FIELDS = [
    "export_id",
    "generated_at",
    "filter_summary",
    "fields",
    "item_count",
    "request_ids",
    "trace_ids",
    "items",
  ];

  const SAFE_AUDIT_COUNT_FIELDS = [
    "metadata_count",
    "resource_metadata_count",
    "role_count",
    "permission_count",
    "citation_count",
    "context_item_count",
    "context_source_count",
    "result_count",
    "event_count",
    "top_k",
    "input_token_count",
    "output_token_count",
    "total_token_count",
    "steps_used",
    "tool_calls_used",
    "tool_event_count",
    "tool_call_count",
    "tool_result_count",
    "tool_error_count",
    "validated_citation_count",
    "unsupported_citation_count",
    "failed_tool_reference_count",
    "termination_reason",
    "failure_stage",
    "auth_method",
    "decision",
    "validation_status",
  ];

  const SAFE_REVIEW_IDENTIFIER_FIELDS = [
    "document_id",
    "version_id",
    "chunk_id",
    "page_start",
    "page_end",
    "citation_ref",
    "eval_report_filename",
    "eval_case_id",
    "audit_log_id",
    "agent_run_id",
    "tool_call_id",
  ];

  const SAFE_REVIEW_SUMMARY_FIELDS = [
    "failure_stage",
    "error_code",
    "reason_code",
    "metric_name",
    "expected_behavior",
    "observed_behavior",
    "risk_label",
    "safe_note",
    "citation_count",
    "unsupported_count",
    "forged_reference_count",
    "prompt_risk_count",
    "retrieval_result_count",
    "context_item_count",
    "tool_call_count",
    "tool_event_count",
    "tool_result_count",
    "tool_error_count",
    "latency_ms",
  ];

  const SAFE_REVIEW_STATUS_HISTORY_FIELDS = [
    "status",
    "changed_by",
    "changed_at",
    "reason_code",
  ];

  const SAFE_EVAL_CANDIDATE_FIELDS = [
    "candidate_id",
    "source_review_item_id",
    "case_type",
    "safe_identifiers",
    "failure_stage",
    "safe_metric_counts",
    "expected_behavior",
    "request_id",
    "trace_id",
    "requires_human_confirmation",
  ];

  const SAFE_REVIEW_ITEM_FIELDS = [
    "id",
    "item_type",
    "severity",
    "status",
    "request_id",
    "trace_id",
    "source_view",
    "safe_identifiers",
    "safe_summary",
    "status_history",
    "allowed_transitions",
    "eval_candidate",
    "created_by",
    "tenant_id",
    "created_at",
    "updated_at",
  ];

  const GOVERNANCE_VIEWS = [
    "document-review",
    "source-evidence",
    "retrieval-diagnostics",
    "eval-evidence",
    "audit-explorer",
    "review-queue",
  ];

  const GOVERNANCE_BACKEND_VIEW_MAP = {
    "source-evidence": "source",
    "retrieval-diagnostics": "diagnostics",
  };

  const GOVERNANCE_SAFE_FIELDS = {
    scope: ["tenant_id", "user_id", "request_id", "trace_id"],
    sourceEvidence: [
      "source_display_name",
      "document_id",
      "version_id",
      "chunk_id",
      "page_start",
      "page_end",
      "status",
      "request_id",
      "trace_id",
    ],
    documentReview: SAFE_DOCUMENT_REVIEW_FIELDS,
    documentReviewDetail: SAFE_DOCUMENT_REVIEW_DETAIL_FIELDS,
    documentReviewLifecycle: SAFE_DOCUMENT_REVIEW_LIFECYCLE_FIELDS,
    documentStatus: SAFE_STATUS_FIELDS,
    diagnosticsSummary: SAFE_DIAGNOSTICS_SUMMARY_FIELDS,
    evalSummary: SAFE_EVAL_REPORT_SUMMARY_FIELDS,
    evalCase: SAFE_EVAL_CASE_FIELDS,
    evalGate: SAFE_EVAL_GATE_FIELDS,
    auditSummary: SAFE_AUDIT_LOG_FIELDS,
    auditAssociation: SAFE_AUDIT_ASSOCIATION_FIELDS,
    toolEvent: SAFE_TOOL_EVENT_FIELDS,
    auditExport: SAFE_AUDIT_EXPORT_FIELDS,
    auditCount: SAFE_AUDIT_COUNT_FIELDS,
    reviewItem: SAFE_REVIEW_ITEM_FIELDS,
    reviewIdentifier: SAFE_REVIEW_IDENTIFIER_FIELDS,
    reviewSummary: SAFE_REVIEW_SUMMARY_FIELDS,
    reviewStatusHistory: SAFE_REVIEW_STATUS_HISTORY_FIELDS,
    evalCandidate: SAFE_EVAL_CANDIDATE_FIELDS,
  };

  const DOCUMENT_STATUS_ENDPOINT_PARTS = ["/documents/", "/versions/", "/status"];
  const DOCUMENT_REVIEW_ENDPOINT = "/documents/review";
  const DIAGNOSTICS_ENDPOINT = "/diagnostics/resolve";
  const EVAL_EVIDENCE_REPORTS_ENDPOINT = "/eval/reports";
  const AUDIT_EXPLORER_LOGS_ENDPOINT = "/audit/logs";
  const AUDIT_EXPLORER_EXPORT_ENDPOINT = "/audit/export";
  const REVIEW_QUEUE_ITEMS_ENDPOINT = "/review/items";

  const STATUS_MAP = {
    "uploaded": ["[UP]", "Uploaded", "working"],
    "parsing": ["[..]", "Parsing", "working"],
    "parsed": ["[OK]", "Parsed", "working"],
    "chunking": ["[..]", "Chunking", "working"],
    "chunked": ["[OK]", "Chunked", "working"],
    "embedding": ["[..]", "Embedding", "working"],
    "embedded": ["[OK]", "Embedded", "working"],
    "indexing": ["[..]", "Indexing", "working"],
    "retrieval_ready": ["[OK]", "Retrieval ready", "ready"],
    "failed_retryable": ["[!]", "Retryable failure", "failed"],
    "failed_terminal": ["[!]", "Terminal failure", "failed"],
    "deleted": ["[X]", "Deleted", "failed"],
    "success": ["[OK]", "Success", "ready"],
    "failure": ["[!]", "Failure", "failed"],
    "denied": ["[!]", "Denied", "failed"],
    "degraded": ["[..]", "Degraded", "working"],
    "open": ["[..]", "Open", "working"],
    "accepted": ["[OK]", "Accepted", "ready"],
    "rejected": ["[X]", "Rejected", "failed"],
    "needs_followup": ["[!]", "Needs follow-up", "working"],
    "converted_to_eval_case": ["[OK]", "Eval candidate", "ready"],
    "not_available": ["[--]", "Not available", "unknown"],
  };

  const state = {
    lastTrigger: null,
    diagnosticsReports: {
      default: null,
      governance: null,
    },
    evalEvidenceReport: null,
    evalEvidenceRequestToken: 0,
    auditExplorerExport: null,
    auditExplorerRequestToken: 0,
    reviewQueueExport: null,
    reviewQueueCandidate: null,
    reviewQueueRequestToken: 0,
    sourceEvidenceSummary: null,
    toolEvents: null,
  };

  document.addEventListener("DOMContentLoaded", init);

  function init() {
    bindTabs();
    bindGovernanceTabs();
    bindGovernanceBackendLinks();
    bindForms();
    hydrateCitationInputs(parseCitationInputsFromLocation());
  }

  function bindTabs() {
    document.querySelectorAll("[data-view]").forEach((tab) => {
      tab.addEventListener("click", () => activateView(tab.dataset.view));
    });
    bindTabKeyboard("[data-view]", "view", activateView);
  }

  function bindGovernanceTabs() {
    document.querySelectorAll("[data-governance-view]").forEach((tab) => {
      tab.addEventListener("click", () => activateGovernanceView(tab.dataset.governanceView));
    });
    bindTabKeyboard("[data-governance-view]", "governanceView", activateGovernanceView);
  }

  function bindGovernanceBackendLinks() {
    document.querySelectorAll("[data-governance-link-view]").forEach((button) => {
      button.addEventListener("click", () => {
        activateView(button.dataset.governanceLinkView, { focusTab: true });
      });
    });
  }

  function bindTabKeyboard(selector, datasetKey, activate) {
    const tabs = Array.from(document.querySelectorAll(selector));
    tabs.forEach((tab, index) => {
      tab.addEventListener("keydown", (event) => {
        const nextIndex = nextTabIndex(event.key, index, tabs.length);
        if (nextIndex === null) {
          return;
        }
        event.preventDefault();
        const nextTab = tabs[nextIndex];
        activate(nextTab.dataset[datasetKey], { focusTab: true });
      });
    });
  }

  function nextTabIndex(key, currentIndex, count) {
    if (!count) {
      return null;
    }
    if (key === "ArrowRight" || key === "ArrowDown") {
      return (currentIndex + 1) % count;
    }
    if (key === "ArrowLeft" || key === "ArrowUp") {
      return (currentIndex - 1 + count) % count;
    }
    if (key === "Home") {
      return 0;
    }
    if (key === "End") {
      return count - 1;
    }
    return null;
  }

  function bindForms() {
    byId("source-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      state.lastTrigger = event.submitter || document.activeElement;
      await fetchSourceResolve(collectSourcePayload());
    });
    byId("clear-source").addEventListener("click", () => {
      byId("source-form").reset();
      byId("citation-json").value = "";
      byId("source-result").replaceChildren();
      hideAlert();
      closeInspector();
    });
    byId("status-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const documentId = byId("status-document").value.trim();
      const versionId = byId("status-version").value.trim();
      await fetchDocumentStatus(documentId, versionId);
    });
    byId("diagnostics-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      await fetchDiagnostics();
    });
    const governanceDiagnosticsForm = optionalById("governance-diagnostics-form");
    if (governanceDiagnosticsForm) {
      governanceDiagnosticsForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        await fetchGovernanceDiagnostics();
      });
    }
    const reviewForm = optionalById("document-review-form");
    if (reviewForm) {
      reviewForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        await fetchDocumentReviewList();
      });
    }
    const reviewDetailButton = optionalById("document-review-detail-button");
    if (reviewDetailButton) {
      reviewDetailButton.addEventListener("click", async () => {
        const documentId = byId("document-review-document").value.trim();
        const versionId = byId("document-review-version").value.trim();
        await fetchDocumentReviewDetail(documentId, versionId || null);
      });
    }
    const evidenceForm = optionalById("source-evidence-form");
    if (evidenceForm) {
      evidenceForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const parsed = parseSourceEvidenceInput({
          raw: optionalById("source-evidence-json").value,
          manual: collectSourceEvidenceManualInputs(),
        });
        if (parsed.errors.length) {
          clearSourceEvidenceRegions();
          renderSourceEvidenceErrors(parsed.errors);
          setLive("Source evidence input needs attention.");
          return;
        }
        await resolveSourceEvidenceSet(parsed.references);
      });
    }
    const copyEvidence = optionalById("copy-source-evidence-summary");
    if (copyEvidence) {
      copyEvidence.addEventListener("click", copySourceEvidenceSummary);
    }
    const evalEvidenceForm = optionalById("eval-evidence-form");
    if (evalEvidenceForm) {
      evalEvidenceForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        await fetchEvalEvidenceReports();
      });
    }
    const evalEvidenceLoad = optionalById("eval-evidence-load");
    if (evalEvidenceLoad) {
      evalEvidenceLoad.addEventListener("click", fetchEvalEvidenceDetail);
    }
    const copyEvalEvidence = optionalById("copy-eval-evidence-report");
    if (copyEvalEvidence) {
      copyEvalEvidence.addEventListener("click", copyEvalEvidenceReport);
    }
    const downloadEvalEvidence = optionalById("download-eval-evidence-report");
    if (downloadEvalEvidence) {
      downloadEvalEvidence.addEventListener("click", downloadEvalEvidenceReport);
    }
    const auditExplorerForm = optionalById("audit-explorer-form");
    if (auditExplorerForm) {
      auditExplorerForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        await fetchAuditExplorerLogs();
      });
    }
    const copyAuditExport = optionalById("audit-explorer-copy-export");
    if (copyAuditExport) {
      copyAuditExport.addEventListener("click", copyAuditExplorerExport);
    }
    const downloadAuditExport = optionalById("audit-explorer-download-export");
    if (downloadAuditExport) {
      downloadAuditExport.addEventListener("click", downloadAuditExplorerExport);
    }
    const reviewQueueCreateForm = optionalById("review-queue-create-form");
    if (reviewQueueCreateForm) {
      reviewQueueCreateForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        await createReviewQueueItem();
      });
    }
    const reviewQueueFilterForm = optionalById("review-queue-filter-form");
    if (reviewQueueFilterForm) {
      reviewQueueFilterForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        await fetchReviewQueueItems();
      });
    }
    const reviewQueueLoadDetail = optionalById("review-queue-load-detail");
    if (reviewQueueLoadDetail) {
      reviewQueueLoadDetail.addEventListener("click", fetchReviewQueueDetail);
    }
    const reviewQueueConvert = optionalById("review-queue-convert-candidate");
    if (reviewQueueConvert) {
      reviewQueueConvert.addEventListener("click", convertReviewQueueCandidate);
    }
    const reviewQueueCopy = optionalById("review-queue-copy-export");
    if (reviewQueueCopy) {
      reviewQueueCopy.addEventListener("click", copyReviewQueueExport);
    }
    const reviewQueueDownload = optionalById("review-queue-download-export");
    if (reviewQueueDownload) {
      reviewQueueDownload.addEventListener("click", downloadReviewQueueExport);
    }
    byId("close-inspector").addEventListener("click", closeInspector);
    byId("copy-diagnostics").addEventListener("click", copyDiagnostics);
    byId("copy-diagnostics-report").addEventListener("click", () =>
      copyDiagnosticsReport("default"),
    );
    byId("download-diagnostics-report").addEventListener("click", () =>
      downloadDiagnosticsReport("default"),
    );
    const copyGovernanceDiagnosticsReport = optionalById("copy-governance-diagnostics-report");
    if (copyGovernanceDiagnosticsReport) {
      copyGovernanceDiagnosticsReport.addEventListener("click", () =>
        copyDiagnosticsReport("governance"),
      );
    }
    const downloadGovernanceDiagnosticsReport = optionalById("download-governance-diagnostics-report");
    if (downloadGovernanceDiagnosticsReport) {
      downloadGovernanceDiagnosticsReport.addEventListener("click", () =>
        downloadDiagnosticsReport("governance"),
      );
    }
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !byId("inspector-sheet").hidden) {
        closeInspector();
      }
      trapInspectorFocus(event);
    });
  }

  function activateView(viewName, options = {}) {
    let selectedTab = null;
    document.querySelectorAll("[data-view]").forEach((tab) => {
      const isActive = tab.dataset.view === viewName;
      tab.classList.toggle("is-active", isActive);
      tab.setAttribute("aria-selected", String(isActive));
      tab.setAttribute("tabindex", isActive ? "0" : "-1");
      if (isActive) {
        selectedTab = tab;
      }
    });
    document.querySelectorAll(".view").forEach((view) => {
      const isActive = view.id === `view-${viewName}`;
      view.hidden = !isActive;
      view.classList.toggle("is-active", isActive);
    });
    if (options.focusTab && selectedTab && typeof selectedTab.focus === "function") {
      selectedTab.focus();
    }
  }

  function activateGovernanceView(viewName, options = {}) {
    if (!GOVERNANCE_VIEWS.includes(viewName)) {
      return;
    }
    let selectedTab = null;
    document.querySelectorAll("[data-governance-view]").forEach((tab) => {
      const isActive = tab.dataset.governanceView === viewName;
      tab.classList.toggle("is-active", isActive);
      tab.setAttribute("aria-selected", String(isActive));
      tab.setAttribute("tabindex", isActive ? "0" : "-1");
      if (isActive) {
        selectedTab = tab;
      }
    });
    document.querySelectorAll(".governance-view").forEach((view) => {
      const isActive = view.id === `governance-view-${viewName}`;
      view.hidden = !isActive;
      view.classList.toggle("is-active", isActive);
    });
    const detail = optionalById("governance-detail");
    if (detail) {
      detail.replaceChildren();
    }
    hideAlert();
    if (GOVERNANCE_BACKEND_VIEW_MAP[viewName]) {
      activateView(GOVERNANCE_BACKEND_VIEW_MAP[viewName]);
    }
    if (viewName === "retrieval-diagnostics") {
      clearDiagnosticsRegions(diagnosticsRenderTargets());
      clearDiagnosticsRegions({
        summaryId: "governance-diagnostics-summary",
        timelineId: "governance-diagnostics-timeline",
        nextStepsId: "governance-diagnostics-next-steps",
        reportKey: "governance",
      });
    }
    if (viewName === "eval-evidence") {
      clearEvalEvidenceRegions();
    }
    if (viewName === "audit-explorer") {
      clearAuditExplorerRegions();
    }
    if (viewName === "review-queue") {
      clearReviewQueueRegions();
    }
    if (options.focusTab && selectedTab && typeof selectedTab.focus === "function") {
      selectedTab.focus();
    }
    setLive(`${viewName.replace(/-/g, " ")} selected.`);
  }

  function collectSourcePayload() {
    const form = byId("source-form");
    const formData = new FormData(form);
    const pasted = parsePastedJson(byId("citation-json").value);
    const payload = {};
    CITATION_INPUT_FIELDS.forEach((field) => {
      const value = normalizeValue(formData.get(field) || pasted[field]);
      if (value !== "") {
        const parsedValue = field.startsWith("page_") ? parsePageValue(value, field) : value;
        if (parsedValue !== null) {
          payload[field] = parsedValue;
        }
      }
    });
    return payload;
  }

  function parseCitationInputsFromLocation() {
    const values = {};
    const params = new URLSearchParams(window.location.search);
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    CITATION_INPUT_FIELDS.forEach((field) => {
      values[field] = params.get(field) || hash.get(field) || "";
    });
    return values;
  }

  function parsePastedJson(raw) {
    if (!raw.trim()) {
      return {};
    }
    try {
      const parsed = JSON.parse(raw);
      const safe = {};
      CITATION_INPUT_FIELDS.forEach((field) => {
        if (Object.prototype.hasOwnProperty.call(parsed, field)) {
          safe[field] = parsed[field];
        }
      });
      return safe;
    } catch {
      showAlert("Citation JSON could not be parsed. Only structured citation identifiers are accepted.");
      return {};
    }
  }

  function parseSourceEvidenceInput({ raw, manual }) {
    const errors = [];
    const candidates = [];
    const trimmedRaw = normalizeValue(raw);
    if (trimmedRaw) {
      const directReference = isJsonLike(trimmedRaw) ? null : parseSourceEvidenceLink(trimmedRaw);
      if (directReference) {
        candidates.push(directReference);
      } else {
        try {
          collectSourceEvidenceCandidates(JSON.parse(trimmedRaw), candidates);
        } catch {
          return {
            references: [],
            errors: ["Citation JSON or evidence link could not be parsed. Evidence results were cleared."],
          };
        }
      }
    }

    const manualReference = normalizeSourceEvidenceReference(manual || {});
    if (manualReference.hasAnyInput) {
      if (manualReference.error) {
        errors.push(manualReference.error);
      } else {
        candidates.push(manualReference.reference);
      }
    }

    const references = [];
    const seen = new Set();
    candidates.forEach((candidate, index) => {
      const normalized = normalizeSourceEvidenceReference(candidate || {});
      if (!normalized.hasAnyInput) {
        return;
      }
      if (normalized.error) {
        errors.push(`Citation ${index + 1}: ${normalized.error}`);
        return;
      }
      const key = sourceEvidenceReferenceKey(normalized.reference);
      if (seen.has(key)) {
        return;
      }
      seen.add(key);
      references.push(normalized.reference);
    });

    if (!references.length && !errors.length) {
      errors.push("At least one document/version/chunk reference is required.");
    }
    if (references.length > SOURCE_EVIDENCE_MAX_ITEMS) {
      errors.push(`Source Evidence accepts at most ${SOURCE_EVIDENCE_MAX_ITEMS} references per batch.`);
      return { references: [], errors };
    }
    return { references: errors.length ? [] : references, errors };
  }

  function collectSourceEvidenceCandidates(value, candidates) {
    if (Array.isArray(value)) {
      value.forEach((item) => collectSourceEvidenceCandidates(item, candidates));
      return;
    }
    if (!value || typeof value !== "object") {
      return;
    }
    const evidenceLinkCandidates = sourceEvidenceLinkCandidates(value);
    if (evidenceLinkCandidates.length) {
      evidenceLinkCandidates.forEach((candidate) => candidates.push(candidate));
    } else if (hasSourceEvidenceIdentifier(value)) {
      candidates.push(value);
    }
    [
      value.citations,
      value.evidence_links,
      value.evidence,
      value.sources,
      value.metadata && value.metadata.citations,
      value.metadata && value.metadata.evidence_links,
      value.metadata && value.metadata.evidence,
      value.data && value.data.citations,
      value.data && value.data.evidence_links,
      value.openwebui_metadata && value.openwebui_metadata.citations,
      value.openwebui_metadata && value.openwebui_metadata.evidence_links,
    ].forEach((nested) => {
      if (Array.isArray(nested)) {
        nested.forEach((item) => collectSourceEvidenceCandidates(item, candidates));
      }
    });
    ["source_evidence_link", "sidecar_link", "source_link", "link"].forEach((field) => {
      const reference = parseSourceEvidenceLink(value[field]);
      if (reference) {
        candidates.push(reference);
      }
    });
  }

  function parseToolEventFallback(raw) {
    const trimmed = normalizeValue(raw);
    if (!trimmed) {
      return { tool_events: [], errors: ["Tool event JSON is required."] };
    }
    try {
      const parsed = JSON.parse(trimmed);
      const events = [];
      collectToolEventCandidates(parsed, events);
      const safeEvents = [];
      const seen = new Set();
      events.forEach((event) => {
        const safe = sanitizeToolEvent(event);
        if (!safe) {
          return;
        }
        const key = [safe.event, safe.agent_run_id || "", safe.tool_call_id, safe.request_id, safe.trace_id].join("\u001f");
        if (seen.has(key)) {
          return;
        }
        seen.add(key);
        safeEvents.push(safe);
      });
      if (!safeEvents.length) {
        return { tool_events: [], errors: ["No safe tool event summaries were found."] };
      }
      return { tool_events: safeEvents, errors: [] };
    } catch {
      return { tool_events: [], errors: ["Tool event JSON could not be parsed. Tool event state was cleared."] };
    }
  }

  function collectToolEventCandidates(value, events) {
    if (Array.isArray(value)) {
      value.forEach((item) => collectToolEventCandidates(item, events));
      return;
    }
    if (!value || typeof value !== "object") {
      return;
    }
    if (isToolEventCandidate(value)) {
      events.push(value);
    }
    SAFE_TOOL_EVENT_METADATA_FIELDS.forEach((field) => {
      const nested = value[field];
      if (Array.isArray(nested)) {
        nested.forEach((item) => collectToolEventCandidates(item, events));
      } else if (nested && typeof nested === "object") {
        collectToolEventCandidates(nested, events);
      }
    });
    [
      value.metadata && value.metadata.tool_event,
      value.metadata && value.metadata.tool_events,
      value.data && value.data.tool_event,
      value.data && value.data.tool_events,
      value.openwebui_metadata && value.openwebui_metadata.tool_event,
      value.openwebui_metadata && value.openwebui_metadata.tool_events,
    ].forEach((nested) => collectToolEventCandidates(nested, events));
  }

  function isToolEventCandidate(value) {
    return value.event === "tool_call" || value.event === "tool_result";
  }

  function sanitizeToolEvent(value) {
    const safe = {};
    SAFE_TOOL_EVENT_FIELDS.forEach((field) => {
      if (!Object.prototype.hasOwnProperty.call(value, field)) {
        return;
      }
      const item = sanitizeToolEventField(field, value[field]);
      if (item !== null && item !== undefined && item !== "") {
        safe[field] = item;
      }
    });
    if (!["tool_call", "tool_result"].includes(safe.event)) {
      return null;
    }
    if (!safe.tool_call_id || !safe.tool_name || !safe.status || !safe.request_id || !safe.trace_id) {
      return null;
    }
    return safe;
  }

  function sanitizeToolEventField(field, value) {
    if (field === "latency_ms") {
      return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
    }
    if (value === null || value === undefined) {
      return field === "error_code" ? null : "";
    }
    if (typeof value !== "string") {
      return null;
    }
    const normalized = value.trim();
    if (!normalized || normalized.length > 240 || looksUnsafeToolEventValue(normalized)) {
      return null;
    }
    if (["audit_ref", "review_ref"].includes(field)) {
      return normalized.startsWith("/governance?") ? normalized : null;
    }
    return normalized;
  }

  function looksUnsafeToolEventValue(value) {
    const lowered = value.toLowerCase();
    return (
      lowered.includes("authorization:") ||
      lowered.includes("bearer ") ||
      lowered.includes("api_key") ||
      lowered.includes("password=") ||
      lowered.includes("secret=") ||
      lowered.includes("token=") ||
      lowered.startsWith("file:") ||
      lowered.startsWith(["mi", "nio:"].join("")) ||
      /^[A-Za-z]:[\\/]/.test(value)
    );
  }

  function renderToolEventFallback(parsed) {
    state.toolEvents = null;
    if (!parsed || parsed.errors?.length) {
      clearAuditExplorerRegions();
      clearReviewQueueRegions();
      const rows = (parsed && parsed.errors ? parsed.errors : ["Tool event fallback could not be displayed."]).map(
        (message) => resultRow("tool_event_error", message, false),
      );
      byId("audit-explorer-detail").replaceChildren(...rows, safeAuditNextStepCommand());
      showAlert("Tool event fallback could not be displayed safely.");
      setLive("Tool event fallback cleared.");
      return;
    }
    const events = (Array.isArray(parsed.tool_events) ? parsed.tool_events : [])
      .map((event) => sanitizeToolEvent(event || {}))
      .filter(Boolean);
    if (!events.length) {
      renderToolEventFallback({ errors: ["No safe tool event summaries were found."] });
      return;
    }
    state.toolEvents = events;
    renderAuditExplorerList({
      items: buildToolEventAuditItems(events),
      next_steps: ["Open Audit Explorer with request_id or trace_id to inspect backend-confirmed records."],
    });
    renderReviewQueueDetail(buildToolEventReviewItem(events));
    setLive("Tool event fallback loaded.");
  }

  function buildToolEventAuditItems(events) {
    return events.map((event, index) => ({
      id: `tool-event-${index + 1}`,
      request_id: event.request_id,
      trace_id: event.trace_id,
      action: "rag.openwebui.tool_event",
      resource_type: "tool_event",
      resource_id: event.tool_call_id,
      status: event.status === "success" || event.status === "started" ? "success" : "failure",
      latency_ms: event.latency_ms,
      error_code: event.error_code,
      safe_counts: {
        tool_event_count: 1,
        tool_call_count: event.event === "tool_call" ? 1 : 0,
        tool_result_count: event.event === "tool_result" ? 1 : 0,
        tool_error_count: event.error_code ? 1 : 0,
      },
      association: {
        agent_run_id: event.agent_run_id,
        tool_call_id: event.tool_call_id,
        tool_name: event.tool_name,
        status: event.status,
        error_code: event.error_code,
        latency_ms: event.latency_ms,
      },
    }));
  }

  function buildToolEventReviewItem(events) {
    const primary = events.find((event) => event.error_code) || events[0];
    return {
      id: `tool-event-${primary.tool_call_id}`,
      item_type: primary.error_code ? "agent_tool_failure" : "agent_tool_event",
      severity: primary.error_code ? "medium" : "low",
      status: "open",
      request_id: primary.request_id,
      trace_id: primary.trace_id,
      source_view: "audit_explorer",
      safe_identifiers: {
        agent_run_id: primary.agent_run_id,
        tool_call_id: primary.tool_call_id,
      },
      safe_summary: {
        failure_stage: "tool_event",
        error_code: primary.error_code,
        tool_call_count: events.filter((event) => event.event === "tool_call").length,
        latency_ms: primary.latency_ms,
      },
      allowed_transitions: ["accepted", "needs_followup"],
      status_history: [],
    };
  }

  function sourceEvidenceLinkCandidates(value) {
    const direct = pickFields(value, SOURCE_EVIDENCE_REFERENCE_FIELDS);
    const candidates = [];
    const urlReference = parseSourceEvidenceLink(value.evidence_url);
    if (urlReference) {
      candidates.push({ ...urlReference, ...direct });
    } else if (value.evidence_query && typeof value.evidence_query === "object") {
      candidates.push({ ...value.evidence_query, ...direct });
    }
    return candidates;
  }

  function hasSourceEvidenceIdentifier(value) {
    return Boolean(value.document_id || value.version_id || value.chunk_id);
  }

  function parseSourceEvidenceLink(value) {
    const raw = normalizeValue(value);
    if (!raw) {
      return null;
    }
    const queryPart = raw.includes("?") ? raw.split("?")[1].split("#")[0] : "";
    const hashPart = sourceEvidenceHashParams(raw.includes("#") ? raw.split("#")[1] : "");
    const queryParams = new URLSearchParams(queryPart);
    const hashParams = new URLSearchParams(hashPart);
    const reference = {};
    SOURCE_EVIDENCE_REFERENCE_FIELDS.forEach((field) => {
      const queryValue = queryParams.get(field) || hashParams.get(field) || "";
      if (queryValue) {
        reference[field] = queryValue;
      }
    });
    return hasSourceEvidenceIdentifier(reference) ? reference : null;
  }

  function sourceEvidenceHashParams(hashPart) {
    const normalized = normalizeValue(hashPart).replace(/^#/, "");
    if (!normalized) {
      return "";
    }
    if (normalized.includes("?")) {
      return normalized.split("?").slice(1).join("?");
    }
    return normalized;
  }

  function collectSourceEvidenceManualInputs() {
    return {
      document_id: optionalById("source-evidence-document").value,
      version_id: optionalById("source-evidence-version").value,
      chunk_id: optionalById("source-evidence-chunk").value,
      page_start: optionalById("source-evidence-page-start").value,
      page_end: optionalById("source-evidence-page-end").value,
      request_id: optionalById("source-evidence-request").value,
    };
  }

  function normalizeSourceEvidenceReference(candidate) {
    const reference = {};
    SOURCE_EVIDENCE_REFERENCE_FIELDS.forEach((field) => {
      if (Object.prototype.hasOwnProperty.call(candidate, field)) {
        const value = normalizeValue(candidate[field]);
        if (value !== "") {
          reference[field] = value;
        }
      }
    });
    const hasAnyInput = SOURCE_EVIDENCE_REFERENCE_FIELDS.some((field) => reference[field]);
    if (!hasAnyInput) {
      return { hasAnyInput: false, reference: {}, error: null };
    }
    if (!reference.document_id || !reference.version_id || !reference.chunk_id) {
      return {
        hasAnyInput: true,
        reference: {},
        error: "document_id, version_id, and chunk_id are required.",
      };
    }
    const pageValidation = normalizeSourceEvidencePageRange(reference);
    if (pageValidation.error) {
      return { hasAnyInput: true, reference: {}, error: pageValidation.error };
    }
    return { hasAnyInput: true, reference: pageValidation.reference, error: null };
  }

  function normalizeSourceEvidencePageRange(reference) {
    const normalized = { ...reference };
    const hasStart = normalized.page_start !== undefined;
    const hasEnd = normalized.page_end !== undefined;
    if (!hasStart && !hasEnd) {
      return { reference: normalized, error: null };
    }
    if (!hasStart || !hasEnd) {
      return { reference: {}, error: "page range must include both page_start and page_end." };
    }
    const pageStart = parsePositiveInteger(normalized.page_start);
    const pageEnd = parsePositiveInteger(normalized.page_end);
    if (pageStart === null || pageEnd === null) {
      return { reference: {}, error: "page range must use positive integers." };
    }
    if (pageEnd < pageStart) {
      return { reference: {}, error: "page_end must be greater than or equal to page_start." };
    }
    normalized.page_start = pageStart;
    normalized.page_end = pageEnd;
    return { reference: normalized, error: null };
  }

  function parsePositiveInteger(value) {
    const normalized = normalizeValue(value);
    if (!/^\d+$/.test(normalized)) {
      return null;
    }
    const parsed = Number(normalized);
    if (!Number.isSafeInteger(parsed) || parsed < 1) {
      return null;
    }
    return parsed;
  }

  function isJsonLike(value) {
    return value.startsWith("{") || value.startsWith("[");
  }

  function sourceEvidenceReferenceKey(reference) {
    return [
      reference.document_id,
      reference.version_id,
      reference.chunk_id,
      reference.page_start || "",
      reference.page_end || "",
      reference.request_id || "",
      reference.citation_ref || "",
    ].join("\u001f");
  }

  async function resolveSourceEvidenceSet(references) {
    if (!references.length) {
      clearSourceEvidenceRegions();
      renderSourceEvidenceErrors(["At least one document/version/chunk reference is required."]);
      return;
    }
    setLive("Resolving source evidence set...");
    hideAlert();
    clearSourceEvidenceRegions();
    const results = [];
    for (const reference of references) {
      results.push(await resolveSourceEvidenceItem(reference));
    }
    renderSourceEvidenceSet(results);
    setLive("Source evidence set resolved.");
  }

  async function resolveSourceEvidenceItem(reference) {
    const payload = pickFields(reference, SOURCE_RESOLVE_BODY_FIELDS);
    try {
      const response = await fetch("/sources/resolve", {
        method: "POST",
        headers: buildHeaders(),
        body: JSON.stringify(payload),
      });
      const envelope = await response.json();
      if (!response.ok || envelope.error) {
        return { status: "failed", error: safeSourceEvidenceFailure(envelope) };
      }
      const data = pickFields(envelope.data || {}, SAFE_SOURCE_EVIDENCE_FIELDS);
      data.authorization_status = data.authorization_status || "authorized";
      if (data.metadata && typeof data.metadata === "object") {
        data.metadata = pickFields(data.metadata, SAFE_SOURCE_EVIDENCE_METADATA_FIELDS);
      } else {
        delete data.metadata;
      }
      return { status: "authorized", data };
    } catch {
      return {
        status: "failed",
        error: safeSourceEvidenceFailure(null),
      };
    }
  }

  function renderSourceEvidenceSet(items) {
    const nodes = items.map((item, index) => sourceEvidenceItemNode(item, index));
    byId("source-evidence-results").replaceChildren(...nodes);
    byId("source-evidence-errors").replaceChildren();
    state.sourceEvidenceSummary = buildSafeSourceEvidenceSummary(items);
  }

  function sourceEvidenceItemNode(item, index) {
    const wrapper = document.createElement("article");
    wrapper.className = "source-evidence-item";
    wrapper.setAttribute("tabindex", "0");
    const label = document.createElement("div");
    label.className = "source-evidence-title";
    label.textContent = `Evidence ${index + 1}`;
    wrapper.append(label);
    if (item.status !== "authorized") {
      const failure = pickFields(item.error || {}, SAFE_SOURCE_EVIDENCE_FAILURE_FIELDS);
      wrapper.append(statusRow("authorization_status", safeStatusNode("safe_failure", "failed")));
      SAFE_SOURCE_EVIDENCE_FAILURE_FIELDS.forEach((field) => {
        const value = failure[field];
        if (value) {
          wrapper.append(resultRow(field, value, false));
        }
      });
      return wrapper;
    }
    const data = pickFields(item.data || {}, SAFE_SOURCE_EVIDENCE_FIELDS);
    wrapper.append(statusRow("authorization_status", safeStatusNode(data.authorization_status || "authorized", "ready")));
    SAFE_SOURCE_EVIDENCE_FIELDS.filter((field) => field !== "authorization_status").forEach((field) => {
      const value = data[field];
      if (value !== undefined && value !== null && value !== "" && !isEmptyObject(value)) {
        wrapper.append(resultRow(field, value, field === "text_excerpt"));
      }
    });
    wrapper.append(sourceEvidenceIdentifierCopyRow(data));
    return wrapper;
  }

  function sourceEvidenceIdentifierCopyRow(data) {
    const identifiers = pickFields(data || {}, [
      "document_id",
      "version_id",
      "chunk_id",
      "page_start",
      "page_end",
      "request_id",
      "trace_id",
    ]);
    const row = resultRow("identifiers", identifiers, false);
    const copy = row.children[2];
    copy.addEventListener("click", () => copyText(JSON.stringify(identifiers, null, 2)));
    return row;
  }

  function safeStatusNode(label, tone) {
    const chip = document.createElement("div");
    chip.className = "status-chip";
    chip.dataset.tone = tone;
    const statusIcon = document.createElement("span");
    statusIcon.textContent = tone === "failed" ? "[!]" : "[OK]";
    const statusLabelElement = document.createElement("span");
    statusLabelElement.textContent = label;
    chip.append(statusIcon, statusLabelElement);
    return chip;
  }

  function safeSourceEvidenceFailure(envelope) {
    const details = (envelope && envelope.error && envelope.error.details) || {};
    const error = (envelope && envelope.error) || {};
    return pickFields(
      {
        request_id: details.request_id || (envelope && envelope.request_id),
        trace_id: details.trace_id || (envelope && envelope.trace_id),
        failure_stage: details.failure_stage || "source_resolve",
        error_code: details.error_code || error.code || "SOURCE_EVIDENCE_UNAVAILABLE",
        next_step: "Retry with authorized identifiers or inspect request_id / trace_id.",
      },
      SAFE_SOURCE_EVIDENCE_FAILURE_FIELDS,
    );
  }

  function renderSourceEvidenceErrors(errors) {
    const rows = errors.map((message) => resultRow("input_error", message, false));
    byId("source-evidence-errors").replaceChildren(...rows);
    state.sourceEvidenceSummary = null;
    showAlert("Source Evidence input could not be resolved safely.");
  }

  function clearSourceEvidenceRegions() {
    byId("source-evidence-results").replaceChildren();
    byId("source-evidence-errors").replaceChildren();
    state.sourceEvidenceSummary = null;
  }

  function buildSafeSourceEvidenceSummary(items) {
    return {
      item_count: items.length,
      items: items.map((item) => {
        if (item.status !== "authorized") {
          return {
            authorization_status: "safe_failure",
            ...pickFields(item.error || {}, SAFE_SOURCE_EVIDENCE_FAILURE_FIELDS),
          };
        }
        return pickFields(item.data || {}, SAFE_SOURCE_EVIDENCE_SUMMARY_FIELDS);
      }),
    };
  }

  function copySourceEvidenceSummary() {
    if (!state.sourceEvidenceSummary) {
      setLive("No source evidence summary available.");
      return;
    }
    copyText(JSON.stringify(state.sourceEvidenceSummary, null, 2));
  }

  function hydrateCitationInputs(values) {
    CITATION_INPUT_FIELDS.forEach((field) => {
      const input = document.querySelector(`[name="${field}"]`);
      if (input && values[field]) {
        input.value = values[field];
      }
    });
  }

  async function fetchSourceResolve(payload) {
    if (!payload.document_id || !payload.version_id || !payload.chunk_id) {
      showAlert("Document, version, and chunk identifiers are required.");
      return;
    }
    setLive("Resolving authorized source...");
    hideAlert();
    try {
      const response = await fetch("/sources/resolve", {
        method: "POST",
        headers: buildHeaders(payload.request_id),
        body: JSON.stringify(payload),
      });
      const envelope = await response.json();
      if (!response.ok || envelope.error) {
        renderSafeFailure("source", envelope, "The source cannot be displayed for this request.");
        return;
      }
      const data = envelope.data || {};
      renderSourceResult(pickFields(data, SAFE_SOURCE_FIELDS));
      openInspector();
      setLive("Authorized source loaded.");
    } catch {
      renderSafeFailure("source", null, "The source cannot be displayed for this request.");
    }
  }

  async function fetchDocumentStatus(documentId, versionId) {
    if (!documentId || !versionId) {
      showAlert("Document and version identifiers are required.");
      return;
    }
    setLive("Loading document status...");
    hideAlert();
    try {
      const response = await fetch(
        `${DOCUMENT_STATUS_ENDPOINT_PARTS[0]}${encodeURIComponent(documentId)}` +
          `${DOCUMENT_STATUS_ENDPOINT_PARTS[1]}${encodeURIComponent(versionId)}` +
          DOCUMENT_STATUS_ENDPOINT_PARTS[2],
        {
          headers: buildHeaders(),
        },
      );
      const envelope = await response.json();
      if (!response.ok || envelope.error) {
        renderSafeFailure("status", envelope, "The status cannot be displayed for this request.");
        return;
      }
      const data = pickFields(envelope.data || {}, SAFE_STATUS_FIELDS);
      renderStatusResult(data);
      setLive("Document status loaded.");
    } catch {
      renderSafeFailure("status", null, "The status cannot be displayed for this request.");
    }
  }

  async function fetchDocumentReviewList() {
    const params = new URLSearchParams();
    const status = byId("document-review-status").value.trim();
    const limit = byId("document-review-limit").value.trim();
    const cursor = byId("document-review-cursor").value.trim();
    if (status) {
      params.set("status", status);
    }
    if (limit) {
      params.set("limit", limit);
    }
    if (cursor) {
      params.set("cursor", cursor);
    }
    setLive("Loading document review list...");
    hideAlert();
    try {
      const query = params.toString();
      const response = await fetch(`${DOCUMENT_REVIEW_ENDPOINT}${query ? `?${query}` : ""}`, {
        headers: buildHeaders(),
      });
      const envelope = await response.json();
      if (!response.ok || envelope.error) {
        renderDocumentReviewFailure(envelope);
        return;
      }
      renderDocumentReviewList(envelope.data || {});
      setLive("Document review list loaded.");
    } catch {
      renderDocumentReviewFailure(null);
    }
  }

  async function fetchDocumentReviewDetail(documentId, versionId) {
    if (!documentId) {
      clearDocumentReviewRegions();
      showAlert("Document ID is required for review detail.");
      setLive("Document review detail requires a document ID.");
      return;
    }
    setLive("Loading document review detail...");
    hideAlert();
    const suffix = versionId
      ? `/versions/${encodeURIComponent(versionId)}/review`
      : "/review";
    try {
      const response = await fetch(
        `/documents/${encodeURIComponent(documentId)}${suffix}`,
        {
          headers: buildHeaders(),
        },
      );
      const envelope = await response.json();
      if (!response.ok || envelope.error) {
        renderDocumentReviewFailure(envelope);
        return;
      }
      renderDocumentReviewDetail(envelope.data || {});
      setLive("Document review detail loaded.");
    } catch {
      renderDocumentReviewFailure(null);
    }
  }

  async function fetchDiagnostics() {
    await fetchDiagnosticsInto({
      payload: collectDiagnosticsPayload(),
      summaryId: "diagnostics-result",
      timelineId: "diagnostics-stages",
      nextStepsId: "diagnostics-next-steps",
      reportKey: "default",
      loadingMessage: "Loading diagnostics summary...",
      successMessage: "Diagnostics summary loaded.",
      failureMessage: "Diagnostics summary cannot be displayed for this request.",
    });
  }

  async function fetchGovernanceDiagnostics() {
    clearDiagnosticsRegions(diagnosticsRenderTargets());
    await fetchDiagnosticsInto({
      payload: collectGovernanceDiagnosticsPayload(),
      summaryId: "governance-diagnostics-summary",
      timelineId: "governance-diagnostics-timeline",
      nextStepsId: "governance-diagnostics-next-steps",
      reportKey: "governance",
      loadingMessage: "Loading retrieval diagnostics timeline...",
      successMessage: "Retrieval diagnostics timeline loaded.",
      failureMessage: "Retrieval diagnostics cannot be displayed for this request.",
    });
  }

  async function fetchDiagnosticsInto(options) {
    const payload = options.payload;
    if (!payload.request_id && !payload.trace_id) {
      showAlert("Request ID or Trace ID is required.");
      clearDiagnosticsRegions(options);
      return;
    }
    setLive(options.loadingMessage);
    hideAlert();
    clearDiagnosticsRegions(options);
    try {
      const response = await fetch(DIAGNOSTICS_ENDPOINT, {
        method: "POST",
        headers: buildHeaders(),
        body: JSON.stringify(payload),
      });
      const envelope = await response.json();
      if (!response.ok || envelope.error) {
        renderDiagnosticsFailure(envelope, options);
        return;
      }
      renderDiagnosticsResult(envelope.data || {}, options);
      setLive(options.successMessage);
    } catch {
      renderDiagnosticsFailure(null, options);
    }
  }

  async function fetchEvalEvidenceReports() {
    const requestToken = ++state.evalEvidenceRequestToken;
    const limit = byId("eval-evidence-limit").value.trim() || "20";
    const params = new URLSearchParams();
    params.set("limit", limit);
    setLive("Loading eval reports...");
    hideAlert();
    clearEvalEvidenceRegions();
    try {
      const response = await fetch(`${EVAL_EVIDENCE_REPORTS_ENDPOINT}?${params.toString()}`, {
        headers: buildHeaders(),
      });
      const envelope = await response.json();
      if (requestToken !== state.evalEvidenceRequestToken) {
        return;
      }
      if (!response.ok || envelope.error) {
        renderEvalEvidenceFailure(envelope);
        return;
      }
      renderEvalEvidenceReportList(envelope.data || {});
      setLive("Eval reports loaded.");
    } catch {
      if (requestToken !== state.evalEvidenceRequestToken) {
        return;
      }
      renderEvalEvidenceFailure(null);
    }
  }

  async function fetchEvalEvidenceDetail() {
    const requestToken = ++state.evalEvidenceRequestToken;
    const reportFilename = byId("eval-evidence-report").value.trim();
    if (!reportFilename) {
      clearEvalEvidenceRegions();
      showAlert("Report filename is required.");
      setLive("Eval report detail requires a report filename.");
      return;
    }
    setLive("Loading eval report detail...");
    hideAlert();
    clearEvalEvidenceDetailRegions();
    try {
      const response = await fetch(
        `${EVAL_EVIDENCE_REPORTS_ENDPOINT}/${encodeURIComponent(reportFilename)}`,
        {
          headers: buildHeaders(),
        },
      );
      const envelope = await response.json();
      if (requestToken !== state.evalEvidenceRequestToken) {
        return;
      }
      if (!response.ok || envelope.error) {
        renderEvalEvidenceFailure(envelope);
        return;
      }
      renderEvalEvidenceDetail(envelope.data || {});
      setLive("Eval report detail loaded.");
    } catch {
      if (requestToken !== state.evalEvidenceRequestToken) {
        return;
      }
      renderEvalEvidenceFailure(null);
    }
  }

  async function fetchAuditExplorerLogs() {
    const requestToken = ++state.auditExplorerRequestToken;
    const query = collectAuditExplorerQuery({ exportLimit: false });
    setLive("Loading audit summaries...");
    hideAlert();
    clearAuditExplorerRegions();
    try {
      const params = new URLSearchParams();
      Object.entries(query).forEach(([key, value]) => {
        if (value !== undefined && value !== null && value !== "") {
          params.set(key, String(value));
        }
      });
      const suffix = params.toString() ? `?${params.toString()}` : "";
      const response = await fetch(`${AUDIT_EXPLORER_LOGS_ENDPOINT}${suffix}`, {
        headers: buildHeaders(),
      });
      const envelope = await response.json();
      if (requestToken !== state.auditExplorerRequestToken) {
        return;
      }
      if (!response.ok || envelope.error) {
        renderAuditExplorerFailure(envelope);
        return;
      }
      renderAuditExplorerList(envelope.data || {});
      setLive("Audit summaries loaded.");
    } catch {
      if (requestToken !== state.auditExplorerRequestToken) {
        return;
      }
      renderAuditExplorerFailure(null);
    }
  }

  async function fetchAuditExplorerExport() {
    const requestToken = ++state.auditExplorerRequestToken;
    const payload = collectAuditExplorerQuery({ exportLimit: true });
    setLive("Preparing audit export...");
    hideAlert();
    state.auditExplorerExport = null;
    try {
      const response = await fetch(AUDIT_EXPLORER_EXPORT_ENDPOINT, {
        method: "POST",
        headers: buildHeaders(),
        body: JSON.stringify(payload),
      });
      const envelope = await response.json();
      if (requestToken !== state.auditExplorerRequestToken) {
        return null;
      }
      if (!response.ok || envelope.error) {
        renderAuditExplorerFailure(envelope);
        return null;
      }
      const exportPayload = buildSafeAuditExportPayload(envelope.data || {});
      state.auditExplorerExport = exportPayload;
      renderAuditExplorerExportSummary(exportPayload);
      setLive("Audit export prepared.");
      return exportPayload;
    } catch {
      if (requestToken !== state.auditExplorerRequestToken) {
        return null;
      }
      renderAuditExplorerFailure(null);
      return null;
    }
  }

  async function createReviewQueueItem() {
    const requestToken = ++state.reviewQueueRequestToken;
    const payload = collectReviewQueueCreatePayload();
    setLive("Creating review item...");
    hideAlert();
    clearReviewQueueRegions({ keepList: false });
    try {
      const response = await fetch(REVIEW_QUEUE_ITEMS_ENDPOINT, {
        method: "POST",
        headers: buildHeaders(),
        body: JSON.stringify(payload),
      });
      const envelope = await response.json();
      if (requestToken !== state.reviewQueueRequestToken) {
        return;
      }
      if (!response.ok || envelope.error) {
        renderReviewQueueFailure(envelope);
        return;
      }
      const item = sanitizeReviewItem(envelope.data || {});
      renderReviewQueueDetail(item);
      byId("review-queue-selected-id").value = item.id || "";
      state.reviewQueueExport = buildSafeReviewQueueExport({ items: [item], next_steps: [] });
      setLive("Review item created.");
    } catch {
      if (requestToken !== state.reviewQueueRequestToken) {
        return;
      }
      renderReviewQueueFailure(null);
    }
  }

  async function fetchReviewQueueItems() {
    const requestToken = ++state.reviewQueueRequestToken;
    const query = collectReviewQueueQuery();
    setLive("Loading review queue...");
    hideAlert();
    clearReviewQueueRegions();
    try {
      const params = new URLSearchParams();
      Object.entries(query).forEach(([key, value]) => {
        if (value !== undefined && value !== null && value !== "") {
          params.set(key, String(value));
        }
      });
      const suffix = params.toString() ? `?${params.toString()}` : "";
      const response = await fetch(`${REVIEW_QUEUE_ITEMS_ENDPOINT}${suffix}`, {
        headers: buildHeaders(),
      });
      const envelope = await response.json();
      if (requestToken !== state.reviewQueueRequestToken) {
        return;
      }
      if (!response.ok || envelope.error) {
        renderReviewQueueFailure(envelope);
        return;
      }
      renderReviewQueueList(envelope.data || {});
      setLive("Review queue loaded.");
    } catch {
      if (requestToken !== state.reviewQueueRequestToken) {
        return;
      }
      renderReviewQueueFailure(null);
    }
  }

  async function fetchReviewQueueDetail() {
    const requestToken = ++state.reviewQueueRequestToken;
    const itemId = byId("review-queue-selected-id").value.trim();
    if (!itemId) {
      clearReviewQueueDetailRegions();
      showAlert("Review item ID is required.");
      setLive("Review detail requires an item ID.");
      return;
    }
    setLive("Loading review item detail...");
    hideAlert();
    clearReviewQueueDetailRegions();
    try {
      const response = await fetch(`${REVIEW_QUEUE_ITEMS_ENDPOINT}/${encodeURIComponent(itemId)}`, {
        headers: buildHeaders(),
      });
      const envelope = await response.json();
      if (requestToken !== state.reviewQueueRequestToken) {
        return;
      }
      if (!response.ok || envelope.error) {
        renderReviewQueueFailure(envelope);
        return;
      }
      renderReviewQueueDetail(sanitizeReviewItem(envelope.data || {}));
      setLive("Review item detail loaded.");
    } catch {
      if (requestToken !== state.reviewQueueRequestToken) {
        return;
      }
      renderReviewQueueFailure(null);
    }
  }

  async function updateReviewQueueStatus(itemId, status) {
    const requestToken = ++state.reviewQueueRequestToken;
    setLive("Updating review item status...");
    hideAlert();
    state.reviewQueueCandidate = null;
    byId("review-queue-candidate").replaceChildren();
    try {
      const response = await fetch(`${REVIEW_QUEUE_ITEMS_ENDPOINT}/${encodeURIComponent(itemId)}/status`, {
        method: "POST",
        headers: buildHeaders(),
        body: JSON.stringify({ status }),
      });
      const envelope = await response.json();
      if (requestToken !== state.reviewQueueRequestToken) {
        return;
      }
      if (!response.ok || envelope.error) {
        renderReviewQueueFailure(envelope);
        return;
      }
      renderReviewQueueDetail(sanitizeReviewItem(envelope.data || {}));
      setLive("Review item status updated.");
    } catch {
      if (requestToken !== state.reviewQueueRequestToken) {
        return;
      }
      renderReviewQueueFailure(null);
    }
  }

  async function convertReviewQueueCandidate() {
    const requestToken = ++state.reviewQueueRequestToken;
    const itemId = byId("review-queue-selected-id").value.trim();
    if (!itemId) {
      clearReviewQueueDetailRegions();
      showAlert("Review item ID is required.");
      setLive("Eval candidate preview requires an item ID.");
      return;
    }
    setLive("Preparing eval candidate preview...");
    hideAlert();
    state.reviewQueueCandidate = null;
    byId("review-queue-candidate").replaceChildren();
    try {
      const response = await fetch(`${REVIEW_QUEUE_ITEMS_ENDPOINT}/${encodeURIComponent(itemId)}/eval-candidate`, {
        method: "POST",
        headers: buildHeaders(),
      });
      const envelope = await response.json();
      if (requestToken !== state.reviewQueueRequestToken) {
        return;
      }
      if (!response.ok || envelope.error) {
        renderReviewQueueFailure(envelope);
        return;
      }
      const candidate = sanitizeEvalCandidate(envelope.data || {});
      state.reviewQueueCandidate = candidate;
      renderEvalCandidatePreview(candidate);
      setLive("Eval candidate preview prepared.");
    } catch {
      if (requestToken !== state.reviewQueueRequestToken) {
        return;
      }
      renderReviewQueueFailure(null);
    }
  }

  function collectDiagnosticsPayload() {
    const requestId = byId("diagnostic-request").value.trim();
    const traceId = byId("diagnostic-trace").value.trim();
    const payload = { include_report: true };
    if (requestId) {
      payload.request_id = requestId;
    }
    if (traceId) {
      payload.trace_id = traceId;
    }
    return payload;
  }

  function collectAuditExplorerQuery(options = {}) {
    const fields = {
      user_id: "audit-explorer-user",
      request_id: "audit-explorer-request",
      trace_id: "audit-explorer-trace",
      action: "audit-explorer-action",
      resource_type: "audit-explorer-resource-type",
      resource_id: "audit-explorer-resource-id",
      status: "audit-explorer-status",
      created_at_from: "audit-explorer-created-from",
      created_at_to: "audit-explorer-created-to",
      limit: "audit-explorer-limit",
    };
    const payload = { include_associations: true };
    Object.entries(fields).forEach(([field, id]) => {
      const element = optionalById(id);
      const value = element ? element.value.trim() : "";
      if (!value) {
        return;
      }
      if (field === "limit") {
        const parsed = parseInt(value, 10);
        if (Number.isFinite(parsed)) {
          payload.limit = Math.min(Math.max(parsed, 1), options.exportLimit ? 500 : 200);
        }
        return;
      }
      payload[field] = value;
    });
    if (!payload.limit) {
      payload.limit = options.exportLimit ? 200 : 50;
    }
    return payload;
  }

  function collectReviewQueueCreatePayload() {
    const safeIdentifiers = {};
    [
      ["document_id", "review-queue-create-document"],
      ["version_id", "review-queue-create-version"],
      ["chunk_id", "review-queue-create-chunk"],
    ].forEach(([field, id]) => {
      const value = byId(id).value.trim();
      if (value) {
        safeIdentifiers[field] = value;
      }
    });
    const safeSummary = {};
    [
      ["failure_stage", "review-queue-create-failure-stage"],
      ["error_code", "review-queue-create-error-code"],
      ["expected_behavior", "review-queue-create-expected"],
    ].forEach(([field, id]) => {
      const value = byId(id).value.trim();
      if (value) {
        safeSummary[field] = value;
      }
    });
    return {
      item_type: byId("review-queue-create-type").value,
      severity: byId("review-queue-create-severity").value,
      source_view: byId("review-queue-create-source-view").value,
      request_id: byId("review-queue-create-request").value.trim(),
      trace_id: byId("review-queue-create-trace").value.trim(),
      safe_identifiers: safeIdentifiers,
      safe_summary: safeSummary,
    };
  }

  function collectReviewQueueQuery() {
    const fields = {
      item_type: "review-queue-filter-type",
      severity: "review-queue-filter-severity",
      status: "review-queue-filter-status",
      source_view: "review-queue-filter-source-view",
      request_id: "review-queue-filter-request",
      trace_id: "review-queue-filter-trace",
      created_at_from: "review-queue-filter-created-from",
      created_at_to: "review-queue-filter-created-to",
      limit: "review-queue-filter-limit",
    };
    const query = {};
    Object.entries(fields).forEach(([field, id]) => {
      const element = optionalById(id);
      const value = element ? element.value.trim() : "";
      if (!value) {
        return;
      }
      if (field === "limit") {
        const parsed = parseInt(value, 10);
        if (Number.isFinite(parsed)) {
          query.limit = Math.min(Math.max(parsed, 1), 100);
        }
        return;
      }
      query[field] = value;
    });
    if (!query.limit) {
      query.limit = 50;
    }
    return query;
  }

  function collectGovernanceDiagnosticsPayload() {
    const requestId = byId("governance-diagnostic-request").value.trim();
    const traceId = byId("governance-diagnostic-trace").value.trim();
    const payload = { include_report: true };
    if (requestId) {
      payload.request_id = requestId;
    }
    if (traceId) {
      payload.trace_id = traceId;
    }
    return payload;
  }

  function buildHeaders(requestId) {
    const headers = {
      "Content-Type": "application/json",
    };
    if (requestId) {
      headers["X-Request-ID"] = requestId;
    }
    const token = byId("auth-token").value.trim();
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
    document.querySelectorAll("[data-auth-header]").forEach((input) => {
      const value = input.value.trim();
      if (value) {
        headers[input.dataset.authHeader] = value;
      }
    });
    return headers;
  }

  function renderSourceResult(data) {
    const rows = [];
    SAFE_SOURCE_FIELDS.forEach((field) => {
      if (data[field] !== undefined && data[field] !== null && data[field] !== "") {
        rows.push(resultRow(field, data[field], field === "text_excerpt"));
      }
    });
    byId("source-result").replaceChildren(...rows);
    syncDiagnostics(data);
  }

  function renderStatusResult(data) {
    const statusMeta = statusLabel(data.status);
    const chip = document.createElement("div");
    chip.className = "status-chip";
    chip.dataset.tone = statusMeta.tone;
    const statusIcon = document.createElement("span");
    statusIcon.textContent = statusMeta.statusIcon;
    const statusLabelElement = document.createElement("span");
    statusLabelElement.textContent = statusMeta.statusLabel;
    chip.append(statusIcon, statusLabelElement);

    const rows = [statusRow("status", chip)];
    SAFE_STATUS_FIELDS.filter((field) => field !== "status").forEach((field) => {
      if (data[field] !== undefined && data[field] !== null && data[field] !== "") {
        rows.push(resultRow(field, data[field], false));
      }
    });
    byId("status-result").replaceChildren(...rows);
    syncDiagnostics(data);
  }

  function renderDocumentReviewList(data) {
    const rows = [];
    const items = Array.isArray(data.items) ? data.items : [];
    items.forEach((item) => {
      const safeItem = pickFields(item || {}, SAFE_DOCUMENT_REVIEW_FIELDS);
      rows.push(resultRow("document", safeItem, false));
    });
    if (!items.length) {
      rows.push(resultRow("documents", "No documents found for this review filter.", false));
    }
    byId("document-review-cursor").value = data.next_cursor || "";
    if (data.next_cursor) {
      rows.push(resultRow("next_cursor", data.next_cursor, false));
    }
    byId("document-review-list").replaceChildren(...rows);
    byId("document-review-detail").replaceChildren();
    byId("document-review-timeline").replaceChildren();
    syncDiagnostics(data);
  }

  function renderDocumentReviewDetail(data) {
    const safeDetail = pickFields(data || {}, SAFE_DOCUMENT_REVIEW_DETAIL_FIELDS);
    const rows = [];
    SAFE_DOCUMENT_REVIEW_DETAIL_FIELDS.forEach((field) => {
      if (safeDetail[field] !== undefined && safeDetail[field] !== null && safeDetail[field] !== "") {
        rows.push(resultRow(field, safeDetail[field], false));
      }
    });
    byId("document-review-detail").replaceChildren(...rows);

    const timelineRows = [];
    (Array.isArray(data.lifecycle) ? data.lifecycle : []).forEach((stage) => {
      timelineRows.push(lifecycleRow(pickFields(stage || {}, SAFE_DOCUMENT_REVIEW_LIFECYCLE_FIELDS)));
    });
    byId("document-review-timeline").replaceChildren(...timelineRows);
    syncDiagnostics(safeDetail);
  }

  function lifecycleRow(stage) {
    const chip = document.createElement("div");
    chip.className = "status-chip";
    chip.dataset.tone = stage.tone || "unknown";
    const statusIcon = document.createElement("span");
    statusIcon.textContent = statusLabel(stage.status).statusIcon;
    const statusLabelElement = document.createElement("span");
    const stateText = stage.is_current ? "Current" : stage.is_failure ? "Failure" : "Stage";
    statusLabelElement.textContent = `${stage.label || "Unknown status"} (${stateText})`;
    chip.append(statusIcon, statusLabelElement);

    const details = [];
    details.push(stateText);
    if (stage.position !== undefined && stage.position !== null) {
      details.push(`#${stage.position}`);
    }
    if (stage.description) {
      details.push(stage.description);
    }
    return statusRow("lifecycle", chip, details.join(" "));
  }

  function renderSafeFailure(target, envelope, fallbackMessage) {
    const details = (envelope && envelope.error && envelope.error.details) || {};
    const safeValues = {
      request_id: details.request_id || (envelope && envelope.request_id),
      trace_id: details.trace_id || (envelope && envelope.trace_id),
    };
    const safeRows = [];
    ["request_id", "trace_id"].forEach((field) => {
      const value = safeValues[field];
      if (value) {
        safeRows.push(resultRow(field, value, false));
      }
    });
    showAlert(fallbackMessage);
    const resultId = target === "status" ? "status-result" : "source-result";
    byId(resultId).replaceChildren(...safeRows, safeNextStepRow());
    setLive("Request ended with a safe failure state.");
  }

  function renderDiagnosticsFailure(envelope, options = diagnosticsRenderTargets()) {
    clearDiagnosticsRegions(options);
    const details = (envelope && envelope.error && envelope.error.details) || {};
    const error = (envelope && envelope.error) || {};
    const safeValues = {
      request_id: details.request_id || (envelope && envelope.request_id),
      trace_id: details.trace_id,
      failure_stage: details.failure_stage,
      error_code: details.error_code || error.code,
    };
    const rows = [];
    ["request_id", "trace_id", "failure_stage", "error_code"].forEach((field) => {
      const value = safeValues[field];
      if (value) {
        rows.push(resultRow(field, value, false));
      }
    });
    byId(options.summaryId).replaceChildren(...rows);
    byId(options.timelineId).replaceChildren();
    byId(options.nextStepsId).replaceChildren(safeNextStepCommand());
    state.diagnosticsReports[options.reportKey || "default"] = null;
    showAlert(options.failureMessage || "Diagnostics summary cannot be displayed for this request.");
    setLive("Diagnostics ended with a safe failure state.");
  }

  function renderGovernanceFailure(envelope) {
    const details = (envelope && envelope.error && envelope.error.details) || {};
    const error = (envelope && envelope.error) || {};
    const safeValues = pickFields(
      {
        request_id: details.request_id || (envelope && envelope.request_id),
        trace_id: details.trace_id || (envelope && envelope.trace_id),
        failure_stage: details.failure_stage,
        error_code: details.error_code || error.code,
      },
      ["request_id", "trace_id", "failure_stage", "error_code"],
    );
    const rows = [];
    ["request_id", "trace_id", "failure_stage", "error_code"].forEach((field) => {
      const value = safeValues[field];
      if (value) {
        rows.push(resultRow(field, value, false));
      }
    });
    byId("governance-detail").replaceChildren(...rows, safeNextStepRow());
    showAlert("Governance detail cannot be displayed for this request.");
    setLive("Governance request ended with a safe failure state.");
  }

  function renderDocumentReviewFailure(envelope) {
    const details = (envelope && envelope.error && envelope.error.details) || {};
    const error = (envelope && envelope.error) || {};
    const safeValues = pickFields(
      {
        request_id: details.request_id || (envelope && envelope.request_id),
        trace_id: details.trace_id || (envelope && envelope.trace_id),
        failure_stage: details.failure_stage,
        error_code: details.error_code || error.code,
      },
      ["request_id", "trace_id", "failure_stage", "error_code"],
    );
    const rows = [];
    ["request_id", "trace_id", "failure_stage", "error_code"].forEach((field) => {
      const value = safeValues[field];
      if (value) {
        rows.push(resultRow(field, value, false));
      }
    });
    byId("document-review-list").replaceChildren();
    byId("document-review-detail").replaceChildren(...rows, safeNextStepRow());
    byId("document-review-timeline").replaceChildren();
    showAlert("Document review cannot be displayed for this request.");
    setLive("Document review ended with a safe failure state.");
  }

  function renderEvalEvidenceFailure(envelope) {
    clearEvalEvidenceRegions();
    const details = (envelope && envelope.error && envelope.error.details) || {};
    const error = (envelope && envelope.error) || {};
    const safeValues = pickFields(
      {
        request_id: details.request_id || (envelope && envelope.request_id),
        trace_id: details.trace_id || (envelope && envelope.trace_id),
        failure_stage: details.failure_stage,
        error_code: details.error_code || error.code,
      },
      ["request_id", "trace_id", "failure_stage", "error_code"],
    );
    const rows = [];
    ["request_id", "trace_id", "failure_stage", "error_code"].forEach((field) => {
      const value = safeValues[field];
      if (value) {
        rows.push(resultRow(field, value, false));
      }
    });
    byId("eval-evidence-summary").replaceChildren(...rows, safeNextStepRow());
    byId("eval-evidence-next-steps").replaceChildren(safeEvalNextStepCommand());
    showAlert("Eval evidence cannot be displayed for this request.");
    setLive("Eval evidence ended with a safe failure state.");
  }

  function renderEvalEvidenceReportList(data) {
    const items = Array.isArray(data.items) ? data.items : [];
    const rows = items.map((item) => evalReportRow(pickFields(item || {}, SAFE_EVAL_REPORT_SUMMARY_FIELDS)));
    if (!items.length) {
      rows.push(resultRow("reports", "No eval reports found.", false));
    }
    byId("eval-evidence-report-list").replaceChildren(...rows);
    clearEvalEvidenceDetailRegions();
    const reportInput = byId("eval-evidence-report");
    const currentReport = reportInput.value;
    const hasCurrentReport = items.some((item) => {
      const safe = pickFields(item || {}, ["report_filename"]);
      return safe.report_filename === currentReport;
    });
    if (!hasCurrentReport) {
      const first = pickFields(items[0] || {}, ["report_filename"]);
      reportInput.value = first.report_filename || "";
    }
    renderEvalEvidenceNextSteps(data.next_steps);
  }

  function renderEvalEvidenceDetail(data) {
    const summary = pickFields(data.summary || {}, SAFE_EVAL_REPORT_SUMMARY_FIELDS);
    const summaryRows = [];
    SAFE_EVAL_REPORT_SUMMARY_FIELDS.forEach((field) => {
      if (summary[field] !== undefined && summary[field] !== null && summary[field] !== "") {
        summaryRows.push(resultRow(field, summary[field], false));
      }
    });
    byId("eval-evidence-summary").replaceChildren(...summaryRows);

    const caseRows = [];
    (Array.isArray(data.failed_cases) ? data.failed_cases : []).forEach((item) => {
      caseRows.push(evalCaseRow(sanitizeEvalCase(item || {})));
    });
    (Array.isArray(data.gate_metrics) ? data.gate_metrics : []).forEach((metric) => {
      caseRows.push(evalGateMetricRow(pickFields(metric || {}, SAFE_EVAL_GATE_FIELDS)));
    });
    if (!caseRows.length) {
      caseRows.push(resultRow("failed_cases", "No failed cases in this safe report.", false));
    }
    byId("eval-evidence-cases").replaceChildren(...caseRows);
    renderEvalEvidenceNextSteps(data.next_steps);
    state.evalEvidenceReport = buildSafeEvalEvidenceReport(data);
  }

  function renderAuditExplorerList(data) {
    const items = Array.isArray(data.items) ? data.items : [];
    const rows = items.map((item) => auditLogRow(sanitizeAuditLog(item || {})));
    if (!items.length) {
      rows.push(resultRow("audit_logs", "No audit records found for this safe filter.", false));
    }
    byId("audit-explorer-results").replaceChildren(...rows);
    renderAuditAssociations(items);
    renderAuditExplorerNextSteps(data.next_steps);
    state.auditExplorerExport = null;
  }

  function renderAuditAssociations(items) {
    const rows = [];
    items.forEach((item) => {
      const safe = sanitizeAuditLog(item || {});
      if (safe.association) {
        rows.push(auditAssociationRow(safe.association));
      }
    });
    byId("audit-explorer-detail").replaceChildren(...rows);
  }

  function renderAuditExplorerFailure(envelope) {
    clearAuditExplorerRegions();
    const details = (envelope && envelope.error && envelope.error.details) || {};
    const error = (envelope && envelope.error) || {};
    const safeValues = pickFields(
      {
        request_id: details.request_id || (envelope && envelope.request_id),
        trace_id: details.trace_id || (envelope && envelope.trace_id),
        failure_stage: details.failure_stage || details.stage,
        error_code: details.error_code || error.code,
      },
      ["request_id", "trace_id", "failure_stage", "error_code"],
    );
    const rows = [];
    ["request_id", "trace_id", "failure_stage", "error_code"].forEach((field) => {
      const value = safeValues[field];
      if (value) {
        rows.push(resultRow(field, value, false));
      }
    });
    byId("audit-explorer-detail").replaceChildren(...rows, safeAuditNextStepCommand());
    showAlert("Audit Explorer cannot display records for this request.");
    setLive("Audit Explorer ended with a safe failure state.");
  }

  function auditLogRow(item) {
    const row = document.createElement("div");
    row.className = "audit-log-row";
    const statusMeta = statusLabel(item.status);
    const chip = document.createElement("span");
    chip.className = "status-chip";
    chip.dataset.tone = statusMeta.tone;
    const icon = document.createElement("span");
    icon.textContent = statusMeta.statusIcon;
    const text = document.createElement("span");
    text.textContent = item.status || "unknown";
    chip.append(icon, text);
    row.append(
      resultInline("action", item.action || "unknown"),
      chip,
      resultInline("resource", `${item.resource_type || ""}:${item.resource_id || ""}`),
      resultInline("request_id", item.request_id || ""),
      resultInline("trace_id", item.trace_id || ""),
      resultInline("counts", item.safe_counts || item.safe_summary || {}),
    );
    return row;
  }

  function auditAssociationRow(association) {
    const row = document.createElement("div");
    row.className = "audit-association-row";
    SAFE_AUDIT_ASSOCIATION_FIELDS.forEach((field) => {
      const value = association[field];
      if (value !== undefined && value !== null && value !== "" && !isEmptyObject(value)) {
        row.append(resultInline(field, value));
      }
    });
    return row;
  }

  function renderAuditExplorerExportSummary(payload) {
    const row = document.createElement("div");
    row.className = "audit-export-row";
    row.append(
      resultInline("export_id", payload.export_id || "audit-export"),
      resultInline("item_count", payload.item_count || 0),
      resultInline("request_ids", payload.request_ids || []),
      resultInline("trace_ids", payload.trace_ids || []),
    );
    byId("audit-explorer-detail").replaceChildren(row);
  }

  function renderAuditExplorerNextSteps(nextSteps) {
    const commands = Array.isArray(nextSteps) ? nextSteps.filter((item) => typeof item === "string") : [];
    byId("audit-explorer-next-steps").replaceChildren(
      ...commands.map((command) => {
        const code = document.createElement("code");
        code.textContent = command;
        return code;
      }),
    );
  }

  function renderReviewQueueList(data) {
    const items = Array.isArray(data.items) ? data.items.map((item) => sanitizeReviewItem(item || {})) : [];
    const rows = items.map((item) => reviewItemRow(item));
    if (!items.length) {
      rows.push(resultRow("review_items", "No review items found for this safe filter.", false));
    }
    byId("review-queue-list").replaceChildren(...rows);
    clearReviewQueueDetailRegions();
    if (items[0] && items[0].id) {
      byId("review-queue-selected-id").value = items[0].id;
    }
    renderReviewQueueNextSteps(data.next_steps);
    state.reviewQueueExport = buildSafeReviewQueueExport({ items, next_steps: data.next_steps || [] });
  }

  function renderReviewQueueDetail(item) {
    const safe = sanitizeReviewItem(item || {});
    const rows = [];
    [
      "id",
      "item_type",
      "severity",
      "status",
      "request_id",
      "trace_id",
      "source_view",
      "created_by",
      "tenant_id",
      "created_at",
      "updated_at",
    ].forEach((field) => {
      if (safe[field] !== undefined && safe[field] !== null && safe[field] !== "") {
        rows.push(resultRow(field, safe[field], false));
      }
    });
    rows.push(resultRow("safe_identifiers", safe.safe_identifiers || {}, false));
    rows.push(resultRow("safe_summary", safe.safe_summary || {}, false));
    const transitionButtons = reviewTransitionButtons(safe);
    if (transitionButtons.length) {
      const row = document.createElement("div");
      row.className = "review-transition-row";
      row.append(...transitionButtons);
      rows.push(row);
    }
    byId("review-queue-detail").replaceChildren(...rows);
    byId("review-queue-selected-id").value = safe.id || byId("review-queue-selected-id").value;
    renderReviewStatusHistory(safe.status_history || []);
    if (safe.eval_candidate) {
      state.reviewQueueCandidate = safe.eval_candidate;
      renderEvalCandidatePreview(safe.eval_candidate);
    } else {
      state.reviewQueueCandidate = null;
      byId("review-queue-candidate").replaceChildren();
    }
    state.reviewQueueExport = buildSafeReviewQueueExport({ items: [safe], next_steps: [] });
  }

  function reviewItemRow(item) {
    const row = document.createElement("div");
    row.className = "review-item-row";
    const statusMeta = statusLabel(item.status);
    const chip = document.createElement("span");
    chip.className = "status-chip";
    chip.dataset.tone = statusMeta.tone;
    const icon = document.createElement("span");
    icon.textContent = statusMeta.statusIcon;
    const text = document.createElement("span");
    text.textContent = item.status || "unknown";
    chip.append(icon, text);
    const select = document.createElement("button");
    select.type = "button";
    select.className = "secondary";
    select.textContent = "Select";
    select.addEventListener("click", () => {
      byId("review-queue-selected-id").value = item.id || "";
      renderReviewQueueDetail(item);
    });
    row.append(
      resultInline("id", item.id || ""),
      chip,
      resultInline("type", item.item_type || ""),
      resultInline("severity", item.severity || ""),
      resultInline("request_id", item.request_id || ""),
      resultInline("trace_id", item.trace_id || ""),
      select,
    );
    return row;
  }

  function reviewTransitionButtons(item) {
    const transitions = Array.isArray(item.allowed_transitions) ? item.allowed_transitions : [];
    return transitions
      .filter((status) => typeof status === "string")
      .map((status) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "secondary";
        button.textContent = `Mark ${status}`;
        button.addEventListener("click", () => updateReviewQueueStatus(item.id, status));
        return button;
      });
  }

  function renderReviewStatusHistory(history) {
    const rows = (Array.isArray(history) ? history : []).map((entry) => {
      const safe = pickFields(entry || {}, SAFE_REVIEW_STATUS_HISTORY_FIELDS);
      const row = document.createElement("div");
      row.className = "review-status-history-row";
      row.append(
        resultInline("status", safe.status || ""),
        resultInline("changed_by", safe.changed_by || ""),
        resultInline("changed_at", safe.changed_at || ""),
        resultInline("reason_code", safe.reason_code || ""),
      );
      return row;
    });
    byId("review-queue-status-history").replaceChildren(...rows);
  }

  function renderEvalCandidatePreview(candidate) {
    const safe = sanitizeEvalCandidate(candidate || {});
    const row = document.createElement("div");
    row.className = "eval-candidate-row";
    SAFE_EVAL_CANDIDATE_FIELDS.forEach((field) => {
      const value = safe[field];
      if (value !== undefined && value !== null && value !== "" && !isEmptyObject(value)) {
        row.append(resultInline(field, value));
      }
    });
    byId("review-queue-candidate").replaceChildren(row);
  }

  function renderReviewQueueFailure(envelope) {
    clearReviewQueueRegions();
    const details = (envelope && envelope.error && envelope.error.details) || {};
    const error = (envelope && envelope.error) || {};
    const safeValues = pickFields(
      {
        request_id: details.request_id || (envelope && envelope.request_id),
        trace_id: details.trace_id || (envelope && envelope.trace_id),
        failure_stage: details.failure_stage || details.stage,
        error_code: details.error_code || error.code,
        review_item_id: details.review_item_id,
      },
      ["request_id", "trace_id", "failure_stage", "error_code", "review_item_id"],
    );
    const rows = [];
    ["request_id", "trace_id", "failure_stage", "error_code", "review_item_id"].forEach((field) => {
      const value = safeValues[field];
      if (value) {
        rows.push(resultRow(field, value, false));
      }
    });
    byId("review-queue-alert").replaceChildren(...rows, safeReviewQueueNextStepCommand());
    showAlert("Review Queue cannot display records for this request.");
    setLive("Review Queue ended with a safe failure state.");
  }

  function renderReviewQueueNextSteps(nextSteps) {
    const commands = Array.isArray(nextSteps) ? nextSteps.filter((item) => typeof item === "string") : [];
    byId("review-queue-next-steps").replaceChildren(
      ...commands.map((command) => {
        const code = document.createElement("code");
        code.textContent = command;
        return code;
      }),
    );
  }

  function sanitizeReviewItem(item) {
    const safe = pickFields(item || {}, SAFE_REVIEW_ITEM_FIELDS);
    safe.safe_identifiers = pickFields(safe.safe_identifiers || {}, SAFE_REVIEW_IDENTIFIER_FIELDS);
    safe.safe_summary = pickFields(safe.safe_summary || {}, SAFE_REVIEW_SUMMARY_FIELDS);
    safe.status_history = (Array.isArray(safe.status_history) ? safe.status_history : []).map((entry) =>
      pickFields(entry || {}, SAFE_REVIEW_STATUS_HISTORY_FIELDS),
    );
    safe.allowed_transitions = (Array.isArray(safe.allowed_transitions) ? safe.allowed_transitions : []).filter(
      (status) => typeof status === "string",
    );
    safe.eval_candidate = safe.eval_candidate ? sanitizeEvalCandidate(safe.eval_candidate) : null;
    return safe;
  }

  function sanitizeEvalCandidate(candidate) {
    const safe = pickFields(candidate || {}, SAFE_EVAL_CANDIDATE_FIELDS);
    safe.safe_identifiers = pickFields(safe.safe_identifiers || {}, SAFE_REVIEW_IDENTIFIER_FIELDS);
    safe.safe_metric_counts = pickFields(safe.safe_metric_counts || {}, SAFE_REVIEW_SUMMARY_FIELDS);
    safe.requires_human_confirmation = safe.requires_human_confirmation === true;
    return safe;
  }

  function buildSafeReviewQueueExport(data) {
    const items = (Array.isArray(data.items) ? data.items : []).map((item) => sanitizeReviewItem(item || {}));
    return {
      fields: SAFE_REVIEW_ITEM_FIELDS,
      item_count: items.length,
      items,
      candidate: state.reviewQueueCandidate ? sanitizeEvalCandidate(state.reviewQueueCandidate) : null,
      next_steps: (Array.isArray(data.next_steps) ? data.next_steps : []).filter((item) => typeof item === "string"),
    };
  }

  function copyReviewQueueExport() {
    if (!state.reviewQueueExport && !state.reviewQueueCandidate) {
      setLive("No review queue export available.");
      return;
    }
    const payload = state.reviewQueueExport || buildSafeReviewQueueExport({ items: [], next_steps: [] });
    copyText(JSON.stringify(payload, null, 2));
  }

  function downloadReviewQueueExport() {
    if (!state.reviewQueueExport && !state.reviewQueueCandidate) {
      setLive("No review queue export available.");
      return;
    }
    const payload = state.reviewQueueExport || buildSafeReviewQueueExport({ items: [], next_steps: [] });
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: "application/json",
    });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.href = url;
    const first = payload.items && payload.items[0] ? payload.items[0] : {};
    link.download = `${safeFilenamePart(first.id || "review-queue")}-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
    link.click();
    URL.revokeObjectURL(url);
    setLive("Review queue export prepared.");
  }

  function sanitizeAuditLog(item) {
    const safe = pickFields(item || {}, SAFE_AUDIT_LOG_FIELDS);
    safe.safe_summary = pickFields(safe.safe_summary || {}, SAFE_AUDIT_COUNT_FIELDS);
    safe.safe_counts = pickFields(safe.safe_counts || {}, SAFE_AUDIT_COUNT_FIELDS);
    if (safe.association) {
      safe.association = sanitizeAuditAssociation(safe.association);
    }
    return safe;
  }

  function sanitizeAuditAssociation(item) {
    const safe = pickFields(item || {}, SAFE_AUDIT_ASSOCIATION_FIELDS);
    safe.arguments_summary = pickFields(safe.arguments_summary || {}, [
      "argument_keys",
      "argument_count",
      "status",
    ]);
    safe.result_summary = pickFields(safe.result_summary || {}, [
      "result_keys",
      "result_count",
      "status",
    ]);
    safe.validation_counts = pickFields(safe.validation_counts || {}, SAFE_AUDIT_COUNT_FIELDS);
    return safe;
  }

  function buildSafeAuditExportPayload(data) {
    const safe = pickFields(data || {}, SAFE_AUDIT_EXPORT_FIELDS);
    safe.fields = (Array.isArray(safe.fields) ? safe.fields : []).filter((field) =>
      SAFE_AUDIT_LOG_FIELDS.includes(field),
    );
    safe.filter_summary = pickFields(safe.filter_summary || {}, [
      "user_id",
      "request_id",
      "trace_id",
      "action",
      "resource_type",
      "resource_id",
      "status",
      "created_at_from",
      "created_at_to",
      "limit",
      "include_associations",
    ]);
    safe.request_ids = (Array.isArray(safe.request_ids) ? safe.request_ids : []).filter(
      (item) => typeof item === "string",
    );
    safe.trace_ids = (Array.isArray(safe.trace_ids) ? safe.trace_ids : []).filter(
      (item) => typeof item === "string",
    );
    safe.items = (Array.isArray(safe.items) ? safe.items : []).map((item) => sanitizeAuditLog(item || {}));
    return safe;
  }

  async function copyAuditExplorerExport() {
    const payload = state.auditExplorerExport || (await fetchAuditExplorerExport());
    if (!payload) {
      setLive("No audit export available.");
      return;
    }
    copyText(JSON.stringify(payload, null, 2));
  }

  async function downloadAuditExplorerExport() {
    const payload = state.auditExplorerExport || (await fetchAuditExplorerExport());
    if (!payload) {
      setLive("No audit export available.");
      return;
    }
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: "application/json",
    });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.href = url;
    link.download = auditExplorerExportFilename(payload);
    link.click();
    URL.revokeObjectURL(url);
    setLive("Audit export prepared.");
  }

  function auditExplorerExportFilename(payload) {
    const id = safeFilenamePart(payload.export_id || payload.request_ids?.[0] || payload.trace_ids?.[0] || "audit-export");
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    return `${id}-${timestamp}.json`;
  }

  function evalReportRow(summary) {
    const row = document.createElement("div");
    row.className = "eval-report-row";
    row.append(
      resultInline("report", summary.report_filename || "unknown"),
      evalDecisionChip(summary.decision),
      resultInline("type", summary.report_type || "unknown"),
      resultInline("cases", summary.case_count),
      resultInline("failed_count", summary.failed_count),
    );
    return row;
  }

  function evalCaseRow(item) {
    const row = document.createElement("div");
    row.className = "eval-case-row";
    row.append(
      resultInline("case_id", item.case_id || "unknown"),
      resultInline("failure_stage", item.failure_stage || "unknown"),
      resultInline("matched_documents", item.matched_documents || []),
      resultInline("matched_chunks", item.matched_chunks || []),
      resultInline("matched_citations", item.matched_citations || []),
      resultInline("counts", {
        retrieval_result_count: item.retrieval_result_count,
        context_item_count: item.context_item_count,
        citation_count: item.citation_count,
        unsupported_count: item.unsupported_count,
        forged_reference_count: item.forged_reference_count,
        prompt_risk_count: item.prompt_risk_count,
      }),
      resultInline("request_id", item.request_id || ""),
      resultInline("trace_id", item.trace_id || ""),
      resultInline("top_k", item.top_k),
      resultInline("latency_ms", item.latency_ms),
      resultInline("generation", item.generation || {}),
    );
    return row;
  }

  function evalGateMetricRow(metric) {
    const row = document.createElement("div");
    row.className = "eval-gate-row";
    row.append(
      resultInline("metric", metric.metric || "unknown"),
      evalDecisionChip(metric.passed ? "passed" : "failed"),
      resultInline("threshold_name", metric.threshold_name || ""),
      resultInline("expected", metric.expected),
      resultInline("actual", metric.actual),
    );
    return row;
  }

  function resultInline(label, value) {
    const item = document.createElement("span");
    item.className = "eval-metric";
    const name = document.createElement("span");
    name.className = "result-label";
    name.textContent = label;
    const content = document.createElement("span");
    content.className = "value id-value";
    content.textContent = formatValue(value);
    item.append(name, content);
    return item;
  }

  function evalDecisionChip(decision) {
    const statusMeta = statusLabel(decision === "passed" ? "success" : decision === "failed" ? "failure" : "unknown");
    const chip = document.createElement("span");
    chip.className = "status-chip";
    chip.dataset.tone = statusMeta.tone;
    const icon = document.createElement("span");
    icon.textContent = statusMeta.statusIcon;
    const text = document.createElement("span");
    text.textContent = decision || "unknown";
    chip.append(icon, text);
    return chip;
  }

  function sanitizeEvalCase(item) {
    const safe = pickFields(item || {}, SAFE_EVAL_CASE_FIELDS);
    safe.generation = pickFields(safe.generation || {}, SAFE_EVAL_GENERATION_FIELDS);
    return safe;
  }

  function renderEvalEvidenceNextSteps(nextSteps) {
    const commands = Array.isArray(nextSteps) ? nextSteps.filter((item) => typeof item === "string") : [];
    byId("eval-evidence-next-steps").replaceChildren(
      ...commands.map((command) => {
        const code = document.createElement("code");
        code.textContent = command;
        return code;
      }),
    );
  }

  function buildSafeEvalEvidenceReport(data) {
    const safe = pickFields(data || {}, SAFE_EVAL_REPORT_EXPORT_FIELDS);
    safe.summary = pickFields(safe.summary || {}, SAFE_EVAL_REPORT_SUMMARY_FIELDS);
    safe.failed_cases = (Array.isArray(safe.failed_cases) ? safe.failed_cases : []).map((item) =>
      sanitizeEvalCase(item || {}),
    );
    safe.gate_metrics = (Array.isArray(safe.gate_metrics) ? safe.gate_metrics : []).map((metric) =>
      pickFields(metric || {}, SAFE_EVAL_GATE_FIELDS),
    );
    safe.next_steps = (Array.isArray(safe.next_steps) ? safe.next_steps : []).filter(
      (item) => typeof item === "string",
    );
    return safe;
  }

  function copyEvalEvidenceReport() {
    if (!state.evalEvidenceReport) {
      setLive("No eval report available.");
      return;
    }
    copyText(JSON.stringify(state.evalEvidenceReport, null, 2));
  }

  function downloadEvalEvidenceReport() {
    if (!state.evalEvidenceReport) {
      setLive("No eval report available.");
      return;
    }
    const blob = new Blob([JSON.stringify(state.evalEvidenceReport, null, 2)], {
      type: "application/json",
    });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.href = url;
    link.download = evalEvidenceReportFilename(state.evalEvidenceReport);
    link.click();
    URL.revokeObjectURL(url);
    setLive("Eval report prepared.");
  }

  function evalEvidenceReportFilename(report) {
    const summary = report.summary || {};
    const id = safeFilenamePart(summary.report_filename || "eval-evidence");
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    return `${id}-${timestamp}.json`;
  }

  function clearEvalEvidenceRegions() {
    const reportList = optionalById("eval-evidence-report-list");
    if (reportList) {
      reportList.replaceChildren();
    }
    clearEvalEvidenceDetailRegions();
  }

  function clearEvalEvidenceDetailRegions() {
    ["eval-evidence-summary", "eval-evidence-cases", "eval-evidence-next-steps"].forEach((id) => {
      const element = optionalById(id);
      if (element) {
        element.replaceChildren();
      }
    });
    state.evalEvidenceReport = null;
  }

  function clearAuditExplorerRegions() {
    ["audit-explorer-results", "audit-explorer-detail", "audit-explorer-next-steps"].forEach((id) => {
      const element = optionalById(id);
      if (element) {
        element.replaceChildren();
      }
    });
    state.auditExplorerExport = null;
    state.toolEvents = null;
  }

  function clearReviewQueueRegions() {
    [
      "review-queue-alert",
      "review-queue-list",
      "review-queue-detail",
      "review-queue-status-history",
      "review-queue-candidate",
      "review-queue-next-steps",
    ].forEach((id) => {
      const element = optionalById(id);
      if (element) {
        element.replaceChildren();
      }
    });
    state.reviewQueueExport = null;
    state.reviewQueueCandidate = null;
    state.toolEvents = null;
  }

  function clearReviewQueueDetailRegions() {
    [
      "review-queue-alert",
      "review-queue-detail",
      "review-queue-status-history",
      "review-queue-candidate",
      "review-queue-next-steps",
    ].forEach((id) => {
      const element = optionalById(id);
      if (element) {
        element.replaceChildren();
      }
    });
    state.reviewQueueExport = null;
    state.reviewQueueCandidate = null;
    state.toolEvents = null;
  }

  function safeAuditNextStepCommand() {
    const code = document.createElement("code");
    code.textContent = ".venv\\Scripts\\python.exe -m pytest tests/unit/audit_explorer tests/integration/api/test_audit_explorer_routes.py -q";
    return code;
  }

  function safeReviewQueueNextStepCommand() {
    const code = document.createElement("code");
    code.textContent = ".venv\\Scripts\\python.exe -m pytest tests/unit/review_queue tests/integration/api/test_review_queue_routes.py -q";
    return code;
  }

  function safeEvalNextStepCommand() {
    const code = document.createElement("code");
    code.textContent = ".venv\\Scripts\\python.exe -m pytest tests/unit/eval_evidence tests/eval -q";
    return code;
  }

  function safeNextStepRow() {
    return resultRow("next_step", "Open docs/demo/governance-workbench.md and retry with request_id or trace_id.", false);
  }

  function clearDocumentReviewRegions() {
    byId("document-review-list").replaceChildren();
    byId("document-review-detail").replaceChildren();
    byId("document-review-timeline").replaceChildren();
  }

  function safeNextStepCommand() {
    const code = document.createElement("code");
    code.textContent = "Open docs/demo/governance-workbench.md and retry with request_id or trace_id.";
    return code;
  }

  function diagnosticsRenderTargets() {
    return {
      summaryId: "diagnostics-result",
      timelineId: "diagnostics-stages",
      nextStepsId: "diagnostics-next-steps",
      reportKey: "default",
      failureMessage: "Diagnostics summary cannot be displayed for this request.",
    };
  }

  function clearDiagnosticsRegions(options = diagnosticsRenderTargets()) {
    const summary = optionalById(options.summaryId);
    const timeline = optionalById(options.timelineId);
    const nextSteps = optionalById(options.nextStepsId);
    if (summary) {
      summary.replaceChildren();
    }
    if (timeline) {
      timeline.replaceChildren();
    }
    if (nextSteps) {
      nextSteps.replaceChildren();
    }
    state.diagnosticsReports[options.reportKey || "default"] = null;
  }

  function sanitizeDiagnosticsStage(stage) {
    const safeStage = pickFields(stage || {}, SAFE_DIAGNOSTICS_TIMELINE_FIELDS);
    safeStage.counts = pickFields(safeStage.counts || {}, SAFE_DIAGNOSTICS_COUNT_FIELDS);
    return safeStage;
  }

  function diagnosticsStageRow(stage) {
    const row = document.createElement("div");
    row.className = "diagnostics-stage-row";
    const label = document.createElement("span");
    label.className = "result-label";
    label.textContent = stage.name || "unknown";

    const statusMeta = statusLabel(stage.status);
    const chip = document.createElement("div");
    chip.className = "status-chip";
    chip.dataset.tone = statusMeta.tone;
    const statusIcon = document.createElement("span");
    statusIcon.textContent = statusMeta.statusIcon;
    const statusText = document.createElement("span");
    statusText.textContent = statusMeta.statusLabel;
    chip.append(statusIcon, statusText);

    const detail = document.createElement("span");
    detail.className = "value id-value";
    const parts = [];
    if (stage.latency_ms !== undefined && stage.latency_ms !== null) {
      parts.push(`latency_ms=${stage.latency_ms}`);
    }
    if (stage.error_code) {
      parts.push(`error_code=${stage.error_code}`);
    }
    Object.entries(stage.counts || {}).forEach(([key, value]) => {
      parts.push(`${key}=${formatValue(value)}`);
    });
    detail.textContent = parts.join(" | ");
    row.append(label, chip, detail);
    return row;
  }

  function renderDiagnosticsResult(data, options = diagnosticsRenderTargets()) {
    const summary = pickFields(data.summary || {}, SAFE_DIAGNOSTICS_SUMMARY_FIELDS);
    const summaryRows = [];
    SAFE_DIAGNOSTICS_SUMMARY_FIELDS.forEach((field) => {
      if (summary[field] !== undefined && summary[field] !== null && summary[field] !== "") {
        summaryRows.push(resultRow(field, summary[field], false));
      }
    });
    byId(options.summaryId).replaceChildren(...summaryRows);

    const stageRows = [];
    (Array.isArray(data.stages) ? data.stages : []).forEach((stage) => {
      const safeStage = sanitizeDiagnosticsStage(stage || {});
      stageRows.push(diagnosticsStageRow(safeStage));
    });
    byId(options.timelineId).replaceChildren(...stageRows);
    renderDiagnosticsNextSteps(data.next_steps, options.nextStepsId);
    state.diagnosticsReports[options.reportKey || "default"] = buildSafeDiagnosticsReport(data);
  }

  function renderDiagnosticsNextSteps(nextSteps, targetId = "diagnostics-next-steps") {
    const commands = Array.isArray(nextSteps) ? nextSteps.filter((item) => typeof item === "string") : [];
    byId(targetId).replaceChildren();
    if (!commands.length) {
      return;
    }
    byId(targetId).replaceChildren(
      ...commands.map((command) => {
        const code = document.createElement("code");
        code.textContent = command;
        return code;
      }),
    );
  }

  function buildSafeDiagnosticsReport(data) {
    const candidate = data.report && typeof data.report === "object" ? data.report : data;
    const safe = pickFields(candidate, SAFE_DIAGNOSTICS_REPORT_FIELDS);
    safe.lookup = pickFields(safe.lookup || data.lookup || {}, [
      "request_id",
      "trace_id",
      "include_report",
    ]);
    safe.summary = pickFields(safe.summary || data.summary || {}, SAFE_DIAGNOSTICS_SUMMARY_FIELDS);
    safe.stages = (Array.isArray(safe.stages) ? safe.stages : data.stages || []).map((stage) =>
      sanitizeDiagnosticsStage(stage || {}),
    );
    safe.next_steps = (Array.isArray(safe.next_steps) ? safe.next_steps : data.next_steps || []).filter(
      (item) => typeof item === "string",
    );
    if (!safe.generated_at && candidate.generated_at) {
      safe.generated_at = candidate.generated_at;
    }
    return safe;
  }

  function copyDiagnosticsReport(reportKey = "default") {
    const report = state.diagnosticsReports[reportKey] || null;
    if (!report) {
      setLive("No diagnostics report available.");
      return;
    }
    copyText(JSON.stringify(report, null, 2));
  }

  function downloadDiagnosticsReport(reportKey = "default") {
    const report = state.diagnosticsReports[reportKey] || null;
    if (!report) {
      setLive("No diagnostics report available.");
      return;
    }
    const blob = new Blob([JSON.stringify(report, null, 2)], {
      type: "application/json",
    });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.href = url;
    link.download = diagnosticsReportFilename(report);
    link.click();
    URL.revokeObjectURL(url);
    setLive("Diagnostics report prepared.");
  }

  function diagnosticsReportFilename(report) {
    const lookup = report.lookup || {};
    const id = safeFilenamePart(lookup.request_id || lookup.trace_id || "diagnostics");
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    return `${id}-${timestamp}.json`;
  }

  function safeFilenamePart(value) {
    const normalized = String(value || "diagnostics")
      .trim()
      .replace(/[^A-Za-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 96);
    return normalized || "diagnostics";
  }

  function resultRow(label, value, isExcerpt) {
    const row = document.createElement("div");
    row.className = "result-row";
    const labelNode = document.createElement("span");
    labelNode.className = "result-label";
    labelNode.textContent = label;
    const valueNode = document.createElement("span");
    valueNode.className = isExcerpt ? "value excerpt" : "value id-value";
    valueNode.textContent = formatValue(value);
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "secondary";
    copy.textContent = "Copy";
    copy.setAttribute("aria-label", `Copy ${label}`);
    copy.addEventListener("click", () => copyText(formatValue(value)));
    row.append(labelNode, valueNode, copy);
    return row;
  }

  function statusRow(label, node, detailText) {
    const row = document.createElement("div");
    row.className = "status-row";
    const labelNode = document.createElement("span");
    labelNode.className = "result-label";
    labelNode.textContent = label;
    const detail = document.createElement("span");
    detail.className = "value id-value";
    detail.textContent = detailText || "";
    row.append(labelNode, node, detail);
    return row;
  }

  function statusLabel(status) {
    const mapped = STATUS_MAP[status] || ["[?]", "Unknown status", "unknown"];
    return {
      statusIcon: mapped[0],
      statusLabel: mapped[1],
      tone: mapped[2],
    };
  }

  function openInspector() {
    const sheet = byId("inspector-sheet");
    sheet.hidden = false;
    byId("inspector-title").focus();
  }

  function closeInspector() {
    const sheet = byId("inspector-sheet");
    sheet.hidden = true;
    if (state.lastTrigger && typeof state.lastTrigger.focus === "function") {
      state.lastTrigger.focus();
    }
  }

  function syncDiagnostics(data) {
    if (data.request_id) {
      byId("diagnostic-request").value = data.request_id;
    }
    if (data.trace_id) {
      byId("diagnostic-trace").value = data.trace_id;
    }
  }

  function copyDiagnostics() {
    const parts = [
      byId("diagnostic-request").value.trim(),
      byId("diagnostic-trace").value.trim(),
    ].filter(Boolean);
    copyText(parts.join("\n"));
  }

  function copyText(text) {
    if (!text || !navigator.clipboard) {
      setLive("Copy unavailable.");
      return;
    }
    navigator.clipboard.writeText(text).then(
      () => setLive("Copied."),
      () => setLive("Copy unavailable."),
    );
  }

  function parsePageValue(value, field) {
    if (!/^\d+$/.test(value)) {
      showAlert(`${field} must be a positive integer when provided.`);
      return null;
    }
    const parsed = Number(value);
    if (!Number.isSafeInteger(parsed) || parsed < 1) {
      showAlert(`${field} must be a positive integer when provided.`);
      return null;
    }
    return parsed;
  }

  function trapInspectorFocus(event) {
    if (event.key !== "Tab" || byId("inspector-sheet").hidden) {
      return;
    }
    const focusable = Array.from(
      byId("inspector-sheet").querySelectorAll(
        "button, [href], input, textarea, select, [tabindex]:not([tabindex='-1'])",
      ),
    ).filter((node) => !node.disabled && !node.hidden);
    if (!focusable.length) {
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
      return;
    }
    if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function pickFields(source, fields) {
    const result = {};
    fields.forEach((field) => {
      if (Object.prototype.hasOwnProperty.call(source, field)) {
        result[field] = source[field];
      }
    });
    return result;
  }

  function formatValue(value) {
    if (Array.isArray(value)) {
      return value.join(" / ");
    }
    if (value && typeof value === "object") {
      return JSON.stringify(value);
    }
    return String(value);
  }

  function isEmptyObject(value) {
    return value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length === 0;
  }

  function normalizeValue(value) {
    if (value === null || value === undefined) {
      return "";
    }
    return String(value).trim();
  }

  function showAlert(message) {
    const alert = byId("alert-region");
    alert.textContent = message;
    alert.hidden = false;
  }

  function hideAlert() {
    const alert = byId("alert-region");
    alert.textContent = "";
    alert.hidden = true;
  }

  function setLive(message) {
    byId("live-region").textContent = message;
  }

  function byId(id) {
    return document.getElementById(id);
  }

  function optionalById(id) {
    return document.getElementById(id);
  }

  window.sidecarContract = {
    CITATION_INPUT_FIELDS,
    SAFE_SOURCE_FIELDS,
    SOURCE_EVIDENCE_MAX_ITEMS,
    SAFE_SOURCE_EVIDENCE_FIELDS,
    SAFE_STATUS_FIELDS,
    SAFE_DOCUMENT_REVIEW_FIELDS,
    SAFE_DOCUMENT_REVIEW_DETAIL_FIELDS,
    SAFE_DOCUMENT_REVIEW_LIFECYCLE_FIELDS,
    SAFE_DIAGNOSTICS_SUMMARY_FIELDS,
    SAFE_DIAGNOSTICS_STAGE_FIELDS,
    SAFE_DIAGNOSTICS_TIMELINE_FIELDS,
    SAFE_DIAGNOSTICS_COUNT_FIELDS,
    SAFE_DIAGNOSTICS_REPORT_FIELDS,
    SAFE_EVAL_REPORT_SUMMARY_FIELDS,
    SAFE_EVAL_CASE_FIELDS,
    SAFE_EVAL_GATE_FIELDS,
    SAFE_EVAL_REPORT_EXPORT_FIELDS,
    SAFE_AUDIT_LOG_FIELDS,
    SAFE_AUDIT_ASSOCIATION_FIELDS,
    SAFE_TOOL_EVENT_FIELDS,
    SAFE_TOOL_EVENT_METADATA_FIELDS,
    SAFE_AUDIT_EXPORT_FIELDS,
    SAFE_AUDIT_COUNT_FIELDS,
    SAFE_REVIEW_ITEM_FIELDS,
    SAFE_REVIEW_IDENTIFIER_FIELDS,
    SAFE_REVIEW_SUMMARY_FIELDS,
    SAFE_REVIEW_STATUS_HISTORY_FIELDS,
    SAFE_EVAL_CANDIDATE_FIELDS,
    GOVERNANCE_VIEWS,
    GOVERNANCE_BACKEND_VIEW_MAP,
    GOVERNANCE_SAFE_FIELDS,
    fetchSourceResolve,
    parseSourceEvidenceInputForTest: parseSourceEvidenceInput,
    resolveSourceEvidenceSetForTest: resolveSourceEvidenceSet,
    renderSourceEvidenceSetForTest: renderSourceEvidenceSet,
    copySourceEvidenceSummaryForTest: copySourceEvidenceSummary,
    parseToolEventFallbackForTest: parseToolEventFallback,
    renderToolEventFallbackForTest: renderToolEventFallback,
    fetchDocumentStatus,
    fetchDocumentReviewListForTest: fetchDocumentReviewList,
    fetchDocumentReviewDetailForTest: fetchDocumentReviewDetail,
    fetchDiagnosticsForTest: fetchDiagnostics,
    fetchGovernanceDiagnosticsForTest: fetchGovernanceDiagnostics,
    fetchEvalEvidenceReportsForTest: fetchEvalEvidenceReports,
    fetchEvalEvidenceDetailForTest: fetchEvalEvidenceDetail,
    fetchAuditExplorerLogsForTest: fetchAuditExplorerLogs,
    fetchAuditExplorerExportForTest: fetchAuditExplorerExport,
    renderStatusResultForTest: renderStatusResult,
    renderDocumentReviewListForTest: renderDocumentReviewList,
    renderDocumentReviewDetailForTest: renderDocumentReviewDetail,
    renderDocumentReviewFailureForTest: renderDocumentReviewFailure,
    renderDiagnosticsResultForTest: renderDiagnosticsResult,
    renderGovernanceDiagnosticsResultForTest: (data) =>
      renderDiagnosticsResult(data, {
        summaryId: "governance-diagnostics-summary",
        timelineId: "governance-diagnostics-timeline",
        nextStepsId: "governance-diagnostics-next-steps",
        failureMessage: "Retrieval diagnostics cannot be displayed for this request.",
      }),
    renderGovernanceDiagnosticsFailureForTest: (envelope) =>
      renderDiagnosticsFailure(envelope, {
        summaryId: "governance-diagnostics-summary",
        timelineId: "governance-diagnostics-timeline",
        nextStepsId: "governance-diagnostics-next-steps",
        failureMessage: "Retrieval diagnostics cannot be displayed for this request.",
      }),
    renderGovernanceFailureForTest: renderGovernanceFailure,
    renderEvalEvidenceReportListForTest: renderEvalEvidenceReportList,
    renderEvalEvidenceDetailForTest: renderEvalEvidenceDetail,
    renderEvalEvidenceFailureForTest: renderEvalEvidenceFailure,
    renderAuditExplorerListForTest: renderAuditExplorerList,
    renderAuditExplorerFailureForTest: renderAuditExplorerFailure,
    copyAuditExplorerExportForTest: copyAuditExplorerExport,
    downloadAuditExplorerExportForTest: downloadAuditExplorerExport,
    createReviewQueueItemForTest: createReviewQueueItem,
    fetchReviewQueueItemsForTest: fetchReviewQueueItems,
    fetchReviewQueueDetailForTest: fetchReviewQueueDetail,
    convertReviewQueueCandidateForTest: convertReviewQueueCandidate,
    renderReviewQueueListForTest: renderReviewQueueList,
    renderReviewQueueDetailForTest: renderReviewQueueDetail,
    renderReviewQueueFailureForTest: renderReviewQueueFailure,
    copyReviewQueueExportForTest: copyReviewQueueExport,
    downloadReviewQueueExportForTest: downloadReviewQueueExport,
    syncDiagnosticsForTest: syncDiagnostics,
    copyTextForTest: copyText,
  };
})();
