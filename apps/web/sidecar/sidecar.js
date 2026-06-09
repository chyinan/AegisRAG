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
    evalSummary: [
      "dataset_version",
      "case_count",
      "failed_count",
      "citation_count",
      "latency_ms",
      "status",
      "request_id",
      "trace_id",
    ],
    auditSummary: [
      "action",
      "resource_type",
      "resource_id",
      "status",
      "error_code",
      "latency_ms",
      "agent_run_id",
      "tool_call_id",
      "request_id",
      "trace_id",
    ],
    reviewItem: [
      "item_type",
      "severity",
      "status",
      "document_id",
      "version_id",
      "chunk_id",
      "failure_stage",
      "error_code",
      "request_id",
      "trace_id",
    ],
  };

  const DOCUMENT_STATUS_ENDPOINT_PARTS = ["/documents/", "/versions/", "/status"];
  const DOCUMENT_REVIEW_ENDPOINT = "/documents/review";
  const DIAGNOSTICS_ENDPOINT = "/diagnostics/resolve";

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
    "not_available": ["[--]", "Not available", "unknown"],
  };

  const state = {
    lastTrigger: null,
    diagnosticsReport: null,
    sourceEvidenceSummary: null,
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
    byId("close-inspector").addEventListener("click", closeInspector);
    byId("copy-diagnostics").addEventListener("click", copyDiagnostics);
    byId("copy-diagnostics-report").addEventListener("click", copyDiagnosticsReport);
    byId("download-diagnostics-report").addEventListener("click", downloadDiagnosticsReport);
    const copyGovernanceDiagnosticsReport = optionalById("copy-governance-diagnostics-report");
    if (copyGovernanceDiagnosticsReport) {
      copyGovernanceDiagnosticsReport.addEventListener("click", copyDiagnosticsReport);
    }
    const downloadGovernanceDiagnosticsReport = optionalById("download-governance-diagnostics-report");
    if (downloadGovernanceDiagnosticsReport) {
      downloadGovernanceDiagnosticsReport.addEventListener("click", downloadDiagnosticsReport);
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
    if (hasSourceEvidenceIdentifier(value)) {
      candidates.push(value);
    }
    [
      value.citations,
      value.evidence,
      value.sources,
      value.metadata && value.metadata.citations,
      value.metadata && value.metadata.evidence,
      value.data && value.data.citations,
      value.openwebui_metadata && value.openwebui_metadata.citations,
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

  function hasSourceEvidenceIdentifier(value) {
    return Boolean(value.document_id || value.version_id || value.chunk_id);
  }

  function parseSourceEvidenceLink(value) {
    const raw = normalizeValue(value);
    if (!raw) {
      return null;
    }
    const queryPart = raw.includes("?") ? raw.split("?")[1].split("#")[0] : "";
    const hashPart = raw.includes("#") ? raw.split("#")[1] : "";
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
      loadingMessage: "Loading diagnostics summary...",
      successMessage: "Diagnostics summary loaded.",
      failureMessage: "Diagnostics summary cannot be displayed for this request.",
    });
  }

  async function fetchGovernanceDiagnostics() {
    await fetchDiagnosticsInto({
      payload: collectGovernanceDiagnosticsPayload(),
      summaryId: "governance-diagnostics-summary",
      timelineId: "governance-diagnostics-timeline",
      nextStepsId: "governance-diagnostics-next-steps",
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
    state.diagnosticsReport = null;
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
    state.diagnosticsReport = null;
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
    state.diagnosticsReport = buildSafeDiagnosticsReport(data);
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

  function copyDiagnosticsReport() {
    if (!state.diagnosticsReport) {
      setLive("No diagnostics report available.");
      return;
    }
    copyText(JSON.stringify(state.diagnosticsReport, null, 2));
  }

  function downloadDiagnosticsReport() {
    if (!state.diagnosticsReport) {
      setLive("No diagnostics report available.");
      return;
    }
    const blob = new Blob([JSON.stringify(state.diagnosticsReport, null, 2)], {
      type: "application/json",
    });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.href = url;
    link.download = diagnosticsReportFilename(state.diagnosticsReport);
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
    GOVERNANCE_VIEWS,
    GOVERNANCE_BACKEND_VIEW_MAP,
    GOVERNANCE_SAFE_FIELDS,
    fetchSourceResolve,
    parseSourceEvidenceInputForTest: parseSourceEvidenceInput,
    resolveSourceEvidenceSetForTest: resolveSourceEvidenceSet,
    renderSourceEvidenceSetForTest: renderSourceEvidenceSet,
    copySourceEvidenceSummaryForTest: copySourceEvidenceSummary,
    fetchDocumentStatus,
    fetchDocumentReviewListForTest: fetchDocumentReviewList,
    fetchDocumentReviewDetailForTest: fetchDocumentReviewDetail,
    fetchDiagnosticsForTest: fetchDiagnostics,
    fetchGovernanceDiagnosticsForTest: fetchGovernanceDiagnostics,
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
    syncDiagnosticsForTest: syncDiagnostics,
    copyTextForTest: copyText,
  };
})();
