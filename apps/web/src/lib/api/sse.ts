import type { Citation, SseEvent, StreamContext, StructuredError } from "./types";
import { stripForbiddenFields } from "./safety";

const EVENT_TYPES = new Set(["token", "citation", "tool_call", "tool_result", "error", "final"]);
const MAX_EVENT_LENGTH = 1024 * 1024;

/** Never includes the rejected payload or a JSON parser exception in the message. */
export class SseProtocolError extends Error {
  readonly structured: StructuredError;

  constructor(code = "CHAT_STREAM_INVALID_EVENT", context?: Partial<StreamContext>) {
    const message = code === "CHAT_STREAM_INCOMPLETE"
      ? "The connection ended before the answer was complete. Please try again."
      : "The answer stream could not be read safely. Please try again.";
    super(message);
    this.name = "SseProtocolError";
    this.structured = { code, message, ...context };
  }
}

export function parseSseEvents(payload: string): SseEvent[] {
  const events: SseEvent[] = [];
  for (const block of payload.replace(/\r\n|\r/g, "\n").split("\n\n")) {
    let eventType = "message";
    const dataLines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
      if (field === "event") eventType = value;
      if (field === "data") dataLines.push(value);
    }
    // Heartbeats, extension events and comments must not be parsed as domain events.
    if (!EVENT_TYPES.has(eventType)) continue;
    let data: unknown;
    try {
      data = stripForbiddenFields(JSON.parse(dataLines.join("\n")) as unknown);
    } catch {
      throw new SseProtocolError();
    }
    events.push(decodeEvent(eventType, data));
  }
  return events;
}

/** Owns and closes the response reader, including abort, invalid payload and early exit. */
export async function* readSseStream(response: Response, signal?: AbortSignal): AsyncGenerator<SseEvent> {
  if (signal?.aborted) {
    await response.body?.cancel().catch(() => undefined);
    signal.throwIfAborted();
  }
  let context: Partial<StreamContext> = {
    request_id: response.headers.get("x-request-id") ?? undefined,
    trace_id: response.headers.get("x-trace-id") ?? undefined
  };
  if (response.body === null) throw new SseProtocolError("CHAT_STREAM_INCOMPLETE", context);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const cancel = () => { void reader.cancel().catch(() => undefined); };
  signal?.addEventListener("abort", cancel, { once: true });

  try {
    while (true) {
      signal?.throwIfAborted();
      const { done, value } = await reader.read();
      signal?.throwIfAborted();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      // Keep a trailing CR until the next read so chunk boundaries inside CRLF work.
      buffer = buffer.replace(/\r\n/g, "\n").replace(done ? /\r/g : /\r(?!$)/g, "\n");
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        if (block.length > MAX_EVENT_LENGTH) throw new SseProtocolError();
        for (const event of parseSseEvents(block)) {
          signal?.throwIfAborted();
          context = { request_id: event.data.request_id, trace_id: event.data.trace_id };
          yield event;
          // Only a validated final event unlocks a completed answer. Ignore trailing frames.
          if (event.type === "final") return;
        }
      }
      if (buffer.length > MAX_EVENT_LENGTH) throw new SseProtocolError();
      // An unterminated frame at EOF is incomplete, even if it looks like a final JSON object.
      if (done) throw new SseProtocolError("CHAT_STREAM_INCOMPLETE", context);
    }
  } catch (error) {
    if (error instanceof SseProtocolError) {
      throw new SseProtocolError(error.structured.code, {
        request_id: error.structured.request_id ?? context.request_id,
        trace_id: error.structured.trace_id ?? context.trace_id
      });
    }
    throw error;
  } finally {
    signal?.removeEventListener("abort", cancel);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

function decodeEvent(type: string, raw: unknown): SseEvent {
  const data = record(raw);
  const context = { request_id: requiredString(data.request_id), trace_id: requiredString(data.trace_id) };
  if (data.event !== undefined && data.event !== type) throw new SseProtocolError(undefined, context);
  switch (type) {
    case "token":
      if (typeof data.index !== "number" || !Number.isInteger(data.index) || data.index < 0 || typeof data.delta !== "string") {
        throw new SseProtocolError(undefined, context);
      }
      return { type, data: { ...context, index: data.index, delta: data.delta } };
    case "citation":
      return { type, data: { ...context, citation: decodeCitation(data.citation) } };
    case "error":
      if (typeof data.terminal !== "boolean") throw new SseProtocolError(undefined, context);
      return { type, data: {
        ...context, code: requiredString(data.code), message: requiredString(data.message), terminal: data.terminal
      } };
    case "final": {
      if ((data.status !== "success" && data.status !== "error") || typeof data.no_answer !== "boolean" ||
          typeof data.answer !== "string" || !Array.isArray(data.citations)) {
        throw new SseProtocolError(undefined, context);
      }
      const metadata = data.metadata === undefined ? {} : record(data.metadata);
      return { type, data: {
        ...context, status: data.status, no_answer: data.no_answer, answer: data.answer,
        citations: data.citations.map(decodeCitation), session_id: optionalString(data.session_id),
        metadata: { error_code: optionalString(metadata.error_code) ?? undefined }
      } };
    }
    case "tool_call":
    case "tool_result": {
      const metadata = record(data.metadata);
      const status = type === "tool_call" ? "started" : requiredString(data.status);
      if (!["started", "success", "error"].includes(status)) throw new SseProtocolError(undefined, context);
      return { type, data: {
        ...context, tool_name: requiredString(data.tool_name), status,
        agent_run_id: optionalString(metadata.agent_run_id) ?? undefined,
        latency_ms: optionalNumber(metadata.latency_ms), error_code: optionalString(metadata.error_code)
      } };
    }
    default:
      throw new SseProtocolError(undefined, context);
  }
}

function decodeCitation(value: unknown): Citation {
  const data = record(value);
  const citationId = optionalString(data.citation_id);
  if (citationId != null && !/^cite-[0-9a-f]{16}$/.test(citationId)) throw new SseProtocolError();
  if (data.title_path != null && (!Array.isArray(data.title_path) ||
      !data.title_path.every((item: unknown) => typeof item === "string"))) throw new SseProtocolError();
  return {
    citation_id: citationId,
    document_id: requiredString(data.document_id), chunk_id: requiredString(data.chunk_id),
    version_id: optionalString(data.version_id), source_display_name: optionalString(data.source_display_name),
    source_type: optionalString(data.source_type), source: optionalString(data.source),
    page: optionalNumber(data.page), page_start: optionalNumber(data.page_start), page_end: optionalNumber(data.page_end),
    title_path: data.title_path, score: optionalNumber(data.score),
    retrieval_method: optionalString(data.retrieval_method)
  };
}

function record(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new SseProtocolError();
  return value as Record<string, unknown>;
}

function requiredString(value: unknown): string {
  if (typeof value !== "string" || value.trim().length === 0) throw new SseProtocolError();
  return value;
}

function optionalString(value: unknown): string | null | undefined {
  if (value === undefined || value === null || typeof value === "string") return value;
  throw new SseProtocolError();
}

function optionalNumber(value: unknown): number | null | undefined {
  if (value === undefined || value === null || (typeof value === "number" && Number.isFinite(value))) return value;
  throw new SseProtocolError();
}
