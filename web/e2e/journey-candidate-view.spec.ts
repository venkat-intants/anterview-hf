// What the candidate is allowed to see about themselves.
//
// The same candidate as the held journey, from their side of the screen. They
// applied, they fell below the round's bar, and a person has not decided yet.
// The truthful thing to tell them at that moment is that their application is
// under review — and the thing never to tell them is the number.
//
// So this walks the route a real applicant walks: the email that confirms the
// application carries a link to set a password, and only then can they see
// where they stand. The assertions are about wording, and about the absence of
// anything that scores them.

import {
  Api,
  createLiveMcqOpening,
  enrolmentFor,
  expect,
  runBackgroundPasses,
  signIn,
  test,
} from './support/fixtures';
import {
  aCandidate,
  applyThroughPublicForm,
  shortlistFromApplicants,
  sitTheExam,
} from './support/journeys';
import { linkIn, waitForMail } from './support/mail';

const PASSWORD = 'E2e-Candidate-View-7';

test.describe('what a candidate sees about themselves', () => {
  test('is told the stage in words, never a score', async ({
    page,
    browser,
    request,
    tenant,
  }) => {
    test.slow();
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const admin = await Api.as(request, tenant.accounts.super_admin);
    const opening = await createLiveMcqOpening(api, admin, 'E2E Candidate View');
    // A distinctive score, so the page can be searched for the exact number
    // the hiring side holds against this person.
    const candidate = aCandidate('Priya', 73);

    const candidateContext = await browser.newContext();
    const candidatePage = await candidateContext.newPage();
    await applyThroughPublicForm(candidatePage, opening.id, candidate);
    await runBackgroundPasses(request);

    await signIn(page, tenant.accounts.hr_manager);
    await shortlistFromApplicants(page, candidate, opening.title);

    // They fail the round, which holds them: internally 'held', and a person
    // has yet to decide anything.
    expect(await sitTheExam(candidatePage, candidate, { answerCorrectly: false }))
      .toContain('Not this time');
    await expect
      .poll(async () => (await enrolmentFor(api, opening.id, candidate.name)).status)
      .toBe('held');

    // ── The applicant claims the account the confirmation email offered ─────
    const confirmation = await waitForMail(candidate.email, /have your application/i);
    const token = linkIn(confirmation, /\/activate#([A-Za-z0-9_-]{16,})/);

    await candidatePage.goto(`/activate#${token}`);
    await expect(candidatePage.getByText('Track your application')).toBeVisible();
    await expect(
      candidatePage.getByText(candidate.email),
      'the page names the address the account will use',
    ).toBeVisible();
    await candidatePage.locator('#ac-new').fill(PASSWORD);
    await candidatePage.locator('#ac-confirm').fill(PASSWORD);
    await candidatePage.getByRole('button', { name: 'Create my account' }).click();
    await expect(candidatePage.getByRole('heading', { name: 'You’re all set' })).toBeVisible();

    // ── Signed in as themselves, on their own applications page ─────────────
    await signIn(candidatePage, { email: candidate.email, password: PASSWORD });
    await candidatePage.goto('/applications');

    const applications = candidatePage.locator('[aria-labelledby="applications-heading"]');
    await expect(applications.getByText(opening.title)).toBeVisible();
    await expect(
      applications.getByText('Under review'),
      'below the bar and awaiting a person reads as under review — they have not been rejected',
    ).toBeVisible();
    await expect(applications.getByText('Not progressing')).toHaveCount(0);

    // ── Nothing on this page scores them ────────────────────────────────────
    await candidatePage.getByRole('button', { name: 'Show history' }).click();
    await expect(candidatePage.getByText('Application received').first()).toBeVisible();

    const shown = (await applications.innerText()).toLowerCase();
    expect(shown, 'no percentage anywhere on the page').not.toMatch(/\d\s?%/);
    expect(shown, 'not the resume score the hiring side holds').not.toContain(
      String(candidate.resumeScore),
    );
    for (const word of ['score', 'threshold', 'rank', 'percentile', 'points']) {
      expect(shown, `the page must not talk about a ${word}`).not.toContain(word);
    }
  });
});
