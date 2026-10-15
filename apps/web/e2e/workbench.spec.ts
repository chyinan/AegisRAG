import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import fixtures from "../src/test/fixtures/backend-workbench.json" with { type: "json" };
import streams from "../src/test/fixtures/chat-stream.json" with { type: "json" };

// Browser contract suite. Fixtures come from the real Python DTO/SSE serializers;
// network responses are mocked, so this is not a deployed-stack acceptance test.
async function installContracts(page: Page) {
  const state = {
    uploaded: false, statusReads: 0, sourceDenied: false, stream: streams.success,
    expectedSourceType: "markdown", expectedVisibility: "tenant"
  };
  await page.route("**/api/auth/login", async (route) => {
    const body = route.request().postDataJSON() as { tenant_id: string; username: string; password: string };
    expect(body.tenant_id).toBe("tenant-pilot");
    expect(body.username).toBe("pilot-admin");
    await route.fulfill({ status: body.password === "wrong" ? 401 : 200,
      json: body.password === "wrong" ? fixtures.loginDenied : fixtures.login });
  });
  await page.route("**/api/backend/documents/review?**", async (route) => {
    await route.fulfill({ json: state.uploaded ? fixtures.documents : fixtures.emptyDocuments });
  });
  await page.route("**/api/backend/upload", async (route) => {
    expect(route.request().headers().authorization).toBe("Bearer synthetic-browser-access-token");
    const form = await new Response(route.request().postData(), {
      headers: { "content-type": route.request().headers()["content-type"] }
    }).formData();
    expect(form.get("source_type")).toBe(state.expectedSourceType);
    const acl = form.get("acl");
    if (typeof acl !== "string") throw new Error("Upload ACL must be a JSON form field.");
    expect(JSON.parse(acl)).toEqual({ visibility: state.expectedVisibility });
    state.uploaded = true;
    await route.fulfill({ json: fixtures.upload });
  });
  await page.route("**/api/backend/documents/doc-pilot/versions/version-pilot/status", async (route) => {
    state.statusReads += 1;
    await route.fulfill({ json: state.statusReads === 1 ? fixtures.indexing : fixtures.ready });
  });
  await page.route("**/api/backend/chat/stream", async (route) => {
    expect(route.request().headers().authorization).toBe("Bearer synthetic-browser-access-token");
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: state.stream });
  });
  await page.route("**/api/backend/chat/history?**", async (route) => {
    await route.fulfill({ json: { request_id: "req-history", data: { session_id: "session-fixture", messages: [] }, error: null } });
  });
  await page.route("**/api/backend/sources/resolve", async (route) => {
    expect(route.request().postDataJSON()).toEqual({ document_id: "doc-pilot", version_id: "version-pilot", chunk_id: "chunk-pilot" });
    await route.fulfill({ status: state.sourceDenied ? 404 : 200,
      json: state.sourceDenied ? fixtures.sourceDenied : fixtures.source });
  });
  return state;
}

async function login(page: Page, password = "Synthetic-Pilot-123!") {
  await page.goto("/");
  await page.getByLabel("Tenant ID", { exact: true }).fill("tenant-pilot");
  await page.getByLabel("Username", { exact: true }).fill("pilot-admin");
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign In", exact: true }).click();
}

async function navigate(page: Page, surface: "Ask" | "Knowledge Base") {
  await page.getByRole("complementary", { name: "Workbench navigation" })
    .getByRole("button", { name: new RegExp(`^${surface}`) }).click();
}

async function ask(page: Page, question = "Can I work remotely?") {
  await page.locator("textarea").fill(question);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
}

test("local login shows a recoverable credential error without opening the workbench", async ({ page }) => {
  await installContracts(page);
  await login(page, "wrong");
  await expect(page.locator(".auth-form").getByRole("alert")).toContainText("Invalid username or password");
  await expect(page.getByTestId("workbench-shell")).toHaveCount(0);
  await page.getByLabel("Password", { exact: true }).fill("Synthetic-Pilot-123!");
  await page.getByRole("button", { name: "Sign In", exact: true }).click();
  await expect(page.getByTestId("workbench-shell")).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  if ((page.viewportSize()?.width ?? 0) < 768) {
    await expect(page.getByRole("complementary", { name: "Evidence and diagnostics panel" }))
      .toHaveCSS("grid-column-start", "1");
  }
});

test("login → Markdown upload → indexing → cited answer → authorized excerpt", async ({ page }) => {
  const state = await installContracts(page);
  await login(page);
  await navigate(page, "Knowledge Base");
  await expect(page.getByText("No documents are visible in the current authorized scope.", { exact: true })).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles({ name: "handbook.md", mimeType: "text/markdown",
    buffer: Buffer.from("# Remote work\nWorking remotely is supported with manager approval.") });
  await page.getByLabel("Title", { exact: true }).fill("Pilot handbook.md");
  const aclPreset = page.getByLabel("ACL preset", { exact: true });
  const departmentOption = aclPreset.locator('option[value="department"]');
  // Playwright 1.60 follows the wrapping label to its select for toBeDisabled().
  // Inspect the option itself and verify real keyboard selection skips it.
  await expect(departmentOption).toHaveJSProperty("disabled", true);
  await expect.poll(() => departmentOption.evaluate((option) => option.matches(":disabled"))).toBe(true);
  await expect(aclPreset).toHaveValue("tenant");
  await aclPreset.focus();
  await aclPreset.press("ArrowDown");
  await expect(aclPreset).toHaveValue("private");
  await aclPreset.press("ArrowUp");
  await expect(aclPreset).toHaveValue("tenant");
  await page.getByRole("button", { name: "Upload and create job", exact: true }).click();
  await expect(page.getByLabel("Upload indexing status")).toContainText("version-pilot");
  await expect(page.getByText("Ready for retrieval", { exact: true })).toBeVisible();
  await expect(page.getByText("Pilot handbook.md", { exact: true }).first()).toBeVisible();
  const readsAtReady = state.statusReads;
  await page.waitForTimeout(2_300);
  expect(state.statusReads).toBe(readsAtReady);
  await navigate(page, "Ask");
  await expect(page.getByRole("button", { name: "Copy answer with citations" })).toBeDisabled();
  await ask(page);
  await expect(page.getByRole("button", { name: "Copy answer with citations" })).toBeEnabled();
  await expect(page.getByText(/Working remotely is supported/).first()).toBeVisible();
  await page.getByRole("button", { name: /Evidence: Remote work/ }).first().click();
  await expect(page.getByText(fixtures.source.data.text_excerpt)).toBeVisible();
  await expect(page.getByRole("complementary", { name: "Evidence and diagnostics panel" })).toContainText("version-pilot");
});

test("quick import accepts TXT and refreshes the main knowledge list", async ({ page }) => {
  const state = await installContracts(page);
  state.expectedSourceType = "txt";
  state.expectedVisibility = "private";
  await login(page);
  await page.getByRole("button", { name: "Quick import", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Quick import", exact: true });
  await dialog.locator('input[type="file"]').setInputFiles({ name: "handbook.txt", mimeType: "text/plain",
    buffer: Buffer.from("Working remotely is supported with manager approval.") });
  await dialog.getByLabel("Source type").selectOption("txt");
  await dialog.getByLabel("ACL preset").selectOption("private");
  await dialog.getByRole("button", { name: "Upload and create job" }).click();
  await expect(dialog.getByText("Ready for retrieval", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Close import drawer" }).click();
  await navigate(page, "Knowledge Base");
  await expect(page.getByText("Pilot handbook.md", { exact: true }).first()).toBeVisible();
});

for (const [name, stream, code] of [
  ["backend error", streams.error, "RAG_QUERY_FAILED"],
  ["early EOF", streams.tokensOnly, "CHAT_STREAM_INCOMPLETE"]
] as const) {
  test(`${name} is visible, cannot be copied as final, and allows another question`, async ({ page }) => {
    const state = await installContracts(page);
    state.stream = stream;
    await login(page);
    await ask(page);
    await expect(page.getByTestId("workbench-shell").getByRole("alert").last()).toContainText(code);
    await expect(page.getByRole("button", { name: "Copy answer with citations" })).toBeDisabled();
    await expect(page.getByText("Streaming tokens…", { exact: true })).toHaveCount(0);
    state.stream = streams.success;
    await ask(page, "Try again");
    await expect(page.getByRole("button", { name: "Copy answer with citations" }).last()).toBeEnabled();
  });
}

test("no-answer is an explicit successful outcome with request ID", async ({ page }) => {
  const state = await installContracts(page);
  state.stream = streams.noAnswer;
  await login(page);
  await ask(page);
  await expect(page.getByText("Cannot confirm from the authorized sources.").first()).toBeVisible();
  await expect(page.getByText(/req-stream-fixture/).first()).toBeVisible();
  await expect(page.getByRole("button", { name: /Evidence: Remote work/ })).toHaveCount(0);
});

test("a denied source never exposes an excerpt", async ({ page }) => {
  const state = await installContracts(page);
  state.sourceDenied = true;
  await login(page);
  await ask(page);
  await page.getByRole("button", { name: /Evidence: Remote work/ }).first().click();
  await expect(page.getByText("Source unavailable", { exact: true })).toBeVisible();
  await expect(page.getByText(fixtures.source.data.text_excerpt)).toHaveCount(0);
});

for (const cancelAction of ["navigation", "new conversation"] as const) {
test(`real delta chunks render before final and ${cancelAction} cancels the old stream`, async ({ page }) => {
  await installContracts(page);
  await page.addInitScript(({ stream }) => {
    const fetchOriginal = window.fetch.bind(window);
    window.fetch = (input, init) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (url.endsWith("/chat/stream")) {
        const frames = stream.split("\n\n").filter(Boolean);
        const encoder = new TextEncoder();
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(encoder.encode(`${frames[0]}\n\n${frames[1]}\n\n`));
            const timer = setTimeout(() => {
              controller.enqueue(encoder.encode(`${frames.slice(2).join("\n\n")}\n\n`));
              controller.close();
            }, 3_000);
            init?.signal?.addEventListener("abort", () => {
              document.documentElement.dataset.testStreamAborted = "true";
              clearTimeout(timer);
              controller.error(new DOMException("Aborted", "AbortError"));
            }, { once: true });
          }
        });
        return Promise.resolve(new Response(body, { headers: { "Content-Type": "text/event-stream" } }));
      }
      return fetchOriginal(input, init);
    };
  }, { stream: streams.success });
  await login(page);
  await ask(page);
  await expect(page.getByText("Working remotely 🌍 is supported.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Copy answer with citations" })).toBeDisabled();
  if (cancelAction === "navigation") {
    await navigate(page, "Knowledge Base");
  } else {
    await page.getByRole("button", { name: "New conversation", exact: true }).click();
  }
  await expect(page.locator("html")).toHaveAttribute("data-test-stream-aborted", "true");
  if (cancelAction === "navigation") await navigate(page, "Ask");
  await page.waitForTimeout(3_100);
  await expect(page.getByText(/Working remotely is supported/)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Copy answer with citations" })).toBeDisabled();
});
}

test("language change preserves the logged-in identity", async ({ page }) => {
  await installContracts(page);
  await login(page);
  await page.getByLabel("Language").selectOption("zh");
  await expect(page.getByRole("heading", { name: "企业知识操作台" })).toBeVisible();
  await expect(page.getByText("Pilot Admin", { exact: true }).first()).toBeVisible();
});
