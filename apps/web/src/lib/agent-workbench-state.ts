import type { DurableAgentRun } from "./api/types";

export function isDurableTerminal(run: DurableAgentRun): boolean {
  return run.status !== "running" && run.status !== "awaiting_approval";
}

export function durableActionAvailable(run: DurableAgentRun, fresh: boolean, now = Date.now()): boolean {
  return fresh && !isDurableTerminal(run) && !durableBudgetExpired(run, now) &&
    !(run.status === "running" && run.execution_mode === "background");
}

export function durableBudgetExpired(run: DurableAgentRun, now = Date.now()): boolean {
  // Active counters are server-checkpointed, never a client-side reset/countdown.
  const budgetExpired = run.budget_mode === "active"
    ? !(typeof run.active_seconds_remaining === "number" && run.active_seconds_remaining > 0)
    : run.deadline_at * 1000 <= now;
  return budgetExpired || (run.approval !== null && run.approval.expires_at * 1000 <= now);
}

/** Approval covers precisely the view the user read. A changed view requires a new click. */
export function sameReviewedRun(shown: DurableAgentRun, fresh: DurableAgentRun): boolean {
  return shown.run_id === fresh.run_id && shown.revision === fresh.revision && shown.status === fresh.status && shown.backend === fresh.backend &&
    shown.deadline_at === fresh.deadline_at && shown.steps_used === fresh.steps_used && shown.tool_calls_used === fresh.tool_calls_used &&
    (shown.budget_mode ?? "wall_clock") === (fresh.budget_mode ?? "wall_clock") &&
    (shown.execution_mode ?? "inline") === (fresh.execution_mode ?? "inline") &&
    (shown.active_seconds_used ?? null) === (fresh.active_seconds_used ?? null) &&
    (shown.active_seconds_remaining ?? null) === (fresh.active_seconds_remaining ?? null) &&
    JSON.stringify(shown.approval) === JSON.stringify(fresh.approval);
}
