import { afterEach, expect, it, vi } from "vitest";
import { listFolderCandidates, loadBudgetSummary, loadCostSummary, listOwnedTickets, transitionTicket, type SupportTicket } from "./operations-client";
import { PERSONAS } from "@/lib/auth";
const signal = () => new AbortController().signal;
const response = (data: unknown) => new Response(JSON.stringify({
  request_id: "req-operation",
  data,
  error: null
}), {
  headers: {
    "Content-Type": "application/json"
  }
});
const stored: SupportTicket = {
  ticket_id: "11111111-1111-4111-8111-111111111111",
  tenant_id: "tenant-demo-alpha",
  user_id: "owner",
  created_by: "owner",
  client_reference: "22222222-2222-4222-8222-222222222222",
  title: "Keep title",
  description: "Keep description exactly\nlocal prose",
  priority: "normal",
  status: "open",
  revision: 2,
  tool_version: "1",
  created_at: "2026-10-15T00:00:00Z",
  updated_at: "2026-10-15T00:00:00Z"
};
afterEach(() => vi.unstubAllGlobals());
it("keeps only candidate identifiers and configured-root URI from the candidate DTO", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
    revision: 4,
    total: 1,
    items: [{
      document_id: "doc-1",
      version_id: "v-1",
      source_uri: "folder://folder/notes.txt",
      unused_metadata: "omit"
    }],
    next_offset: null
  })));
  const page = await listFolderCandidates(PERSONAS.knowledge_manager, "plan-1", 0, signal());
  expect(page.items).toEqual([{
    document_id: "doc-1",
    version_id: "v-1",
    source_uri: "folder://folder/notes.txt"
  }]);
});
it("preserves exact decimal budget strings and absent configuration as null", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
    currency: "USD",
    period: "2026-10",
    limit: null,
    spent: "0.000000001",
    reserved: "0.000000002",
    remaining: null,
    over_budget: false
  })));
  expect(await loadBudgetSummary(PERSONAS.auditor, signal())).toEqual({
    currency: "USD",
    period: "2026-10",
    limit: null,
    spent: "0.000000001",
    reserved: "0.000000002",
    remaining: null,
    over_budget: false
  });
});
it("rejects a missing monetary value instead of substituting zero", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
    currency: "USD",
    period: "2026-10",
    limit: null,
    reserved: "1",
    remaining: null,
    over_budget: false
  })));
  await expect(loadBudgetSummary(PERSONAS.auditor, signal())).rejects.toMatchObject({
    structured: {
      code: "OPERATIONS_INVALID_RESPONSE"
    }
  });
});
it("rejects incomplete summary DTOs instead of rendering a successful empty cost view", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
    currency: "USD",
    calls: 0,
    priced_cost: "0"
  })));
  await expect(loadCostSummary(PERSONAS.auditor, "", "", signal())).rejects.toMatchObject({
    structured: {
      code: "OPERATIONS_INVALID_RESPONSE"
    }
  });
});
it("loads stored ticket prose through a typed allowlist", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
    items: [{
      ...stored,
      unused_metadata: "omit"
    }]
  })));
  expect(await listOwnedTickets(PERSONAS.employee, signal())).toEqual([stored]);
});
it("requires an exact next-revision ticket response before reporting update success", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
    ...stored,
    status: "in_progress",
    revision: 2
  })));
  await expect(transitionTicket(PERSONAS.platform_admin, stored, "in_progress", signal())).rejects.toMatchObject({
    structured: {
      code: "OPERATIONS_INVALID_RESPONSE"
    }
  });
});
