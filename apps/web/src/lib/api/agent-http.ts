import { AgentProtocolError } from "./agent-contract";
import { parseEnvelope } from "./client";
import type { ApiEnvelope } from "./types";

/** Bound bytes before JSON parsing; DTO limits alone do not bound body buffering. */
export const MAX_AGENT_JSON_BYTES = 1024 * 1024;

export async function parseAgentEnvelope<T>(response: Response, decodeData?: (value: unknown) => T, signal?: AbortSignal): Promise<ApiEnvelope<T>> {
  if (signal?.aborted) {
    void response.body?.cancel().catch(() => undefined);
    signal.throwIfAborted();
  }
  const declared = response.headers.get("content-length");
  if (declared !== null && /^\d+$/.test(declared) && Number(declared) > MAX_AGENT_JSON_BYTES) {
    void response.body?.cancel().catch(() => undefined);
    throw new AgentProtocolError("AGENT_RESPONSE_TOO_LARGE");
  }
  if (response.body === null) return parseEnvelope(response, decodeData);
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let size = 0;
  let body = "";
  let cancellationRequested = false;
  const cancel = () => {
    if (cancellationRequested) return;
    cancellationRequested = true;
    // A custom transport may never settle cancel(). Do not keep a revoked UI
    // operation alive; consume rejection without exposing private transport text.
    void reader.cancel().catch(() => undefined);
  };
  signal?.addEventListener("abort", cancel, { once: true });
  try {
    while (true) {
      signal?.throwIfAborted();
      const { value, done } = await reader.read();
      signal?.throwIfAborted();
      if (done) { body += decoder.decode(); break; }
      if (value.byteLength > MAX_AGENT_JSON_BYTES - size) throw new AgentProtocolError("AGENT_RESPONSE_TOO_LARGE");
      size += value.byteLength;
      body += decoder.decode(value, { stream: true });
    }
  } catch (error) {
    if (error instanceof AgentProtocolError || (error instanceof Error && (error.name === "AbortError" || error.name === "TimeoutError"))) throw error;
    throw new AgentProtocolError();
  } finally {
    signal?.removeEventListener("abort", cancel);
    cancel();
    reader.releaseLock();
  }
  // Keep exact JSON text, safe envelope errors and the strict approval decoder.
  return parseEnvelope(new Response(body || null, { status: response.status, statusText: response.statusText, headers: response.headers }), decodeData);
}
