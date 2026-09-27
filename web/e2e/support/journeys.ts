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

/**
 * A candidate with a unique email AND a unique name, so runs never collide.
 *
 * The name used to be just the first name given ("Ananya"), and specs find
 * their candidate on shared screens by it — "Open details for Ananya". That
 * held while every run got a fresh tenant, and broke in UI mode, where global
 * setup runs once per session: re-running a spec left a second "Ananya" on
 * the same board and the click resolved to two buttons (strict-mode
 * violation). So each candidate now carries a short tag: "Ananya Kvtz".
 *
 * Consonants only, deliberately. journey-candidate-view searches the page for
 * words that must never appear (score, threshold, rank, percentile, points),
 * and a tag with no vowels cannot spell any of them.
 */
export function aCandidate(first: string, resumeScore: number): Candidate {
  const consonants = 'bcdfghjklmnpqrstvwxz';
  const pick = () => consonants[Math.floor(Math.random() * consonants.length)];
  const tag = pick().toUpperCase() + pick() + pick() + pick();
  const name = `${first} ${tag}`;
  const stamp = `${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;
  return { name, email: `${name.toLowerCase().replace(/\W+/g, '.')}.${stamp}@e2e-anthire.com`, resumeScore };
}

/** Apply through the real public form: details, CV, consent, send. */
export async function applyThroughPublicForm(
  page: Page,
  openingId: string,
  candidate: Candidate,
  // `expectReceived: false` submits and returns without asserting success —
  // for the cases where the refusal IS the behaviour under test, such as
  // applying again inside a reapplication waiting period (PH3-B4).
  // `src` arrives through the link, exactly as a campaign or job board would
  // tag it (PH3-B1). The page passes it through; the server decides what
  // channel it means.
  opts: { language?: 'en' | 'hi' | 'te'; expectReceived?: boolean; src?: string } = {},
): Promise<void> {
  const query = opts.src ? `?src=${encodeURIComponent(opts.src)}` : '';
  await page.goto(`/apply/${openingId}${query}`);
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

  if (opts.expectReceived === false) return;
  await expect(page.getByText('Application received')).toBeVisible();
}

/** Open the emailed exam link and answer every question. */
export async function sitTheExam(
  page: Page,
  candidate: Candidate,
  opts: { answerCorrectly: boolean },
): Promise<string> {
  const token = await emailedExamToken(candidate);
  await page.goto(`/exam#${token}`);
  return answerTheExam(page, opts);
}

/** The single-use token out of the exam invitation email. */
export async function emailedExamToken(candidate: Candidate): Promise<string> {
  // 60s. The invitation is not sent when the shortlist button returns: a person
  // shortlists, the runner assigns the round and queues the email, and the
  // outbox worker delivers it on its own poll. That is about 5s on a quiet local
  // stack and comfortably more on a busy one, where the default 30s expired just
  // short of the email arriving — a failure that reads as a broken invitation.
  // The invitation's own subject ("<company> · Your assessment: <round>"), not
  // just the word "exam": that matched the shortlisting email too, whenever the
  // opening's title contained "Exam", and that email carries no link.
  const invite = await waitForMail(candidate.email, /Your (assessment|exam)\b/i, 60_000);
  return linkIn(invite, /\/exam#([A-Za-z0-9_-]{16,})/);
}

/**
 * Take the exam that is already open on this page: consent, start, answer
 * every question, submit, and return the verdict line.
 *
 * Split out of sitTheExam so a spec can reach the exam some other way than the
 * email — from the candidate's own dashboard, say.
 */
export async function answerTheExam(
  page: Page,
  opts: { answerCorrectly: boolean },
): Promise<string> {
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

/**
 * Claim the account the application-received email offered, and sign in.
 *
 * An applicant has no password until they follow that link: applying mints a
 * placeholder account so consent has somewhere to live. Specs that look at the
 * candidate's own pages go through this door, the one a real applicant uses.
 */
export async function claimAccountAndSignIn(
  page: Page,
  candidate: Candidate,
  password: string,
): Promise<void> {
  const confirmation = await waitForMail(candidate.email, /have your application/i);
  const token = linkIn(confirmation, /\/activate#([A-Za-z0-9_-]{16,})/);

  await page.goto(`/activate#${token}`);
  await expect(page.getByText('Track your application')).toBeVisible();
  await expect(
    page.getByText(candidate.email),
    'the page names the address the account will use',
  ).toBeVisible();
  await page.locator('#ac-new').fill(password);
  await page.locator('#ac-confirm').fill(password);
  await page.getByRole('button', { name: 'Create my account' }).click();
  await expect(page.getByRole('heading', { name: 'You’re all set' })).toBeVisible();

  await page.goto('/login');
  await page.getByTestId('login-email').fill(candidate.email);
  await page.getByTestId('login-password').fill(password);
  await page.getByTestId('login-submit').click();
  await page.waitForURL((url) => !url.pathname.startsWith('/login'));
}
