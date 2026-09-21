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
  // 2 minutes, and `test.slow()` triples it to 6 for the journeys. Headless, a
  // journey takes about 50 seconds; the budget is for UI mode (`--ui`), which
  // records a continuous video of every browser window. The candidate's exam
  // page has a countdown that repaints every second, so its window alone wrote
  // ~1,200 frames (37 MB), and saving them when the test closed that window
  // took 63 seconds — which, at the old 3-minute limit, failed journey-hire
  // one step after the hire had gone through. A passing test is not slowed by
  // this; only a hung one takes longer to report.
  timeout: 120_000,
  // 30s rather than Playwright's 5s. Every assertion here waits on real server
  // work — a shortlist that starts a workflow, an exam submit that grades it and
  // runs the runner. On a quiet machine those are well under a second and the
  // whole suite finishes in minutes; the headroom is for a machine that is not
  // quiet. data_gateway runs as ONE uvicorn process for this suite, so anything
  // else hitting it — the vitest suite, a smoke run, a profiler — is not
  // background noise, it is a queue in front of every assertion. Measured: with
  // that load these journeys took 9 minutes and failed at a different step each
  // run; without it, 2.6 minutes and green.
  expect: { timeout: 30_000 },
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
    // Watching a run: `npm run e2e -- --headed` with E2E_SLOWMO=500 pauses
    // that many milliseconds between actions so each step can be followed.
    launchOptions: { slowMo: Number(process.env.E2E_SLOWMO ?? 0) },
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], ...(channel ? { channel } : {}) },
    },
  ],
});
