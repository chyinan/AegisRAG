import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Citation, ToolEvent } from "@/lib/api/types";
import { PERSONAS } from "@/lib/auth";
import {
  CitationChip,
  CopyIdButton,
  NoAnswerPanel,
  SafeErrorBanner,
  ToolEventRow
} from "./primitives";
import { AnswerMarkdown, normalizeAnswerMarkdown } from "./answer-markdown";
import { AuditPanel } from "./audit-panel";
import { EvidencePanel } from "./evidence-panel";
import { KnowledgeBasePanel } from "./knowledge-base";
import { WorkbenchShell, groupCitationsForDisplay } from "./workbench-shell";

describe("workbench primitives", () => {
  it("renders answer markdown and turns citation tokens into evidence controls", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    const citation: Citation = {
      document_id: "doc-1",
      version_id: "v1",
      chunk_id: "chunk-1",
      title_path: ["README"]
    };

    render(
      <AnswerMarkdown
        answer={"**AegisRAG** supports:\n\n- RAG answers [cite-source-a]\n- Audit trails [cite-source-a]"}
        citations={[citation]}
        onOpenCitation={onOpen}
      />
    );

    expect(screen.getByText("AegisRAG")).toBeInTheDocument();
    expect(screen.getByRole("list")).toBeInTheDocument();
    expect(screen.queryByText(/cite-source-a/)).not.toBeInTheDocument();

    const citationButtons = screen.getAllByRole("button", { name: /Evidence: README/ });
    expect(citationButtons).toHaveLength(2);
    await user.click(citationButtons[0]);

    expect(onOpen).toHaveBeenCalledWith(citation);
  });

  it("removes unmatched citation tokens from rendered answer markdown", () => {
    expect(normalizeAnswerMarkdown("Answer [cite-hidden] with **markdown**.", 0)).toBe("Answer with **markdown**.");
  });

  it("opens a citation through an explicit button", async () => {
    const citation: Citation = {
      document_id: "doc-1",
      version_id: "v1",
      chunk_id: "chunk-1",
      title_path: ["HR", "Leave"],
      page_start: 3
    };
    const onOpen = vi.fn();
    render(<CitationChip citation={citation} onOpen={onOpen} />);

    await userEvent.click(screen.getByRole("button", { name: /evidence/i }));

    expect(onOpen).toHaveBeenCalledWith(citation);
  });

  it("shows grouped citation counts on source chips", () => {
    const citation: Citation = {
      document_id: "doc-1",
      version_id: "v1",
      chunk_id: "chunk-1",
      source_display_name: "README.md"
    };
    render(<CitationChip citation={citation} onOpen={vi.fn()} label="README.md · v1" count={4} />);

    expect(screen.getByRole("button", { name: /README\.md .* x4/i })).toBeInTheDocument();
    expect(screen.getByText("x4")).toBeInTheDocument();
  });

  it("keeps copy disabled when final answer is not available", () => {
    render(<CopyIdButton value="" label="Copy answer with citations" disabled />);

    expect(screen.getByRole("button", { name: /copy answer/i })).toBeDisabled();
  });

  it("renders no-answer as a successful action state with request copy", () => {
    render(<NoAnswerPanel requestId="req-no-answer" onDiagnostics={vi.fn()} />);

    expect(screen.getByText("Cannot confirm from the authorized sources.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /copy request_id/i })).toBeEnabled();
  });

  it("renders no-answer in Chinese when language is selected", () => {
    render(<NoAnswerPanel requestId="req-no-answer" onDiagnostics={vi.fn()} language="zh" />);

    expect(screen.getByText("无法从当前授权资料确认。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /复制 request_id/i })).toBeEnabled();
  });

  it("renders tool events without raw arguments or output", () => {
    const event: ToolEvent = {
      tool_name: "rag_search",
      status: "ok",
      latency_ms: 24,
      request_id: "req-1"
    };
    render(<ToolEventRow event={event} />);

    expect(screen.getByText(/rag_search/)).toBeInTheDocument();
    expect(screen.getByText(/Raw arguments and output are hidden/)).toBeInTheDocument();
  });

  it("shows safe errors with request IDs", () => {
    render(<SafeErrorBanner code="AUTH_CONTEXT_REQUIRED" message="Missing auth." requestId="req-auth" />);

    expect(screen.getByRole("alert")).toHaveTextContent("AUTH_CONTEXT_REQUIRED");
    expect(screen.getByText(/req-auth/)).toBeInTheDocument();
  });
});

describe("workbench audit surface", () => {
  it("embeds Audit Explorer for authorized auditors", () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
    });

    render(
      <QueryClientProvider client={client}>
        <WorkbenchShell
          auth={PERSONAS.auditor}
          language="en"
          onLanguageChange={vi.fn()}
          onSignOut={vi.fn()}
        />
      </QueryClientProvider>
    );

    expect(screen.getByRole("heading", { name: "Audit Explorer" })).toBeInTheDocument();
    expect(screen.getByLabelText("request_id")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Search logs" })).toBeEnabled();
  });

  it("renders localized audit date filters with application-controlled placeholders", () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
    });

    render(
      <QueryClientProvider client={client}>
        <AuditPanel auth={PERSONAS.platform_admin} language="zh" canRead />
      </QueryClientProvider>
    );

    const createdFrom = screen.getByLabelText("创建时间起");
    const createdTo = screen.getByLabelText("创建时间止");
    expect(createdFrom).toHaveAttribute("type", "text");
    expect(createdFrom).toHaveAttribute("placeholder", "YYYY-MM-DD HH:mm");
    expect(createdTo).toHaveAttribute("type", "text");
    expect(createdTo).toHaveAttribute("placeholder", "YYYY-MM-DD HH:mm");
    expect(screen.queryByText("created_at_from")).not.toBeInTheDocument();
    expect(screen.queryByText("created_at_to")).not.toBeInTheDocument();
  });
});

describe("workbench evidence panel", () => {
  it("shows source summaries without resolving backend evidence", () => {
    const originalFetch = globalThis.fetch;
    const fetchSpy = vi.fn();
    globalThis.fetch = fetchSpy;
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
    });
    const citation: Citation = {
      document_id: "doc-1",
      version_id: "v1",
      chunk_id: "chunk-1",
      source_display_name: "README.md"
    };

    try {
      render(
        <QueryClientProvider client={client}>
          <EvidencePanel auth={PERSONAS.platform_admin} citation={citation} language="en" shouldResolve={false} />
        </QueryClientProvider>
      );

      expect(screen.getByText(/README\.md/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /copy chunk_id/i })).toBeEnabled();
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("renders unavailable sources as a citation state instead of a generic safe error", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = () =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            request_id: "req-source",
            data: null,
            error: {
              code: "SOURCE_ACCESS_DENIED",
              message: "Source reference cannot be resolved.",
              request_id: "req-source",
              trace_id: "trace-source"
            }
          }),
          { status: 404, headers: { "Content-Type": "application/json" } }
        )
      );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
    });
    const citation: Citation = {
      document_id: "doc-1",
      version_id: "v1",
      chunk_id: "chunk-missing",
      source_display_name: "README.md"
    };

    try {
      render(
        <QueryClientProvider client={client}>
          <EvidencePanel auth={PERSONAS.platform_admin} citation={citation} language="en" />
        </QueryClientProvider>
      );

      expect(await screen.findByText("Source unavailable")).toBeInTheDocument();
      expect(screen.getByText(/cannot be re-authorized/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /copy chunk_id/i })).toBeEnabled();
      expect(screen.queryByText("SAFE_ERROR")).not.toBeInTheDocument();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("workbench chat history", () => {
  it("groups repeated citation chunks by source document for display", () => {
    const groups = groupCitationsForDisplay([
      {
        document_id: "doc-1",
        version_id: "v1",
        chunk_id: "chunk-1",
        source_display_name: "README.md"
      },
      {
        document_id: "doc-1",
        version_id: "v1",
        chunk_id: "chunk-2",
        source_display_name: "README.md"
      },
      {
        document_id: "doc-2",
        version_id: "v1",
        chunk_id: "chunk-3",
        source_display_name: "PRD.md"
      }
    ]);

    expect(groups).toHaveLength(2);
    expect(groups[0]?.label).toBe("README.md · v1");
    expect(groups[0]?.count).toBe(2);
    expect(groups[1]?.label).toBe("PRD.md · v1");
    expect(groups[1]?.count).toBe(1);
  });

  it("restores the current backend chat session from sessionStorage", async () => {
    const originalFetch = globalThis.fetch;
    const sessionKey = "aegisrag.chat.session.tenant-demo-alpha.demo-user-employee";
    window.sessionStorage.setItem(sessionKey, "session-1");
    globalThis.fetch = (input) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      if (url.includes("/chat/history")) {
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
                  },
                  {
                    role: "assistant",
                    content: "**AegisRAG** is a trusted RAG workbench.",
                    sequence_no: 2,
                    request_id: "req-assistant",
                    trace_id: "trace-assistant",
                    created_at: "2026-06-10T12:00:01Z",
                    citations: []
                  }
                ]
              },
              error: null
            }),
            { status: 200, headers: { "Content-Type": "application/json" } }
          )
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ request_id: "req", data: null, error: null }), {
          status: 200,
          headers: { "Content-Type": "application/json" }
        })
      );
    };
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
    });

    try {
      render(
        <QueryClientProvider client={client}>
          <WorkbenchShell
            auth={PERSONAS.employee}
            language="en"
            onLanguageChange={vi.fn()}
            onSignOut={vi.fn()}
          />
        </QueryClientProvider>
      );

      expect(await screen.findByText("What is AegisRAG?")).toBeInTheDocument();
      expect(screen.getByText("History restored from backend memory")).toBeInTheDocument();
      expect(screen.getByText(/Current session: session-1/)).toBeInTheDocument();
    } finally {
      globalThis.fetch = originalFetch;
      window.sessionStorage.removeItem(sessionKey);
    }
  });
});

describe("workbench migrated governance surfaces", () => {
  it("renders first-class panels for review, eval, agent, and settings", async () => {
    const user = userEvent.setup();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
    });

    render(
      <QueryClientProvider client={client}>
        <WorkbenchShell
          auth={PERSONAS.platform_admin}
          language="en"
          onLanguageChange={vi.fn()}
          onSignOut={vi.fn()}
        />
      </QueryClientProvider>
    );

    await user.click(screen.getByRole("button", { name: /Review/ }));
    expect(screen.getByRole("heading", { name: "Review Queue" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Eval/ }));
    expect(screen.getByRole("heading", { name: "Eval Evidence" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Agent Runs/ }));
    expect(screen.getByRole("heading", { name: "Agent Run Console" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Settings/ }));
    expect(screen.getByRole("heading", { name: "Identity Boundaries" })).toBeInTheDocument();
  });
});

describe("knowledge base document list", () => {
  it("shows a localized selected file label after choosing a file", async () => {
    const user = userEvent.setup();
    const originalFetch = globalThis.fetch;
    globalThis.fetch = () =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            request_id: "req-list",
            data: { items: [] },
            error: null
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
    });

    try {
      render(
        <QueryClientProvider client={client}>
          <KnowledgeBasePanel auth={PERSONAS.platform_admin} language="zh" />
        </QueryClientProvider>
      );

      await user.upload(screen.getByLabelText(/文件/), new File(["# PRD"], "PRD.md", { type: "text/markdown" }));

      expect(screen.getByText("已选中 PRD.md")).toBeInTheDocument();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("formats document timestamps compactly so table cells do not overflow", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = () =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            request_id: "req-1",
            data: {
              items: [
                {
                  document_id: "doc-1",
                  source_display_name: "README.md",
                  source_type: "markdown",
                  status: "parsed",
                  updated_at: "2026-06-10T12:11:56.764442Z"
                }
              ]
            },
            error: null
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
    });

    try {
      render(
        <QueryClientProvider client={client}>
          <KnowledgeBasePanel auth={PERSONAS.platform_admin} language="en" />
        </QueryClientProvider>
      );

      expect(await screen.findByText("2026-06-10 12:11")).toBeInTheDocument();
      expect(screen.queryByText("2026-06-10T12:11:56.764442Z")).not.toBeInTheDocument();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("lets document managers delete a document and refresh the list", async () => {
    const user = userEvent.setup();
    const originalFetch = globalThis.fetch;
    const originalConfirm = window.confirm;
    const requests: Array<{ method: string; url: string }> = [];
    window.confirm = vi.fn(() => true);
    globalThis.fetch = (input, init) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      const method = init?.method ?? "GET";
      requests.push({ method, url });
      if (method === "DELETE") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              request_id: "req-delete",
              data: {
                document_id: "doc-1",
                status: "deleted",
                deleted_versions: 1,
                deleted_chunks: 2,
                deleted_vectors: 2,
                request_id: "req-delete",
                trace_id: "trace-delete"
              },
              error: null
            }),
            { status: 200, headers: { "Content-Type": "application/json" } }
          )
        );
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            request_id: "req-list",
            data: {
              items: [
                {
                  document_id: "doc-1",
                  source_display_name: "README.md",
                  source_type: "markdown",
                  status: "retrieval_ready",
                  updated_at: "2026-06-10T13:41:00Z"
                }
              ]
            },
            error: null
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      );
    };
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
    });

    try {
      render(
        <QueryClientProvider client={client}>
          <KnowledgeBasePanel auth={PERSONAS.platform_admin} language="en" />
        </QueryClientProvider>
      );

      await user.click(await screen.findByRole("button", { name: /delete document: README\.md/i }));

      expect(window.confirm).toHaveBeenCalledWith(
        "Delete this document and its indexed chunks? This removes it from retrieval."
      );
      expect(requests).toContainEqual({
        method: "DELETE",
        url: "/api/backend/documents/doc-1"
      });
      expect(requests.filter((request) => request.method === "GET")).toHaveLength(2);
    } finally {
      globalThis.fetch = originalFetch;
      window.confirm = originalConfirm;
    }
  });
});
