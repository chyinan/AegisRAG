import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { WorkbenchShell } from "./workbench-shell";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CostPanel, SupportTicketsPanel } from "./business-operations";
import { FolderSyncPanel, QueueOperationsPanel } from "./source-operations";
import { PERSONAS, type AuthSession } from "@/lib/auth";
import type { BudgetSummary, CostSummary, FolderPlan, QueueItem, SupportTicket } from "@/lib/api/operations-client";
const auth: AuthSession = {
  ...PERSONAS.platform_admin,
  permissions: [...PERSONAS.platform_admin.permissions, "cost:read", "support:ticket:read", "support:ticket:manage"]
};
const plan: FolderPlan = {
  id: "plan-1",
  connector_id: "folder",
  enabled: true,
  interval_seconds: 300,
  batch_size: 25,
  delete_missing: true,
  status: "completed",
  phase: "scan",
  cycle: 2,
  imported_count: 3,
  skipped_count: 4,
  deleted_count: 0,
  managed_count: 7,
  revision: 8,
  candidate_count: 2,
  error_code: null,
  next_run_at: 1000
};
const queue: QueueItem = {
  job_kind: "ingestion",
  job_id: "job-1",
  document_id: "doc-1",
  version_id: "v-1",
  queue_job_id: "rq-1",
  queue_name: "ingestion",
  queue_status: "failed",
  state: "failed",
  domain_status: "failed_retryable",
  attempt_count: 2,
  retries_remaining: 1,
  age_seconds: 1000,
  last_heartbeat_at: null,
  error_code: "WORKER_FAILURE",
  retry_allowed: true,
  terminate_allowed: true
};
const cost: CostSummary = {
  currency: "USD",
  start: "2026-10-01T00:00:00Z",
  end: "2026-10-16T00:00:00Z",
  calls: 4,
  successful_calls: 2,
  failed_calls: 1,
  incomplete_calls: 1,
  unknown_usage_calls: 1,
  unpriced_calls: 1,
  input_tokens: 12,
  output_tokens: 5,
  total_tokens: 17,
  priced_cost: "0.000123456789",
  pending_reserved_cost: "0.010000001",
  cost_complete: false,
  success_rate: 0.5,
  average_latency_ms: null,
  priced_cost_per_success: null,
  breakdown: [{
    operation: "agent",
    provider: "fake",
    model: "fixture",
    price_version: null,
    calls: 4,
    input_tokens: 12,
    output_tokens: 5,
    total_tokens: 17,
    priced_cost: "0.000123456789",
    unpriced_calls: 1
  }]
};
const budget: BudgetSummary = {
  currency: "USD",
  period: "2026-10",
  limit: null,
  spent: "0.000123456789",
  reserved: "0.010000001",
  remaining: null,
  over_budget: false
};
const ticket: SupportTicket = {
  ticket_id: "11111111-1111-4111-8111-111111111111",
  tenant_id: "tenant-demo-alpha",
  user_id: "ticket-owner",
  created_by: "ticket-owner",
  client_reference: "22222222-2222-4222-8222-222222222222",
  title: "Local support title <script>literal</script>",
  description: "Local stored ticket description\nSecond line",
  priority: "normal",
  status: "open",
  revision: 3,
  tool_version: "1",
  created_at: "2026-10-15T00:00:00Z",
  updated_at: "2026-10-15T00:00:00Z"
};
const response = (data: unknown, status = 200) => new Response(JSON.stringify({
  request_id: "req-1",
  data,
  error: null
}), {
  status,
  headers: {
    "Content-Type": "application/json"
  }
});
const error = (code: string) => new Response(JSON.stringify({
  request_id: "req-conflict",
  data: null,
  error: {
    code,
    message: "provider details must not appear"
  }
}), {
  status: 409,
  headers: {
    "Content-Type": "application/json"
  }
});
const queuePage = (item = queue, next: string | null = null) => ({
  items: [item],
  next_cursor: next,
  scanned_count: 1,
  request_id: "req-list",
  trace_id: "trace-list"
});
function mockFetch(handler: (url: string, init: RequestInit | undefined) => Response | Promise<Response>) {
  const fn = vi.fn((input: string, init?: RequestInit) => Promise.resolve(handler(String(input), init)));
  vi.stubGlobal("fetch", fn);
  return fn;
}
function field(container: HTMLElement, label: string) {
  return within(container).getByText(label).parentElement!;
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
describe("folder sync controls", () => {
  it("saves only configured-root settings and displays real returned plan state", async () => {
    const fetch = mockFetch(() => response(plan));
    render(<FolderSyncPanel auth={auth} language="en" />);
    fireEvent.change(screen.getByLabelText("Sync interval (seconds)"), {
      target: {
        value: "20"
      }
    });
    expect(screen.getByRole("button", {
      name: "Save configured-root sync plan"
    })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Sync interval (seconds)"), {
      target: {
        value: "600"
      }
    });
    await userEvent.click(screen.getByLabelText("Collect missing-file deletion candidates"));
    await userEvent.click(screen.getByRole("button", {
      name: "Save configured-root sync plan"
    }));
    await screen.findByRole("button", {
      name: "Run one sync batch"
    });
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("/api/backend/sources/folder/sync/plan");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(init?.body as string)).toEqual({
      connector_id: "folder",
      enabled: true,
      interval_seconds: 600,
      batch_size: 25,
      delete_missing: true
    });
    expect(screen.getByText("completed · scan")).toBeInTheDocument();
  });
  it("requires exact selected document IDs and the candidate revision before soft-delete", async () => {
    const fetch = mockFetch(url => url.endsWith("/plans") ? response([plan]) : url.includes("/candidates") ? response({
      revision: 9,
      total: 2,
      items: [{
        document_id: "doc-1",
        version_id: "v-1",
        source_uri: "folder://folder/a.txt"
      }, {
        document_id: "doc-2",
        version_id: "v-2",
        source_uri: "folder://folder/b.txt"
      }],
      next_offset: null
    }) : response({
      ...plan,
      revision: 10,
      deleted_count: 1,
      candidate_count: 1
    }));
    render(<FolderSyncPanel auth={auth} language="en" />);
    await userEvent.click(screen.getByRole("button", {
      name: "Refresh sync plans"
    }));
    await userEvent.click(await screen.findByRole("button", {
      name: "Load deletion candidates"
    }));
    await screen.findByLabelText("Select doc-1");
    expect(fetch.mock.calls.some(([url]) => url.includes("/reconcile"))).toBe(false);
    await userEvent.click(screen.getByLabelText("Select doc-2"));
    await userEvent.click(screen.getByRole("button", {
      name: "Review selected soft-deletions"
    }));
    const confirm = screen.getByRole("group", {
      name: "Confirm selected soft-deletions"
    });
    expect(field(confirm, "expected_revision")).toHaveTextContent("9");
    expect(within(confirm).getByText("doc-2")).toBeInTheDocument();
    expect(within(confirm).queryByText("doc-1")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", {
      name: "Confirm soft-delete selected documents"
    }));
    await screen.findByText(/Selected documents soft-deleted/);
    const request = fetch.mock.calls.find(([url]) => url.endsWith("/reconcile"))!;
    expect(request[0]).toBe("/api/execution/sources/folder/sync/plans/plan-1/reconcile");
    expect(JSON.parse(request[1]?.body as string)).toEqual({
      expected_revision: 9,
      document_ids: ["doc-2"]
    });
    expect(screen.queryByLabelText("Select doc-2")).not.toBeInTheDocument();
  });
  it("changes candidate pages without carrying selection or confirmation forward", async () => {
    const fetch = mockFetch(url => url.endsWith("/plans") ? response([plan]) : response({
      revision: 8,
      total: 26,
      items: [{
        document_id: url.includes("offset=25") ? "doc-last" : "doc-first",
        version_id: "v",
        source_uri: "folder://folder/doc.txt"
      }],
      next_offset: url.includes("offset=25") ? null : 25
    }));
    render(<FolderSyncPanel auth={auth} language="en" />);
    await userEvent.click(screen.getByRole("button", {
      name: "Refresh sync plans"
    }));
    await userEvent.click(await screen.findByRole("button", {
      name: "Load deletion candidates"
    }));
    await userEvent.click(await screen.findByLabelText("Select doc-first"));
    await userEvent.click(screen.getByRole("button", {
      name: "Review selected soft-deletions"
    }));
    await userEvent.click(screen.getByRole("button", {
      name: "Next candidates"
    }));
    expect(await screen.findByLabelText("Select doc-last")).not.toBeChecked();
    expect(screen.queryByRole("group", {
      name: "Confirm selected soft-deletions"
    })).not.toBeInTheDocument();
    expect(fetch.mock.calls.at(-1)?.[0]).toContain("offset=25&limit=25");
  });
  it("clears stale mutation targets after a conflict and requires a refresh", async () => {
    mockFetch(url => url.endsWith("/plans") ? response([plan]) : error("FOLDER_SYNC_CHANGED"));
    render(<FolderSyncPanel auth={auth} language="en" />);
    await userEvent.click(screen.getByRole("button", {
      name: "Refresh sync plans"
    }));
    await userEvent.click(await screen.findByRole("button", {
      name: "Run one sync batch"
    }));
    expect(await screen.findByRole("alert")).toHaveTextContent("FOLDER_SYNC_CHANGED");
    expect(screen.queryByRole("button", {
      name: "Run one sync batch"
    })).not.toBeInTheDocument();
    expect(screen.queryByText("provider details must not appear")).not.toBeInTheDocument();
  });
});
describe("queue operator controls", () => {
  it("uses the selected delivery counter/status and guards repeated retry clicks", async () => {
    let resolve!: (value: Response) => void;
    const fetch = mockFetch(url => url.includes("/retry") ? new Promise<Response>(done => {
      resolve = done;
    }) : response(queuePage()));
    render(<QueueOperationsPanel auth={auth} language="en" />);
    await userEvent.click(screen.getByRole("button", {
      name: "Refresh queue jobs"
    }));
    const retry = await screen.findByRole("button", {
      name: "Retry rq-1"
    });
    act(() => {
      fireEvent.click(retry);
      fireEvent.click(retry);
    });
    expect(fetch.mock.calls.filter(([url]) => url.endsWith("/retry"))).toHaveLength(1);
    expect(screen.queryByRole("button", {
      name: "Retry rq-1"
    })).not.toBeInTheDocument();
    await act(async () => {
      resolve(response({
        status: "queued",
        job_kind: "ingestion",
        job_id: "job-1",
        queue_job_id: "rq-1",
        queue_name: "ingestion",
        attempt_count: 2,
        retries_remaining: 0,
        request_id: "req-operation",
        trace_id: "trace-operation"
      }));
      await Promise.resolve();
    });
    expect(await screen.findByText("queued")).toBeInTheDocument();
    const request = fetch.mock.calls.find(([url]) => url.endsWith("/retry"))!;
    expect(JSON.parse(request[1]?.body as string)).toEqual({
      expected_attempt_count: 2,
      expected_queue_status: "failed"
    });
    expect(screen.getByText(/it never resets domain attempts/)).toBeInTheDocument();
  });
  it("labels running-worker stop acknowledgement as unconfirmed termination", async () => {
    const fetch = mockFetch(url => url.endsWith("/terminate") ? response({
      status: "stop_requested",
      job_kind: "ingestion",
      job_id: "job-1",
      queue_job_id: "rq-1",
      queue_name: "ingestion",
      attempt_count: 2,
      retries_remaining: 1,
      request_id: "req-operation",
      trace_id: "trace-operation"
    }) : response(queuePage({
      ...queue,
      queue_status: "started",
      state: "stuck",
      retry_allowed: false
    })));
    render(<QueueOperationsPanel auth={auth} language="en" />);
    await userEvent.click(screen.getByRole("button", {
      name: "Refresh queue jobs"
    }));
    await userEvent.click(await screen.findByRole("button", {
      name: "Review termination rq-1"
    }));
    const group = screen.getByRole("group", {
      name: "Confirm queue termination"
    });
    expect(field(group, "expected_queue_status")).toHaveTextContent("started");
    expect(fetch.mock.calls.some(([url]) => url.endsWith("/terminate"))).toBe(false);
    await userEvent.click(screen.getByRole("button", {
      name: "Confirm terminate delivery"
    }));
    expect(await screen.findByText(/Termination is not confirmed/)).toBeInTheDocument();
    expect(screen.getByText("stop_requested")).toBeInTheDocument();
  });
  it("disables exhausted retries and follows the server pagination cursor", async () => {
    const fetch = mockFetch(() => response(queuePage({
      ...queue,
      retries_remaining: 0,
      retry_allowed: false
    }, "next-page")));
    render(<QueueOperationsPanel auth={auth} language="en" />);
    await userEvent.click(screen.getByRole("button", {
      name: "Refresh queue jobs"
    }));
    expect(await screen.findByRole("button", {
      name: "Retry rq-1"
    })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", {
      name: "Next queue page"
    }));
    await waitFor(() => expect(fetch.mock.calls.at(-1)?.[0]).toContain("cursor=next-page"));
  });
});
describe("cost and ticket controls", () => {
  it("shows exact known costs, holds, partial coverage and unknown metrics without zero substitution", async () => {
    mockFetch(url => response(url.includes("/budget") ? budget : cost));
    render(<CostPanel auth={auth} language="en" />);
    await userEvent.click(screen.getByRole("button", {
      name: "Refresh costs and budget"
    }));
    expect(await screen.findByText("Partial cost coverage")).toBeInTheDocument();
    expect(screen.getByText(/Unknown usage and unpriced calls are not zero-cost calls/)).toBeInTheDocument();
    expect(screen.getByText("No budget limit configured")).toBeInTheDocument();
    expect(screen.getAllByText("0.000123456789")).toHaveLength(3);
    expect(screen.getAllByText("0.010000001")).toHaveLength(2);
    expect(screen.getByText("average_latency_ms").parentElement).toHaveTextContent("unknown");
    expect(screen.getByText("priced_cost_per_success").parentElement).toHaveTextContent("unknown");
  });
  it("validates cost window dates and sends their timezone intact", async () => {
    const fetch = mockFetch(url => response(url.includes("/budget") ? budget : cost));
    render(<CostPanel auth={auth} language="en" />);
    fireEvent.change(screen.getByLabelText("Cost start (ISO with timezone)"), {
      target: {
        value: "2026-10-01"
      }
    });
    expect(screen.getByRole("button", {
      name: "Refresh costs and budget"
    })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Cost start (ISO with timezone)"), {
      target: {
        value: "2026-10-01T00:00:00+08:00"
      }
    });
    await userEvent.click(screen.getByRole("button", {
      name: "Refresh costs and budget"
    }));
    expect(fetch.mock.calls.find(([url]) => url.includes("/summary"))?.[0]).toContain("start=2026-10-01T00%3A00%3A00%2B08%3A00");
  });
  it("loads actual own ticket prose as text and does not offer direct ticket creation", async () => {
    const fetch = mockFetch(() => response({
      items: [ticket]
    }));
    render(<SupportTicketsPanel auth={{
      ...auth,
      permissions: ["support:ticket:read"]
    }} language="en" />);
    await userEvent.click(screen.getByRole("button", {
      name: "Refresh tickets"
    }));
    expect(await screen.findByText(ticket.title)).toBeInTheDocument();
    expect(screen.getByText(/Local stored ticket description/)).toHaveTextContent("Second line");
    expect(fetch.mock.calls[0][0]).toBe("/api/backend/support-tickets?limit=50");
    expect(screen.queryByRole("button", {
      name: /Review status/
    })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", {
      name: /Create/
    })).not.toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
  });
  it("looks up managed tickets and confirms the current exact revision transition", async () => {
    const fetch = mockFetch((_url, init) => response(init?.method === "PATCH" ? {
      ...ticket,
      status: "in_progress",
      revision: 4
    } : {
      items: [ticket]
    }));
    render(<SupportTicketsPanel auth={{
      ...auth,
      permissions: ["support:ticket:manage"]
    }} language="en" />);
    fireEvent.change(screen.getByLabelText("Exact ticket ID (optional UUID)"), {
      target: {
        value: ticket.ticket_id
      }
    });
    await userEvent.click(screen.getByRole("button", {
      name: "Refresh tickets"
    }));
    expect(fetch.mock.calls[0][0]).toBe(`/api/backend/support-tickets/manage?limit=50&ticket_id=${ticket.ticket_id}`);
    await userEvent.click(await screen.findByRole("button", {
      name: `Review status ${ticket.ticket_id}`
    }));
    const group = screen.getByRole("group", {
      name: "Confirm ticket transition"
    });
    expect(field(group, "expected_revision")).toHaveTextContent("3");
    expect(field(group, "next_status")).toHaveTextContent("in_progress");
    await userEvent.click(screen.getByRole("button", {
      name: "Confirm ticket status change"
    }));
    await screen.findByText(/Status updated:/);
    const update = fetch.mock.calls.find(([, init]) => init?.method === "PATCH")!;
    expect(update[0]).toBe(`/api/backend/support-tickets/${ticket.ticket_id}/status`);
    expect(JSON.parse(update[1]?.body as string)).toEqual({
      expected_revision: 3,
      status: "in_progress"
    });
    expect(screen.getByText("revision").parentElement).toHaveTextContent("4");
  });
  it("removes old ticket actions when management lookup filters change", async () => {
    mockFetch(() => response({
      items: [ticket]
    }));
    render(<SupportTicketsPanel auth={{
      ...auth,
      permissions: ["support:ticket:manage"]
    }} language="en" />);
    await userEvent.click(screen.getByRole("button", {
      name: "Refresh tickets"
    }));
    await userEvent.click(await screen.findByRole("button", {
      name: `Review status ${ticket.ticket_id}`
    }));
    fireEvent.change(screen.getByLabelText("Ticket status filter"), {
      target: {
        value: "resolved"
      }
    });
    expect(screen.queryByRole("group", {
      name: "Confirm ticket transition"
    })).not.toBeInTheDocument();
    expect(screen.queryByText(ticket.title)).not.toBeInTheDocument();
  });
});
it("shows permission notices without loading operational data", () => {
  const fetch = mockFetch(() => response(null));
  const props = {
    auth: PERSONAS.employee,
    language: "en" as const
  };
  render(<><FolderSyncPanel {...props} /><QueueOperationsPanel {...props} /><CostPanel {...props} /><SupportTicketsPanel {...props} /></>);
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});
it("lets an authorized support reader navigate the existing Review surface", async () => {
  const fetch = mockFetch(() => response({
    items: [ticket]
  }));
  const client = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false
      },
      mutations: {
        retry: false
      }
    }
  });
  render(<QueryClientProvider client={client}><WorkbenchShell auth={{
      ...auth,
      userId: "support-reader",
      permissions: ["support:ticket:read"]
    }} language="en" onLanguageChange={vi.fn()} onSignOut={vi.fn()} /></QueryClientProvider>);
  const navigation = screen.getByRole("button", {
    name: /Review.*costs and support/
  });
  expect(navigation).not.toHaveClass("is-disabled");
  await userEvent.click(navigation);
  expect(screen.getByRole("heading", {
    name: "Support tickets"
  })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", {
    name: "Refresh tickets"
  }));
  expect(await screen.findByText(ticket.title)).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("discards a pending panel read when its session is refreshed", async () => {
  let resolve!: (value: Response) => void;
  let signal!: AbortSignal;
  mockFetch((_url, init) => {
    signal = init!.signal!;
    return new Promise<Response>(done => {
      resolve = done;
    });
  });
  const first: AuthSession = {
    ...auth,
    mode: "bearer",
    bearerToken: "test-access-first"
  };
  const second: AuthSession = {
    ...first,
    bearerToken: "test-access-refreshed"
  };
  const view = render(<QueueOperationsPanel auth={first} language="en" />);
  await userEvent.click(screen.getByRole("button", {
    name: "Refresh queue jobs"
  }));
  view.rerender(<QueueOperationsPanel auth={second} language="en" />);
  expect(signal.aborted).toBe(true);
  await act(async () => {
    resolve(response(queuePage()));
    await Promise.resolve();
  });
  expect(screen.queryByRole("button", {
    name: "Retry rq-1"
  })).not.toBeInTheDocument();
  expect(screen.getByRole("button", {
    name: "Refresh queue jobs"
  })).toBeEnabled();
});
