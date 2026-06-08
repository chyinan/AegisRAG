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

  const DOCUMENT_STATUS_ENDPOINT_PARTS = ["/documents/", "/versions/", "/status"];

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
  };

  document.addEventListener("DOMContentLoaded", init);

  function init() {
    bindTabs();
    bindForms();
    hydrateCitationInputs(parseCitationInputsFromLocation());
  }

  function bindTabs() {
    document.querySelectorAll("[data-view]").forEach((tab) => {
      tab.addEventListener("click", () => activateView(tab.dataset.view));
    });
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
    byId("close-inspector").addEventListener("click", closeInspector);
    byId("copy-diagnostics").addEventListener("click", copyDiagnostics);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !byId("inspector-sheet").hidden) {
        closeInspector();
      }
      trapInspectorFocus(event);
    });
  }

  function activateView(viewName) {
    document.querySelectorAll("[data-view]").forEach((tab) => {
      const isActive = tab.dataset.view === viewName;
      tab.classList.toggle("is-active", isActive);
      tab.setAttribute("aria-selected", String(isActive));
    });
    document.querySelectorAll(".view").forEach((view) => {
      const isActive = view.id === `view-${viewName}`;
      view.hidden = !isActive;
      view.classList.toggle("is-active", isActive);
    });
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
    byId(resultId).replaceChildren(...safeRows);
    setLive("Request ended with a safe failure state.");
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

  function statusRow(label, node) {
    const row = document.createElement("div");
    row.className = "status-row";
    const labelNode = document.createElement("span");
    labelNode.className = "result-label";
    labelNode.textContent = label;
    row.append(labelNode, node, document.createElement("span"));
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

  window.sidecarContract = {
    CITATION_INPUT_FIELDS,
    SAFE_SOURCE_FIELDS,
    SAFE_STATUS_FIELDS,
    fetchSourceResolve,
    fetchDocumentStatus,
    renderStatusResultForTest: renderStatusResult,
    copyTextForTest: copyText,
  };
})();
