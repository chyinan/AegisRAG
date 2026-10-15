import { describe, expect, it } from "vitest";
import fixtures from "@/test/fixtures/chat-stream.json";
import { parseSseEvents } from "./api/sse";
import { applyChatEvent, canCopyAnswer, failChatMessage, historyMessageToChatMessage, type ChatMessage } from "./chat-stream-state";

function empty(): ChatMessage {
  return { id: "assistant", role: "assistant", text: "", citations: [], isFinal: false, toolEvents: [] };
}

function apply(payload: string, initial = empty()): ChatMessage {
  return parseSseEvents(payload).reduce(applyChatEvent, initial);
}

describe("chat stream state", () => {
  it("accumulates deltas, deduplicates streamed citations and replaces provisional text at final", () => {
    const events = parseSseEvents(fixtures.success);
    let message = events.slice(0, -1).reduce(applyChatEvent, empty());
    expect(message.text).toBe("Working remotely 🌍 is supported.");
    expect(message.citations).toHaveLength(1);
    expect(canCopyAnswer(message)).toBe(false);
    message = applyChatEvent(message, events.at(-1)!);
    expect(message.text).toBe("Working remotely is supported [cite-a3b7d982cee7ed64].");
    expect(message.citations).toHaveLength(1);
    expect(message.status).toBe("answered");
    expect(canCopyAnswer(message)).toBe(true);
  });

  it("honors no_answer independently of empty citations and clears provisional citations", () => {
    const events = parseSseEvents(fixtures.success);
    const provisional = events.slice(0, -1).reduce(applyChatEvent, empty());
    const message = apply(fixtures.noAnswer, provisional);
    expect(message.status).toBe("no_answer");
    expect(message.citations).toEqual([]);
    expect(canCopyAnswer(message)).toBe(true);
    const final = parseSseEvents(fixtures.noAnswer)[0];
    if (final.type !== "final") throw new Error("fixture must end with final");
    expect(applyChatEvent(empty(), { ...final, data: { ...final.data, no_answer: false } }).status).toBe("answered");
  });

  it("shows HTTP-200 error events with their safe code/message/IDs, never as copyable success", () => {
    const message = apply(fixtures.error);
    expect(message.error).toMatchObject({ code: "RAG_QUERY_FAILED", message: "Chat query could not be completed. Please try again." });
    expect(message.requestId).toBe("req-stream-fixture");
    expect(message.isFinal).toBe(true);
    expect(message.status).toBe("error");
    expect(message.citations).toEqual([]);
    expect(canCopyAnswer(message)).toBe(false);
    expect(canCopyAnswer(apply(fixtures.failedFinal))).toBe(false);
  });

  it("never unlocks a successful final after a terminal error or overwrites completed answers", () => {
    const error = parseSseEvents(fixtures.error).find((event) => event.type === "error")!;
    const failed = applyChatEvent(empty(), error);
    const message = apply(fixtures.success, failed);
    expect(message.status).toBe("error");
    expect(canCopyAnswer(message)).toBe(false);
    const success = apply(fixtures.success);
    expect(applyChatEvent(success, error)).toBe(success);
  });

  it("retains last IDs and domain error when the connection fails after error", () => {
    const beforeFinal = parseSseEvents(fixtures.error).slice(0, -1).reduce(applyChatEvent, empty());
    const message = failChatMessage(beforeFinal, { code: "CHAT_STREAM_INCOMPLETE", message: "Retry" });
    expect(message.requestId).toBe("req-stream-fixture");
    expect(message.error?.code).toBe("RAG_QUERY_FAILED");
    expect(canCopyAnswer(message)).toBe(false);
  });

  it("restores failed history as a safe error while successful no-answer remains copyable", () => {
    const base = { role: "assistant" as const, sequence_no: 2, content: "stream_error:RAG_QUERY_FAILED", request_id: "req-history", trace_id: "trace-history", created_at: "2026-10-14T00:00:00Z", no_answer: true };
    const failed = historyMessageToChatMessage({ ...base, error_code: "RAG_QUERY_FAILED" });
    expect(failed.text).toBe("");
    expect(failed.status).toBe("error");
    expect(canCopyAnswer(failed)).toBe(false);
    expect(canCopyAnswer(historyMessageToChatMessage({ ...base, content: "Cannot confirm." }))).toBe(true);
    expect(historyMessageToChatMessage({ ...base, role: "user", error_code: "UNTRUSTED" }).error).toBeUndefined();
  });
});
