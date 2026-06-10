import { expect, test } from "@playwright/test";

test("desktop workbench shows role-aware shell and disabled copy until final", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /员工/ }).click();

  await expect(page.getByTestId("workbench-shell")).toBeVisible();
  const sidebar = page.getByRole("complementary", { name: "Workbench navigation" });
  await expect(sidebar.getByRole("button", { name: "Ask" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("button", { name: "Import" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Copy answer with citations" })).toBeDisabled();
});

test("knowledge manager can open quick import and sees knowledge default", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /知识管理员/ }).click();

  await expect(page.getByRole("heading", { name: "Knowledge Base" })).toBeVisible();
  await page.getByRole("button", { name: "Import" }).click();
  const dialog = page.getByRole("dialog", { name: "Quick import" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Source type")).toBeVisible();
});
