import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { Readable } from "node:stream";

import process from "node:process";

/** Only synchronous execution paths bypass the ordinary backend rewrite. */
export const EXECUTION_PROXY_BUDGET_MS = Object.freeze({
  requestBody: 10_000,
  stateRead: 10_000,
  background: 30_000,
  agent: 135_000,
  folder: 315_000,
  maximumInline: 3_615_000
});
export const EXECUTION_PROXY_MAX_REQUEST_BYTES = 262_144;
export const EXECUTION_PROXY_MAX_RESPONSE_BYTES = 8_388_608;
const MAX_STATE_BYTES = 262_144;
const OPAQUE_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const REQUEST_HEADERS = [
  "authorization", "content-type", "accept", "x-request-id", "x-trace-id", "traceparent", "tracestate",
  // Match authHeaders' existing development/test transport. The API, not this
  // transport, decides whether these headers are an enabled authentication mode.
  "x-user-id", "x-tenant-id", "x-roles", "x-department", "x-permissions"
];
const RESPONSE_HEADERS = ["content-type", "content-encoding", "x-accel-buffering", "x-request-id", "x-trace-id", "traceparent", "tracestate", "retry-after", "www-authenticate"];

type Operation = "agent" | "start" | "resume" | "folder";
type Fetch = typeof fetch;
type Options = { fetch?: Fetch; apiBaseUrl?: string };
type EndReason = "timeout" | "aborted" | "upstream";

class ProxyFailure extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string) { super(code); this.status = status; this.code = code; }
}

/** A single deadline/cancellation owner, retained until the body actually ends. */
function lifecycle(incoming: AbortSignal) {
  const controller = new AbortController();
  let reason: EndReason | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let rejectAbort: (error: Error) => void = () => undefined;
  const aborted = new Promise<never>((_resolve, reject) => { rejectAbort = reject; });
  // It may be triggered between two reads. Keep rejection handled in that gap.
  void aborted.catch(() => undefined);
  const abort = (why: EndReason) => {
    if (controller.signal.aborted) return;
    reason = why;
    const error = new DOMException("Execution proxy request ended.", "AbortError");
    controller.abort(error);
    rejectAbort(error);
  };
  const onAbort = () => abort("aborted");
  incoming.addEventListener("abort", onAbort, { once: true });
  if (incoming.aborted) onAbort();
  return {
    signal: controller.signal,
    abort,
    reason: () => reason,
    until(deadline: number) {
      clearTimeout(timer);
      const remaining = deadline - Date.now();
      if (remaining <= 0) abort("timeout");
      else timer = setTimeout(() => abort("timeout"), remaining);
    },
    wait<T>(promise: Promise<T>): Promise<T> { return Promise.race([promise, aborted]); },
    close() { clearTimeout(timer); incoming.removeEventListener("abort", onAbort); }
  };
}
type Lifecycle = ReturnType<typeof lifecycle>;

function copyHeaders(source: Headers, names: string[]): Headers {
  const headers = new Headers();
  for (const name of names) {
    const value = source.get(name);
    if (value !== null) headers.set(name, value);
  }
  return headers;
}
function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}
function operation(path: readonly string[]): Operation | null {
  const joined = path.join("/");
  if ((path.length === 2 && joined === "agent/run") || (path.length === 3 && joined === "agent/run/stream")) return "agent";
  if (path.length === 2 && joined === "agent/durable") return "start";
  if (path.length === 4 && path[0] === "agent" && path[1] === "durable" && OPAQUE_ID.test(path[2]) && path[3] === "resume") return "resume";
  if (path.length === 6 && path.slice(0, 4).join("/") === "sources/folder/sync/plans" && OPAQUE_ID.test(path[4]) && (path[5] === "batch" || path[5] === "reconcile")) return "folder";
  return null;
}
function destination(base: string, path: readonly string[]): URL {
  const url = new URL(base);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new ProxyFailure(502, "EXECUTION_PROXY_CONFIGURATION");
  }
  url.pathname = `${url.pathname.replace(/\/$/, "")}/${path.map(encodeURIComponent).join("/")}`;
  return url;
}
function abandon(reader: ReadableStreamDefaultReader<Uint8Array>, reason?: unknown) {
  // Cancellation must not wait indefinitely for a remote stream to acknowledge.
  void reader.cancel(reason).catch(() => undefined);
}
async function readBounded(body: ReadableStream<Uint8Array> | null, limit: number, owner: Lifecycle): Promise<Uint8Array<ArrayBuffer>> {
  if (body === null) return new Uint8Array();
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const next = await owner.wait(reader.read());
      if (next.done) break;
      length += next.value.byteLength;
      if (length > limit) throw new ProxyFailure(413, "EXECUTION_PROXY_BODY_TOO_LARGE");
      chunks.push(next.value);
    }
  } catch (error) {
    abandon(reader, error);
    throw error;
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return bytes;
}
function startBudget(body: Uint8Array): number {
  let value: Record<string, unknown> | null;
  try { value = object(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body))); }
  catch { throw new ProxyFailure(400, "EXECUTION_PROXY_INVALID_REQUEST"); }
  if (!value) throw new ProxyFailure(400, "EXECUTION_PROXY_INVALID_REQUEST");
  const budgetMode = value.budget_mode ?? "wall_clock";
  const mode = value.execution_mode ?? "inline";
  const seconds = value.timeout_seconds ?? 120;
  if ((budgetMode !== "wall_clock" && budgetMode !== "active") || (mode !== "inline" && mode !== "background") ||
      typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0 || seconds > (budgetMode === "active" ? 3600 : 120)) {
    throw new ProxyFailure(400, "EXECUTION_PROXY_INVALID_BUDGET");
  }
  if (mode === "background") return EXECUTION_PROXY_BUDGET_MS.background;
  return Math.max(EXECUTION_PROXY_BUDGET_MS.agent, seconds * 1000 + 15_000);
}
function resumeBudget(bytes: Uint8Array, runId: string): number {
  let data: Record<string, unknown> | null;
  try { data = object(object(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)))?.data); }
  catch { throw new ProxyFailure(502, "EXECUTION_PROXY_INVALID_STATE"); }
  if (!data || data.run_id !== runId) throw new ProxyFailure(502, "EXECUTION_PROXY_INVALID_STATE");
  const mode = data.execution_mode ?? "inline";
  const budgetMode = data.budget_mode ?? "wall_clock";
  if ((mode !== "inline" && mode !== "background") || (budgetMode !== "wall_clock" && budgetMode !== "active")) throw new ProxyFailure(502, "EXECUTION_PROXY_INVALID_STATE");
  if (mode === "background") return EXECUTION_PROXY_BUDGET_MS.background;
  if (budgetMode === "wall_clock") return EXECUTION_PROXY_BUDGET_MS.agent;
  const remaining = data.active_seconds_remaining;
  if (typeof remaining !== "number" || !Number.isFinite(remaining) || remaining < 0 || remaining > 3600) throw new ProxyFailure(502, "EXECUTION_PROXY_INVALID_STATE");
  return Math.max(EXECUTION_PROXY_BUDGET_MS.agent, remaining * 1000 + 15_000);
}

function forward(response: Response, owner: Lifecycle): Response {
  const headers = copyHeaders(response.headers, RESPONSE_HEADERS);
  headers.set("cache-control", "no-store");
  const init = { status: response.status, statusText: response.statusText, headers };
  if (response.body === null || [204, 205, 304].includes(response.status)) {
    void response.body?.cancel().catch(() => undefined);
    owner.close();
    return new Response(null, init);
  }
  const reader = response.body.getReader();
  let length = 0;
  let finished = false;
  let streamController: ReadableStreamDefaultController<Uint8Array>;
  const finish = () => {
    if (finished) return;
    finished = true;
    owner.signal.removeEventListener("abort", onAbort);
    owner.close();
    reader.releaseLock();
  };
  const onAbort = () => {
    if (finished) return;
    abandon(reader, owner.signal.reason);
    streamController.error(new DOMException("Execution proxy response ended.", "AbortError"));
    finish();
  };
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      streamController = controller;
      owner.signal.addEventListener("abort", onAbort, { once: true });
      if (owner.signal.aborted) onAbort();
    },
    async pull(controller) {
      try {
        const next = await owner.wait(reader.read());
        if (finished) return;
        if (next.done) { controller.close(); finish(); return; }
        length += next.value.byteLength;
        if (length > EXECUTION_PROXY_MAX_RESPONSE_BYTES) throw new ProxyFailure(502, "EXECUTION_PROXY_RESPONSE_TOO_LARGE");
        controller.enqueue(next.value);
      } catch {
        if (finished) return;
        controller.error(new Error("Execution proxy response could not complete."));
        abandon(reader);
        finish();
        owner.abort("upstream");
      }
    },
    cancel(reason) { abandon(reader, reason); finish(); owner.abort("aborted"); }
  });
  return new Response(stream, init);
}
function failure(status: number, code: string, request: Request): Response {
  const requestId = request.headers.get("x-request-id");
  return Response.json({ request_id: requestId, data: null, error: { code, message: "Execution request could not complete.", request_id: requestId } }, {
    status, headers: { "cache-control": "no-store", ...(status === 405 ? { allow: "POST" } : {}) }
  });
}

/** Fixed-origin transport; no cookies, redirects, or caller-supplied destinations. */
export async function proxyExecution(request: Request, path: readonly string[], options: Options = {}): Promise<Response> {
  if (request.method !== "POST") return failure(405, "EXECUTION_PROXY_METHOD", request);
  const kind = operation(path);
  if (kind === null) return failure(404, "EXECUTION_PROXY_PATH", request);
  if (new URL(request.url).search) return failure(400, "EXECUTION_PROXY_QUERY", request);
  const started = Date.now();
  const owner = lifecycle(request.signal);
  owner.until(started + EXECUTION_PROXY_BUDGET_MS.requestBody);
  try {
    if (owner.signal.aborted) throw owner.signal.reason;
    const target = destination(options.apiBaseUrl ?? process.env.RAG_API_BASE_URL ?? "http://127.0.0.1:8000", path);
    const transport = options.fetch ?? executionTransport;
    const bytes = await readBounded(request.body, EXECUTION_PROXY_MAX_REQUEST_BYTES, owner);
    const headers = copyHeaders(request.headers, REQUEST_HEADERS);
    let budget: number = kind === "folder" ? EXECUTION_PROXY_BUDGET_MS.folder : EXECUTION_PROXY_BUDGET_MS.agent;
    if (kind === "start") budget = startBudget(bytes);
    if (kind === "resume") {
      // The resume DTO intentionally contains no budget/mode hint. Read the
      // current owned run using exactly the same authentication context instead.
      owner.until(Math.min(started + EXECUTION_PROXY_BUDGET_MS.background, Date.now() + EXECUTION_PROXY_BUDGET_MS.stateRead));
      const stateHeaders = new Headers(headers);
      stateHeaders.delete("content-type");
      owner.signal.throwIfAborted();
      const state = await owner.wait(transport(destination(options.apiBaseUrl ?? process.env.RAG_API_BASE_URL ?? "http://127.0.0.1:8000", path.slice(0, 3)), {
        method: "GET", headers: stateHeaders, signal: owner.signal, cache: "no-store", redirect: "manual"
      }));
      if (!state.ok) return forward(state, owner);
      try { budget = resumeBudget(await readBounded(state.body, MAX_STATE_BYTES, owner), path[2]); }
      catch (error) {
        if (error instanceof ProxyFailure && error.status === 413) throw new ProxyFailure(502, "EXECUTION_PROXY_INVALID_STATE");
        throw error;
      }
    }
    owner.until(started + Math.min(budget, EXECUTION_PROXY_BUDGET_MS.maximumInline));
    owner.signal.throwIfAborted();
    const response = await owner.wait(transport(target, {
      method: "POST", headers, ...(bytes.byteLength ? { body: bytes } : {}),
      signal: owner.signal, cache: "no-store", redirect: "manual"
    }));
    return forward(response, owner);
  } catch (error) {
    const reason = owner.reason();
    owner.abort("upstream");
    owner.close();
    if (reason === "timeout") return failure(504, "EXECUTION_PROXY_TIMEOUT", request);
    if (reason === "aborted") return failure(499, "EXECUTION_PROXY_ABORTED", request);
    if (error instanceof ProxyFailure) return failure(error.status, error.code, request);
    return failure(502, "EXECUTION_PROXY_UNAVAILABLE", request);
  }
}

/**
 * Scoped streaming HTTP transport. Node fetch has an independent 300-second
 * Undici deadline; using builtins keeps this route under its own finite total
 * deadline without changing any global dispatcher or relying on a dev package.
 * The caller owns the abort signal through response EOF/cancellation.
 */
export const executionTransport: typeof fetch = (input, init = {}) => new Promise<Response>((resolve, reject) => {
  const target = new URL(input instanceof Request ? input.url : String(input));
  const headers = new Headers(init.headers);
  // These execution endpoints return JSON/SSE. Keep raw HTTP bytes consistent
  // with the response body instead of introducing transparent decompression.
  headers.set("accept-encoding", "identity");
  const body = init.body;
  if (body !== undefined && body !== null && !(body instanceof Uint8Array)) {
    reject(new TypeError("Execution transport requires owned request bytes."));
    return;
  }
  const send = target.protocol === "https:" ? httpsRequest : httpRequest;
  const outgoing = send(target, {
    method: init.method ?? "GET", headers: Object.fromEntries(headers),
    signal: init.signal ?? undefined, agent: false
  }, (incoming) => {
    const responseHeaders = new Headers();
    for (const [name, value] of Object.entries(incoming.headers)) {
      if (Array.isArray(value)) for (const item of value) responseHeaders.append(name, item);
      else if (value !== undefined) responseHeaders.set(name, value);
    }
    try {
      const status = incoming.statusCode ?? 502;
      const empty = [204, 205, 304].includes(status);
      const stream = empty ? null : Readable.toWeb(incoming, {
        strategy: { highWaterMark: 65_536, size: (chunk: Uint8Array) => chunk.byteLength }
      }) as ReadableStream<Uint8Array>;
      if (empty) incoming.resume();
      resolve(new Response(stream, { status, statusText: incoming.statusMessage, headers: responseHeaders }));
    } catch (error) { incoming.destroy(); reject(error instanceof Error ? error : new Error("Execution response was invalid.")); }
  });
  // Do not inherit an idle/socket policy from the global Agent. The proxy's
  // selected finite deadline covers headers and the complete streaming body.
  outgoing.setTimeout(0);
  outgoing.once("error", reject);
  outgoing.end(body);
});
