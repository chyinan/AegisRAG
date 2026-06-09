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
  };

  const state = {
    lastTrigger: null,
    diagnosticsReport: null,
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
    byId("close-inspector").addEventListener("click", closeInspector);
    byId("copy-diagnostics").addEventListener("click", copyDiagnostics);
    byId("copy-diagnostics-report").addEventListener("click", copyDiagnosticsReport);
    byId("download-diagnostics-report").addEventListener("click", downloadDiagnosticsReport);
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
    const payload = collectDiagnosticsPayload();
    if (!payload.request_id && !payload.trace_id) {
      showAlert("Request ID or Trace ID is required.");
      return;
    }
    setLive("Loading diagnostics summary...");
    hideAlert();
    state.diagnosticsReport = null;
    try {
      const response = await fetch(DIAGNOSTICS_ENDPOINT, {
        method: "POST",
        headers: buildHeaders(),
        body: JSON.stringify(payload),
      });
      const envelope = await response.json();
      if (!response.ok || envelope.error) {
        renderDiagnosticsFailure(envelope);
        return;
      }
      renderDiagnosticsResult(envelope.data || {});
      setLive("Diagnostics summary loaded.");
    } catch {
      renderDiagnosticsFailure(null);
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

  function renderDiagnosticsFailure(envelope) {
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
    byId("diagnostics-result").replaceChildren(...rows);
    byId("diagnostics-stages").replaceChildren();
    byId("diagnostics-next-steps").replaceChildren(safeNextStepCommand());
    state.diagnosticsReport = null;
    showAlert("Diagnostics summary cannot be displayed for this request.");
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

  function renderDiagnosticsResult(data) {
    const summary = pickFields(data.summary || {}, SAFE_DIAGNOSTICS_SUMMARY_FIELDS);
    const summaryRows = [];
    SAFE_DIAGNOSTICS_SUMMARY_FIELDS.forEach((field) => {
      if (summary[field] !== undefined && summary[field] !== null && summary[field] !== "") {
        summaryRows.push(resultRow(field, summary[field], false));
      }
    });
    byId("diagnostics-result").replaceChildren(...summaryRows);

    const stageRows = [];
    (Array.isArray(data.stages) ? data.stages : []).forEach((stage) => {
      const safeStage = pickFields(stage || {}, SAFE_DIAGNOSTICS_STAGE_FIELDS);
      stageRows.push(resultRow("stage", safeStage, false));
    });
    byId("diagnostics-stages").replaceChildren(...stageRows);
    renderDiagnosticsNextSteps(data.next_steps);
    state.diagnosticsReport = buildSafeDiagnosticsReport(data);
  }

  function renderDiagnosticsNextSteps(nextSteps) {
    const commands = Array.isArray(nextSteps) ? nextSteps.filter((item) => typeof item === "string") : [];
    byId("diagnostics-next-steps").replaceChildren();
    if (!commands.length) {
      return;
    }
    byId("diagnostics-next-steps").replaceChildren(
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
      pickFields(stage || {}, SAFE_DIAGNOSTICS_STAGE_FIELDS),
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
    SAFE_STATUS_FIELDS,
    SAFE_DOCUMENT_REVIEW_FIELDS,
    SAFE_DOCUMENT_REVIEW_DETAIL_FIELDS,
    SAFE_DOCUMENT_REVIEW_LIFECYCLE_FIELDS,
    SAFE_DIAGNOSTICS_SUMMARY_FIELDS,
    SAFE_DIAGNOSTICS_STAGE_FIELDS,
    SAFE_DIAGNOSTICS_REPORT_FIELDS,
    GOVERNANCE_VIEWS,
    GOVERNANCE_BACKEND_VIEW_MAP,
    GOVERNANCE_SAFE_FIELDS,
    fetchSourceResolve,
    fetchDocumentStatus,
    fetchDocumentReviewListForTest: fetchDocumentReviewList,
    fetchDocumentReviewDetailForTest: fetchDocumentReviewDetail,
    fetchDiagnosticsForTest: fetchDiagnostics,
    renderStatusResultForTest: renderStatusResult,
    renderDocumentReviewListForTest: renderDocumentReviewList,
    renderDocumentReviewDetailForTest: renderDocumentReviewDetail,
    renderDocumentReviewFailureForTest: renderDocumentReviewFailure,
    renderDiagnosticsResultForTest: renderDiagnosticsResult,
    renderGovernanceFailureForTest: renderGovernanceFailure,
    syncDiagnosticsForTest: syncDiagnostics,
    copyTextForTest: copyText,
  };
})();
