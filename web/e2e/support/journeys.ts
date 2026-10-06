// The steps a candidate takes, written once: applying through the public form
// and sitting an exam from the emailed link. Two journeys share them, and a
// spec reads as what a person did rather than as a list of clicks.

import { expect, type Locator, type Page } from '@playwright/test';
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
  await sendRespectingTheRateLimit(page, send);

  if (opts.expectReceived === false) return;
  await expect(page.getByText('Application received')).toBeVisible();
}

/** The rate-limit alert the apply form renders on a 429. */
const TOO_MANY = 'Too many requests. Please wait a minute and try again.';

/**
 * Click Send, and if the door answers 429, wait the window out and click again.
 *
 * `POST /apply/{id}` and `POST /apply/draft/submit` share
 * `rate_limit("public_apply_submit", 6)` — a FIXED 60-second window, keyed on
 * the client IP, counted across both doors. Every spec in this suite submits
 * from the same address, so a run that happens to put seven submissions inside
 * one minute gets a 429 on the seventh, the success panel never renders, and
 * whichever spec was unlucky fails.
 *
 * That is what was happening. In CI it showed up as one or two "flaky" specs
 * per run — `exam-from-dashboard` and `save-and-resume` — and it took three
 * attempts to diagnose because the evidence was in a page snapshot that only
 * the failure artifact carries, and the artifact was not uploaded on a run that
 * went green on retry. The alert is right there in the snapshot:
 * "Too many requests. Please wait a minute and try again."
 *
 * WHY WAIT RATHER THAN RESET THE LIMITER. The limit is the product's defence on
 * an anonymous, unauthenticated write path, and the integration suite only
 * clears it because it can reach Redis directly. Doing the same from here would
 * mean either a Redis client in the browser suite or a test-only endpoint whose
 * whole purpose is switching a security control off — and `test_hooks.py` says
 * of itself that its hooks "change nothing a pass would not change anyway",
 * which that would not. Waiting needs no new surface and models what a real
 * rate-limited candidate does: they wait and press the button again.
 *
 * The window is fixed at 60s and expires from the first request in it, so one
 * wait is always enough; the second attempt is a safety net, not an expectation.
 */
async function sendRespectingTheRateLimit(page: Page, send: Locator): Promise<void> {
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    await send.click();
    // Whichever lands first: the reply (any state renders the shared panel) or
    // the limiter. Racing them rather than waiting on the alert keeps the
    // normal path at its old speed.
    const limited = page.getByText(TOO_MANY);
    const accepted = page.getByText('Application received');
    try {
      await expect(limited.or(accepted).first()).toBeVisible({ timeout: 30_000 });
    } catch {
      return; // neither appeared; let the caller's own assertion report it
    }
    if (!(await limited.isVisible())) return;
    if (attempt === 3) {
      throw new Error(
        `the apply door answered 429 on ${attempt} attempts, ~${attempt} minutes apart. ` +
          'That is more than a shared 6-per-minute window explains — check whether ' +
          'the hourly cap (public_apply_submit_hourly, 60/hour per IP) is exhausted, ' +
          'which a whole suite re-run inside one hour can do.',
      );
    }
    // The window is 60s from its first request; 65 clears it whenever it opened.
    await page.waitForTimeout(65_000);
  }
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
