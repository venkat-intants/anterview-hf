// The steps a candidate takes, written once: applying through the public form
// and sitting an exam from the emailed link. Two journeys share them, and a
// spec reads as what a person did rather than as a list of clicks.

import { expect, type Page } from '@playwright/test';
import { waitForMail, linkIn } from './mail';
import { makeCvPdf } from './pdf';

export interface Candidate {
  name: string;
  email: string;
  /** Pinned by fake AI mode: "E2E-SCORE: 85" in the CV scores exactly 85. */
  resumeScore: number;
}

/** A candidate with a unique email, so runs never collide. */
export function aCandidate(name: string, resumeScore: number): Candidate {
  const stamp = `${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;
  return { name, email: `${name.toLowerCase().replace(/\W+/g, '.')}.${stamp}@e2e-anthire.com`, resumeScore };
}

/** Apply through the real public form: details, CV, consent, send. */
export async function applyThroughPublicForm(
  page: Page,
  openingId: string,
  candidate: Candidate,
  opts: { language?: 'en' | 'hi' | 'te' } = {},
): Promise<void> {
  await page.goto(`/apply/${openingId}`);
  await page.locator('#name').fill(candidate.name);
  await page.locator('#email').fill(candidate.email);
  await page.getByRole('button', { name: 'Continue' }).click();

  // Experience — every field optional.
  await page.getByRole('button', { name: 'Skip' }).click();

  await page.locator('input#cv').setInputFiles({
    name: 'cv.pdf',
    mimeType: 'application/pdf',
    buffer: makeCvPdf([
      candidate.name,
      candidate.email,
      'Python FastAPI PostgreSQL pytest',
      `E2E-SCORE: ${candidate.resumeScore}`,
    ]),
  });
  await page.getByRole('button', { name: 'Continue' }).click();

  if (opts.language) await page.locator('#apply-language').selectOption(opts.language);
  // Nothing is stored before this box is ticked, so the send button waits on it.
  const send = page.getByRole('button', { name: 'Send application' });
  await expect(send, 'sending is refused until consent is given').toBeDisabled();
  await page.locator('label').filter({ hasText: 'may store my name, email and CV' })
    .locator('input[type="checkbox"]').check();
  await expect(send).toBeEnabled();
  await send.click();

  await expect(page.getByText('Application received')).toBeVisible();
}

/** Open the emailed exam link and answer every question. */
export async function sitTheExam(
  page: Page,
  candidate: Candidate,
  opts: { answerCorrectly: boolean },
): Promise<string> {
  // 60s. The invitation is not sent when the shortlist button returns: a person
  // shortlists, the runner assigns the round and queues the email, and the
  // outbox worker delivers it on its own poll. That is about 5s on a quiet local
  // stack and comfortably more on a busy one, where the default 30s expired just
  // short of the email arriving — a failure that reads as a broken invitation.
  const invite = await waitForMail(candidate.email, /assessment|exam/i, 60_000);
  const token = linkIn(invite, /\/exam#([A-Za-z0-9_-]{16,})/);

  await page.goto(`/exam#${token}`);
  const start = page.getByRole('button', { name: 'Start exam' });
  await start.waitFor();
  await expect(start, 'the exam cannot start before consent').toBeDisabled();
  await page.locator('input[type="checkbox"]').first().check();
  await start.click();

  const questions = page.getByRole('radiogroup');
  await expect(questions.first()).toBeVisible();
  for (const group of await questions.all()) {
    const option = opts.answerCorrectly
      ? group.locator('label').filter({ hasText: 'Correct answer' })
      : group.locator('label').filter({ hasText: /^Wrong answer/ }).first();
    await option.click();
  }

  await page.getByRole('button', { name: 'Submit exam' }).click();
  const verdict = page.getByText(/You passed|Not this time/);
  await expect(verdict).toBeVisible();
  return (await verdict.innerText()).trim();
}

/** HR shortlists one candidate from the applicant board. */
export async function shortlistFromApplicants(
  page: Page,
  candidate: Candidate,
  openingTitle: string,
): Promise<void> {
  await page.goto('/hr/applicants');
  const row = page.getByRole('button', { name: `Open details for ${candidate.name}` });
  await row.first().waitFor();
  await row.first().click();
  const drawer = page.getByRole('dialog', { name: `${candidate.name} applicant details` });
  await drawer.waitFor();
  const perOpening = drawer.getByRole('button', { name: `Shortlist for ${openingTitle}` });
  const single = drawer.getByRole('button', { name: 'Shortlist', exact: true });
  const button = (await perOpening.count()) ? perOpening : single;
  await button.first().click();
  await expect(button.first()).toBeDisabled();
}
