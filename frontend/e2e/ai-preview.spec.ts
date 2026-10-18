import { expect, test } from "@playwright/test";

async function analyzeFlow(page: import('@playwright/test').Page, noAi: boolean) {
  await page.goto('/investigations/new');
  await page.getByLabel('Client ID', { exact: true }).fill('C.SYNTH0000000002');
  await page.getByLabel('Flow ID', { exact: true }).fill('F.SYNTH0002');
  if (noAi) await page.getByRole('checkbox', { name: 'Evidence only / no AI', exact: true }).check();
  await page.getByRole('button', { name: 'Review evidence scope' }).click();
  await expect(page.getByText('Scope checked', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Analyze Existing Evidence' }).click();
  await expect(page).toHaveURL(/\/investigations\/inv-/);
  await expect(page.getByRole('listitem').filter({ hasText: 'Report Ready' })).toHaveClass(/done/);
}

test('existing flow to exact evidence row and report, without model requests', async ({ page }) => {
  await analyzeFlow(page, true);
  await expect(page.getByText('Evidence was saved and checked with deterministic review signals. No AI analysis was requested.', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'View Evidence', exact: true }).click();
  await page.getByRole('list', { name: 'Findings', exact: true }).getByRole('button').first().click();
  const evidence = page.getByRole('button', { name: /^Open evidence ev-[a-f0-9]+$/ }).first();
  await evidence.click();
  await expect(page.getByText(/zero-based position/)).toBeVisible();
  await page.getByRole('navigation').getByRole('link', { name: 'Reports', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Reports', exact: true })).toBeVisible();
});

test('local model proposals stay distinct from evidence and no backend writes are exposed', async ({ page, request }) => {
  await analyzeFlow(page, false);
  await page.getByRole('link', { name: 'View Evidence', exact: true }).click();
  await expect(page.getByText(/fake model/i).first()).toBeVisible();
  const session = (await (await request.get('/api/v1/session')).json()).data;
  const response = await request.post('/api/v1/plans', { headers: { 'X-CSRF-Token': session.csrf_token }, data: { mode: 'new_collection' } });
  expect(response.status()).toBe(422);
  await expect(page.getByRole('navigation').getByRole('link')).toHaveCount(3);
});

test('report preview separates model coverage and loads embedded fonts in its sandbox', async ({ page, request }) => {
  await analyzeFlow(page, false);
  const id = page.url().split('/').at(-1)!;
  await page.goto(`/reports?investigation=${id}&format=html`);
  const report = page.frameLocator('iframe[title="HTML report preview"]');
  await expect(report.getByRole('heading', { name: 'Model input coverage', exact: true })).toBeVisible();
  await expect(report.getByRole('heading', { name: 'Evidence coverage', exact: true })).toBeVisible();
  const frame = await page.locator('iframe[title="HTML report preview"]').contentFrame();
  const loaded = await frame.locator('body').evaluate(async () => {
    const samples = [['Inter Variable', 'Review'], ['Vazirmatn Variable', 'مدیر سیستم'], ['JetBrains Mono Variable', 'evidence_id']];
    return Promise.all(samples.map(async ([family, text]) => {
      const fonts = await document.fonts.load(`14px "${family}"`, text);
      return fonts.length > 0 && fonts.every(font => font.status === 'loaded');
    }));
  });
  expect(loaded).toEqual([true, true, true]);
  const response = await request.get(`/api/v1/investigations/${id}/report?format=html`);
  expect(response.headers()['content-security-policy']).toContain('font-src data:');
  expect(response.headers()['content-security-policy']).toContain("script-src 'none'");
  await page.screenshot({ path: 'test-results/report-preview.png', fullPage: true });
});
