import { afterEach, describe, expect, it, vi } from "vitest";
import { PERSONAS } from "@/lib/auth";
import fixtures from "@/test/fixtures/agent-lifecycle.json";
import { getDurableAgent, startDurableAgent } from "./agent-client";
import { decodeDurableAgentRun } from "./agent-contract";
import type { DurableAgentStartRequest } from "./types";

function json(value: unknown) { return new Response(JSON.stringify(value), { headers: { "content-type": "application/json" } }); }
const active = { ...fixtures.durable.awaitingApproval.data, budget_mode: "active", execution_mode: "background", active_seconds_used: 12.5,
  active_seconds_remaining: 1787.5, approval: { ...fixtures.durable.awaitingApproval.data.approval, expires_at: fixtures.clockEpochSeconds + 86400 } };
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("durable execution options protocol", () => {
  it("defaults old response snapshots to wall-clock inline without inventing active counters", () => {
    expect(decodeDurableAgentRun(fixtures.durable.awaitingApproval.data)).toMatchObject({ budget_mode: "wall_clock", execution_mode: "inline", active_seconds_used: null, active_seconds_remaining: null });
  });
  it("reads background active state with approval expiry beyond its execution checkpoint", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ ...fixtures.durable.awaitingApproval, data: active })));
    const run = await getDurableAgent(PERSONAS.platform_admin, active.run_id);
    expect(run).toMatchObject({ budget_mode: "active", execution_mode: "background", active_seconds_used: 12.5, active_seconds_remaining: 1787.5 });
    expect(run.approval!.expires_at).toBeGreaterThan(run.deadline_at);
  });
  it("sends the full active timeout and approval TTL without the one-shot cap", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ ...fixtures.durable.awaitingApproval, data: active })); vi.stubGlobal("fetch", fetchMock);
    await startDurableAgent(PERSONAS.platform_admin, { input: "Review", budget_mode: "active", execution_mode: "background", timeout_seconds: 3600, approval_timeout_seconds: 604800 });
    expect(JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)).toMatchObject({ timeout_seconds: 3600, budget_mode: "active", execution_mode: "background", approval_timeout_seconds: 604800 });
  });
  it.each([
    { budget_mode: "active", timeout_seconds: 3601 }, { budget_mode: "wall_clock", timeout_seconds: 121 },
    { budget_mode: "wall_clock", approval_timeout_seconds: 60 }, { budget_mode: "active", approval_timeout_seconds: 604801 },
    { budget_mode: "active", approval_timeout_seconds: 0 }, { budget_mode: "active", timeout_seconds: Infinity },
    { budget_mode: "other" }, { execution_mode: "other" }
  ])("rejects unsupported start options before dispatch: %j", (options) => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    expect(() => startDurableAgent(PERSONAS.platform_admin, { input: "Review", ...options } as DurableAgentStartRequest)).toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it.each([
    { active_seconds_remaining: null }, { active_seconds_used: null }, { active_seconds_remaining: -1 },
    { active_seconds_remaining: 3601 }, { active_seconds_used: Infinity }, { budget_mode: "other" }, { execution_mode: "other" }
  ])("rejects an incomplete or unsupported active response: %j", (patch) => {
    expect(() => decodeDurableAgentRun({ ...active, ...patch })).toThrow();
  });
  it("retains an exhausted active checkpoint even when lease accounting exceeded the initial budget", () => {
    expect(decodeDurableAgentRun({ ...active, status: "stopped", approval: null, active_seconds_used: 3602, active_seconds_remaining: 0 })).toMatchObject({ active_seconds_used: 3602, active_seconds_remaining: 0 });
  });
});
