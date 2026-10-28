import { expect, test } from "@playwright/test";
import { startFlow } from "./helpers";

test("existing flow analysis from the Start page, without model requests", async ({ page }) => {
  await page.goto("/investigations/new");
  await page.getByLabel("Client ID", { exact: true }).fill("C.SYNTH0000000002");
  await page.getByLabel("Flow ID", { exact: true }).fill("F.SYNTH0002");
  await page.getByRole("checkbox", { name: "Evidence only / no AI", exact: true }).check();
  await page.getByRole("button", { name: "Review evidence scope" }).click();
  await expect(page.getByText("Scope checked", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Analyze Existing Evidence" }).click();
  await expect(page).toHaveURL(/\/investigations\/inv-/);
  await expect(page.getByText("Next step")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("complementary", { name: "Primary" }).getByRole("link")).toHaveCount(3);
});

test("local model proposals stay distinct from evidence and no backend writes are exposed", async ({ page, request }) => {
  const id = await startFlow(page, "C.SYNTH0000000002", "F.SYNTH0002", false);
  await page.goto(`/investigations/${id}/evidence`);
  await expect(page.getByText(/Fake model \(test fixture\)/)).toBeVisible();
  await expect(page.getByRole("list", { name: "Findings" }).getByText(/AI proposal/).first()).toBeVisible();
  const session = (await (await request.get("/api/v1/session")).json()).data;
  const response = await request.post("/api/v1/plans", { headers: { "X-CSRF-Token": session.csrf_token }, data: { mode: "new_collection" } });
  expect(response.status()).toBe(422);
});

test("full report preview separates model coverage and loads embedded fonts in its sandbox", async ({ page, request }) => {
  const id = await startFlow(page, "C.SYNTH0000000002", "F.SYNTH0002", false);
  await page.goto(`/reports?investigation=${id}&scope=full&format=html`);
  const report = page.frameLocator('iframe[title="HTML report preview"]');
  await expect(report.getByRole("heading", { name: "Model input coverage", exact: true })).toBeVisible();
  await expect(report.getByRole("heading", { name: "Evidence coverage", exact: true })).toBeVisible();
  const frame = await page.locator('iframe[title="HTML report preview"]').contentFrame();
  const loaded = await frame.locator("body").evaluate(async () => {
    const samples = [["Inter Variable", "Review"], ["Vazirmatn Variable", "مدیر سیستم"], ["JetBrains Mono Variable", "evidence_id"]];
    return Promise.all(samples.map(async ([family, text]) => {
      const fonts = await document.fonts.load(`14px "${family}"`, text);
      return fonts.length > 0 && fonts.every((font) => font.status === "loaded");
    }));
  });
  expect(loaded).toEqual([true, true, true]);
  const response = await request.get(`/api/v1/investigations/${id}/report?format=html`);
  expect(response.headers()["content-security-policy"]).toContain("font-src data:");
  expect(response.headers()["content-security-policy"]).toContain("script-src 'none'");
});
