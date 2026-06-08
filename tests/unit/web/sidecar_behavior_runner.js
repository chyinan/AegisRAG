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
    "inspector-sheet",
    "inspector-title",
    "diagnostic-request",
    "diagnostic-trace",
    "auth-token",
    "alert-region",
    "live-region",
  ];
  ids.forEach((id) => document.add(id));
  document.elements["source-form"].tagName = "FORM";
  document.elements["status-form"].tagName = "FORM";
  document.elements["close-inspector"].tagName = "BUTTON";
  document.elements["copy-diagnostics"].tagName = "BUTTON";
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
  assert(result.children.length === 0, "source failure should clear stale source rows");
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

const tests = {
  testSafeFailureClearsStaleSourceResults,
  testSafeFailureDoesNotInventTraceIdFromRequestId,
  testStatusFailureCopyButtonKeepsHandler,
  testInvalidPageInputDoesNotSendNullPageBounds,
  testDialogTrapKeepsTabFocusInsideInspector,
  testUnknownStatusIsNotRenderedAsWorking,
  testClipboardFallbackReportsUnavailableCopy,
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
