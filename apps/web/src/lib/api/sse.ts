import type { SseEvent } from "./types";
import { stripForbiddenFields } from "./safety";

export function parseSseEvents(payload: string): SseEvent[] {
  const events: SseEvent[] = [];
  const normalized = payload.replace(/\r\n/g, "\n");

  for (const block of normalized.split("\n\n")) {
    const lines = block
      .split("\n")
      .map((line) => line.trimEnd())
      .filter(Boolean);
    if (lines.length === 0) {
      continue;
    }

    let eventType = "message";
    const dataLines: string[] = [];
    for (const line of lines) {
      if (line.startsWith("event:")) {
        eventType = line.slice("event:".length).trim();
      }
      if (line.startsWith("data:")) {
        dataLines.push(line.slice("data:".length).trimStart());
      }
    }

    const rawData = dataLines.join("\n");
    const parsedData: unknown = rawData.length > 0 ? JSON.parse(rawData) : {};
    const safeData = stripForbiddenFields(parsedData);
    if (isKnownSseEvent(eventType, safeData)) {
      events.push({ type: eventType, data: safeData } as SseEvent);
    }
  }

  return events;
}

export async function* readSseStream(response: Response): AsyncGenerator<SseEvent> {
  if (response.body === null) {
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split(/\n\n/);
    buffer = parts.pop() ?? "";
    for (const event of parseSseEvents(parts.join("\n\n") + "\n\n")) {
      yield event;
    }
  }

  if (buffer.trim().length > 0) {
    for (const event of parseSseEvents(buffer + "\n\n")) {
      yield event;
    }
  }
}

function isKnownSseEvent(type: string, data: unknown): boolean {
  return (
    ["token", "citation", "tool_call", "tool_result", "error", "final"].includes(type) &&
    data !== null &&
    typeof data === "object"
  );
}
