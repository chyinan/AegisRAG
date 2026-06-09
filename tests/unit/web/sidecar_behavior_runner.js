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
    "inspector-sheet",
    "inspector-title",
    "diagnostic-request",
    "diagnostic-trace",
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
  document.elements["close-inspector"].tagName = "BUTTON";
  document.elements["copy-diagnostics"].tagName = "BUTTON";
  document.elements["copy-diagnostics-report"].tagName = "BUTTON";
  document.elements["download-diagnostics-report"].tagName = "BUTTON";
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

  const script = fs.readFileSync("apps/web/sidecar/sidecar.js", "utf8");
  vm.runInThisContext(script);
  document.dispatch("DOMContentLoaded");
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
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
  assert(statusTab.attributes["aria-selected"] === "true", "document review should activate status lookup");
  assert(sourceTab.attributes["aria-selected"] === "false", "source lookup should no longer be active");

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
  testGovernanceNavigationSwitchesViews,
  testGovernanceLinksBackendViews,
  testGovernanceKeyboardTabs,
  testGovernanceFailureClearsStalePanel,
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
