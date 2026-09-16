// Playwright configuration for the AntHire browser end-to-end suite.
//
// Run: npm run e2e   (see e2e/README.md for prerequisites)
//
// The suite drives the real stack: the Vite dev server, data_gateway and the
// local Postgres/Redis/Mailpit containers. global-setup provisions a throwaway
// company and one account per role for each run, against the LOCAL database
// only.

import { defineConfig, devices } from '@playwright/test';

const WEB_URL = process.env.E2E_WEB_URL ?? 'http://localhost:5174';

// Playwright downloads its own Chromium (`npx playwright install chromium`).
// Where that download is not wanted, point it at an installed browser instead,
// e.g. E2E_BROWSER_CHANNEL=msedge or chrome.
const channel = process.env.E2E_BROWSER_CHANNEL;

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  // One worker: the specs share one local stack, and the background loops in
  // data_gateway are not built to be raced by parallel browsers.
  workers: 1,
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  outputDir: 'test-results',
  use: {
    baseURL: WEB_URL,
    headless: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], ...(channel ? { channel } : {}) },
    },
  ],
});
