// The company super admin's hiring board (E3).
//
// A board for the person who answers for hiring across the company and does not
// run it. So two claims: the health of each opening is worked out from how
// candidates have actually moved — an opening with no published workflow is a
// state of its own, not a blank row — and there is nothing here to press that
// changes anyone's candidacy. The second is the one worth a test: a read-only
// screen stays read-only only if something says so.

import {
  Api,
  createLiveMcqOpening,
  createOpening,
  expect,
  signIn,
  test,
} from './support/fixtures';

test.describe('the company hiring board', () => {
  test('bands every opening by how it is really going, and offers nothing to change', async ({
    page,
    request,
    tenant,
  }) => {
    test.slow();
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const admin = await Api.as(request, tenant.accounts.super_admin);

    const live = await createLiveMcqOpening(api, admin, 'E2E Board Live');
    const unpublished = await createOpening(api, 'E2E Board Unpublished');

    await signIn(page, tenant.accounts.super_admin);
    await page.goto('/superadmin/board');
    await expect(page.getByRole('heading', { name: 'Hiring board' })).toBeVisible();

    const liveRow = page.getByTestId(`board-row-${live.id}`);
    const unpublishedRow = page.getByTestId(`board-row-${unpublished.id}`);
    await expect(liveRow).toBeVisible();
    await expect(unpublishedRow).toBeVisible();

    // ── An opening with no workflow is its own state, not an empty row ───────
    await expect(
      unpublishedRow.getByText('Not published'),
      'nobody is moving through an opening with no published workflow, and the band says so',
    ).toBeVisible();

    // ── The filters are the only thing to press, and they only filter ────────
    await page.getByRole('button', { name: /^Not published · \d+$/ }).click();
    await expect(unpublishedRow).toBeVisible();
    await expect(liveRow, 'filtering hides the openings in other bands').toBeHidden();
    await page.getByRole('button', { name: /^Not published · \d+$/ }).click();
    await expect(liveRow).toBeVisible();

    // ── Read-only: nothing here acts on a candidate ─────────────────────────
    for (const label of [/^Hire/, /^Reject/, /^Shortlist/, /^Publish/, /^Close/, /^Delete/]) {
      await expect(
        page.getByRole('button', { name: label }),
        `a board for oversight must not offer "${String(label)}"`,
      ).toHaveCount(0);
    }

    // What it does offer is a way to look, which is the point of oversight.
    await expect(
      liveRow.getByRole('link', { name: live.title }),
      'each opening opens its own dashboard, to read',
    ).toBeVisible();
  });
});
