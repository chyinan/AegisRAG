const fs = require("fs");
const vm = require("vm");

class Element {
  constructor(id = "", tagName = "div") {
    this.id = id;
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.eventListeners = {};
    this.attributes = {};
    this.dataset = {};
    this.hidden = false;
    this.value = "";
    this.textContent = "";
    this.className = "";
    this.type = "";
    this.name = "";
    this.required = false;
    this.controls = [];
    this.classList = {
      toggle: () => undefined,
    };
  }

  addEventListener(type, handler) {
    this.eventListeners[type] = this.eventListeners[type] || [];
    this.eventListeners[type].push(handler);
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  append(...nodes) {
    this.children.push(...nodes);
  }

  replaceChildren(...nodes) {
    this.children = [...nodes];
  }

  cloneNode(deep) {
    const clone = new Element(this.id, this.tagName);
    clone.hidden = this.hidden;
    clone.value = this.value;
    clone.textContent = this.textContent;
    clone.className = this.className;
    clone.type = this.type;
    clone.name = this.name;
    clone.dataset = { ...this.dataset };
    clone.attributes = { ...this.attributes };
    if (deep) {
      clone.children = this.children.map((child) =>
        typeof child.cloneNode === "function" ? child.cloneNode(true) : child,
      );
    }
    return clone;
  }

  focus() {
    document.activeElement = this;
  }

  click() {
    if (this.tagName === "A") {
      document.lastClickedLink = {
        href: this.href || "",
        download: this.download || "",
      };
    }
    (this.eventListeners.click || []).forEach((handler) => handler({ target: this }));
  }

  dispatch(type, event = {}) {
    (this.eventListeners[type] || []).forEach((handler) => handler(event));
  }

  querySelectorAll(selector) {
    const matches = [];
    const visit = (node) => {
      if ((selector === "button" || selector.startsWith("button,")) && node.tagName === "BUTTON") {
        matches.push(node);
      }
      node.children.forEach(visit);
    };
    visit(this);
    return matches;
  }
}

class DocumentStub {
  constructor() {
    this.elements = {};
    this.eventListeners = {};
    this.activeElement = null;
    this.tabs = [];
    this.views = [];
    this.authInputs = [];
    this.lastClickedLink = null;
  }

  add(id, tagName = "div") {
    const element = new Element(id, tagName);
    this.elements[id] = element;
    return element;
  }

  getElementById(id) {
    return this.elements[id] || null;
  }

  createElement(tagName) {
    return new Element("", tagName);
  }

  addEventListener(type, handler) {
    this.eventListeners[type] = this.eventListeners[type] || [];
    this.eventListeners[type].push(handler);
  }

  dispatch(type, event = {}) {
    (this.eventListeners[type] || []).forEach((handler) => handler(event));
  }

  querySelector(selector) {
    const nameMatch = selector.match(/^\[name="(.+)"\]$/);
    if (nameMatch) {
      return Object.values(this.elements).find((element) => element.name === nameMatch[1]) || null;
    }
    return null;
  }

  querySelectorAll(selector) {
    if (selector === "[data-view]") {
      return this.tabs;
    }
    if (selector === ".view") {
      return this.views;
    }
    if (selector === "[data-auth-header]") {
      return this.authInputs;
    }
    if (selector === "[data-governance-view]") {
      return this.governanceTabs || [];
    }
    if (selector === ".governance-view") {
      return this.governanceViews || [];
    }
    if (selector === "[data-governance-link-view]") {
      return this.governanceLinkButtons || [];
    }
    if (selector === "#inspector-sheet button, #inspector-sheet [href], #inspector-sheet input, #inspector-sheet textarea, #inspector-sheet select, #inspector-sheet [tabindex]:not([tabindex='-1'])") {
      return this.elements["inspector-sheet"].querySelectorAll("button");
    }
    return [];
  }
}

function setupSidecar() {
  global.document = new DocumentStub();
  global.window = {
    location: {
      search: "",
      hash: "",
    },
  };
  Object.defineProperty(globalThis, "navigator", {
    value: {
      clipboard: {
      writes: [],
      writeText(value) {
        this.writes.push(value);
        return Promise.resolve();
      },
    },
    },
    configurable: true,
  });
  global.URLSearchParams = URLSearchParams;
  global.URL = {
    created: [],
    revoked: [],
    createObjectURL(blob) {
      this.created.push(blob);
      return "blob:diagnostics-report";
    },
    revokeObjectURL(url) {
      this.revoked.push(url);
    },
  };
  global.Blob = class {
    constructor(parts, options) {
      this.parts = parts;
      this.options = options;
    }
  };
  global.FormData = class {
    constructor(form) {
      this.values = {};
      form.controls.forEach((control) => {
        this.values[control.name] = control.value;
      });
    }

    get(name) {
      return this.values[name] || "";
    }
  };

  const ids = [
    "source-form",
    "clear-source",
    "citation-json",
    "source-result",
    "status-form",
    "status-document",
    "status-version",
    "status-result",
    "close-inspector",
    "copy-diagnostics",
    "copy-diagnostics-report",
    "download-diagnostics-report",
    "diagnostics-form",
    "diagnostics-result",
    "diagnostics-stages",
    "diagnostics-next-steps",
    "governance-diagnostics-form",
    "governance-diagnostic-request",
    "governance-diagnostic-trace",
    "governance-diagnostics-summary",
    "governance-diagnostics-timeline",
    "governance-diagnostics-next-steps",
    "copy-governance-diagnostics-report",
    "download-governance-diagnostics-report",
    "inspector-sheet",
    "inspector-title",
    "diagnostic-request",
    "diagnostic-trace",
    "document-review-form",
    "document-review-status",
    "document-review-limit",
    "document-review-cursor",
    "document-review-document",
    "document-review-version",
    "document-review-list",
    "document-review-detail",
    "document-review-timeline",
    "document-review-detail-button",
    "source-evidence-form",
    "source-evidence-json",
    "source-evidence-document",
    "source-evidence-version",
    "source-evidence-chunk",
    "source-evidence-page-start",
    "source-evidence-page-end",
    "source-evidence-request",
    "source-evidence-results",
    "source-evidence-errors",
    "copy-source-evidence-summary",
    "eval-evidence-form",
    "eval-evidence-limit",
    "eval-evidence-report",
    "eval-evidence-refresh",
    "eval-evidence-load",
    "eval-evidence-report-list",
    "eval-evidence-summary",
    "eval-evidence-cases",
    "eval-evidence-next-steps",
    "copy-eval-evidence-report",
    "download-eval-evidence-report",
    "audit-explorer-form",
    "audit-explorer-user",
    "audit-explorer-request",
    "audit-explorer-trace",
    "audit-explorer-action",
    "audit-explorer-resource-type",
    "audit-explorer-resource-id",
    "audit-explorer-status",
    "audit-explorer-created-from",
    "audit-explorer-created-to",
    "audit-explorer-limit",
    "audit-explorer-search",
    "audit-explorer-copy-export",
    "audit-explorer-download-export",
    "audit-explorer-results",
    "audit-explorer-detail",
    "audit-explorer-next-steps",
    "auth-token",
    "alert-region",
    "live-region",
    "governance-scope",
    "governance-detail",
  ];
  ids.forEach((id) => document.add(id));
  document.elements["source-form"].tagName = "FORM";
  document.elements["status-form"].tagName = "FORM";
  document.elements["diagnostics-form"].tagName = "FORM";
  document.elements["governance-diagnostics-form"].tagName = "FORM";
  document.elements["document-review-form"].tagName = "FORM";
  document.elements["source-evidence-form"].tagName = "FORM";
  document.elements["eval-evidence-form"].tagName = "FORM";
  document.elements["audit-explorer-form"].tagName = "FORM";
  document.elements["close-inspector"].tagName = "BUTTON";
  document.elements["document-review-detail-button"].tagName = "BUTTON";
  document.elements["copy-source-evidence-summary"].tagName = "BUTTON";
  document.elements["eval-evidence-refresh"].tagName = "BUTTON";
  document.elements["eval-evidence-load"].tagName = "BUTTON";
  document.elements["copy-eval-evidence-report"].tagName = "BUTTON";
  document.elements["download-eval-evidence-report"].tagName = "BUTTON";
  document.elements["audit-explorer-search"].tagName = "BUTTON";
  document.elements["audit-explorer-copy-export"].tagName = "BUTTON";
  document.elements["audit-explorer-download-export"].tagName = "BUTTON";
  document.elements["copy-diagnostics"].tagName = "BUTTON";
  document.elements["copy-diagnostics-report"].tagName = "BUTTON";
  document.elements["download-diagnostics-report"].tagName = "BUTTON";
  document.elements["copy-governance-diagnostics-report"].tagName = "BUTTON";
  document.elements["download-governance-diagnostics-report"].tagName = "BUTTON";
  document.elements["inspector-title"].tagName = "H2";
  document.elements["inspector-sheet"].hidden = true;
  document.elements["inspector-sheet"].append(document.elements["close-inspector"]);

  ["document_id", "version_id", "chunk_id", "page_start", "page_end", "request_id", "citation_ref"].forEach(
    (name) => {
      const input = document.add(`source-${name}`, "input");
      input.name = name;
      document.elements["source-form"].controls.push(input);
    },
  );

  ["source", "status", "diagnostics"].forEach((viewName) => {
    const tab = new Element(`tab-${viewName}`, "button");
    tab.dataset.view = viewName;
    document.tabs.push(tab);
    const view = new Element(`view-${viewName}`, "section");
    document.views.push(view);
  });
  document.governanceTabs = [];
  document.governanceViews = [];
  document.governanceLinkButtons = [];
  ["document-review", "source-evidence", "retrieval-diagnostics", "eval-evidence", "audit-explorer", "review-queue"].forEach(
    (viewName) => {
      const tab = new Element(`governance-tab-${viewName}`, "button");
      tab.dataset.governanceView = viewName;
      document.governanceTabs.push(tab);
      const view = new Element(`governance-view-${viewName}`, "section");
      document.governanceViews.push(view);
    },
  );
  ["status", "source", "diagnostics"].forEach((viewName) => {
    const button = new Element(`governance-link-${viewName}`, "button");
    button.dataset.governanceLinkView = viewName;
    document.governanceLinkButtons.push(button);
  });
  ["status", "limit", "cursor", "document", "version"].forEach((name) => {
    const input = document.elements[`document-review-${name}`];
    input.name = name;
    document.elements["document-review-form"].controls.push(input);
  });
  [
    "document",
    "version",
    "chunk",
    "page-start",
    "page-end",
    "request",
  ].forEach((name) => {
    const input = document.elements[`source-evidence-${name}`];
    input.name = name;
    document.elements["source-evidence-form"].controls.push(input);
  });
  ["limit", "report"].forEach((name) => {
    const input = document.elements[`eval-evidence-${name}`];
    input.name = name;
    document.elements["eval-evidence-form"].controls.push(input);
  });
  [
    ["user", "user_id"],
    ["request", "request_id"],
    ["trace", "trace_id"],
    ["action", "action"],
    ["resource-type", "resource_type"],
    ["resource-id", "resource_id"],
    ["status", "status"],
    ["created-from", "created_at_from"],
    ["created-to", "created_at_to"],
    ["limit", "limit"],
  ].forEach(([idPart, name]) => {
    const input = document.elements[`audit-explorer-${idPart}`];
    input.name = name;
    document.elements["audit-explorer-form"].controls.push(input);
  });

  const script = fs.readFileSync("apps/web/sidecar/sidecar.js", "utf8");
  vm.runInThisContext(script);
  document.dispatch("DOMContentLoaded");
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function nodeText(node) {
  if (!node) {
    return "";
  }
  return [node.textContent || "", ...(node.children || []).map((child) => nodeText(child))].join(" ");
}

async function testSafeFailureClearsStaleSourceResults() {
  setupSidecar();
  const stale = new Element("", "div");
  stale.textContent = "authorized excerpt from prior request";
  document.getElementById("source-result").replaceChildren(stale);
  global.fetch = async () => {
    throw new Error("network");
  };

  await window.sidecarContract.fetchSourceResolve({
    document_id: "doc",
    version_id: "ver",
    chunk_id: "chunk",
  });

  const result = document.getElementById("source-result");
  const rendered = result.children.flatMap((row) => row.children.map((child) => child.textContent)).join(" ");
  assert(!rendered.includes("authorized excerpt"), "source failure should clear stale source rows");
  assert(rendered.includes("next_step"), "source failure should render a safe next step");
}

async function testSafeFailureDoesNotInventTraceIdFromRequestId() {
  setupSidecar();
  global.fetch = async () => ({
    ok: false,
    json: async () => ({
      request_id: "envelope-req",
      error: {
        details: {
          request_id: "detail-req",
        },
      },
    }),
  });

  await window.sidecarContract.fetchSourceResolve({
    document_id: "doc",
    version_id: "ver",
    chunk_id: "chunk",
  });

  const rendered = document
    .getElementById("source-result")
    .children.flatMap((row) => row.children.map((child) => child.textContent));
  assert(rendered.includes("request_id"), "failure should render request_id");
  assert(!rendered.includes("trace_id"), "failure should not render trace_id when trace_id is absent");
}

async function testStatusFailureCopyButtonKeepsHandler() {
  setupSidecar();
  global.fetch = async () => ({
    ok: false,
    json: async () => ({
      error: {
        details: {
          request_id: "req-status",
          trace_id: "trace-status",
        },
      },
    }),
  });

  await window.sidecarContract.fetchDocumentStatus("doc/with slash", "ver");
  const firstCopy = document.getElementById("status-result").children[0].children[2];
  firstCopy.click();

  assert(
    navigator.clipboard.writes.includes("req-status"),
    "status failure copy button should write copied request_id",
  );
}

async function testInvalidPageInputDoesNotSendNullPageBounds() {
  setupSidecar();
  document.getElementById("source-document_id").value = "doc";
  document.getElementById("source-version_id").value = "ver";
  document.getElementById("source-chunk_id").value = "chunk";
  document.getElementById("source-page_start").value = "abc";
  let body = "";
  global.fetch = async (_url, options) => {
    body = options.body;
    return {
      ok: true,
      json: async () => ({ data: { document_id: "doc", version_id: "ver", chunk_id: "chunk" } }),
    };
  };

  await document.getElementById("source-form").eventListeners.submit[0]({
    preventDefault: () => undefined,
    submitter: document.getElementById("source-form"),
  });

  const parsed = JSON.parse(body);
  assert(!Object.prototype.hasOwnProperty.call(parsed, "page_start"), "invalid page_start should not be sent");
}

function testDialogTrapKeepsTabFocusInsideInspector() {
  setupSidecar();
  document.getElementById("inspector-sheet").hidden = false;
  document.getElementById("close-inspector").focus();
  let prevented = false;
  document.dispatch("keydown", {
    key: "Tab",
    shiftKey: false,
    preventDefault: () => {
      prevented = true;
    },
  });

  assert(prevented, "Tab at end of modal should be trapped");
  assert(
    document.activeElement === document.getElementById("close-inspector"),
    "focus should remain inside inspector",
  );
}

function testUnknownStatusIsNotRenderedAsWorking() {
  setupSidecar();
  window.sidecarContract.renderStatusResultForTest({ status: "failed_blocked" });
  const chip = document.getElementById("status-result").children[0].children[1];

  assert(chip.dataset.tone !== "working", "unknown statuses should not use working tone");
}

async function testClipboardFallbackReportsUnavailableCopy() {
  setupSidecar();
  global.navigator.clipboard = undefined;
  window.sidecarContract.copyTextForTest("req-1");

  assert(
    document.getElementById("live-region").textContent.includes("Copy unavailable"),
    "clipboard fallback should report unavailable copy",
  );
}

async function testDiagnosticsLookupUsesSafePayload() {
  setupSidecar();
  document.getElementById("diagnostic-request").value = "req-diagnostic";
  document.getElementById("diagnostic-trace").value = "trace-diagnostic";
  let request = null;
  global.fetch = async (url, options) => {
    request = { url, options };
    return {
      ok: true,
      json: async () => ({
        data: {
          lookup: {
            request_id: "req-diagnostic",
            trace_id: "trace-diagnostic",
            include_report: true,
          },
          summary: {
            tenant_id: "tenant-1",
            user_id: "user-1",
            request_id: "req-diagnostic",
            trace_id: "trace-diagnostic",
            status: "success",
            result_count: 2,
          },
          stages: [],
          next_steps: [],
          report: {
            generated_at: "2026-06-09T00:00:00+08:00",
            summary: { request_id: "req-diagnostic", status: "success" },
          },
        },
      }),
    };
  };

  await window.sidecarContract.fetchDiagnosticsForTest();

  const payload = JSON.parse(request.options.body);
  assert(request.url === "/diagnostics/resolve", "diagnostics should call backend endpoint");
  assert(request.options.method === "POST", "diagnostics should use POST");
  assert(payload.request_id === "req-diagnostic", "diagnostics payload should include request_id");
  assert(payload.trace_id === "trace-diagnostic", "diagnostics payload should include trace_id");
  assert(payload.include_report === true, "diagnostics payload should request report");
  assert(!Object.prototype.hasOwnProperty.call(payload, "tenant_id"), "payload must not send tenant_id");
  assert(!Object.prototype.hasOwnProperty.call(payload, "user_id"), "payload must not send user_id");
}

async function testDiagnosticsFailureRendersOnlySafeDetails() {
  setupSidecar();
  document.getElementById("diagnostic-request").value = "req-diagnostic";
  global.fetch = async () => ({
    ok: false,
    json: async () => ({
      request_id: "envelope-req",
      error: {
        code: "DIAGNOSTICS_STORAGE_READ_FAILED",
        details: {
          request_id: "req-diagnostic",
          trace_id: "trace-diagnostic",
          failure_stage: "infrastructure",
          error_code: "DIAGNOSTICS_STORAGE_READ_FAILED",
          query_text: "must not render",
          raw_exception: "select * from secrets",
        },
      },
    }),
  });

  await window.sidecarContract.fetchDiagnosticsForTest();

  const rendered = document
    .getElementById("diagnostics-result")
    .children.flatMap((row) => row.children.map((child) => child.textContent))
    .join(" ");
  assert(rendered.includes("req-diagnostic"), "safe diagnostics failure should render request_id");
  assert(rendered.includes("infrastructure"), "safe diagnostics failure should render failure_stage");
  assert(!rendered.includes("must not render"), "diagnostics failure must not render query text");
  assert(!rendered.includes("select *"), "diagnostics failure must not render raw exception");
  assert(
    document.getElementById("diagnostics-next-steps").children.length === 1,
    "diagnostics failure must replace stale next steps with a safe fallback",
  );
}

async function testDiagnosticsReportExportUsesAllowlist() {
  setupSidecar();
  window.sidecarContract.renderDiagnosticsResultForTest({
    lookup: { request_id: "../unsafe req-report", include_report: true },
    summary: {
      tenant_id: "tenant-1",
      request_id: "req-report",
      status: "success",
      source_uri: "file:///secret",
    },
    stages: [],
    next_steps: [],
    report: {
      generated_at: "2026-06-09T00:00:00+08:00",
      summary: {
        request_id: "req-report",
        status: "success",
        answer_text: "must not export",
      },
      raw_exception: "must not export",
    },
  });

  document.getElementById("copy-diagnostics-report").click();

  const copied = navigator.clipboard.writes[navigator.clipboard.writes.length - 1];
  assert(copied.includes("req-report"), "report copy should include safe request_id");
  assert(!copied.includes("answer_text"), "report copy must not include answer text field");
  assert(!copied.includes("must not export"), "report copy must not include forbidden values");
  assert(!copied.includes("source_uri"), "report copy must not include source_uri");

  document.getElementById("download-diagnostics-report").click();

  assert(document.lastClickedLink.href === "blob:diagnostics-report", "download should use object URL");
  assert(URL.created.length === 1, "download should create one blob URL");
  assert(URL.revoked[0] === "blob:diagnostics-report", "download should revoke blob URL");
  assert(document.lastClickedLink.download.includes("unsafe-req-report"), "download filename should keep a sanitized lookup id");
  assert(!document.lastClickedLink.download.includes(".."), "download filename must not include path traversal");
  assert(!document.lastClickedLink.download.includes("/"), "download filename must not include path separators");
}

function testDiagnosticsNextStepsClearsStaleCommands() {
  setupSidecar();
  window.sidecarContract.renderDiagnosticsResultForTest({
    lookup: { request_id: "req-old" },
    summary: { request_id: "req-old", status: "success" },
    stages: [],
    next_steps: ["python -m pytest old.py"],
  });
  assert(
    document.getElementById("diagnostics-next-steps").children.length === 1,
    "first diagnostics result should render next steps",
  );

  window.sidecarContract.renderDiagnosticsResultForTest({
    lookup: { request_id: "req-new" },
    summary: { request_id: "req-new", status: "success" },
    stages: [],
    next_steps: [],
  });

  assert(
    document.getElementById("diagnostics-next-steps").children.length === 0,
    "empty diagnostics next steps must clear stale commands",
  );
}

function testSyncDiagnosticsDoesNotAutoLookup() {
  setupSidecar();
  let calls = 0;
  global.fetch = async () => {
    calls += 1;
    return { ok: true, json: async () => ({ data: {} }) };
  };

  window.sidecarContract.syncDiagnosticsForTest({
    request_id: "req-sync",
    trace_id: "trace-sync",
  });

  assert(document.getElementById("diagnostic-request").value === "req-sync", "request_id should sync");
  assert(document.getElementById("diagnostic-trace").value === "trace-sync", "trace_id should sync");
  assert(calls === 0, "syncDiagnostics must not auto-fetch diagnostics");
}

async function testGovernanceDiagnosticsLookupRendersTimeline() {
  setupSidecar();
  document.getElementById("governance-diagnostic-request").value = "req-governance-diagnostic";
  document.getElementById("governance-diagnostic-trace").value = "trace-governance-diagnostic";
  let request = null;
  global.fetch = async (url, options) => {
    request = { url, options };
    return {
      ok: true,
      json: async () => ({
        data: {
          lookup: {
            request_id: "req-governance-diagnostic",
            trace_id: "trace-governance-diagnostic",
            include_report: true,
          },
          summary: {
            tenant_id: "tenant-1",
            user_id: "user-1",
            request_id: "req-governance-diagnostic",
            trace_id: "trace-governance-diagnostic",
            status: "success",
            result_count: 2,
            highest_rerank_score: 0.91,
            raw_query: "must not render",
          },
          stages: [
            {
              name: "retrieval",
              status: "success",
              latency_ms: 12,
              counts: {
                dense_top_k: 8,
                candidate_ids: ["chunk-secret"],
              },
            },
            {
              name: "rrf_merge",
              status: "success",
              counts: {
                deduped_count: 4,
                filtered_count: 2,
                threshold_decision: "accepted",
                prompt: "must not render",
              },
            },
          ],
          next_steps: ["python -m pytest tests/unit/diagnostics -q"],
          report: {
            generated_at: "2026-06-09T00:00:00+08:00",
            summary: { request_id: "req-governance-diagnostic", status: "success" },
          },
        },
      }),
    };
  };

  await window.sidecarContract.fetchGovernanceDiagnosticsForTest();

  const payload = JSON.parse(request.options.body);
  assert(request.url === "/diagnostics/resolve", "governance diagnostics should call backend endpoint");
  assert(payload.request_id === "req-governance-diagnostic", "payload should include lookup request_id");
  assert(payload.trace_id === "trace-governance-diagnostic", "payload should include lookup trace_id");
  assert(payload.include_report === true, "payload should request report");
  assert(!Object.prototype.hasOwnProperty.call(payload, "tenant_id"), "payload must not send tenant_id");
  assert(!Object.prototype.hasOwnProperty.call(payload, "user_id"), "payload must not send user_id");

  const summaryText = nodeText(document.getElementById("governance-diagnostics-summary"));
  const timelineText = nodeText(document.getElementById("governance-diagnostics-timeline"));
  assert(summaryText.includes("req-governance-diagnostic"), "summary should render request_id");
  assert(timelineText.includes("retrieval"), "timeline should render retrieval stage");
  assert(timelineText.includes("rrf_merge"), "timeline should render rrf stage");
  assert(timelineText.includes("threshold_decision"), "timeline should render safe threshold decision");
  assert(!timelineText.includes("chunk-secret"), "timeline must not render candidate IDs");
  assert(!timelineText.includes("must not render"), "timeline must not render forbidden values");
  assert(
    document.getElementById("governance-diagnostics-next-steps").children.length === 1,
    "next steps should render backend safe commands",
  );
}

async function testGovernanceDiagnosticsPermissionFailureClearsStaleState() {
  setupSidecar();
  window.sidecarContract.renderGovernanceDiagnosticsResultForTest({
    lookup: { request_id: "req-old" },
    summary: { request_id: "req-old", status: "success" },
    stages: [{ name: "retrieval", status: "success", counts: { result_count: 1 } }],
    next_steps: ["python old.py"],
    report: { summary: { request_id: "req-old" } },
  });
  global.fetch = async () => ({
    ok: false,
    json: async () => ({
      error: {
        code: "DIAGNOSTICS_FORBIDDEN",
        details: {
          request_id: "req-denied",
          failure_stage: "permission",
          error_code: "DIAGNOSTICS_FORBIDDEN",
          chunk_content: "must not render",
        },
      },
    }),
  });
  document.getElementById("governance-diagnostic-request").value = "req-denied";

  await window.sidecarContract.fetchGovernanceDiagnosticsForTest();
  document.getElementById("copy-governance-diagnostics-report").click();

  const rendered = [
    nodeText(document.getElementById("governance-diagnostics-summary")),
    nodeText(document.getElementById("governance-diagnostics-timeline")),
    nodeText(document.getElementById("governance-diagnostics-next-steps")),
  ].join(" ");
  const copied = navigator.clipboard.writes[navigator.clipboard.writes.length - 1] || "";
  assert(rendered.includes("req-denied"), "failure should render safe request_id");
  assert(rendered.includes("permission"), "failure should render safe failure_stage");
  assert(!rendered.includes("req-old"), "failure should clear stale summary and timeline");
  assert(!rendered.includes("must not render"), "failure must not render forbidden details");
  assert(!copied.includes("req-old"), "failure should clear stale report copy state");
}

function testGovernanceDiagnosticsTabClearsBackendReportState() {
  setupSidecar();
  window.sidecarContract.renderDiagnosticsResultForTest({
    lookup: { request_id: "req-backend-old", include_report: true },
    summary: { request_id: "req-backend-old", status: "success" },
    stages: [{ name: "retrieval", status: "success", counts: { result_count: 1 } }],
    next_steps: ["python old.py"],
    report: { lookup: { request_id: "req-backend-old" }, summary: { request_id: "req-backend-old" } },
  });

  document.governanceTabs.find((tab) => tab.dataset.governanceView === "retrieval-diagnostics").click();
  document.getElementById("copy-governance-diagnostics-report").click();
  document.getElementById("copy-diagnostics-report").click();

  const copied = navigator.clipboard.writes.join("\n");
  assert(!copied.includes("req-backend-old"), "governance diagnostics tab should clear stale backend report state");
  assert(document.getElementById("diagnostics-result").children.length === 0, "backend summary should be cleared");
  assert(document.getElementById("diagnostics-stages").children.length === 0, "backend timeline should be cleared");
}

async function testGovernanceDiagnosticsFailureClearsBackendDiagnosticsDom() {
  setupSidecar();
  window.sidecarContract.renderDiagnosticsResultForTest({
    lookup: { request_id: "req-backend-old", include_report: true },
    summary: { request_id: "req-backend-old", status: "success" },
    stages: [{ name: "retrieval", status: "success", counts: { result_count: 1 } }],
    next_steps: ["python old.py"],
    report: { lookup: { request_id: "req-backend-old" }, summary: { request_id: "req-backend-old" } },
  });
  global.fetch = async () => ({
    ok: false,
    json: async () => ({
      error: {
        code: "DIAGNOSTICS_FORBIDDEN",
        details: {
          request_id: "req-denied",
          failure_stage: "permission",
          error_code: "DIAGNOSTICS_FORBIDDEN",
        },
      },
    }),
  });
  document.getElementById("governance-diagnostic-request").value = "req-denied";

  await window.sidecarContract.fetchGovernanceDiagnosticsForTest();

  assert(document.getElementById("diagnostics-result").children.length === 0, "governance failure clears backend summary");
  assert(document.getElementById("diagnostics-stages").children.length === 0, "governance failure clears backend timeline");
  assert(!nodeText(document.getElementById("diagnostics-next-steps")).includes("python old.py"), "governance failure clears backend next steps");
}

async function testGovernanceDiagnosticsNewLookupClearsReportCopyExport() {
  setupSidecar();
  window.sidecarContract.renderGovernanceDiagnosticsResultForTest({
    lookup: { request_id: "../old report" },
    summary: { request_id: "req-old", status: "success" },
    stages: [],
    next_steps: [],
    report: { summary: { request_id: "req-old" } },
  });
  let releaseResolve;
  global.fetch = async () =>
    new Promise((resolve) => {
      releaseResolve = () =>
        resolve({
          ok: true,
          json: async () => ({
            data: {
              lookup: { request_id: "../new report", include_report: true },
              summary: { request_id: "req-new", status: "success" },
              stages: [],
              next_steps: [],
              report: { summary: { request_id: "req-new" } },
            },
          }),
        });
    });
  document.getElementById("governance-diagnostic-request").value = "../new report";

  const pending = window.sidecarContract.fetchGovernanceDiagnosticsForTest();
  document.getElementById("copy-governance-diagnostics-report").click();
  const interimCopy = navigator.clipboard.writes[navigator.clipboard.writes.length - 1] || "";
  assert(!interimCopy.includes("req-old"), "new lookup should clear stale report before response");
  releaseResolve();
  await pending;

  document.getElementById("download-governance-diagnostics-report").click();
  assert(document.lastClickedLink.download.includes("new-report"), "download should use sanitized new lookup id");
  assert(!document.lastClickedLink.download.includes(".."), "download filename must not include path traversal");
}

function testGovernanceNavigationSwitchesViews() {
  setupSidecar();
  const evalTab = document.governanceTabs.find((tab) => tab.dataset.governanceView === "eval-evidence");
  evalTab.click();

  const evalView = document.governanceViews.find((view) => view.id === "governance-view-eval-evidence");
  const documentView = document.governanceViews.find((view) => view.id === "governance-view-document-review");
  assert(evalTab.attributes["aria-selected"] === "true", "selected governance tab should update aria state");
  assert(evalView.hidden === false, "selected governance view should become visible");
  assert(documentView.hidden === true, "previous governance view should be hidden");
}

function testGovernanceLinksBackendViews() {
  setupSidecar();
  const documentReviewTab = document.governanceTabs.find((tab) => tab.dataset.governanceView === "document-review");
  documentReviewTab.click();
  const statusTab = document.tabs.find((tab) => tab.dataset.view === "status");
  const sourceTab = document.tabs.find((tab) => tab.dataset.view === "source");
  assert(statusTab.attributes["aria-selected"] !== "true", "document review should no longer auto-activate status lookup");
  assert(sourceTab.attributes["aria-selected"] !== "false", "source lookup should remain the default backend view");

  const diagnosticsLink = document.governanceLinkButtons.find((button) => button.dataset.governanceLinkView === "diagnostics");
  diagnosticsLink.click();
  const diagnosticsTab = document.tabs.find((tab) => tab.dataset.view === "diagnostics");
  assert(diagnosticsTab.attributes["aria-selected"] === "true", "governance link should activate diagnostics view");
}

function testGovernanceKeyboardTabs() {
  setupSidecar();
  let prevented = false;
  document.governanceTabs[0].dispatch("keydown", {
    key: "ArrowRight",
    preventDefault: () => {
      prevented = true;
    },
  });

  const sourceEvidenceTab = document.governanceTabs[1];
  assert(prevented, "governance keyboard navigation should prevent default arrow handling");
  assert(sourceEvidenceTab.attributes["aria-selected"] === "true", "ArrowRight should select next governance tab");
  assert(sourceEvidenceTab.attributes.tabindex === "0", "selected governance tab should be tabbable");
  assert(document.activeElement === sourceEvidenceTab, "keyboard navigation should move focus");
}

function testGovernanceFailureClearsStalePanel() {
  setupSidecar();
  const stale = new Element("", "div");
  stale.textContent = "prior authorized governance detail";
  document.getElementById("governance-detail").replaceChildren(stale);

  window.sidecarContract.renderGovernanceFailureForTest({
    error: {
      details: {
        request_id: "req-governance",
        trace_id: "trace-governance",
        failure_stage: "permission",
        error_code: "ACCESS_DENIED",
        chunk_content: "must not render",
      },
    },
  });

  const rendered = document
    .getElementById("governance-detail")
    .children.flatMap((row) => row.children.map((child) => child.textContent))
    .join(" ");
  assert(rendered.includes("req-governance"), "safe governance failure should render request_id");
  assert(rendered.includes("ACCESS_DENIED"), "safe governance failure should render error_code");
  assert(rendered.includes("next_step"), "safe governance failure should render next step guidance");
  assert(!rendered.includes("prior authorized"), "failure should clear stale governance detail");
  assert(!rendered.includes("must not render"), "failure must not render forbidden details");

  const alert = document.getElementById("alert-region");
  assert(alert.hidden === false, "governance failure should show alert");
  document.governanceTabs.find((tab) => tab.dataset.governanceView === "review-queue").click();
  assert(alert.hidden === true, "governance tab switch should clear stale alert");
}

function testDocumentReviewRendersSafeList() {
  setupSidecar();
  window.sidecarContract.renderDocumentReviewListForTest({
    items: [
      {
        document_id: "doc-1",
        version_id: "ver-1",
        source_display_name: "Policy",
        source_type: "txt",
        status: "retrieval_ready",
        created_by: "user-1",
        chunk_count: 2,
        request_id: "req-review",
        trace_id: "trace-review",
        source_uri: "file:///secret",
        object_key: "raw/tenant/doc/ver/file.txt",
        chunk_content: "must not render",
      },
    ],
    next_cursor: "1",
  });

  const rendered = document
    .getElementById("document-review-list")
    .children.flatMap((row) => row.children.map((child) => child.textContent))
    .join(" ");
  assert(rendered.includes("doc-1"), "document review list should render document_id");
  assert(rendered.includes("Policy"), "document review list should render safe display name");
  assert(rendered.includes("next_cursor"), "document review list should render cursor");
  assert(!rendered.includes("file:///secret"), "document review list must not render source_uri");
  assert(!rendered.includes("raw/tenant"), "document review list must not render object_key");
  assert(!rendered.includes("must not render"), "document review list must not render chunk content");
}

function testDocumentReviewFailureClearsStaleRegions() {
  setupSidecar();
  ["document-review-list", "document-review-detail", "document-review-timeline"].forEach((id) => {
    const stale = new Element("", "div");
    stale.textContent = "prior authorized document data";
    document.getElementById(id).replaceChildren(stale);
  });

  window.sidecarContract.renderDocumentReviewFailureForTest({
    error: {
      code: "DOCUMENT_MANAGE_FORBIDDEN",
      details: {
        request_id: "req-review",
        trace_id: "trace-review",
        failure_stage: "permission",
        error_code: "DOCUMENT_MANAGE_FORBIDDEN",
        source_uri: "file:///secret",
      },
    },
  });

  const rendered = document
    .getElementById("document-review-detail")
    .children.flatMap((row) => row.children.map((child) => child.textContent))
    .join(" ");
  assert(rendered.includes("req-review"), "document review failure should render request_id");
  assert(rendered.includes("DOCUMENT_MANAGE_FORBIDDEN"), "document review failure should render safe error_code");
  assert(!rendered.includes("prior authorized"), "document review failure should clear stale detail");
  assert(!rendered.includes("file:///secret"), "document review failure must not render source_uri");
  assert(document.getElementById("document-review-list").children.length === 0, "failure clears stale list");
  assert(document.getElementById("document-review-timeline").children.length === 0, "failure clears stale timeline");
}

async function testDocumentReviewMissingDocumentIdClearsStaleRegions() {
  setupSidecar();
  ["document-review-list", "document-review-detail", "document-review-timeline"].forEach((id) => {
    const stale = new Element("", "div");
    stale.textContent = "prior authorized document data";
    document.getElementById(id).replaceChildren(stale);
  });

  await window.sidecarContract.fetchDocumentReviewDetailForTest("", null);

  assert(document.getElementById("document-review-list").children.length === 0, "missing id clears stale list");
  assert(document.getElementById("document-review-detail").children.length === 0, "missing id clears stale detail");
  assert(document.getElementById("document-review-timeline").children.length === 0, "missing id clears stale timeline");
  assert(document.getElementById("alert-region").hidden === false, "missing id should show alert");
}

function testDocumentReviewEmptyListClearsCursorAndShowsEmptyState() {
  setupSidecar();
  document.getElementById("document-review-cursor").value = "20";

  window.sidecarContract.renderDocumentReviewListForTest({
    items: [],
    next_cursor: null,
  });

  const rendered = document
    .getElementById("document-review-list")
    .children.flatMap((row) => row.children.map((child) => child.textContent))
    .join(" ");
  assert(rendered.includes("No documents found"), "empty document review list should render an empty state");
  assert(document.getElementById("document-review-cursor").value === "", "empty last page should clear stale cursor");
}

function testDocumentReviewUnknownStatusIsSafe() {
  setupSidecar();
  window.sidecarContract.renderDocumentReviewDetailForTest({
    document_id: "doc-1",
    version_id: "ver-1",
    status: "vendor_custom_state",
    lifecycle: [
      {
        status: "unknown",
        label: "Unknown status",
        description: "Backend returned unrecognized status: vendor_custom_state",
        tone: "unknown",
        is_current: true,
        is_known: false,
      },
    ],
  });

  const timeline = document.getElementById("document-review-timeline");
  const chip = timeline.children[0].children[1];
  const rendered = timeline.children[0].children.map((child) => child.textContent).join(" ");
  assert(chip.dataset.tone === "unknown", "unknown review lifecycle status should use unknown tone");
  assert(rendered.includes("Current"), "timeline should include non-color current state text");
}

function testSourceEvidenceParsesCitationsSafely() {
  setupSidecar();
  const parsed = window.sidecarContract.parseSourceEvidenceInputForTest({
    raw: JSON.stringify({
      citations: [
        {
          document_id: "doc-1",
          version_id: "ver-1",
          chunk_id: "chunk-1",
          page_start: 2,
          page_end: 3,
          request_id: "req-1",
          text_excerpt: "must not trust",
          source_uri: "file:///secret",
          score: 0.99,
        },
        {
          document_id: "doc-1",
          version_id: "ver-1",
          chunk_id: "chunk-1",
          page_start: 2,
          page_end: 3,
          request_id: "req-1",
        },
      ],
      metadata: {
        citations: [
          {
            document_id: "doc-2",
            version_id: "ver-2",
            chunk_id: "chunk-2",
            citation_ref: "2",
          },
        ],
      },
      source_evidence_link:
        "/sidecar?document_id=doc-3&version_id=ver-3&chunk_id=chunk-3#request_id=req-3",
    }),
    manual: {},
  });

  assert(parsed.errors.length === 0, "valid citations should parse without errors");
  assert(parsed.references.length === 3, "duplicate citations should be deduplicated and links accepted");
  assert(parsed.references[0].document_id === "doc-1", "document_id should be preserved");
  assert(parsed.references[0].page_start === 2, "page_start should be parsed");
  assert(!Object.prototype.hasOwnProperty.call(parsed.references[0], "text_excerpt"), "pasted excerpt must not be trusted");
  assert(!Object.prototype.hasOwnProperty.call(parsed.references[0], "source_uri"), "pasted locator must not be trusted");
  assert(!Object.prototype.hasOwnProperty.call(parsed.references[0], "score"), "pasted score must not be trusted");
  assert(parsed.references[2].request_id === "req-3", "source evidence link hash should be parsed");

  const link = window.sidecarContract.parseSourceEvidenceInputForTest({
    raw: "/sidecar?document_id=doc-4&version_id=ver-4&chunk_id=chunk-4#request_id=req-4",
    manual: {},
  });
  assert(link.errors.length === 0, "direct sidecar links should parse without JSON wrapping");
  assert(link.references[0].document_id === "doc-4", "direct sidecar links should preserve document_id");

  const invalid = window.sidecarContract.parseSourceEvidenceInputForTest({
    raw: JSON.stringify([{ document_id: "doc", version_id: "ver", chunk_id: "chunk", page_start: 5 }]),
    manual: {},
  });
  assert(invalid.errors.some((message) => message.includes("page range")), "partial page range should be rejected");

  const tooMany = Array.from({ length: 21 }, (_, index) => ({
    document_id: `doc-${index}`,
    version_id: "ver",
    chunk_id: "chunk",
  }));
  const limited = window.sidecarContract.parseSourceEvidenceInputForTest({
    raw: JSON.stringify(tooMany),
    manual: {},
  });
  assert(limited.errors.some((message) => message.includes("20")), "batch limit should be enforced");
}

async function testSourceEvidenceResolvesEachReference() {
  setupSidecar();
  let calls = [];
  global.fetch = async (url, options) => {
    calls.push({ url, options });
    const payload = JSON.parse(options.body);
    return {
      ok: true,
      json: async () => ({
        data: {
          authorization_status: "authorized",
          document_id: payload.document_id,
          version_id: payload.version_id,
          chunk_id: payload.chunk_id,
          source_display_name: "Policy",
          source_type: "markdown",
          page_start: payload.page_start,
          page_end: payload.page_end,
          title_path: ["Policy", "Section"],
          text_excerpt: "Authorized short excerpt.",
          excerpt_char_count: 25,
          token_count: 5,
          retrieval_method: "hybrid",
          score: 0.87,
          request_id: "req-resolve",
          trace_id: "trace-resolve",
          source_uri: "file:///secret",
          metadata: "file:///secret",
        },
      }),
    };
  };

  await window.sidecarContract.resolveSourceEvidenceSetForTest([
    {
      document_id: "doc-1",
      version_id: "ver-1",
      chunk_id: "chunk-1",
      page_start: 1,
      page_end: 1,
      request_id: "req-1",
      trace_id: "trace-1",
      citation_ref: "1",
    },
    { document_id: "doc-2", version_id: "ver-2", chunk_id: "chunk-2" },
  ]);

  assert(calls.length === 2, "each evidence reference should call source resolve");
  assert(calls[0].url === "/sources/resolve", "source evidence should use source resolve endpoint");
  const firstPayload = JSON.parse(calls[0].options.body);
  assert(firstPayload.document_id === "doc-1", "payload should include document_id");
  assert(firstPayload.page_start === 1, "payload should include valid page_start");
  assert(firstPayload.citation_ref === "1", "payload should include citation_ref");
  assert(!Object.prototype.hasOwnProperty.call(firstPayload, "trace_id"), "trace_id must not be sent to source resolve body");
  assert(calls[0].options.headers["X-Request-ID"] !== "req-1", "pasted citation request_id must not become the current request header");
  const rendered = document
    .getElementById("source-evidence-results")
    .children.map((item) => nodeText(item))
    .join(" ");
  assert(rendered.includes("authorized"), "authorized evidence should render status");
  assert(rendered.includes("Authorized short excerpt."), "authorized evidence should render backend excerpt");
  assert(!rendered.includes("file:///secret"), "rendered evidence must not include forbidden locator");
}

async function testSourceEvidenceClearsStaleResultsBeforeResolveCompletes() {
  setupSidecar();
  window.sidecarContract.renderSourceEvidenceSetForTest([
    {
      status: "authorized",
      data: {
        authorization_status: "authorized",
        document_id: "doc-old",
        version_id: "ver-old",
        chunk_id: "chunk-old",
        text_excerpt: "prior authorized evidence",
      },
    },
  ]);
  let releaseResolve;
  global.fetch = async () =>
    new Promise((resolve) => {
      releaseResolve = () =>
        resolve({
          ok: true,
          json: async () => ({
            data: {
              document_id: "doc-new",
              version_id: "ver-new",
              chunk_id: "chunk-new",
              source_display_name: "Policy",
              source_type: "markdown",
              title_path: [],
              text_excerpt: "new authorized evidence",
              excerpt_char_count: 23,
              token_count: 4,
              request_id: "req-new",
              trace_id: "trace-new",
            },
          }),
        });
    });

  const pending = window.sidecarContract.resolveSourceEvidenceSetForTest([
    { document_id: "doc-new", version_id: "ver-new", chunk_id: "chunk-new" },
  ]);
  const interim = document
    .getElementById("source-evidence-results")
    .children.map((item) => nodeText(item))
    .join(" ");
  window.sidecarContract.copySourceEvidenceSummaryForTest();
  const copied = navigator.clipboard.writes[navigator.clipboard.writes.length - 1] || "";
  assert(!interim.includes("prior authorized evidence"), "new resolve should clear stale evidence immediately");
  assert(!copied.includes("doc-old"), "new resolve should clear stale copy summary immediately");
  releaseResolve();
  await pending;
}

function testSourceEvidenceDenialClearsStaleItem() {
  setupSidecar();
  window.sidecarContract.renderSourceEvidenceSetForTest([
    {
      status: "authorized",
      data: {
        authorization_status: "authorized",
        document_id: "doc-old",
        version_id: "ver-old",
        chunk_id: "chunk-old",
        text_excerpt: "prior authorized excerpt",
        retrieval_method: "hybrid",
        score: 0.9,
      },
    },
  ]);

  window.sidecarContract.renderSourceEvidenceSetForTest([
    {
      status: "failed",
      error: {
        request_id: "req-denied",
        trace_id: "trace-denied",
        failure_stage: "source_resolve",
        error_code: "SOURCE_ACCESS_DENIED",
        document_id: "doc-secret",
        text_excerpt: "must not render",
      },
    },
  ]);

  const rendered = document
    .getElementById("source-evidence-results")
    .children.map((item) => nodeText(item))
    .join(" ");
  assert(rendered.includes("safe_failure"), "denial should render a uniform safe failure");
  assert(rendered.includes("req-denied"), "denial should keep request_id");
  assert(!rendered.includes("prior authorized excerpt"), "denial should clear stale excerpt");
  assert(!rendered.includes("doc-secret"), "denial should not reveal target identifiers from error details");
  assert(!rendered.includes("must not render"), "denial should not render unsafe error fields");
}

async function testSourceEvidenceMalformedInputClearsResults() {
  setupSidecar();
  const stale = new Element("", "div");
  stale.textContent = "prior authorized evidence";
  document.getElementById("source-evidence-results").replaceChildren(stale);
  document.getElementById("source-evidence-json").value = "{not-json";

  await document.getElementById("source-evidence-form").eventListeners.submit[0]({
    preventDefault: () => undefined,
  });

  const rendered = document
    .getElementById("source-evidence-results")
    .children.map((child) => child.textContent)
    .join(" ");
  assert(!rendered.includes("prior authorized evidence"), "malformed input should clear stale evidence");
  assert(document.getElementById("source-evidence-errors").children.length > 0, "malformed input should render safe errors");
}

function testSourceEvidenceCopySummaryUsesAllowlist() {
  setupSidecar();
  window.sidecarContract.renderSourceEvidenceSetForTest([
    {
      status: "authorized",
      data: {
        authorization_status: "authorized",
        document_id: "doc-1",
        version_id: "ver-1",
        chunk_id: "chunk-1",
        source_display_name: "Policy",
        text_excerpt: "Authorized short excerpt must not be copied wholesale.",
        retrieval_method: "hybrid",
        score: 0.9,
        request_id: "req-1",
        trace_id: "trace-1",
        provider_payload: "must not copy",
      },
    },
  ]);

  window.sidecarContract.copySourceEvidenceSummaryForTest();

  const copied = navigator.clipboard.writes[navigator.clipboard.writes.length - 1];
  assert(copied.includes("doc-1"), "summary should include safe identifiers");
  assert(copied.includes("hybrid"), "summary should include retrieval method");
  assert(!copied.includes("Authorized short excerpt"), "summary should not copy excerpt text");
  assert(!copied.includes("provider_payload"), "summary should not include forbidden field names");
  assert(!copied.includes("must not copy"), "summary should not include forbidden values");
}

async function testEvalEvidenceReportListRendering() {
  setupSidecar();
  document.getElementById("eval-evidence-limit").value = "3";
  let request = null;
  global.fetch = async (url, options) => {
    request = { url, options };
    return {
      ok: true,
      json: async () => ({
        data: {
          items: [
            {
              report_filename: "rag-smoke-20260609T100000Z-safe.json",
              generated_at: "2026-06-09T10:00:00Z",
              report_type: "rag_quality_runner",
              dataset_version: "rag-smoke-v1",
              case_count: 2,
              passed_count: 1,
              failed_count: 1,
              retrieval_hit_rate: 0.5,
              citation_coverage: 0.5,
              no_answer_correctness: 1,
              average_latency_ms: 12.5,
              decision: "failed",
              source_uri: "file:///secret",
              query: "must not render",
            },
          ],
          next_steps: ["python -m pytest tests/eval -q"],
        },
      }),
    };
  };

  await window.sidecarContract.fetchEvalEvidenceReportsForTest();

  assert(request.url === "/eval/reports?limit=3", "eval list should call backend reports endpoint");
  assert(request.options.headers?.["X-Request-ID"] === undefined, "eval list should not invent request headers");
  const rendered = nodeText(document.getElementById("eval-evidence-report-list"));
  assert(rendered.includes("rag-smoke-20260609T100000Z-safe.json"), "report filename should render");
  assert(rendered.includes("failed_count"), "safe count should render");
  assert(!rendered.includes("file:///secret"), "report list must not render source_uri");
  assert(!rendered.includes("must not render"), "report list must not render raw query");
  assert(
    document.getElementById("eval-evidence-report").value === "rag-smoke-20260609T100000Z-safe.json",
    "first report should populate safe selector value",
  );
}

function testEvalEvidenceDetailRenderingUsesAllowlists() {
  setupSidecar();
  window.sidecarContract.renderEvalEvidenceDetailForTest({
    summary: {
      report_filename: "rag-smoke-20260609T100000Z-safe.json",
      report_type: "rag_quality_runner",
      case_count: 2,
      failed_count: 1,
      citation_coverage: 0.5,
      decision: "failed",
      source_uri: "file:///secret",
    },
    failed_cases: [
      {
        case_id: "case-failed",
        failure_stage: "citation",
        matched_documents: ["doc-1"],
        matched_chunks: ["chunk-1"],
        matched_citations: ["doc-1:v1:chunk-1"],
        retrieval_result_count: 1,
        context_item_count: 1,
        citation_count: 0,
        unsupported_count: 0,
        forged_reference_count: 1,
        prompt_risk_count: 0,
        request_id: "req-case",
        trace_id: "trace-case",
        top_k: 5,
        latency_ms: 20,
        generation: {
          provider: "fake",
          model: "fake-llm",
          version: "fake-v1",
          token_usage: { input_tokens: 9, output_tokens: 3, total_tokens: 12 },
          provider_raw_response: "must not render",
        },
        query: "must not render",
        answer: "must not render",
      },
    ],
    gate_metrics: [{ metric: "citation_coverage", threshold_name: "min", passed: false, expected: 0.9, actual: 0.5 }],
    next_steps: ["python -m pytest tests/eval -q"],
  });

  const rendered = [
    nodeText(document.getElementById("eval-evidence-summary")),
    nodeText(document.getElementById("eval-evidence-cases")),
    nodeText(document.getElementById("eval-evidence-next-steps")),
  ].join(" ");
  assert(rendered.includes("case-failed"), "failed case id should render");
  assert(rendered.includes("token_usage"), "safe token usage summary should render");
  assert(rendered.includes("citation_coverage"), "gate metric should render");
  assert(!rendered.includes("file:///secret"), "detail must not render source_uri");
  assert(!rendered.includes("must not render"), "detail must not render raw fields");
}

async function testEvalEvidencePermissionFailureClearsStaleState() {
  setupSidecar();
  window.sidecarContract.renderEvalEvidenceDetailForTest({
    summary: { report_filename: "old.json", report_type: "rag_quality_runner", case_count: 1 },
    failed_cases: [{ case_id: "old-case", failure_stage: "citation" }],
    next_steps: ["python old.py"],
  });
  global.fetch = async () => ({
    ok: false,
    json: async () => ({
      request_id: "req-denied",
      error: {
        code: "EVAL_EVIDENCE_FORBIDDEN",
        details: {
          request_id: "req-denied",
          trace_id: "trace-denied",
          failure_stage: "permission",
          error_code: "EVAL_EVIDENCE_FORBIDDEN",
          raw_exception: "must not render",
        },
      },
    }),
  });
  document.getElementById("eval-evidence-report").value = "rag-smoke-20260609T100000Z-safe.json";

  await window.sidecarContract.fetchEvalEvidenceDetailForTest();
  document.getElementById("copy-eval-evidence-report").click();

  const rendered = [
    nodeText(document.getElementById("eval-evidence-summary")),
    nodeText(document.getElementById("eval-evidence-cases")),
    nodeText(document.getElementById("eval-evidence-next-steps")),
  ].join(" ");
  const copied = navigator.clipboard.writes[navigator.clipboard.writes.length - 1] || "";
  assert(rendered.includes("req-denied"), "failure should render safe request_id");
  assert(!rendered.includes("old-case"), "failure should clear stale case detail");
  assert(!rendered.includes("must not render"), "failure must not render raw exception");
  assert(!copied.includes("old-case"), "failure should clear stale export state");
}

function testEvalEvidenceReportExportUsesAllowlist() {
  setupSidecar();
  window.sidecarContract.renderEvalEvidenceDetailForTest({
    summary: {
      report_filename: "../unsafe report.json",
      report_type: "rag_quality_runner",
      case_count: 1,
      failed_count: 1,
      source_uri: "file:///secret",
    },
    failed_cases: [
      {
        case_id: "case-failed",
        failure_stage: "citation",
        request_id: "req-case",
        query: "must not export",
        prompt: "must not export",
      },
    ],
    next_steps: ["python -m pytest tests/eval -q"],
    raw_exception: "must not export",
  });

  document.getElementById("copy-eval-evidence-report").click();
  const copied = navigator.clipboard.writes[navigator.clipboard.writes.length - 1];
  assert(copied.includes("case-failed"), "eval export should include safe failed case id");
  assert(!copied.includes("source_uri"), "eval export must not include source_uri");
  assert(!copied.includes("must not export"), "eval export must not include raw values");

  document.getElementById("download-eval-evidence-report").click();
  assert(document.lastClickedLink.download.includes("unsafe-report-json"), "download filename should sanitize report filename");
  assert(!document.lastClickedLink.download.includes(".."), "download filename must not include traversal");
  assert(!document.lastClickedLink.download.includes("/"), "download filename must not include path separators");
  assert(!document.lastClickedLink.download.includes("\\"), "download filename must not include Windows path separators");
  assert(!document.lastClickedLink.download.includes(":"), "download filename must not include drive separators");
}

async function testEvalEvidenceReportListRefreshReplacesStaleFilename() {
  setupSidecar();
  document.getElementById("eval-evidence-report").value = "old-report.json";
  global.fetch = async () => ({
    ok: true,
    json: async () => ({
      data: {
        items: [
          {
            report_filename: "new-report.json",
            report_type: "rag_quality_runner",
            case_count: 1,
            failed_count: 0,
            decision: "passed",
          },
        ],
        next_steps: [],
      },
    }),
  });

  await window.sidecarContract.fetchEvalEvidenceReportsForTest();

  assert(
    document.getElementById("eval-evidence-report").value === "new-report.json",
    "refresh should replace a stale selected filename with the first returned report",
  );
}

async function testEvalEvidenceDetailIgnoresOlderOverlappingResponse() {
  setupSidecar();
  const first = {};
  const second = {};
  first.promise = new Promise((resolve) => {
    first.resolve = resolve;
  });
  second.promise = new Promise((resolve) => {
    second.resolve = resolve;
  });
  let callCount = 0;
  global.fetch = async () => {
    callCount += 1;
    const callIndex = callCount;
    const deferred = callIndex === 1 ? first : second;
    await deferred.promise;
    return {
      ok: true,
      json: async () => ({
        data: {
          summary: {
            report_filename: callIndex === 1 ? "old-report.json" : "new-report.json",
            report_type: "rag_quality_runner",
            case_count: 1,
            failed_count: callIndex === 1 ? 1 : 0,
            decision: callIndex === 1 ? "failed" : "passed",
          },
          failed_cases: callIndex === 1 ? [{ case_id: "old-case" }] : [{ case_id: "new-case" }],
          next_steps: [],
        },
      }),
    };
  };

  document.getElementById("eval-evidence-report").value = "old-report.json";
  const oldLookup = window.sidecarContract.fetchEvalEvidenceDetailForTest();
  document.getElementById("eval-evidence-report").value = "new-report.json";
  const newLookup = window.sidecarContract.fetchEvalEvidenceDetailForTest();
  second.resolve();
  await newLookup;
  first.resolve();
  await oldLookup;

  const rendered = [
    nodeText(document.getElementById("eval-evidence-summary")),
    nodeText(document.getElementById("eval-evidence-cases")),
  ].join(" ");
  assert(rendered.includes("new-report.json"), "newer eval detail should render");
  assert(rendered.includes("new-case"), "newer failed case should render");
  assert(!rendered.includes("old-report.json"), "older eval detail response must not overwrite newer summary");
  assert(!rendered.includes("old-case"), "older eval detail response must not overwrite newer cases");
}

function testEvalEvidenceTabSwitchDoesNotAutoLookup() {
  setupSidecar();
  let calls = 0;
  global.fetch = async () => {
    calls += 1;
    return { ok: true, json: async () => ({ data: {} }) };
  };

  document.governanceTabs.find((tab) => tab.dataset.governanceView === "eval-evidence").click();

  assert(calls === 0, "eval evidence tab switch must not auto-fetch reports");
  assert(document.getElementById("eval-evidence-summary").children.length === 0, "tab switch clears stale summary");
}

async function testAuditExplorerListRenderingUsesAllowlists() {
  setupSidecar();
  document.getElementById("audit-explorer-request").value = "req-audit";
  document.getElementById("audit-explorer-trace").value = "trace-audit";
  document.getElementById("audit-explorer-limit").value = "5";
  let request = null;
  global.fetch = async (url, options) => {
    request = { url, options };
    return {
      ok: true,
      json: async () => ({
        data: {
          items: [
            {
              id: "audit-1",
              tenant_id: "tenant-1",
              user_id: "user-1",
              request_id: "req-audit",
              trace_id: "trace-audit",
              action: "agent.tool.execute",
              resource_type: "tool_call",
              resource_id: "tool-1",
              status: "success",
              latency_ms: 12,
              safe_counts: { citation_count: 2, source_uri: "file:///secret" },
              association: {
                agent_run_id: "run-1",
                tool_name: "rag_search",
                permission: "agent:tool:rag_search",
                arguments_summary: { argument_keys: ["query"], query: "must not render" },
                result_summary: { result_count: 1, raw_output: "must not render" },
              },
              prompt: "must not render",
            },
          ],
          next_steps: ["python -m pytest tests/unit/audit_explorer -q"],
        },
      }),
    };
  };

  await window.sidecarContract.fetchAuditExplorerLogsForTest();

  assert(request.url.includes("/audit/logs?"), "audit list should call logs endpoint");
  assert(request.url.includes("request_id=req-audit"), "audit query should include request_id");
  assert(!request.url.includes("tenant_id"), "audit query must not send tenant_id");
  const rendered = [
    nodeText(document.getElementById("audit-explorer-results")),
    nodeText(document.getElementById("audit-explorer-detail")),
    nodeText(document.getElementById("audit-explorer-next-steps")),
  ].join(" ");
  assert(rendered.includes("agent.tool.execute"), "audit list should render action");
  assert(rendered.includes("run-1"), "audit association should render agent_run_id");
  assert(rendered.includes("citation_count"), "audit list should render safe counts");
  assert(!rendered.includes("file:///secret"), "audit list must not render unsafe nested values");
  assert(!rendered.includes("must not render"), "audit list must not render raw fields");
}

async function testAuditExplorerPermissionFailureClearsStaleState() {
  setupSidecar();
  window.sidecarContract.renderAuditExplorerListForTest({
    items: [
      {
        request_id: "req-old",
        trace_id: "trace-old",
        action: "rag.query",
        resource_type: "rag_query",
        resource_id: "req-old",
        status: "success",
      },
    ],
  });
  global.fetch = async () => ({
    ok: false,
    json: async () => ({
      error: {
        code: "AUDIT_EXPLORER_FORBIDDEN",
        details: {
          request_id: "req-denied",
          trace_id: "trace-denied",
          stage: "permission",
          raw_exception: "must not render",
        },
      },
    }),
  });
  document.getElementById("audit-explorer-request").value = "req-denied";

  await window.sidecarContract.fetchAuditExplorerLogsForTest();
  document.getElementById("audit-explorer-copy-export").click();

  const rendered = [
    nodeText(document.getElementById("audit-explorer-results")),
    nodeText(document.getElementById("audit-explorer-detail")),
  ].join(" ");
  const copied = navigator.clipboard.writes[navigator.clipboard.writes.length - 1] || "";
  assert(rendered.includes("req-denied"), "failure should render safe request_id");
  assert(rendered.includes("permission"), "failure should render safe stage");
  assert(!rendered.includes("req-old"), "failure should clear stale audit rows");
  assert(!rendered.includes("must not render"), "failure must not render raw exception");
  assert(!copied.includes("req-old"), "failure should clear stale export state");
}

async function testAuditExplorerBackendExportUsesAllowlist() {
  setupSidecar();
  document.getElementById("audit-explorer-request").value = "../unsafe req";
  let request = null;
  global.fetch = async (url, options) => {
    request = { url, options };
    return {
      ok: true,
      json: async () => ({
        data: {
          export_id: "../audit export",
          generated_at: "2026-06-09T10:00:00Z",
          filter_summary: { request_id: "../unsafe req", tenant_id: "must not export" },
          fields: ["id", "request_id", "prompt"],
          item_count: 1,
          request_ids: ["req-export"],
          trace_ids: ["trace-export"],
          items: [
            {
              id: "audit-1",
              request_id: "req-export",
              trace_id: "trace-export",
              action: "rag.query",
              resource_type: "rag_query",
              resource_id: "req-export",
              status: "success",
              safe_summary: { citation_count: 1, source_uri: "file:///secret" },
              prompt: "must not export",
            },
          ],
          raw_metadata: "must not export",
        },
      }),
    };
  };

  await window.sidecarContract.copyAuditExplorerExportForTest();

  assert(request.url === "/audit/export", "audit export should call backend export endpoint");
  assert(request.options.method === "POST", "audit export should use POST");
  const payload = JSON.parse(request.options.body);
  assert(payload.request_id === "../unsafe req", "export body should include lookup request_id");
  assert(!Object.prototype.hasOwnProperty.call(payload, "tenant_id"), "export body must not send tenant_id");
  const copied = navigator.clipboard.writes[navigator.clipboard.writes.length - 1];
  assert(copied.includes("req-export"), "audit export should include safe request id");
  assert(!copied.includes("must not export"), "audit export copy must not include raw values");
  assert(!copied.includes("source_uri"), "audit export copy must not include unsafe nested keys");
  assert(!copied.includes('"prompt"'), "audit export copy must not include unsafe field names");

  await window.sidecarContract.downloadAuditExplorerExportForTest();
  assert(document.lastClickedLink.download.includes("audit-export"), "download filename should sanitize export id");
  assert(!document.lastClickedLink.download.includes(".."), "download filename must not include traversal");
  assert(!document.lastClickedLink.download.includes("/"), "download filename must not include separators");
  assert(!document.lastClickedLink.download.includes("\\"), "download filename must not include Windows separators");
  assert(!document.lastClickedLink.download.includes(":"), "download filename must not include drive separators");
}

function testAuditExplorerTabSwitchDoesNotAutoLookup() {
  setupSidecar();
  let calls = 0;
  global.fetch = async () => {
    calls += 1;
    return { ok: true, json: async () => ({ data: {} }) };
  };

  document.governanceTabs.find((tab) => tab.dataset.governanceView === "audit-explorer").click();

  assert(calls === 0, "audit explorer tab switch must not auto-fetch logs");
  assert(document.getElementById("audit-explorer-results").children.length === 0, "tab switch clears audit results");
}

const tests = {
  testSafeFailureClearsStaleSourceResults,
  testSafeFailureDoesNotInventTraceIdFromRequestId,
  testStatusFailureCopyButtonKeepsHandler,
  testInvalidPageInputDoesNotSendNullPageBounds,
  testDialogTrapKeepsTabFocusInsideInspector,
  testUnknownStatusIsNotRenderedAsWorking,
  testClipboardFallbackReportsUnavailableCopy,
  testDiagnosticsLookupUsesSafePayload,
  testDiagnosticsFailureRendersOnlySafeDetails,
  testDiagnosticsReportExportUsesAllowlist,
  testDiagnosticsNextStepsClearsStaleCommands,
  testSyncDiagnosticsDoesNotAutoLookup,
  testGovernanceDiagnosticsLookupRendersTimeline,
  testGovernanceDiagnosticsPermissionFailureClearsStaleState,
  testGovernanceDiagnosticsTabClearsBackendReportState,
  testGovernanceDiagnosticsFailureClearsBackendDiagnosticsDom,
  testGovernanceDiagnosticsNewLookupClearsReportCopyExport,
  testGovernanceNavigationSwitchesViews,
  testGovernanceLinksBackendViews,
  testGovernanceKeyboardTabs,
  testGovernanceFailureClearsStalePanel,
  testDocumentReviewRendersSafeList,
  testDocumentReviewFailureClearsStaleRegions,
  testDocumentReviewMissingDocumentIdClearsStaleRegions,
  testDocumentReviewEmptyListClearsCursorAndShowsEmptyState,
  testDocumentReviewUnknownStatusIsSafe,
  testSourceEvidenceParsesCitationsSafely,
  testSourceEvidenceResolvesEachReference,
  testSourceEvidenceClearsStaleResultsBeforeResolveCompletes,
  testSourceEvidenceDenialClearsStaleItem,
  testSourceEvidenceMalformedInputClearsResults,
  testSourceEvidenceCopySummaryUsesAllowlist,
  testEvalEvidenceReportListRendering,
  testEvalEvidenceDetailRenderingUsesAllowlists,
  testEvalEvidencePermissionFailureClearsStaleState,
  testEvalEvidenceReportExportUsesAllowlist,
  testEvalEvidenceReportListRefreshReplacesStaleFilename,
  testEvalEvidenceDetailIgnoresOlderOverlappingResponse,
  testEvalEvidenceTabSwitchDoesNotAutoLookup,
  testAuditExplorerListRenderingUsesAllowlists,
  testAuditExplorerPermissionFailureClearsStaleState,
  testAuditExplorerBackendExportUsesAllowlist,
  testAuditExplorerTabSwitchDoesNotAutoLookup,
};

(async () => {
  const selected = process.argv[2];
  const names = selected ? [selected] : Object.keys(tests);
  for (const name of names) {
    if (!tests[name]) {
      throw new Error(`Unknown test ${name}`);
    }
    await tests[name]();
  }
})();
