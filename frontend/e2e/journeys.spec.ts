// The five manual demonstration journeys of the release brief, automated against the synthetic
// test backend (fake Velociraptor server, no model). They check outcomes, not timing.
import path from "node:path";
import { expect, test } from "@playwright/test";
import { demoBundles, startFlow, watchModelRequests } from "./helpers";

test("1. review without AI: priority filter, exact source row, review decision", async ({ page }) => {
  const model = watchModelRequests(page);
  const id = await startFlow(page, "C.SYNTH0000000002", "F.SYNTH0002");
  await page.goto(`/investigations/${id}`);
  const metrics = page.getByRole("list", { name: "Summary" });
  await expect(metrics.getByRole("listitem")).toHaveCount(3);
  await metrics.getByRole("listitem").filter({ hasText: "Needs review" }).click();
  await expect(page).toHaveURL(/evidence\?review=open/);
  await page.getByRole("button", { name: /^Medium \(\d+\)$/ }).click();
  await page.getByRole("list", { name: "Findings" }).getByRole("button").first().click();
  await page.getByRole("button", { name: /^Open evidence ev-/ }).first().click();
  const insp = page.getByRole("region", { name: "Evidence inspector" });
  await expect(insp.getByText("Reason for attention")).toBeVisible();
  await insp.getByRole("button", { name: /Advanced: raw JSON and provenance/ }).click();
  await expect(insp.getByText(/zero-based position in the server result set/)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(insp).toHaveCount(0);
  const panel = page.getByRole("region", { name: /^Finding / });
  await panel.getByLabel("Review decision").selectOption("benign");
  await panel.getByRole("button", { name: "Save decision" }).click();
  await expect(panel.locator(".review-chip", { hasText: "Benign / expected" })).toBeVisible();
  await expect(page).toHaveURL(/review=open/);
  await expect(page).toHaveURL(/sev=medium/);
  expect(model).toHaveLength(0);
});

test("2. find a meaningful change and open both original records", async ({ page }) => {
  const model = watchModelRequests(page);
  await startFlow(page, "C.SYNTH0000000002", "F.SYNTH0002");
  const later = await startFlow(page, "C.SYNTH0000000002", "F.SYNTH0008");
  await page.goto(`/investigations/${later}/changes`);
  await expect(page.getByLabel("What to compare")).toHaveValue(/Users$/);
  const list = page.getByRole("list", { name: "Changes" });
  await expect(list.getByRole("button", { name: /Added\s*deploy/ })).toBeVisible();
  await expect(list.getByRole("button", { name: /Removed\s*backup/ })).toBeVisible();
  await list.getByRole("button", { name: /Modified\s*admin/ }).click();
  const detail = page.getByRole("region", { name: "Change detail" });
  await expect(detail.getByRole("row", { name: /shell/ })).toContainText("/bin/bash");
  await expect(detail.getByRole("row", { name: /shell/ })).toContainText("/bin/zsh");
  await detail.getByRole("button", { name: "Open baseline record" }).click();
  await expect(page.getByRole("region", { name: "Evidence inspector" })).toContainText("/bin/bash");
  await detail.getByRole("button", { name: "Open current record" }).click();
  await expect(page.getByRole("region", { name: "Evidence inspector" })).toContainText("/bin/zsh");
  expect(model).toHaveLength(0);
});

test("3. incomplete current evidence gives Unknown, not a false removal", async ({ page }) => {
  await startFlow(page, "C.SYNTH0000000002", "F.SYNTH0002");
  await startFlow(page, "C.SYNTH0000000002", "F.SYNTH0008");
  const partial = await startFlow(page, "C.SYNTH0000000002", "F.SYNTH0009");
  await page.goto(`/investigations/${partial}/changes`);
  await expect(page.getByText(/nothing is reported as Removed/)).toBeVisible();
  await expect(page.getByRole("button", { name: /^Removed: 0 entities/ })).toBeVisible();
  await expect(page.getByRole("list", { name: "Changes" }).getByText("Unknown").first()).toBeVisible();
  await expect(page.getByRole("list", { name: "Changes" }).getByText(/cannot establish absence/).first()).toBeVisible();
});

test("4. scope and time: eligible-host denominator, where else seen, timeline bucket and untimed rows", async ({ page }) => {
  const model = watchModelRequests(page);
  await page.goto("/investigations/new?mode=import");
  await page.getByLabel("Evidence bundle archive").setInputFiles(path.join(demoBundles(), "fleet-baseline.tar.gz"));
  await page.getByRole("button", { name: "Import evidence" }).click();
  await expect(page).toHaveURL(/\/investigations\/inv-/);
  await expect(page.getByText("Next step")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Hosts and outliers" }).click();
  const table = page.getByRole("region", { name: "Low-prevalence entities" });
  const row = table.getByRole("row", { name: /\/opt\/\.cache\/agentd\s/ }).filter({ hasText: "Executable path" });
  await expect(row).toContainText("Seen on 1 of 4 eligible hosts");
  await row.getByRole("link", { name: "Show evidence" }).click();
  const insp = page.getByRole("region", { name: "Evidence inspector" });
  await insp.getByRole("button", { name: "Where else was this seen?" }).click();
  const we = page.getByRole("region", { name: "Where else was this seen?" });
  await expect(we.getByText("Seen on 1 of 4 eligible hosts").first()).toBeVisible();
  await expect(we.getByText(/Also observed on demo-web-05/).first()).toBeVisible();
  await page.getByRole("navigation", { name: "Investigation views" }).getByRole("link", { name: "Timeline" }).click();
  await expect(page.getByText(/row\(s\) without a usable event time/)).toBeVisible();
  await page.getByText("Show as a table").click();
  await page.getByRole("button", { name: "Show rows" }).first().click();
  await expect(page.getByRole("region", { name: /Timeline rows/ })).toBeVisible();
  await page.getByRole("button", { name: "Show these rows" }).click();
  await expect(page).toHaveURL(/untimed=any/);
  expect(model).toHaveLength(0);
});

test("5. pin evidence and a change, add a note, reload, build the selected-evidence report", async ({ page }) => {
  await startFlow(page, "C.SYNTH0000000002", "F.SYNTH0002");
  const later = await startFlow(page, "C.SYNTH0000000002", "F.SYNTH0008");
  await page.goto(`/investigations/${later}/changes`);
  await page.getByRole("list", { name: "Changes" }).getByRole("button", { name: /Modified\s*admin/ }).click();
  await page.getByRole("region", { name: "Change detail" }).getByRole("button", { name: "Pin to report" }).click();
  await expect(page.getByRole("region", { name: "Change detail" }).getByRole("button", { name: "Pinned" })).toBeVisible();
  await page.getByRole("region", { name: "Change detail" }).getByRole("button", { name: "Open current record" }).click();
  await page.getByRole("region", { name: "Evidence inspector" }).getByRole("button", { name: "Pin to report" }).click();
  await expect(page.getByRole("button", { name: /Report selection/ })).toContainText("2");
  await page.getByRole("button", { name: /Report selection/ }).click();
  const tray = page.getByRole("dialog", { name: /Report selection/ });
  await tray.getByLabel("Analyst note").first().fill("Shell change confirmed with the owner");
  await tray.getByRole("button", { name: "Save note" }).first().click();
  await page.keyboard.press("Escape");
  await page.reload();
  await expect(page.getByRole("button", { name: /Report selection/ })).toContainText("2");
  // A pinned change reopens that exact change from any view.
  await page.goto(`/investigations/${later}/timeline`);
  await page.getByRole("button", { name: /Report selection/ }).click();
  await page.getByRole("dialog", { name: /Report selection/ }).getByRole("button", { name: /^Modified: admin/ }).click();
  await expect(page.getByRole("region", { name: "Change detail" })).toContainText("Modified: admin");
  await page.goto(`/reports?investigation=${later}&scope=selected`);
  await page.getByRole("button", { name: "Build selected-evidence report" }).click();
  const report = page.frameLocator('iframe[title="HTML report preview"]');
  await expect(report.getByText("Shell change confirmed with the owner")).toBeVisible();
  await expect(report.getByRole("heading", { level: 1 })).toContainText("Selected evidence");
  await expect(report.getByText(/compare-2026/).first()).toBeVisible();
});
