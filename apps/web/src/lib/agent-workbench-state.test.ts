import { describe, expect, it } from "vitest";
import fixtures from "@/test/fixtures/agent-lifecycle.json";
import { decodeDurableAgentRun } from "./api/agent-contract";
import { durableActionAvailable, isDurableTerminal, sameReviewedRun } from "./agent-workbench-state";

const run = decodeDurableAgentRun(fixtures.durable.awaitingApproval.data);
const now = fixtures.clockEpochSeconds * 1000;
describe("durable action guards", () => {
  it("requires a fresh nonterminal snapshot before the original deadline", () => {
    expect(durableActionAvailable(run, true, now)).toBe(true);
    expect(durableActionAvailable(run, false, now)).toBe(false);
    expect(durableActionAvailable(run, true, run.deadline_at * 1000)).toBe(false);
    expect(durableActionAvailable({ ...run, approval: { ...run.approval!, expires_at: now / 1000 } }, true, now)).toBe(false);
  });
  it.each(["completed", "failed", "stopped", "cancelled", "intervention_required"] as const)("never resumes terminal %s", (status) => {
    const terminal = { ...run, status, approval: null };
    expect(isDurableTerminal(terminal)).toBe(true);
    expect(durableActionAvailable(terminal, true, now)).toBe(false);
  });
  it("compares the exact displayed revision, arguments, definition, and budgets before approval", () => {
    expect(sameReviewedRun(run, structuredClone(run))).toBe(true);
    for (const patch of [{ revision: 3 }, { run_id: "other" }, { deadline_at: run.deadline_at + 1 }, { steps_used: 2 }, { tool_calls_used: 1 }, { approval: { ...run.approval!, arguments: { query: "changed" } } }, { approval: { ...run.approval!, definition_digest: "a".repeat(64) } }]) {
      expect(sameReviewedRun(run, { ...run, ...patch })).toBe(false);
    }
  });
  it("uses the independent approval expiry and checkpointed remaining active time", () => {
    const active = { ...run, budget_mode: "active" as const, deadline_at: now / 1000 - 60, active_seconds_used: 12, active_seconds_remaining: 1788,
      approval: { ...run.approval!, expires_at: now / 1000 + 86400 } };
    expect(durableActionAvailable(active, true, now)).toBe(true);
    expect(durableActionAvailable({ ...active, active_seconds_remaining: 0 }, true, now)).toBe(false);
    expect(durableActionAvailable(active, true, active.approval.expires_at * 1000)).toBe(false);
    expect(sameReviewedRun(active, { ...active, active_seconds_used: 13, active_seconds_remaining: 1787 })).toBe(false);
    expect(sameReviewedRun(active, { ...active, execution_mode: "background" })).toBe(false);
  });
  it("never manually dispatches a background running snapshot", () => {
    const queued = { ...run, status: "running" as const, approval: null, execution_mode: "background" as const };
    expect(durableActionAvailable(queued, true, now)).toBe(false);
    expect(durableActionAvailable({ ...run, execution_mode: "background" }, true, now)).toBe(true);
  });
});
