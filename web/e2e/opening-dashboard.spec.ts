// The opening's dashboard, with real people in it (E1).
//
// opening.spec.ts covers the empty case — a new opening, nothing live. This is
// the one that matters day to day: someone applied, a person shortlisted them,
// they fell below the round's bar. The dashboard has to say all of that in the
// language the product means it, and the held card is the test of that. "Held
// for your decision. Below a round's threshold. Held, not rejected" is the
// whole D-05 promise written on the screen where HR reads it — and it has to
// name the person, or it is a number nobody acts on.

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

test.describe('an opening with candidates in it', () => {
  test('shows who is held, why, and what is waiting on a person', async ({
    page,
    browser,
    request,
    tenant,
  }) => {
    test.slow();
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const admin = await Api.as(request, tenant.accounts.super_admin);
    const opening = await createLiveMcqOpening(api, admin, 'E2E Dashboard');
    const candidate = aCandidate('Devika', 68);

    const candidateContext = await browser.newContext();
    const candidatePage = await candidateContext.newPage();
    await applyThroughPublicForm(candidatePage, opening.id, candidate);
    await runBackgroundPasses(request);

    await signIn(page, tenant.accounts.hr_manager);
    await shortlistFromApplicants(page, candidate, opening.title);
    expect(await sitTheExam(candidatePage, candidate, { answerCorrectly: false }))
      .toContain('Not this time');
    await candidateContext.close();

    await expect
      .poll(async () => (await enrolmentFor(api, opening.id, candidate.name)).status)
      .toBe('held');

    // ── The dashboard, read as HR reads it ──────────────────────────────────
    await page.goto(`/hr/requisitions/${opening.id}`);
    await expect(page.getByTestId('workflow-state')).toBeVisible();

    const held = page.getByTestId('held-pool');
    await expect(held.getByText('Held for your decision')).toBeVisible();
    await expect(
      held.getByText(/Held, not rejected/),
      'the card says what a hold is, where the person acting on it will read it',
    ).toBeVisible();
    await expect(
      held.getByText(candidate.name),
      'and names them — a count alone is not something anyone acts on',
    ).toBeVisible();
    await expect(
      held.getByText(/Aptitude: \d+% against a \d+% threshold/),
      'with the round and the bar they fell under, not just "held"',
    ).toBeVisible();

    // ── The rest of the picture ─────────────────────────────────────────────
    await expect(page.getByTestId('funnel')).toBeVisible();
    await expect(page.getByTestId('manual-steps').getByText('Waiting on you, by design'))
      .toBeVisible();
    await expect(page.getByTestId('timing').getByText(/Median time from applying/))
      .toBeVisible();
    await expect(page.getByTestId('needs-attention')).toBeVisible();
    await expect(
      page.getByTestId('held-pool').getByText('Nobody is held.'),
      'the empty-state line is gone once somebody is',
    ).toHaveCount(0);
  });
});
