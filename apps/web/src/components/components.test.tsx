import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Citation, ToolEvent } from "@/lib/api/types";
import {
  CitationChip,
  CopyIdButton,
  NoAnswerPanel,
  SafeErrorBanner,
  ToolEventRow
} from "./primitives";

describe("workbench primitives", () => {
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

    await userEvent.click(screen.getByRole("button", { name: /open evidence/i }));

    expect(onOpen).toHaveBeenCalledWith(citation);
  });

  it("keeps copy disabled when final answer is not available", () => {
    render(<CopyIdButton value="" label="Copy answer with citations" disabled />);

    expect(screen.getByRole("button", { name: /copy answer/i })).toBeDisabled();
  });

  it("renders no-answer as a successful action state with request copy", () => {
    render(<NoAnswerPanel requestId="req-no-answer" onDiagnostics={vi.fn()} />);

    expect(screen.getByText("无法从当前授权资料确认。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /copy request_id/i })).toBeEnabled();
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
    expect(screen.getByText(/未展示 raw arguments\/output/)).toBeInTheDocument();
  });

  it("shows safe errors with request IDs", () => {
    render(<SafeErrorBanner code="AUTH_CONTEXT_REQUIRED" message="Missing auth." requestId="req-auth" />);

    expect(screen.getByRole("alert")).toHaveTextContent("AUTH_CONTEXT_REQUIRED");
    expect(screen.getByText(/req-auth/)).toBeInTheDocument();
  });
});
