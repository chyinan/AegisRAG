import { expect, test } from "@playwright/test";

test("desktop workbench shows role-aware shell and disabled copy until final", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Employee/ }).click();

  await expect(page.getByTestId("workbench-shell")).toBeVisible();
  const sidebar = page.getByRole("complementary", { name: "Workbench navigation" });
  await expect(sidebar.getByRole("button", { name: "Ask" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("button", { name: "Quick import" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Copy answer with citations" })).toBeDisabled();
});

test("knowledge manager can open quick import and sees knowledge default", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Knowledge Manager/ }).click();

  await expect(page.getByRole("heading", { name: "Knowledge Base" })).toBeVisible();
  await page.getByRole("button", { name: "Quick import" }).click();
  const dialog = page.getByRole("dialog", { name: "Quick import" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Source type")).toBeVisible();
});

test("language selector switches the workbench to Chinese", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Trusted Enterprise Knowledge Workbench" })).toBeVisible();

  await page.getByLabel("Language").selectOption("zh");
  await expect(page.getByRole("heading", { name: "可信企业知识工作台" })).toBeVisible();
  await page.getByRole("button", { name: /员工/ }).click();
  await expect(page.getByRole("heading", { name: "企业知识操作台" })).toBeVisible();
});

test("auditor can query audit summaries inside the workbench", async ({ page }) => {
  await page.route("**/api/backend/audit/logs?**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        request_id: "req-audit",
        data: {
          items: [
            {
              id: "audit-1",
              tenant_id: "tenant-demo-alpha",
              user_id: "demo-user-employee",
              request_id: "req-target",
              trace_id: "trace-target",
              action: "rag.query",
              resource_type: "rag_query",
              resource_id: "req-target",
              status: "success",
              latency_ms: 12.5,
              error_code: null,
              created_at: "2026-06-09T10:00:00+00:00",
              safe_summary: { citation_count: 1 },
              safe_counts: { citation_count: 1 }
            }
          ],
          next_steps: ["Open diagnostics with request_id if retrieval evidence is required."]
        },
        error: null
      })
    });
  });

  await page.goto("/");
  await page.getByRole("button", { name: /Auditor/ }).click();

  await expect(page.getByRole("heading", { name: "Audit Explorer" })).toBeVisible();
  await page.getByLabel("request_id").fill("req-target");
  await page.getByRole("button", { name: "Search logs" }).click();

  await expect(page.getByText("rag.query · success")).toBeVisible();
  await expect(page.getByText("audit-1")).toBeVisible();
  await expect(page.getByText("citation_count: 1").first()).toBeVisible();
});

test("platform admin can use migrated governance panels", async ({ page }) => {
  await page.route("**/api/backend/review/items?**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        request_id: "req-review-list",
        data: {
          items: [
            {
              id: "review-1",
              item_type: "no_answer",
              severity: "high",
              status: "open",
              request_id: "req-target",
              trace_id: "trace-target",
              source_view: "audit_explorer",
              safe_identifiers: { audit_log_id: "audit-1" },
              safe_summary: { reason_code: "needs_eval" },
              allowed_transitions: ["accepted", "needs_followup"],
              created_by: "demo-user-platform-admin",
              tenant_id: "tenant-demo-alpha",
              created_at: "2026-06-09T10:00:00+00:00",
              updated_at: "2026-06-09T10:00:00+00:00"
            }
          ],
          next_steps: ["Review safe identifiers before converting to eval."]
        },
        error: null
      })
    });
  });
  await page.route("**/api/backend/eval/reports?**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        request_id: "req-eval-list",
        data: {
          items: [
            {
              report_filename: "rag-smoke.json",
              generated_at: "2026-06-09T10:00:00+00:00",
              report_type: "rag_dataset_smoke",
              dataset_name: "synthetic",
              case_count: 12,
              passed_count: 11,
              failed_count: 1,
              retrieval_hit_rate: 0.92,
              citation_coverage: 0.95,
              average_latency_ms: 84,
              decision: "pass",
              failed_metric_names: [],
              failure_stages: []
            }
          ],
          next_steps: []
        },
        error: null
      })
    });
  });
  await page.route("**/api/backend/agent/run", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        request_id: "req-agent",
        data: {
          agent_run_id: "agent-run-1",
          request_id: "req-agent",
          trace_id: "trace-agent",
          tenant_id: "tenant-demo-alpha",
          user_id: "demo-user-platform-admin",
          status: "completed",
          termination_reason: "final_answer",
          steps_used: 2,
          tool_calls_used: 1,
          final_answer: "Governed tool run completed.",
          final_citations: [],
          error_code: null,
          created_at: "2026-06-09T10:00:00+00:00",
          updated_at: "2026-06-09T10:00:00+00:00",
          metadata: {}
        },
        error: null
      })
    });
  });

  await page.goto("/");
  await page.getByRole("button", { name: /Platform Admin/ }).click();

  await page.getByRole("button", { name: /Review/ }).click();
  await expect(page.getByRole("heading", { name: "Review Queue" })).toBeVisible();
  await page.getByLabel("request_id").fill("req-target");
  await page.getByRole("button", { name: "Load review items" }).click();
  await expect(page.getByText("no_answer · open")).toBeVisible();
  await expect(page.getByText("reason_code: needs_eval")).toBeVisible();

  await page.getByRole("button", { name: /Eval/ }).click();
  await expect(page.getByRole("heading", { name: "Eval Evidence" })).toBeVisible();
  await page.getByRole("button", { name: "Load reports" }).click();
  await expect(page.getByText("rag_dataset_smoke · pass")).toBeVisible();
  await expect(page.getByText("rag-smoke.json")).toBeVisible();

  await page.getByRole("button", { name: /Agent Runs/ }).click();
  await expect(page.getByRole("heading", { name: "Agent Run Console" })).toBeVisible();
  await page.getByLabel("Agent input").fill("Check safe tool access.");
  await page.getByRole("button", { name: "Run agent" }).click();
  await expect(page.getByText("completed · final_answer")).toBeVisible();
  await expect(page.getByText("agent-run-1")).toBeVisible();

  await page.getByRole("button", { name: /Settings/ }).click();
  await expect(page.getByRole("heading", { name: "Identity Boundaries" })).toBeVisible();
  await expect(page.getByText("admin:settings").first()).toBeVisible();
});

test("fallback sidecar and governance routes are available on the Next origin", async ({ page }) => {
  await page.goto("/sidecar");
  await expect(page.getByRole("heading", { level: 1, name: "AegisRAG Source Inspector" })).toBeVisible();

  const sidecarCss = await page.request.get("/sidecar/assets/sidecar.css");
  expect(sidecarCss.ok()).toBeTruthy();

  await page.goto("/governance");
  await expect(page.getByRole("heading", { level: 1, name: "AegisRAG Governance Workbench" })).toBeVisible();
});
