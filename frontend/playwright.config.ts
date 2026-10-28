import { defineConfig, devices } from "@playwright/test";

// Journeys run against the *installed* application serving the built assets (not the Vite
// dev server), with the synthetic test runtime injected by tests/support/e2e_server.py.
// Start it first or let `webServer` start it via VR_DFIR_E2E_COMMAND (see docs/ui.md).
const baseURL = process.env.VR_DFIR_E2E_URL ?? "http://127.0.0.1:8766";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL,
    viewport: { width: 1280, height: 720 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  // VR_DFIR_E2E_CHROMIUM may name an already installed Chromium (offline build hosts).
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 }, launchOptions: process.env.VR_DFIR_E2E_CHROMIUM ? { executablePath: process.env.VR_DFIR_E2E_CHROMIUM } : {} } }],
  webServer: process.env.VR_DFIR_E2E_COMMAND
    ? { command: process.env.VR_DFIR_E2E_COMMAND, url: `${baseURL}/api/v1/status`, reuseExistingServer: false, timeout: 60_000 }
    : undefined,
});
