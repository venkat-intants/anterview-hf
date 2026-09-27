// Applying again after a rejection (PH3-B4, criteria 7-9).
//
// Phase 3 shipped this rule as a backend: a per-opening waiting period, a
// refusal that names the date, an override endpoint, an audit entry. None of it
// was reachable from the product — no screen set the period, no control called
// the override — so the two acceptance criteria that say "organizations can
// define" and "authorized users can override" were not true of the product,
// only of its API. This is the journey that makes them true, and the first
// browser test Phase 3 has.
//
// One run, in the order it happens: HR sets the period, rejects someone, the
// candidate is refused and told when they may apply, HR lets that one person
// through, and only then does the application go in.

import {
  Api,
  createLiveMcqOpening,
  expect,
  runBackgroundPasses,
  signIn,
  test,
} from './support/fixtures';
import { aCandidate, applyThroughPublicForm } from './support/journeys';

test.describe('applying again after a rejection', () => {
  test('is refused until the waiting period passes, or a person allows it', async ({
    page,
    browser,
    request,
    tenant,
  }) => {
    test.slow();
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const admin = await Api.as(request, tenant.accounts.super_admin);
    const opening = await createLiveMcqOpening(api, admin, 'E2E Reapply Cooldown');
    const candidate = aCandidate('Devika', 82);

    // ── 1. HR sets the waiting period on the opening ────────────────────────
    await signIn(page, tenant.accounts.hr_manager);
    await page.goto(`/hr/requisitions/${opening.id}/workflow`);

    const waitingPeriod = page.getByLabel(/waiting period/i);
    await expect(
      waitingPeriod,
      'blank until someone sets one — 0 would claim "reapply straight away"',
    ).toHaveValue('');
    await waitingPeriod.fill('30');
    await page.getByRole('button', { name: /save waiting period/i }).click();
    await expect(page.getByText('Rejected candidates must wait 30 days.')).toBeVisible();

    // It is the opening's setting, not this page's state: reload and it holds.
    await page.reload();
    await expect(page.getByLabel(/waiting period/i)).toHaveValue('30');

    // ── 2. Someone applies, and is rejected by a person with a reason ───────
    const candidateContext = await browser.newContext();
    const candidatePage = await candidateContext.newPage();
    await applyThroughPublicForm(candidatePage, opening.id, candidate);
    await runBackgroundPasses(request);

    await page.goto('/hr/applicants');
    const row = page.getByRole('button', { name: `Open details for ${candidate.name}` });
    await row.first().waitFor();
    await row.first().click();
    const drawer = page.getByRole('dialog', { name: `${candidate.name} applicant details` });
    await drawer.getByRole('button', { name: 'Reject', exact: true }).click();
    await drawer.getByLabel('Reason', { exact: true }).selectOption({ index: 1 });
    await drawer.getByLabel(/^why/i).fill('Not enough production experience for this role.');
    await drawer.getByRole('button', { name: /confirm reject/i }).click();

    // ── 3. They apply again, and are told the date, not just "no" ───────────
    // A refusal with no date cannot be acted on, so the candidate simply
    // retries. The message carries the day they may apply from.
    await applyThroughPublicForm(candidatePage, opening.id, candidate, {
      expectReceived: false,
    });
    await expect(
      candidatePage.getByText(/not able to consider a new application until \d{4}-\d{2}-\d{2}/),
      'refused, with the date they may apply from',
    ).toBeVisible();
    await expect(candidatePage.getByText('Application received')).toHaveCount(0);

    // ── 4. HR lets this one person through ──────────────────────────────────
    await page.goto('/hr/applicants');
    const rejectedRow = page.getByRole('button', {
      name: `Open details for ${candidate.name}`,
    });
    await rejectedRow.first().waitFor();
    await rejectedRow.first().click();
    const again = page.getByRole('dialog', { name: `${candidate.name} applicant details` });

    await again
      .getByRole('button', { name: new RegExp(`let ${candidate.name} reapply`, 'i') })
      .click();
    await expect(
      again.getByText(/earlier rejection stays on their record/i),
      'the override forgives the wait, not the rejection',
    ).toBeVisible();
    await again
      .getByRole('button', { name: new RegExp(`confirm ${candidate.name} may reapply`, 'i') })
      .click();
    await expect(again.getByText(/may reapply now/i)).toBeVisible();

    // ── 5. Now the same person can apply, and it goes in ────────────────────
    await applyThroughPublicForm(candidatePage, opening.id, candidate);
    await candidateContext.close();
  });
});
