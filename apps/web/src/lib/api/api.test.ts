import { describe, expect, it } from "vitest";
import { buildAuditLogsPath, buildReviewItemsPath, listDocumentReview, loadChatHistory, streamChat } from "./client";
import { stripForbiddenFields } from "./safety";
import { parseSseEvents } from "./sse";

describe("API safety helpers", () => {
  it("unwraps document review list envelopes before components render rows", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = () =>
      Promise.resolve(new Response(
        JSON.stringify({
          request_id: "req-1",
          data: {
            items: [
              {
                document_id: "doc-1",
                version_id: "ver-1",
                source_display_name: "Policy",
                source_type: "markdown",
                status: "parsed",
                updated_at: "2026-06-10T12:00:00Z"
              }
            ],
            limit: 25,
            next_cursor: null
          },
          error: null
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      ));

    try {
      const rows = await listDocumentReview({
        mode: "dev_headers",
        label: "Platform Admin",
        userId: "admin",
        tenantId: "tenant",
        roles: ["admin"],
        permissions: ["document:read"]
      });

      expect(rows).toEqual([
        {
          document_id: "doc-1",
          version_id: "ver-1",
          source_display_name: "Policy",
          title: "Policy",
          source_type: "markdown",
          status: "parsed",
          updated_at: "2026-06-10T12:00:00Z"
        }
      ]);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

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

  it("sends chat stream requests with the backend query contract", async () => {
    const originalFetch = globalThis.fetch;
    let capturedBody: unknown;
    globalThis.fetch = (_input, init) => {
      if (typeof init?.body !== "string") {
        throw new Error("Expected JSON request body.");
      }
      capturedBody = JSON.parse(init.body);
      return Promise.resolve(
        new Response('event: final\ndata: {"request_id":"req-1","answer":"done","citations":[]}\n\n', {
          status: 200,
          headers: { "Content-Type": "text/event-stream" }
        })
      );
    };

    try {
      const events = [];
      for await (const event of streamChat(
        {
          mode: "dev_headers",
          label: "Platform Admin",
          userId: "admin",
          tenantId: "tenant",
          roles: ["admin"],
          permissions: ["retrieval:query"]
        },
        "Can you see the README?",
        "session-1"
      )) {
        events.push(event);
      }

      expect(capturedBody).toEqual({
        query: "Can you see the README?",
        session_id: "session-1"
      });
      expect(events).toHaveLength(1);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("loads chat history through the backend session contract", async () => {
    const originalFetch = globalThis.fetch;
    let capturedUrl = "";
    globalThis.fetch = (input) => {
      capturedUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      return Promise.resolve(
        new Response(
          JSON.stringify({
            request_id: "req-history",
            data: {
              session_id: "session-1",
              messages: [
                {
                  role: "user",
                  content: "What is AegisRAG?",
                  sequence_no: 1,
                  request_id: "req-user",
                  trace_id: "trace-user",
                  created_at: "2026-06-10T12:00:00Z"
                }
              ]
            },
            error: null
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      );
    };

    try {
      const history = await loadChatHistory(
        {
          mode: "dev_headers",
          label: "Platform Admin",
          userId: "admin",
          tenantId: "tenant",
          roles: ["admin"],
          permissions: ["retrieval:query"]
        },
        "session-1",
        500
      );

      expect(capturedUrl).toContain("/chat/history?");
      expect(capturedUrl).toContain("session_id=session-1");
      expect(capturedUrl).toContain("limit=100");
      expect(history.messages[0].content).toBe("What is AegisRAG?");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("ignores unknown SSE event names", () => {
    const events = parseSseEvents('event: provider_raw\ndata: {"raw_output":"secret"}\n\n');

    expect(events).toEqual([]);
  });

  it("builds Audit Explorer queries without frontend-controlled identity scope", () => {
    const path = buildAuditLogsPath({
      request_id: " req-1 ",
      trace_id: "trace-1",
      action: "rag.query",
      limit: 500
    });

    expect(path).toContain("/audit/logs?");
    expect(path).toContain("request_id=req-1");
    expect(path).toContain("trace_id=trace-1");
    expect(path).toContain("action=rag.query");
    expect(path).toContain("limit=200");
    expect(path).not.toContain("tenant_id");
    expect(path).not.toContain("roles");
    expect(path).not.toContain("permissions");
  });

  it("builds Review Queue queries without frontend-controlled identity scope", () => {
    const path = buildReviewItemsPath({
      request_id: " req-review ",
      trace_id: "trace-review",
      status: "open",
      severity: "high",
      limit: 500
    });

    expect(path).toContain("/review/items?");
    expect(path).toContain("request_id=req-review");
    expect(path).toContain("trace_id=trace-review");
    expect(path).toContain("status=open");
    expect(path).toContain("severity=high");
    expect(path).toContain("limit=100");
    expect(path).not.toContain("tenant_id");
    expect(path).not.toContain("roles");
    expect(path).not.toContain("permissions");
  });
});
