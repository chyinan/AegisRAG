import { describe, expect, it } from "vitest";
import { stripForbiddenFields } from "./safety";
import { parseSseEvents } from "./sse";

describe("API safety helpers", () => {
  it("removes forbidden response fields recursively", () => {
    const cleaned = stripForbiddenFields({
      request_id: "req-1",
      data: {
        document_id: "doc-1",
        source_uri: "s3://private",
        nested: {
          prompt: "do not expose",
          safe: "value"
        },
        items: [{ raw_output: "secret" }, { chunk_id: "chunk-1" }]
      },
      error: null
    });

    expect(cleaned).toEqual({
      request_id: "req-1",
      data: {
        document_id: "doc-1",
        nested: {
          safe: "value"
        },
        items: [{}, { chunk_id: "chunk-1" }]
      },
      error: null
    });
  });

  it("parses known POST SSE events and strips unsafe data", () => {
    const events = parseSseEvents(
      [
        "event: token",
        'data: {"token":"hello"}',
        "",
        "event: citation",
        'data: {"document_id":"doc-1","chunk_id":"chunk-1","source_uri":"hidden"}',
        "",
        "event: final",
        'data: {"request_id":"req-1","trace_id":"trace-1","answer":"done","citations":[]}',
        ""
      ].join("\n")
    );

    expect(events).toHaveLength(3);
    expect(events[0]).toEqual({ type: "token", data: { token: "hello" } });
    expect(events[1]).toEqual({
      type: "citation",
      data: { document_id: "doc-1", chunk_id: "chunk-1" }
    });
    expect(events[2].type).toBe("final");
  });

  it("ignores unknown SSE event names", () => {
    const events = parseSseEvents('event: provider_raw\ndata: {"raw_output":"secret"}\n\n');

    expect(events).toEqual([]);
  });
});
