import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import fixtures from "@/test/fixtures/chat-stream.json";
import { parseSseEvents } from "@/lib/api/sse";
import type { Citation } from "@/lib/api/types";
import { AnswerMarkdown } from "./answer-markdown";

describe("inline evidence identity", () => {
  it("opens the exact backend source for reversed and repeated markers, with no unused-source button", async () => {
    const [event] = parseSseEvents(fixtures.reversedCitations);
    if (event.type !== "final") throw new Error("Expected generated final fixture");
    const onOpen = vi.fn<(citation: Citation) => void>();
    render(<AnswerMarkdown answer={event.data.answer} citations={event.data.citations} onOpenCitation={onOpen} />);
    const buttons = screen.getAllByRole("button", { name: /Evidence: Remote work/ });
    expect(buttons.map((button) => button.textContent)).toEqual(["3", "1", "3"]);
    const user = userEvent.setup();
    for (const button of buttons) await user.click(button);
    expect(onOpen.mock.calls.map(([citation]) => [citation.document_id, citation.version_id, citation.chunk_id])).toEqual([
      ["doc-pilot", "version-pilot", "chunk-approval"],
      ["doc-pilot", "version-pilot", "chunk-pilot"],
      ["doc-pilot", "version-pilot", "chunk-approval"]
    ]);
  });

  it("never turns unknown or old positional links into an evidence control", () => {
    const [event] = parseSseEvents(fixtures.success).filter((item) => item.type === "final");
    if (event.type !== "final") throw new Error("Expected generated final fixture");
    render(<AnswerMarkdown answer="Unknown [cite-ffffffffffffffff] and [old](citation:0)." citations={event.data.citations} onOpenCitation={vi.fn()} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText("old")).toBeInTheDocument();
  });
});
