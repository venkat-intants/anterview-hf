// An opening needs someone else's approval before the public can apply
// (PH3-B2).
//
// Two people, because that is the point: HR writes the opening and its budget,
// and the company's super admin decides. HR cannot approve their own — not by
// a hidden button, but by not having the power at all — and until someone has
// approved it, the public application page for that opening does not open.
//
// One run, in the order it happens: budget, submit, refused, changes asked
// for, resubmit, approved, and only then can anybody apply.

import { expect, test } from './support/fixtures';
import { Api, createOpening, signIn } from './support/fixtures';

test.describe('an opening waiting to be approved', () => {
  test('cannot be approved by the person who wrote it, and takes no applications until it is', async ({
    page,
    browser,
    request,
    tenant,
  }) => {
    test.slow();
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const opening = await createOpening(api, 'E2E Approval');

    await signIn(page, tenant.accounts.hr_manager);
    await page.goto(`/hr/requisitions/${opening.id}/workflow`);
    await expect(page.getByText('Not submitted')).toBeVisible();

    // ── The budget, which is what the approver is really being asked about ──
    await page.getByLabel('Budget amount').fill('1800000');
    await page.getByLabel('Budget notes (optional)').fill('Signed off in the Q4 plan');
    await page.getByRole('button', { name: 'Save budget' }).click();
    await expect(page.getByText('Budget saved.')).toBeVisible();

    // ── HR has no way to approve it, and the page says who does ─────────────
    await expect(
      page.getByRole('button', { name: 'Approve' }),
      'HR cannot approve their own opening — the control does not exist for them',
    ).toHaveCount(0);

    // ── Until it is approved, the public cannot apply ───────────────────────
    const publicView = await browser.newContext();
    const visitor = await publicView.newPage();
    await visitor.goto(`/apply/${opening.id}`);
    await expect(
      visitor.getByText('This opening is not accepting applications'),
      'an unapproved opening is not a place anyone can apply to',
    ).toBeVisible({ timeout: 30_000 });
    const refusal = (await visitor.locator('body').innerText()).toLowerCase();
    for (const word of ['approval', 'approved', 'pending', 'draft']) {
      expect(refusal, `a candidate is not told the opening is ${word}`).not.toContain(word);
    }

    // ── Sent for approval ───────────────────────────────────────────────────
    await page
      .getByLabel('Note for your company admin (optional)')
      .fill('Budget agreed with Finance.');
    await page.getByRole('button', { name: 'Submit for approval' }).click();
    await expect(page.getByText('Waiting for approval')).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Submit for approval' }),
      'and cannot be submitted twice while it is already in the queue',
    ).toHaveCount(0);

    // ── The approver asks for changes, in their own words ───────────────────
    const adminContext = await browser.newContext();
    const adminPage = await adminContext.newPage();
    await signIn(adminPage, tenant.accounts.super_admin);
    await adminPage.goto('/superadmin/approvals');
    await expect(adminPage.getByRole('link', { name: opening.title })).toBeVisible({
      timeout: 30_000,
    });
    await expect(
      adminPage.getByText('Budget agreed with Finance.'),
      'the approver reads why HR asked, not just what they asked for',
    ).toBeVisible();
    await expect(
      adminPage.getByText(/18,00,000/),
      'and the money, which is the thing being approved',
    ).toBeVisible();

    await adminPage.getByLabel(`Note for ${opening.title}`).fill('Cap it at 15 lakh.');
    // Two clicks on purpose: sending an opening back costs somebody a week, so
    // the first click only arms it.
    await adminPage.getByRole('button', { name: 'Request changes' }).click();
    await adminPage.getByRole('button', { name: 'Confirm — send back' }).click();

    await page.reload();
    await expect(page.getByText('Changes requested')).toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByText('Cap it at 15 lakh.'),
      'HR is told WHAT to change, not merely that something was wrong',
    ).toBeVisible();

    // Sent back is not published: the refusal has to survive a round trip.
    await visitor.reload();
    await expect(
      visitor.getByText('This opening is not accepting applications'),
      'an opening sent back for changes still takes no applications',
    ).toBeVisible({ timeout: 30_000 });

    // ── HR changes it and sends it back ─────────────────────────────────────
    await page.getByLabel('Budget amount').fill('1500000');
    await page.getByRole('button', { name: 'Save budget' }).click();
    await expect(page.getByText('Budget saved.')).toBeVisible();
    await page.getByRole('button', { name: 'Submit for approval' }).click();
    await expect(page.getByText('Waiting for approval')).toBeVisible();

    // ── Approved by the other person ────────────────────────────────────────
    await adminPage.reload();
    await expect(adminPage.getByRole('link', { name: opening.title })).toBeVisible({
      timeout: 30_000,
    });
    await expect(
      adminPage.getByText(/15,00,000/),
      'the approver decides on the corrected number, not the one they refused',
    ).toBeVisible();
    await adminPage.getByRole('button', { name: 'Approve' }).first().click();

    await page.reload();
    await expect(page.getByText('Approved', { exact: true })).toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByText(/^Approved .+\./),
      'and when, because an approval is a decision somebody has to be able to date',
    ).toBeVisible();

    // ── What approval actually unlocks ──────────────────────────────────────
    // The warning that stood above the budget is gone, and the control it was
    // warning about works. Approval is the gate in front of publishing
    // (PH3-B4a), not publishing itself — so the public page is still shut, and
    // that is correct rather than a failure.
    await expect(
      page.getByText('Until this opening is approved it cannot accept public applications'),
    ).toHaveCount(0);
    await expect(
      page.getByLabel('Go live at'),
      'scheduling was refused while it was unapproved, and is offered now',
    ).toBeEnabled();

    await adminContext.close();
    await publicView.close();
  });
});
