"use client";

import { useEffect, useRef, useState } from "react";
import type { AuthSession } from "@/lib/auth";
import { ApiClientError } from "@/lib/api/client";
import { AgentProtocolError } from "@/lib/api/agent-contract";
import { cancelDurableAgent, getDurableAgent, recoverDurableAgent, resumeDurableAgent, runAgentOnce, startDurableAgent, streamAgent } from "@/lib/api/agent-client";
import type { AgentExecutionMode, AgentRunResponse, AgentStreamEvent, DurableAgentRun, DurableAgentStartRequest } from "@/lib/api/types";
import { createDurableStartupKey, durableStartupKey } from "@/lib/api/agent-startup-key";
import { authScopeKey } from "@/lib/api/auth-scope";
import { durableActionAvailable, isDurableTerminal, sameReviewedRun } from "@/lib/agent-workbench-state";

type Operation = "one_shot" | "live" | "start" | "read" | "recover" | "resume" | "approve" | "reject" | "cancel";
export type AgentNotice = "stream_interrupted" | "request_interrupted" | "start_unknown" | "recovery_not_found" | "start_discarded" | "durable_unknown" | "changed" | "expired" | "cancel_confirmed" | null;
type Failure = { code: string; requestId?: string };
export type AgentWorkbenchState = {
  pending: Operation | null;
  result: AgentRunResponse | null;
  events: AgentStreamEvent[];
  durable: DurableAgentRun | null;
  fresh: boolean;
  startupKey: string | null;
  unresolvedStart: boolean;
  error: Failure | null;
  notice: AgentNotice;
};
const INITIAL: AgentWorkbenchState = { pending: null, result: null, events: [], durable: null, fresh: false, startupKey: null, unresolvedStart: false, error: null, notice: null };
type OwnedOperation = { kind: Operation; scope: string; controller: AbortController; timer: ReturnType<typeof setTimeout> };

function executionWaitMs(request: Pick<DurableAgentStartRequest, "budget_mode" | "execution_mode" | "timeout_seconds">) {
  if (request.execution_mode === "background") return 45_000;
  if (request.budget_mode !== "active") return 135_000;
  const seconds = request.timeout_seconds ?? 30;
  return ((Number.isFinite(seconds) ? Math.min(Math.max(seconds, 1), 3600) : 30) + 15) * 1000;
}

export function useAgentWorkbench(auth: AuthSession) {
  const scope = authScopeKey(auth);
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const [ownedState, setState] = useState({ scope, value: INITIAL });
  const state = ownedState.scope === scope ? ownedState.value : INITIAL;
  const stateRef = useRef(state);
  const mounted = useRef(false);
  const active = useRef<OwnedOperation | null>(null);

  function update(patch: Partial<AgentWorkbenchState>) {
    stateRef.current = { ...stateRef.current, ...patch };
    setState({ scope, value: stateRef.current });
  }
  function owns(operation: OwnedOperation) {
    return mounted.current && active.current === operation && operation.scope === currentScope.current && !operation.controller.signal.aborted;
  }
  function interrupt(timeout = false) {
    const operation = active.current;
    if (operation === null) return;
    active.current = null;
    clearTimeout(operation.timer);
    operation.controller.abort();
    const notice: AgentNotice = (operation.kind === "start" || operation.kind === "recover") ? "start_unknown"
      : operation.kind === "live" ? "stream_interrupted"
      : operation.kind === "one_shot" ? "request_interrupted" : "durable_unknown";
    update({ pending: null, fresh: false, notice, error: timeout ? { code: "AGENT_REQUEST_TIMEOUT" } : null });
  }
  function begin(kind: Operation, waitMs = 135_000): OwnedOperation | null {
    // A synchronous owner guard also rejects same-frame double clicks before React renders.
    if (!mounted.current || scope !== currentScope.current || active.current !== null) return null;
    const controller = new AbortController();
    const operation: OwnedOperation = { kind, scope, controller, timer: setTimeout(() => { if (active.current === operation) interrupt(true); }, (kind === "read" || kind === "recover") ? 12_000 : waitMs) };
    active.current = operation;
    update({ pending: kind, error: null, notice: null, fresh: false });
    return operation;
  }
  function finish(operation: OwnedOperation, patch: Partial<AgentWorkbenchState>) {
    if (!owns(operation)) return;
    clearTimeout(operation.timer);
    active.current = null;
    update({ ...patch, pending: null });
  }
  function fail(operation: OwnedOperation, error: unknown) {
    if (!owns(operation)) return;
    const structured = error instanceof ApiClientError || error instanceof AgentProtocolError ? error.structured : undefined;
    const code = typeof structured?.code === "string" && /^[A-Z][A-Z0-9_]{0,79}$/.test(structured.code) ? structured.code : "AGENT_REQUEST_FAILED";
    // HTTP 401/403/422 can surface during post-commit result revalidation.
    // Only documented pre-start factory guards and local validation prove no dispatch.
    const knownRejection = (error instanceof ApiClientError && ["DURABLE_AGENT_DISABLED", "DURABLE_BACKGROUND_UNCONFIGURED"].includes(code)) ||
      (error instanceof AgentProtocolError && ["AGENT_INVALID_DURABLE_OPTIONS", "AGENT_INVALID_APPROVAL_TOOLS", "AGENT_INVALID_STARTUP_KEY"].includes(code));
    const unresolvedStart = stateRef.current.unresolvedStart && !(operation.kind === "start" && knownRejection);
    const notice: AgentNotice = operation.kind === "recover" && error instanceof ApiClientError && error.status === 404 ? "recovery_not_found"
      : unresolvedStart ? "start_unknown"
      : ["approve", "resume", "reject", "cancel"].includes(operation.kind) ? "durable_unknown" : null;
    finish(operation, { error: { code, requestId: structured?.request_id }, fresh: false, notice,
      unresolvedStart, startupKey: unresolvedStart ? stateRef.current.startupKey : null });
  }

  async function start(mode: AgentExecutionMode, request: DurableAgentStartRequest) {
    if (stateRef.current.unresolvedStart || (stateRef.current.durable !== null && !isDurableTerminal(stateRef.current.durable))) return;
    const operation = begin(mode === "durable" ? "start" : mode, mode === "durable" ? executionWaitMs(request) : 135_000);
    if (operation === null) return;
    update({ result: null, events: [], durable: null, startupKey: null, unresolvedStart: false });
    try {
      if (mode === "one_shot") {
        const result = await runAgentOnce(auth, request, operation.controller.signal);
        finish(operation, { result });
      } else if (mode === "durable") {
        // Generate and retain the reference before dispatch. Each explicit new start
        // gets a new key; an uncertain start is never automatically resubmitted.
        const startupKey = createDurableStartupKey();
        update({ startupKey, unresolvedStart: true });
        const durable = await startDurableAgent(auth, { ...request, idempotency_key: startupKey }, operation.controller.signal);
        finish(operation, { durable, fresh: true, startupKey: null, unresolvedStart: false });
      } else {
        for await (const event of streamAgent(auth, request, operation.controller.signal)) {
          if (!owns(operation)) return;
          if (event.event === "final") {
            finish(operation, { result: event.data });
          } else if (event.event === "error") {
            finish(operation, { error: { code: event.data.code, requestId: event.request_id } });
          } else {
            update({ events: [...stateRef.current.events, event] });
          }
        }
      }
    } catch (error) { fail(operation, error); }
  }

  async function read(runId: string) {
    // A different run ID cannot reconcile an unidentified start or replace a live run.
    if (stateRef.current.unresolvedStart || (stateRef.current.durable !== null &&
        !isDurableTerminal(stateRef.current.durable) && stateRef.current.durable.run_id !== runId)) return;
    const operation = begin("read");
    if (operation === null) return;
    update({ result: null, events: [] });
    try {
      const durable = await getDurableAgent(auth, runId, operation.controller.signal);
      finish(operation, { durable, fresh: true });
    } catch (error) { fail(operation, error); }
  }

  async function recover(key: string) {
    if (stateRef.current.durable !== null && !isDurableTerminal(stateRef.current.durable)) return;
    const operation = begin("recover");
    if (operation === null) return;
    try {
      const startupKey = durableStartupKey(key);
      // Never let a different lookup silently discard a possibly committed start.
      if (stateRef.current.unresolvedStart && stateRef.current.startupKey !== startupKey) {
        throw new AgentProtocolError("AGENT_UNRESOLVED_START");
      }
      update({ startupKey, unresolvedStart: true, durable: null, result: null, events: [] });
      const durable = await recoverDurableAgent(auth, startupKey, operation.controller.signal);
      finish(operation, { durable, fresh: true, startupKey: null, unresolvedStart: false });
    } catch (error) { fail(operation, error); }
  }

  function discardUnresolvedStart(acknowledged: boolean) {
    if (!mounted.current || active.current !== null || !acknowledged || !stateRef.current.unresolvedStart) return;
    // Local only: explicitly acknowledging uncertainty never cancels server work.
    update({ startupKey: null, unresolvedStart: false, error: null, notice: "start_discarded" });
  }

  async function decide(approve?: boolean) {
    const shown = stateRef.current.durable;
    if (shown === null || !durableActionAvailable(shown, stateRef.current.fresh) ||
        (shown.status === "awaiting_approval") !== (typeof approve === "boolean")) return;
    const operation = begin(approve === undefined ? "resume" : approve ? "approve" : "reject", executionWaitMs({
      budget_mode: shown.budget_mode, execution_mode: shown.execution_mode, timeout_seconds: shown.active_seconds_remaining ?? undefined
    }));
    if (operation === null) return;
    try {
      const latest = await getDurableAgent(auth, shown.run_id, operation.controller.signal);
      if (!owns(operation)) return;
      if (!sameReviewedRun(shown, latest)) {
        finish(operation, { durable: latest, fresh: true, notice: "changed" });
        return;
      }
      if (!durableActionAvailable(latest, true)) {
        finish(operation, { durable: latest, fresh: true, notice: "expired" });
        return;
      }
      const durable = await resumeDurableAgent(auth, latest.run_id, {
        expected_revision: latest.revision,
        ...(latest.approval === null ? {} : { approval_id: latest.approval.approval_id, approve })
      }, operation.controller.signal);
      finish(operation, { durable, fresh: true, notice: durable.status === "cancelled" ? "cancel_confirmed" : null });
    } catch (error) { fail(operation, error); }
  }

  async function cancel() {
    const shown = stateRef.current.durable;
    if (shown === null || !stateRef.current.fresh || isDurableTerminal(shown)) return;
    const operation = begin("cancel");
    if (operation === null) return;
    try {
      const durable = await cancelDurableAgent(auth, shown.run_id, shown.revision, operation.controller.signal);
      finish(operation, { durable, fresh: true, notice: durable.status === "cancelled" ? "cancel_confirmed" : null });
    } catch (error) { fail(operation, error); }
  }

  useEffect(() => {
    mounted.current = true;
    stateRef.current = INITIAL;
    return () => {
      mounted.current = false;
      stateRef.current = INITIAL;
      if (active.current !== null) {
        clearTimeout(active.current.timer);
        active.current.controller.abort();
        active.current = null;
      }
    };
  }, [scope]);

  return { state, start, read, recover, discardUnresolvedStart, decide, cancel, interrupt };
}
