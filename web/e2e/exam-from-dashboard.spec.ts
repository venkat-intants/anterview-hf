// Starting the assessment without the email.
//
// The assessment used to reach a candidate only as an emailed link. Their
// applications page named the round — "Round 1 of 1 · Aptitude" — and offered
// no way into it, so an invitation lost to a spam filter left an exam that
// existed, was visible by name, and could not be opened.
//
// This candidate never opens that email. They sign in, start the assessment
// from their own dashboard, and pass — and the emailed link, which they never
// used, stops working the moment a new one is handed out: one assessment, one
// live link.

import {
  Api,
  createLiveMcqOpening,
  decisionQueue,
  expect,
  runBackgroundPasses,
  signIn,
  test,
} from './support/fixtures';
import {
  aCandidate,
  answerTheExam,
  applyThroughPublicForm,
  claimAccountAndSignIn,
  emailedExamToken,
  shortlistFromApplicants,
} from './support/journeys';

const PASSWORD = 'E2e-Exam-Dashboard-7';

test.describe('an assessment started from the candidate’s dashboard', () => {
  test('opens without the email, is passed, and retires the emailed link', async ({
    page,
    browser,
    request,
    tenant,
  }) => {
    test.slow();
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const admin = await Api.as(request, tenant.accounts.super_admin);
    const opening = await createLiveMcqOpening(api, admin, 'E2E Exam From Dashboard');
    const candidate = aCandidate('Nisha', 81);

    const candidateContext = await browser.newContext();
    const candidatePage = await candidateContext.newPage();
    await applyThroughPublicForm(candidatePage, opening.id, candidate);
    await runBackgroundPasses(request);

    await signIn(page, tenant.accounts.hr_manager);
    await shortlistFromApplicants(page, candidate, opening.title);

    // The invitation goes out as usual. The spec reads it only to hold on to
    // the link, and proves at the end that it no longer works — the candidate
    // never clicks it.
    const emailedToken = await emailedExamToken(candidate);

    // ── Signed in, on their own page, the assessment is there to start ──────
    await claimAccountAndSignIn(candidatePage, candidate, PASSWORD);
    await candidatePage.goto('/applications');

    const cta = candidatePage.getByTestId('exam-cta');
    await expect(cta.getByText('Your assessment is ready')).toBeVisible();
    await expect(
      candidatePage.getByText('Your assessment is ready. Start it from here.'),
      'the next step points at the button, not at an email they may not have',
    ).toBeVisible();
    await expect(candidatePage.getByText(/Watch your email/)).toHaveCount(0);

    // ── Started from here, taken, passed ────────────────────────────────────
    await cta.getByRole('button', { name: 'Start assessment' }).click();
    // `/exam`, and then without the token: the page reads the fragment once and
    // clears it, so a live credential is not sitting in the address bar of a
    // fullscreened, proctored, frequently screen-shared session, nor left in
    // history on a shared machine.
    await candidatePage.waitForURL(/\/exam(#|$)/);
    await expect
      .poll(() => new URL(candidatePage.url()).hash, {
        message: 'the token is taken out of the URL once the page holds it',
      })
      .toBe('');
    expect(await answerTheExam(candidatePage, { answerCorrectly: true })).toContain(
      'You passed',
    );

    // ── It counts, exactly as if it had come from the email ─────────────────
    await expect
      .poll(async () => (await decisionQueue(api, opening.id)).map((r) => r.full_name), {
        message: 'passing it moves them on, like any other way in',
      })
      .toContain(candidate.name);

    // ── With the assessment done, the dashboard stops offering it ────────────
    await candidatePage.goto('/applications');
    await expect(candidatePage.getByText(opening.title)).toBeVisible();
    await expect(candidatePage.getByTestId('exam-cta')).toHaveCount(0);

    // ── And the link they never used is dead ────────────────────────────────
    // Opened from /applications, not from the exam page: /exam#new → /exam#old
    // changes only the fragment, the browser keeps the page, and the exam page
    // (which reads its token once, on load) never tries the old one at all.
    await candidatePage.goto(`/exam#${emailedToken}`);
    await expect(
      candidatePage.getByText("This exam link isn't valid"),
      'handing out a fresh link retires the emailed one: one assessment, one live link',
    ).toBeVisible();

    await candidateContext.close();
  });
});
