import type { ChatHistoryMessage, Citation, SseEvent, StructuredError, ToolEvent } from "./api/types";
import { safeErrorMessage } from "./api/safety";

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  citations: Citation[];
  requestId?: string | null;
  traceId?: string | null;
  isFinal: boolean;
  status?: "answered" | "no_answer" | "error";
  error?: StructuredError;
  terminalError?: boolean;
  toolEvents: ToolEvent[];
};

/** Pure state transition: no callbacks, storage writes or React updates. */
export function applyChatEvent(message: ChatMessage, event: SseEvent): ChatMessage {
  if (message.isFinal) return message;
  const withContext = { ...message, requestId: event.data.request_id, traceId: event.data.trace_id };
  if (event.type === "error") {
    return {
      ...withContext,
      error: event.data,
      terminalError: message.terminalError === true || event.data.terminal,
      status: event.data.terminal ? "error" : message.status,
      citations: event.data.terminal ? [] : message.citations
    };
  }
  if (event.type === "final") {
    if (event.data.status === "error" || message.terminalError === true) {
      const code = event.data.metadata?.error_code ?? "CHAT_STREAM_FAILED";
      return failChatMessage(withContext, message.error ?? {
        code,
        message: safeErrorMessage(code),
        request_id: event.data.request_id,
        trace_id: event.data.trace_id
      });
    }
    return {
      ...withContext,
      text: event.data.answer,
      // The final snapshot is authoritative, including an explicitly empty citation set.
      citations: dedupeCitations(event.data.citations),
      status: event.data.no_answer ? "no_answer" : "answered",
      isFinal: true,
      error: undefined
    };
  }
  if (message.terminalError === true) return withContext;
  if (event.type === "token") return { ...withContext, text: message.text + event.data.delta };
  if (event.type === "citation") {
    return { ...withContext, citations: dedupeCitations([...message.citations, event.data.citation]) };
  }
  return { ...withContext, toolEvents: [...message.toolEvents, event.data] };
}

export function failChatMessage(message: ChatMessage, error: StructuredError): ChatMessage {
  return {
    ...message, isFinal: true, status: "error", terminalError: true, citations: [],
    // Preserve the server's safe domain error if the stream subsequently disconnects.
    error: message.error ?? error,
    requestId: error.request_id ?? message.requestId,
    traceId: error.trace_id ?? message.traceId
  };
}

export function canCopyAnswer(message: ChatMessage): boolean {
  return message.role === "assistant" && message.isFinal &&
    (message.status === "answered" || message.status === "no_answer");
}

export function historyMessageToChatMessage(message: ChatHistoryMessage): ChatMessage {
  const errorCode = message.role === "assistant" ? message.error_code : null;
  const restored: ChatMessage = {
    id: `history-${message.sequence_no}`, role: message.role === "assistant" ? "assistant" : "user",
    text: errorCode ? "" : message.content, citations: dedupeCitations(message.citations ?? []),
    requestId: message.request_id, traceId: message.trace_id, isFinal: true,
    status: message.no_answer === true ? "no_answer" : "answered", toolEvents: []
  };
  return errorCode ? failChatMessage(restored, {
    code: errorCode, message: safeErrorMessage(errorCode),
    request_id: message.request_id, trace_id: message.trace_id
  }) : restored;
}

export function dedupeCitations(citations: Citation[]): Citation[] {
  const seen = new Set<string>();
  return citations.filter((citation) => {
    const key = JSON.stringify([citation.document_id, citation.version_id ?? null, citation.chunk_id]);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
