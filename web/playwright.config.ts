// Playwright configuration for Intants web E2E smoke tests.
// Targets the locally running Vite dev server (http://localhost:5174).
// Run: npm run e2e
// Prerequisites: data_gateway on :8002, interview_core on :8001, Vite on :5174, Postgres.
//
// NOTE (FE-3): `./e2e` currently contains a README and NO specs. This config is
// retained for the rewrite described in e2e/README.md, not because a suite runs.
// `npm run e2e` goes through scripts/run-e2e.mjs, which fails loudly rather than
// letting Playwright's exit-0-on-no-specs read as a passing suite.

import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:5174',
    headless: true,
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
