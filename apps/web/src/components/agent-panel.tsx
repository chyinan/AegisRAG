"use client";

import { Bot, Play, RefreshCw, Square } from "lucide-react";
import { useEffect, useState } from "react";
import { useAgentWorkbench, type AgentNotice } from "@/hooks/use-agent-workbench";
import { authScopeKey } from "@/lib/api/auth-scope";
import { isDurableStartupKey } from "@/lib/api/agent-startup-key";
import type { AgentCitationRef, Citation, AgentExecutionMode, AgentRunResponse, DurableAgentRun, DurableBudgetMode, DurableExecutionMode } from "@/lib/api/types";
import type { AuthSession } from "@/lib/auth";
import { durableActionAvailable, durableBudgetExpired, isDurableTerminal } from "@/lib/agent-workbench-state";
import { durableText } from "@/lib/agent-workbench-copy";
import { agentStatusText, agentText, text, uiText, type Language } from "@/lib/i18n";
import { AnswerMarkdown } from "./answer-markdown";
import { EvidencePanel } from "./evidence-panel";
import { CitationChip, CopyIdButton, PermissionNotice, SafeErrorBanner, StatusPill } from "./primitives";
import { Button } from "./ui/button";
import { Card, CardHeader, CardInset } from "./ui/card";
import { Input } from "./ui/input";
import { Select } from "./ui/select";
import { Textarea } from "./ui/textarea";

type Props = Readonly<{ auth: AuthSession; language: Language; canRun: boolean }>;
export function AgentPanel({ auth, language, canRun }: Props) {
  if (!canRun) return <PermissionNotice permission="agent:run" language={language} />;
  return <AgentPanelContent key={authScopeKey(auth)} auth={auth} language={language} />;
}

function AgentPanelContent({ auth, language }: Omit<Props, "canRun">) {
  const [mode, setMode] = useState<AgentExecutionMode>("one_shot");
  const [input, setInput] = useState("");
  const [maxSteps, setMaxSteps] = useState("8");
  const [maxToolCalls, setMaxToolCalls] = useState("4");
  const [timeout, setTimeout] = useState("30");
  const [budgetMode, setBudgetMode] = useState<DurableBudgetMode>("wall_clock");
  const [dispatchMode, setDispatchMode] = useState<DurableExecutionMode>("inline");
  const [approvalTimeout, setApprovalTimeout] = useState("3600");
  const [approvalTools, setApprovalTools] = useState(["calculator", "rag_search"]);
  const [runId, setRunId] = useState("");
  const [recoveryKey, setRecoveryKey] = useState("");
  const { state, start, read, recover, discardUnresolvedStart, decide, cancel, interrupt } = useAgentWorkbench(auth);
  const busy = state.pending !== null;
  const activeDurable = state.durable !== null && !isDurableTerminal(state.durable);
  const blocked = busy || activeDurable || state.unresolvedStart;
  const activeBudget = mode === "durable" && budgetMode === "active";
  const validBudget = [[maxSteps, 1, 20], [maxToolCalls, 0, 20], [timeout, 1, activeBudget ? 3600 : 120], ...(activeBudget ? [[approvalTimeout, 1, 604800]] : [])].every(([value, min, max]) =>
    typeof value === "string" && /^\d+$/.test(value) && Number(value) >= Number(min) && Number(value) <= Number(max));
  const notice = state.notice === "expired" && state.durable?.budget_mode === "active" ? durableText.activeExpired : noticeText(state.notice);
  const errorText = state.error?.code === "DURABLE_AGENT_DISABLED" ? (state.unresolvedStart ? agentText.disabledRecovery : agentText.disabled)
    : state.error?.code === "DURABLE_BACKGROUND_UNCONFIGURED" ? (state.unresolvedStart || activeDurable ? durableText.backgroundUnavailableRecovery : durableText.backgroundUnavailable)
    : state.error?.code === "AGENT_INVALID_DURABLE_OPTIONS" ? durableText.invalidOptions
    : state.error?.code === "AGENT_SECURE_RANDOM_UNAVAILABLE" ? agentText.secureRandomUnavailable
    : state.error?.code === "AGENT_INVALID_STARTUP_KEY" ? agentText.invalidStartupKey
    : state.error?.code.includes("CONFLICT") || state.error?.code.includes("APPROVAL") ? agentText.conflict : uiText.agentError;
  return (
    <Card as="section" aria-labelledby="agent-console-title">
      <CardHeader><div><h2 id="agent-console-title" className="surface-title">{text(uiText.agentConsoleTitle, language)}</h2><p className="muted">{text(uiText.agentConsoleHelp, language)}</p></div><StatusPill tone="index">agent:run</StatusPill></CardHeader>
      <label><span className="scope-label">{text(agentText.mode, language)}</span>
        <Select value={mode} disabled={blocked} onChange={(event) => setMode(event.target.value as AgentExecutionMode)}>
          <option value="one_shot">{text(agentText.oneShot, language)}</option><option value="live">{text(agentText.live, language)}</option><option value="durable">{text(agentText.durable, language)}</option>
        </Select>
      </label>
      {mode === "live" && <p className="muted">{text(agentText.liveHelp, language)}</p>}
      {mode === "durable" && <CardInset><p>{text(durableText.help, language)}</p><p>{text(agentText.privacy, language)}</p>
        <div className="two-col">
          <label><span className="scope-label">{text(durableText.budget, language)}</span><Select value={budgetMode} disabled={blocked} onChange={(event) => setBudgetMode(event.target.value as DurableBudgetMode)}>
            <option value="wall_clock">{text(durableText.wallClock, language)}</option><option value="active">{text(durableText.active, language)}</option>
          </Select></label>
          <label><span className="scope-label">{text(durableText.dispatch, language)}</span><Select value={dispatchMode} disabled={blocked} onChange={(event) => setDispatchMode(event.target.value as DurableExecutionMode)}>
            <option value="inline">{text(durableText.inline, language)}</option><option value="background">{text(durableText.background, language)}</option>
          </Select></label>
        </div>
        {activeBudget && <p className="muted">{text(durableText.activeHelp, language)}</p>}
        {activeBudget && Number(timeout) > 120 && <p className="muted">{text(durableText.preferBackground, language)}</p>}
        {dispatchMode === "background" && <p className="muted">{text(durableText.backgroundHelp, language)}</p>}
      </CardInset>}
      <label><span className="scope-label">{text(uiText.agentInput, language)}</span><Textarea value={input} maxLength={4000} autoComplete="off" disabled={blocked} onChange={(event) => setInput(event.target.value)} placeholder={text(uiText.agentInputPlaceholder, language)} /></label>
      <p className="muted">{text(activeBudget ? durableText.activeInputHelp : agentText.inputHelp, language)}</p>
      <div className="two-col">
        <BudgetInput label="max_steps" value={maxSteps} onChange={setMaxSteps} disabled={blocked} min={1} max={20} />
        <BudgetInput label="max_tool_calls" value={maxToolCalls} onChange={setMaxToolCalls} disabled={blocked} min={0} max={20} />
        <BudgetInput label="timeout_seconds" value={timeout} onChange={setTimeout} disabled={blocked} min={1} max={activeBudget ? 3600 : 120} />
        {activeBudget && <BudgetInput label={text(durableText.approvalTimeout, language)} value={approvalTimeout} onChange={setApprovalTimeout} disabled={blocked} min={1} max={604800} />}
      </div>
      {mode === "durable" && <fieldset disabled={blocked} className="grid gap-2">
        <legend className="scope-label">{text(agentText.approvalTools, language)}</legend>
        <div className="actions-row">{["calculator", "rag_search"].map((name) => <label key={name} className="actions-row"><input type="checkbox" checked={approvalTools.includes(name)} onChange={(event) => setApprovalTools((current) => event.target.checked ? [...current, name] : current.filter((item) => item !== name))} />{name}</label>)}</div>
        <label className="actions-row"><input type="checkbox" checked disabled readOnly />{text(durableText.supportApproval, language)}</label>
        <p className="muted">{text(durableText.supportHelp, language)}</p>
        <p className="muted">{text(agentText.approvalPolicy, language)}</p>
      </fieldset>}
      <div className="actions-row">
        <Button type="button" variant="primary" disabled={blocked || !validBudget || input.trim().length === 0} onClick={() => void start(mode, { input, max_steps: Number(maxSteps), max_tool_calls: Number(maxToolCalls), timeout_seconds: Number(timeout), metadata: { surface: "main_workbench" }, approval_tools: approvalTools,
          ...(mode !== "durable" ? {} : { ...(budgetMode === "wall_clock" ? {} : { budget_mode: budgetMode, approval_timeout_seconds: Number(approvalTimeout) }), ...(dispatchMode === "inline" ? {} : { execution_mode: dispatchMode }) })
        })}><Play aria-hidden="true" />{text(uiText.runAgent, language)}</Button>
        {busy && <Button type="button" onClick={() => interrupt()}><Square aria-hidden="true" />{text(agentText.stop, language)}</Button>}
      </div>
      {busy && <p role="status">{text(agentText.pending, language)}</p>}
      {activeDurable && <p className="muted">{text(agentText.activeRun, language)}</p>}
      {notice !== null && <CardInset role="status">{text(notice, language)}</CardInset>}
      {state.startupKey !== null && <StartupReference startupKey={state.startupKey} language={language} busy={busy}
        onRead={() => void recover(state.startupKey!)} onDiscard={discardUnresolvedStart} />}
      {state.error !== null && <SafeErrorBanner code={state.error.code} message={text(errorText, language)} requestId={state.error.requestId} />}
      {state.events.length > 0 && <section aria-label={text(agentText.progress, language)} className="grid gap-2">
        <h3 className="scope-label">{text(agentText.progress, language)}</h3>
        <ol className="grid gap-2">{state.events.map((event) => <li key={event.sequence} className="actions-row">
          <span className="id-text">{event.sequence + 1}</span>
          {event.event === "started" ? <StatusPill tone="index">{text(agentStatusText.running, language)}</StatusPill>
            : (event.event === "tool_call" || event.event === "tool_result") ? <StatusPill tone={event.data.status === "error" ? "danger" : "neutral"}>{event.data.tool_name} · {event.data.status}</StatusPill> : null}
          {(event.event === "tool_call" || event.event === "tool_result") && <span className="id-text">{event.data.tool_call_id}{event.data.metadata.latency_ms !== undefined ? ` · ${event.data.metadata.latency_ms} ms` : ""}{event.data.metadata.error_code ? ` · ${event.data.metadata.error_code}` : ""}</span>}
        </li>)}</ol>
        {state.result === null && state.error === null && busy && <p className="muted">{text(agentText.waitingFinal, language)}</p>}
        <CopyIdButton value={state.events[0].request_id} label={text(uiText.copyRequestId, language)} language={language} />
      </section>}
      {state.result !== null && <AgentResult key={state.result.agent_run_id} auth={auth} result={state.result} language={language} />}
      {state.durable !== null && <DurableResult key={`${state.durable.run_id}:${state.durable.revision}`} auth={auth} run={state.durable} language={language} fresh={state.fresh} busy={busy}
        onRead={() => void read(state.durable!.run_id)} onDecision={(decision) => void decide(decision)} onCancel={() => void cancel()} />}
      {mode === "durable" && <CardInset as="section" aria-labelledby="recover-agent-title">
        <h3 id="recover-agent-title" className="surface-title">{text(agentText.recoverTitle, language)}</h3><p className="muted">{text(agentText.recoverHelp, language)}</p>
        <label><span className="scope-label">{text(agentText.runId, language)}</span><Input value={runId} maxLength={128} autoComplete="off" spellCheck={false} disabled={blocked} onChange={(event) => setRunId(event.target.value)} /></label>
        <Button type="button" disabled={blocked || runId.trim().length === 0} onClick={() => void read(runId.trim())}>{text(agentText.read, language)}</Button>
        <p className="muted">{text(agentText.recoverKeyHelp, language)}</p>
        <label><span className="scope-label">{text(agentText.savedStartupKey, language)}</span><Input value={recoveryKey} maxLength={36} autoComplete="off" spellCheck={false} disabled={blocked} onChange={(event) => setRecoveryKey(event.target.value)} /></label>
        <Button type="button" disabled={blocked || !isDurableStartupKey(recoveryKey.trim())} onClick={() => { const key = recoveryKey.trim(); setRecoveryKey(""); void recover(key); }}>{text(agentText.readSavedKey, language)}</Button>
      </CardInset>}
    </Card>
  );
}

function StartupReference({ startupKey, language, busy, onRead, onDiscard }: Readonly<{
  startupKey: string; language: Language; busy: boolean; onRead: () => void; onDiscard: (acknowledged: boolean) => void;
}>) {
  const [acknowledged, setAcknowledged] = useState(false);
  return <CardInset as="section" aria-label={text(agentText.startupKey, language)}>
    <Field label={text(agentText.startupKey, language)} value={startupKey} />
    <p className="muted">{text(agentText.startupKeyHelp, language)}</p>
    <div className="actions-row">
      <CopyIdButton value={startupKey} label={text(agentText.copyStartupKey, language)} language={language} />
      <Button type="button" disabled={busy} onClick={onRead}>{text(agentText.readStartupKey, language)}</Button>
    </div>
    {!busy && <div className="grid gap-2">
      <p>{text(agentText.unresolvedStart, language)}</p>
      <label className="actions-row"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />{text(agentText.discardAcknowledgement, language)}</label>
      <Button type="button" disabled={!acknowledged} onClick={() => onDiscard(acknowledged)}>{text(agentText.discardStartupKey, language)}</Button>
    </div>}
  </CardInset>;
}

function DurableResult({ auth, run, language, fresh, busy, onRead, onDecision, onCancel }: Readonly<{
  auth: AuthSession; run: DurableAgentRun; language: Language; fresh: boolean; busy: boolean;
  onRead: () => void; onDecision: (approval?: boolean) => void; onCancel: () => void;
}>) {
  const [now, setNow] = useState(() => Date.now());
  const terminal = isDurableTerminal(run);
  useEffect(() => {
    if (terminal) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [terminal]);
  const eligible = durableActionAvailable(run, fresh, now) && !busy;
  const activeBudget = run.budget_mode === "active";
  const expired = durableBudgetExpired(run, now);
  return <CardInset as="section" aria-label={text(agentText.latest, language)}>
    <div className="actions-row"><Bot aria-hidden="true" /><StatusPill tone={run.status === "completed" ? "source" : terminal ? "danger" : "index"}>{text(agentStatusText[run.status], language)}</StatusPill><span className="id-text">revision: {run.revision} · {run.backend}</span></div>
    {!fresh && <p role="status">{text(agentText.stale, language)}</p>}
    <div className="two-col">
      <Field label="run_id" value={run.run_id} /><Field label={text(activeBudget ? durableText.windowDeadline : agentText.deadline, language)} value={new Date(run.deadline_at * 1000).toISOString()} />
      <Field label={text(durableText.budgetMode, language)} value={run.budget_mode ?? "wall_clock"} /><Field label={text(durableText.executionMode, language)} value={run.execution_mode ?? "inline"} />
      {activeBudget && <><Field label={text(durableText.activeUsed, language)} value={run.active_seconds_used ?? 0} /><Field label={text(durableText.activeRemaining, language)} value={run.active_seconds_remaining ?? 0} /></>}
      <Field label={text(agentText.stepsUsed, language)} value={run.steps_used} /><Field label={text(agentText.toolsUsed, language)} value={run.tool_calls_used} />
      {run.error_code !== null && <Field label="error_code" value={run.error_code} />}{run.termination_reason !== null && <Field label="termination_reason" value={run.termination_reason} />}
    </div>
    <p className="muted">{text(activeBudget ? durableText.activeBudgetHelp : agentText.budgetHelp, language)}</p>
    {run.status === "intervention_required" ? <p role="alert">{text(agentText.intervention, language)}</p> : terminal ? <p>{text(agentText.terminal, language)}</p> : expired ? <p role="alert">{text(activeBudget ? durableText.activeExpired : agentText.expired, language)}</p> : run.status === "running" ? <p>{text(run.execution_mode === "background" ? durableText.queued : agentText.lease, language)}</p> : null}
    {run.approval !== null && <section className="grid gap-3" aria-labelledby="agent-approval-title">
      <h3 id="agent-approval-title" className="surface-title">{text(agentText.approvalTitle, language)}</h3><p className="muted">{text(agentText.approvalHelp, language)}</p>
      <Field label="tool_name" value={run.approval.tool_name} />
      <div><span className="scope-label">{text(agentText.arguments, language)}</span><pre aria-label={text(agentText.arguments, language)} className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded-md bg-[var(--panel-raised)] p-3 text-sm">{JSON.stringify(run.approval.arguments, null, 2)}</pre></div>
      <div className="two-col"><Field label="approval_id" value={run.approval.approval_id} /><Field label={text(agentText.approvalExpiry, language)} value={new Date(run.approval.expires_at * 1000).toISOString()} /><Field label="action_digest" value={run.approval.action_digest} /><Field label="definition_digest" value={run.approval.definition_digest} /></div>
      <div className="actions-row"><Button type="button" variant="primary" disabled={!eligible} onClick={() => onDecision(true)}>{text(agentText.approve, language)}</Button><Button type="button" disabled={!eligible} onClick={() => onDecision(false)}>{text(agentText.reject, language)}</Button></div>
    </section>}
    <div className="actions-row">
      <CopyIdButton value={run.run_id} label={text(agentText.copyRunId, language)} language={language} />
      <Button type="button" disabled={busy} onClick={onRead}><RefreshCw aria-hidden="true" />{text(agentText.refresh, language)}</Button>
      {run.status === "running" && run.execution_mode !== "background" && <Button type="button" variant="primary" disabled={!eligible} onClick={() => onDecision()}>{text(agentText.resume, language)}</Button>}
      {!terminal && <Button type="button" disabled={busy || !fresh} onClick={onCancel}>{text(agentText.cancel, language)}</Button>}
    </div>
    {run.status === "completed" && fresh && <AgentAnswer auth={auth} answer={run.final_answer} citations={run.final_citations} language={language} />}
  </CardInset>;
}

function AgentResult({ auth, result, language }: Readonly<{ auth: AuthSession; result: AgentRunResponse; language: Language }>) {
  return <CardInset><StatusPill tone={result.status === "completed" ? "source" : "danger"}>{text(agentStatusText[result.status], language)} · {result.termination_reason ?? "run"}</StatusPill>
    <div className="two-col"><Field label="agent_run_id" value={result.agent_run_id} /><Field label="request_id" value={result.request_id} /><Field label="trace_id" value={result.trace_id} /><Field label={text(agentText.stepsUsed, language)} value={result.steps_used} /><Field label={text(agentText.toolsUsed, language)} value={result.tool_calls_used} /></div>
    {result.error_code && <Field label="error_code" value={result.error_code} />}
    {result.status === "completed" && <AgentAnswer auth={auth} answer={result.final_answer ?? null} citations={result.final_citations ?? []} language={language} />}
    <CopyIdButton value={result.request_id} label={text(uiText.copyRequestId, language)} language={language} />
  </CardInset>;
}
function AgentAnswer({ auth, answer, citations, language }: Readonly<{ auth: AuthSession; answer: string | null; citations: AgentCitationRef[]; language: Language }>) {
  const [selected, setSelected] = useState<Citation | null>(null);
  return <div className="grid gap-3">{answer !== null && <AnswerMarkdown answer={answer} citations={citations} onOpenCitation={setSelected} language={language} />}
    {citations.length > 0 && <section aria-label={text(agentText.sourceRefs, language)}><h3 className="scope-label">{text(agentText.sourceRefs, language)}</h3><div className="chip-row">{citations.map((citation, index) => <CitationChip key={`${citation.chunk_id}:${index}`} citation={citation} onOpen={() => setSelected(citation)} language={language} />)}</div></section>}
    {selected !== null && <CardInset><Button type="button" onClick={() => setSelected(null)}>{text(agentText.closeSource, language)}</Button><EvidencePanel auth={auth} citation={selected} language={language} /></CardInset>}
  </div>;
}
function BudgetInput({ label, value, onChange, disabled, min, max }: Readonly<{ label: string; value: string; onChange: (value: string) => void; disabled: boolean; min: number; max: number }>) {
  return <label><span className="scope-label">{label}</span><Input type="number" inputMode="numeric" min={min} max={max} step={1} value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)} /></label>;
}
function Field({ label, value }: Readonly<{ label: string; value: string | number }>) {
  return <div><span className="scope-label">{label}</span><span className="id-text wrap break-all">{value}</span></div>;
}
function noticeText(notice: AgentNotice) {
  switch (notice) {
    case "changed": return agentText.changed;
    case "expired": return agentText.expired;
    case "stream_interrupted": return agentText.streamInterrupted;
    case "request_interrupted": return agentText.requestInterrupted;
    case "start_unknown": return agentText.startUnknown;
    case "recovery_not_found": return agentText.recoveryNotFound;
    case "start_discarded": return agentText.startDiscarded;
    case "durable_unknown": return agentText.durableUnknown;
    case "cancel_confirmed": return agentText.cancelConfirmed;
    default: return null;
  }
}
