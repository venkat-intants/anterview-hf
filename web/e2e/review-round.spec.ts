// The round a person judges (C3/C4).
//
// Every other round produces a number. This one produces a verdict, and the
// whole point is that the verdict is someone's — so the screen has to give the
// reviewer what the round was built to assess, and the two things they can say
// have to mean what they claim. "Passes this round" advances. "Hold for a
// decision" does NOT reject: a reviewer saying "not from me" is still not the
// act that ends a candidacy, and D-05 keeps those separate on purpose.
//
// One candidate walks the whole shape: reviewed, held, released by a person,
// then passed.

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

test.describe('a round a person judges', () => {
  test('shows the reviewer the checklist, holds without rejecting, and advances on their word', async ({
    page,
    browser,
    request,
    tenant,
  }) => {
    test.slow();
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const admin = await Api.as(request, tenant.accounts.super_admin);
    const opening = await createLiveReviewOpening(api, admin, 'E2E Review Round');
    const candidate = aCandidate('Ravi', 77);

    const candidateContext = await browser.newContext();
    const candidatePage = await candidateContext.newPage();
    await applyThroughPublicForm(candidatePage, opening.id, candidate);
    await candidateContext.close();
    await runBackgroundPasses(request);

    const enrolment = await enrolmentFor(api, opening.id, candidate.name);
    await signIn(page, tenant.accounts.hr_manager);
    await shortlistFromApplicants(page, candidate, opening.title);

    // ── Waiting on a person, and the queue says so ───────────────────────────
    // A candidate parked on a review round is easy to lose: the system is not
    // going to move them, so if the queue does not show them, nobody will.
    await expect
      .poll(async () => (await decisionQueue(api, opening.id)).map((r) => r.full_name), {
        message: 'a candidate on a review round waits in the decision queue',
      })
      .toContain(candidate.name);

    await page.goto(`/hr/requisitions/${opening.id}/decisions`);
    await expect(page.getByText(`Waiting for review on ${REVIEW_ROUND_TITLE}`)).toBeVisible();

    // ── The round's own competencies, as the reviewer's checklist ────────────
    await expect(page.getByText(`What ${REVIEW_ROUND_TITLE} assesses`)).toBeVisible();
    await expect(page.getByText(/Communication · weight 0\.60/)).toBeVisible();
    await expect(page.getByText(/Ownership · weight 0\.40/)).toBeVisible();

    // ── "Not from me" holds. It does not reject ─────────────────────────────
    await page.getByRole('button', { name: 'Hold for a decision' }).click();
    await expect
      .poll(async () => (await enrolmentFor(api, opening.id, candidate.name)).status, {
        message: 'the reviewer’s verdict holds the candidate',
      })
      .toBe('held');

    const afterHold = await stageHistory(api, enrolment.id);
    expect(
      afterHold.filter((h) => h.to_status === 'rejected'),
      'a reviewer declining is not a rejection — that is a separate, explicit act',
    ).toHaveLength(0);

    // ── A person can overrule the hold ──────────────────────────────────────
    await page.reload();
    await page.getByRole('button', { name: 'Let them continue' }).click();
    await expect
      .poll(async () => (await enrolmentFor(api, opening.id, candidate.name)).status, {
        message: 'releasing the hold puts them back on the round',
      })
      .not.toBe('held');

    // ── And passing them finishes the workflow ──────────────────────────────
    await expect(page.getByRole('button', { name: 'Passes this round' })).toBeVisible();
    await page.getByRole('button', { name: 'Passes this round' }).click();

    await expect
      .poll(async () => {
        const row = (await decisionQueue(api, opening.id)).find(
          (r) => r.full_name === candidate.name,
        );
        return row?.awaiting_review ?? true;
      }, { message: 'once passed, they are no longer waiting on a review' })
      .toBe(false);

    // Where the reviewer's name is kept. The stage ledger marks both moves as
    // the runner's, which is literally what happened — it moved them in
    // response to a verdict. The verdict itself is the round result, and that
    // is what records that a person gave it.
    const row = (await decisionQueue(api, opening.id)).find(
      (r) => r.full_name === candidate.name,
    )!;
    // One live result, not two: passing after a hold SUPERSEDES the earlier
    // verdict rather than leaving both to be read as a history of one round.
    expect(row.round_results, 'the round carries one live verdict').toHaveLength(1);
    expect(row.round_results[0].graded_by, 'given by a person, and recorded as such').toBe(
      'human',
    );
    expect(row.round_results[0].passed).toBe(true);

    const history = await stageHistory(api, enrolment.id);
    expect(
      history.some((h) => h.to_status === 'held'),
      'the hold is on the ledger',
    ).toBe(true);
    expect(
      history.some((h) => h.from_status === 'held' && !h.automated && h.actor),
      'and the release names the person who overruled it',
    ).toBe(true);
    expect(
      history.filter((h) => h.to_status === 'rejected'),
      'nobody was rejected anywhere along the way',
    ).toHaveLength(0);
  });
});
