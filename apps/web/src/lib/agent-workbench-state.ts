import type { DurableAgentRun } from "./api/types";

export function isDurableTerminal(run: DurableAgentRun): boolean {
  return run.status !== "running" && run.status !== "awaiting_approval";
}

export function durableActionAvailable(run: DurableAgentRun, fresh: boolean, now = Date.now()): boolean {
  return fresh && !isDurableTerminal(run) && run.deadline_at * 1000 > now &&
    (run.approval === null || run.approval.expires_at * 1000 > now);
}

/** Approval covers precisely the view the user read. A changed view requires a new click. */
export function sameReviewedRun(shown: DurableAgentRun, fresh: DurableAgentRun): boolean {
  return shown.run_id === fresh.run_id && shown.revision === fresh.revision && shown.status === fresh.status && shown.backend === fresh.backend &&
    shown.deadline_at === fresh.deadline_at && shown.steps_used === fresh.steps_used && shown.tool_calls_used === fresh.tool_calls_used &&
    JSON.stringify(shown.approval) === JSON.stringify(fresh.approval);
}
