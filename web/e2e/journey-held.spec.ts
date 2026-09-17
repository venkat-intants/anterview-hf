// The candidate who falls below the bar.
//
// This is the journey the whole design turns on. A threshold decides whether
// someone advances; it never decides whether their candidacy ends. So a
// candidate who fails the exam is HELD — parked in front of a person, with the
// reason they stopped — and stays held until a person either lets them carry
// on or rejects them in as many words (D-05).
//
// The test therefore checks two things a passing suite could otherwise miss:
// that nothing anywhere rejected them, and that the screen offers a person the
// option to overrule the score.

import {
  Api,
  createLiveMcqOpening,
  decisionQueue,
  enrolmentFor,
  expect,
  runBackgroundPasses,
  signIn,
  stageHistory,
  test,
} from './support/fixtures';
import {
  aCandidate,
  applyThroughPublicForm,
  shortlistFromApplicants,
  sitTheExam,
} from './support/journeys';

test.describe('a candidate below the bar', () => {
  test('is held rather than rejected, and is rejected only by a person, with a reason', async ({
    page,
    browser,
    request,
    tenant,
  }) => {
    test.slow();
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const admin = await Api.as(request, tenant.accounts.super_admin);
    const opening = await createLiveMcqOpening(api, admin, 'E2E Held Journey');
    const candidate = aCandidate('Arjun', 74);

    const candidateContext = await browser.newContext();
    const candidatePage = await candidateContext.newPage();
    await applyThroughPublicForm(candidatePage, opening.id, candidate);
    await runBackgroundPasses(request);

    const enrolment = await enrolmentFor(api, opening.id, candidate.name);
    await signIn(page, tenant.accounts.hr_manager);
    await shortlistFromApplicants(page, candidate, opening.title);

    // ── They sit the exam and fail it ───────────────────────────────────────
    const verdict = await sitTheExam(candidatePage, candidate, { answerCorrectly: false });
    expect(verdict).toContain('Not this time');
    await candidateContext.close();

    // ── Held, not rejected ──────────────────────────────────────────────────
    await expect
      .poll(async () => (await enrolmentFor(api, opening.id, candidate.name)).status, {
        message: 'failing a round holds the candidate',
      })
      .toBe('held');

    const queued = (await decisionQueue(api, opening.id)).find(
      (r) => r.full_name === candidate.name,
    );
    expect(queued, 'a held candidate is in the queue, not filed away out of sight').toBeTruthy();
    expect(queued!.held).toBe(true);
    expect(queued!.held_reason, 'the queue says why they stopped').toBeTruthy();

    const beforeDecision = await stageHistory(api, enrolment.id);
    expect(
      beforeDecision.filter((h) => h.to_status === 'rejected'),
      'nothing may reject a candidate for falling below a threshold',
    ).toHaveLength(0);
    expect(
      beforeDecision.some((h) => h.to_status === 'held' && h.automated),
      'the hold is the system’s work, and holding is all it may do',
    ).toBe(true);

    // ── On the screen: a person is offered both ways out ────────────────────
    await page.goto(`/hr/requisitions/${opening.id}/decisions`);
    await expect(page.getByText(candidate.name).first()).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Let them continue' }),
      'a person can overrule the score and let a held candidate carry on',
    ).toBeVisible();

    await page.getByRole('button', { name: 'Reject', exact: true }).click();
    await expect(page.getByText('Choose a reason above first.')).toBeVisible();
    const confirm = page.getByRole('button', { name: 'Confirm' });
    await expect(confirm, 'rejecting without a reason is refused too').toBeDisabled();

    // Only reasons that apply to a rejection are offered.
    const reason = page.getByLabel('Reason', { exact: true });
    await expect(reason.locator('option', { hasText: 'Position closed' })).toHaveCount(1);
    await reason.selectOption({ label: 'Skills / competency fit' });

    await page
      .getByLabel('Why (recorded against your name)')
      .fill('Aptitude below the bar for this role');
    await expect(confirm).toBeEnabled();
    await confirm.click();

    // ── Rejected — by a named person, with their reason ─────────────────────
    await expect
      .poll(async () => (await enrolmentFor(api, opening.id, candidate.name)).status, {
        message: 'the person’s rejection is recorded',
      })
      .toBe('rejected');

    const history = await stageHistory(api, enrolment.id);
    const rejections = history.filter((h) => h.to_status === 'rejected');
    expect(rejections, 'exactly one rejection, the one a person made').toHaveLength(1);
    expect(rejections[0].automated).toBe(false);
    expect(rejections[0].actor, 'the ledger names who decided').toBeTruthy();
    expect(rejections[0].reason).toContain('below the bar');
    expect(rejections[0].reason_code).toBe('skills_fit');
  });
});
