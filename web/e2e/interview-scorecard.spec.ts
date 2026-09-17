// A human interview scored by a named interviewer (PH4-A1, with the A5 kit).
//
// Two people and one rule. HR assigns an interviewer to a candidate's review
// round; the interviewer — who can see nothing but their own assignments —
// reads the kit HR wrote, keeps private notes, scores each frozen criterion and
// submits. HR then reads that evidence. And the rule underneath (D-05): a
// scorecard is evidence, never a decision — submitting it moves nobody.

import {
  Api,
  createLiveReviewOpening,
  decisionQueue,
  enrolmentFor,
  expect,
  REVIEW_ROUND_TITLE,
  runBackgroundPasses,
  signIn,
  stageHistory,
  test,
} from './support/fixtures';
import { aCandidate, applyThroughPublicForm, shortlistFromApplicants } from './support/journeys';

interface EnrolmentScorecards {
  rounds: {
    round_title: string;
    scorecards: {
      interviewer_name: string;
      state: string;
      summary: string | null;
      scores: Record<string, { score: number | null; not_assessed: boolean; evidence: string | null }> | null;
    }[];
  }[];
}

test.describe('a human interview, scored by the interviewer HR assigned', () => {
  test('is assigned, scored from the kit and submitted — and moves nobody', async ({
    page,
    browser,
    request,
    tenant,
  }) => {
    test.slow();
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const admin = await Api.as(request, tenant.accounts.super_admin);
    const opening = await createLiveReviewOpening(api, admin, 'E2E Interview Scorecard');

    // HR's kit for the round — guidance on the frozen criteria, not new ones.
    await api.put(`/hr/rounds/${opening.roundId}/kit`, {
      instructions: 'Forty minutes. Start with a project they led.',
      interviewer_notes_from_hr: null,
      criteria: [
        {
          competency_id: 'ownership',
          what_to_evaluate: ['Owns outcomes, not just tasks'],
          look_for: ['Names what they would do differently'],
          probes: ['Tell me about something you shipped that went wrong.'],
        },
      ],
    });

    const candidate = aCandidate('Meera', 81);
    const candidateContext = await browser.newContext();
    await applyThroughPublicForm(await candidateContext.newPage(), opening.id, candidate);
    await candidateContext.close();
    await runBackgroundPasses(request);
    const enrolment = await enrolmentFor(api, opening.id, candidate.name);

    await signIn(page, tenant.accounts.hr_manager);
    await shortlistFromApplicants(page, candidate, opening.title);
    await expect
      .poll(async () => (await decisionQueue(api, opening.id)).map((r) => r.full_name), {
        message: 'a candidate on a review round waits in the decision queue',
      })
      .toContain(candidate.name);

    // ── HR assigns the interviewer, from the candidate's drawer ─────────────
    await page.goto(`/hr/requisitions/${opening.id}/decisions`);
    await page.getByRole('button', { name: `Open details for ${candidate.name}` }).first().click();
    const drawer = page.getByRole('dialog', { name: candidate.name });
    await drawer.getByRole('button', { name: 'Assign interviewers' }).click();
    await drawer.getByLabel('Round').selectOption({ label: REVIEW_ROUND_TITLE });
    await drawer.getByLabel(/E2E Interviewer/).check();
    await drawer.getByRole('button', { name: 'Assign', exact: true }).click();
    await expect(drawer.getByText('E2E Interviewer', { exact: true })).toBeVisible();
    await expect(drawer.getByText('Not submitted yet.')).toBeVisible();

    const before = await enrolmentFor(api, opening.id, candidate.name);
    const ledgerBefore = (await stageHistory(api, enrolment.id)).length;

    // ── The interviewer: their own assignments, the kit, a scorecard ────────
    const ivContext = await browser.newContext();
    const iv = await ivContext.newPage();
    await signIn(iv, tenant.accounts.interviewer);
    await expect(iv).toHaveURL(/\/interviewer$/);
    await iv
      .getByRole('link', { name: `Open scorecard for ${candidate.name} — ${REVIEW_ROUND_TITLE}` })
      .click();

    await expect(iv.getByText('Forty minutes. Start with a project they led.')).toBeVisible();
    await expect(iv.getByText('Owns outcomes, not just tasks')).toBeVisible();
    await expect(iv.getByText('Tell me about something you shipped that went wrong.')).toBeVisible();

    const notes = iv.getByLabel(/Private notes/);
    await notes.fill('Led the payments migration; candid about the rollback.');
    await iv.getByRole('button', { name: 'Save notes' }).click();
    await expect(iv.getByText(/^Saved /)).toBeVisible();

    await iv.getByRole('tab', { name: 'Scorecard' }).click();
    await iv
      .getByRole('radiogroup', { name: 'Score — Communication' })
      .getByRole('radio', { name: 'Score 4' })
      .click();
    // The other criterion by keyboard: one Tab stop, arrows move and select.
    const ownership = iv.getByRole('radiogroup', { name: 'Score — Ownership' });
    await ownership.getByRole('radio', { name: 'Score 1' }).focus();
    await iv.keyboard.press('ArrowRight');
    await iv.keyboard.press('ArrowRight');
    await expect(ownership.getByRole('radio', { name: 'Score 3' })).toHaveAttribute('aria-checked', 'true');

    await iv.getByLabel('Evidence').first().fill('Explained the migration plan clearly to a non-engineer.');
    await iv.getByLabel('Overall summary').fill('Clear communicator; ownership is still growing.');
    await iv.getByRole('button', { name: /submit scorecard/i }).click();
    await iv.getByRole('button', { name: /confirm submit/i }).click();
    await expect(iv.getByText('Submitted', { exact: true }).first()).toBeVisible();
    await expect(iv.getByRole('button', { name: /request correction/i })).toBeVisible();
    await ivContext.close();

    // ── HR reads the evidence ───────────────────────────────────────────────
    const evidence = await api.get<EnrolmentScorecards>(`/hr/enrolments/${enrolment.id}/scorecards`);
    const card = evidence.rounds[0].scorecards.find((s) => s.interviewer_name === 'E2E Interviewer');
    expect(card?.state).toBe('submitted');
    expect(card?.scores?.communication.score).toBe(4);
    expect(card?.scores?.ownership.score).toBe(3);
    expect(JSON.stringify(evidence), 'private notes never reach HR').not.toContain('payments migration');

    await page.reload();
    await expect(page.getByText('1/1 scorecards in')).toBeVisible();
    await page.getByRole('button', { name: `Open details for ${candidate.name}` }).first().click();
    await expect(
      page.getByRole('dialog', { name: candidate.name }).getByText('Clear communicator; ownership is still growing.'),
    ).toBeVisible();

    // ── And it decided nothing ──────────────────────────────────────────────
    const after = await enrolmentFor(api, opening.id, candidate.name);
    expect(after.status, 'a submitted scorecard does not change the candidate’s status').toBe(before.status);
    expect(
      (await stageHistory(api, enrolment.id)).length,
      'nor write anything to the stage ledger',
    ).toBe(ledgerBefore);
  });
});
