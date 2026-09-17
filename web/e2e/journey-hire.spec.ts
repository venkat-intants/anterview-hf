// The whole hire, as the people in it live it: a candidate applies from the
// public page, HR shortlists, the candidate sits the exam that arrives by
// email and passes, and HR hires them with a reason.
//
// One run, because that is how it happens. It covers automatic enrolment into
// the published workflow, the runner advancing a candidate on a result, the
// emailed exam link, and the final decision — and D-05 the whole way through:
// scoring moves nobody, and the hire is refused until a person writes why.

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

test.describe('a candidate is hired', () => {
  test('applies, is shortlisted, passes the exam, and is hired with a reason', async ({
    page,
    browser,
    request,
    tenant,
  }) => {
    test.slow(); // four screens, two people, and a background pass
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const admin = await Api.as(request, tenant.accounts.super_admin);
    const opening = await createLiveMcqOpening(api, admin, 'E2E Hire Journey');
    const candidate = aCandidate('Meera', 82);

    // ── The candidate applies, signed in to nothing ─────────────────────────
    const candidateContext = await browser.newContext();
    const candidatePage = await candidateContext.newPage();
    await applyThroughPublicForm(candidatePage, opening.id, candidate);

    // Scoring runs on a timer; run that pass now rather than waiting for it.
    await runBackgroundPasses(request);

    // ── Scored, and still exactly where they were ───────────────────────────
    // The score is the whole point of the pass that just ran, and it must not
    // have moved anybody: nobody is waiting on a decision until a person acts.
    const enrolment = await enrolmentFor(api, opening.id, candidate.name);
    expect(
      await decisionQueue(api, opening.id),
      'scoring an application must not put anyone in front of a decision',
    ).toHaveLength(0);

    await signIn(page, tenant.accounts.hr_manager);
    await shortlistFromApplicants(page, candidate, opening.title);

    // ── The candidate sits the exam from the emailed link ───────────────────
    const verdict = await sitTheExam(candidatePage, candidate, { answerCorrectly: true });
    expect(verdict).toContain('You passed');
    await candidateContext.close();

    // ── Finishing the workflow puts them in front of a person ───────────────
    await expect
      .poll(async () => (await decisionQueue(api, opening.id)).map((r) => r.full_name), {
        message: 'a candidate who finished the workflow reaches the decision queue',
      })
      .toContain(candidate.name);

    await page.goto(`/hr/requisitions/${opening.id}/decisions`);
    await expect(page.getByText(candidate.name).first()).toBeVisible();

    // Hiring is refused until a reason category is chosen AND a reason is
    // written (PH4-O4): the panel says which is missing, and the confirm
    // button stays dead until both are there.
    await page.getByRole('button', { name: 'Hire', exact: true }).click();
    await expect(page.getByText('Choose a reason above first.')).toBeVisible();
    const confirm = page.getByRole('button', { name: 'Confirm' });
    await expect(confirm).toBeDisabled();

    await page.getByLabel('Reason', { exact: true }).selectOption({ label: 'Skills / competency fit' });
    await expect(page.getByText(/Write at least \d+ characters above first\./)).toBeVisible();
    await expect(confirm, 'a category alone is not a reason').toBeDisabled();

    await page.getByLabel('Why (recorded against your name)').fill('Strong aptitude result');
    await expect(confirm).toBeEnabled();
    await confirm.click();

    // ── Recorded, and they leave the queue ──────────────────────────────────
    await expect
      .poll(async () => (await decisionQueue(api, opening.id)).map((r) => r.full_name), {
        message: 'a decided candidate leaves the queue',
      })
      .not.toContain(candidate.name);

    const decided = await enrolmentFor(api, opening.id, candidate.name);
    expect(decided.status).toBe('hired');

    const history = await stageHistory(api, enrolment.id);
    const hire = history.find((h) => h.to_status === 'hired');
    expect(hire, 'the hire is on the stage ledger').toBeTruthy();
    expect(hire!.automated, 'a hire is a person’s act, never the system’s').toBe(false);
    expect(hire!.actor, 'the ledger names who decided').toBeTruthy();
    expect(hire!.reason).toContain('Strong aptitude');
    expect(hire!.reason_code, 'the category is on the ledger, for analytics').toBe('skills_fit');
    expect(hire!.reason_label, 'with its label as chosen').toBe('Skills / competency fit');
  });
});
