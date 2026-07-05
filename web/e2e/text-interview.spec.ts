/**
 * S3-013 — Text interview happy-path E2E smoke test
 *
 * HOW TO RUN LOCALLY
 * ------------------
 * Prerequisites (all must be running):
 *   - Postgres (default port 5432, schema migrated)
 *   - data_gateway  → http://localhost:8002
 *   - interview_core → http://localhost:8001
 *   - Vite dev server → http://localhost:5174  (npm run dev)
 *
 * Then, from the web/ directory:
 *   npx playwright install chromium   # first time only
 *   npm run e2e
 *
 * COST NOTE
 * ---------
 * Each run hits real Gemini Flash Lite + Sarvam APIs (~₹1/run).
 * Do not add to any automated loop without budget approval.
 *
 * DEPENDENCY
 * ----------
 * S3-011 (ConsentModal) must be deployed — the test handles the DPDP
 * consent gate. Without it the session create returns 403.
 *
 * TURN COUNT
 * ----------
 * MAX_TURNS server-side is 5. The test sends exactly 5 candidate messages.
 */

import { test, expect } from '@playwright/test';

test('text interview: register → consent → 5 turns → complete', async ({ page }) => {
  const email = `e2e_${Date.now()}_${Math.random().toString(36).slice(2, 8)}@example.com`;
  const password = 'TestPass123!';
  const fullName = 'E2E Smoke';

  // ------------------------------------------------------------------ Register
  // Registration form uses id="full_name", id="email", id="password".
  await page.goto('/register');

  await page.fill('#full_name', fullName);
  await page.fill('#email', email);
  await page.fill('#password', password);
  await page.locator('button[type="submit"]').click();

  // Register.tsx navigates to /dashboard on success (not /jobs — auto-login
  // lands at dashboard, which then links to jobs).
  await expect(page).toHaveURL(/\/dashboard$/, { timeout: 15_000 });

  // ---------------------------------------------------------- Dashboard → Jobs
  // Dashboard has a "Browse Jobs" button that navigates to /jobs.
  await page.getByRole('button', { name: /browse jobs/i }).click();
  await expect(page).toHaveURL(/\/jobs$/, { timeout: 10_000 });

  // ---------------------------------------------------------------- Pick a job
  // JobCard renders a button with text "Start Interview" (exact).
  // aria-label is "Start interview for <title>" but we match by visible text.
  await page.getByRole('button', { name: /start interview/i }).first().click();

  // ----------------------------------------------- DPDP consent modal (S3-011)
  // ConsentModal "I Agree" button carries aria-label="I Agree" — stable even
  // while isSubmitting=true (visible text swaps to "Saving…" but label stays).
  const agreeBtn = page.getByRole('button', { name: 'I Agree' });
  await agreeBtn.waitFor({ state: 'visible', timeout: 5_000 });
  await agreeBtn.click();

  // After consent is recorded, JobsList calls createSession and navigates.
  await expect(page).toHaveURL(/\/interview\/[0-9a-f-]+$/, { timeout: 15_000 });

  // ---------------------------------------------------------- 5 candidate turns
  // Interview.tsx renders id="candidate-input" (a text <input>).
  // It is disabled while status !== 'connected' or isInterviewerTyping.
  // We wait for it to be enabled before each fill so we don't race the WS.
  const input = page.locator('#candidate-input');

  for (let i = 1; i <= 5; i++) {
    await input.waitFor({ state: 'visible' });
    // Wait up to 30 s per turn for the input to become enabled — the
    // interviewer response (LLM + optional TTS) must arrive first.
    await expect(input).toBeEnabled({ timeout: 30_000 });
    await input.fill(`This is my answer number ${i}.`);
    await page.keyboard.press('Enter');
  }

  // ----------------------------------------------------------- Complete screen
  // After the 5th turn the server closes the session; the Interview page
  // navigates to /interview/<uuid>/complete via useEffect on isComplete.
  await expect(page).toHaveURL(/\/interview\/.+\/complete$/, { timeout: 30_000 });
});
