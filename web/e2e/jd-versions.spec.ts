// Rewriting a job description without changing what candidates are reading
// (PH3-B3, and the version-aware half of PH3-B6).
//
// A recruiter reworks an advert over a week. The whole feature rests on one
// claim no unit test can make: while they are drafting, the public page still
// shows the OLD wording, and the moment they publish, it shows the new one.
// That takes two browsers — an HR tab and a candidate looking at the advert —
// so it is tested here or nowhere.
//
// The second claim is about the history. Going back to earlier wording is a
// publish of that version, not an edit of it, so the wording that was replaced
// stays in the record. An advert is what a company told applicants the job
// was; a history that can be rewritten is not evidence of anything.

import { expect, test } from './support/fixtures';
import { Api, createLiveMcqOpening, signIn } from './support/fixtures';

const ORIGINAL = 'We are hiring a backend engineer to look after the payments service.';
const DRAFTED = 'We are hiring a backend engineer. This role carries a weekly on-call rota.';

test.describe('a job description being rewritten', () => {
  test('drafts privately, publishes deliberately, and keeps every version it replaced', async ({
    page,
    browser,
    request,
    tenant,
  }) => {
    test.slow();
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const admin = await Api.as(request, tenant.accounts.super_admin);
    const opening = await createLiveMcqOpening(api, admin, 'E2E JD Versions');

    // The advert as it stands. Writing it through the ordinary requisition
    // patch is the compatibility case (PH3-B3 #10): every screen that has
    // always written a JD this way still works, and now the wording it
    // replaces is kept instead of being destroyed.
    await api.patch(`/hr/requisitions/${opening.id}`, { jd_text: ORIGINAL });

    // A candidate reading the advert. This tab stays open for the whole test:
    // what it shows at each point is the only thing that actually matters.
    const publicView = await browser.newContext();
    const visitor = await publicView.newPage();
    await visitor.goto(`/apply/${opening.id}`);
    await expect(visitor.getByText(ORIGINAL)).toBeVisible({ timeout: 30_000 });

    await signIn(page, tenant.accounts.hr_manager);
    await page.goto(`/hr/requisitions/${opening.id}/workflow`);
    const panel = page.getByRole('region', { name: 'Job description' });
    await expect(panel.getByText('Published: v1')).toBeVisible({ timeout: 30_000 });

    // ── A rewrite, saved as a draft ─────────────────────────────────────────
    await panel.getByLabel('New draft').fill(DRAFTED);
    await panel.getByLabel('What changed (optional)').fill('Added the on-call rota.');
    await panel.getByRole('button', { name: 'Save draft' }).click();
    await expect(
      page.getByText('Draft saved. Candidates still see the published version.'),
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      panel.getByText('This draft is not visible to candidates'),
      'and the panel says so where the person is looking, not only in a toast',
    ).toBeVisible();

    // ── And the candidate still sees the old advert ─────────────────────────
    // This is the claim the feature is. Not "hidden behind a flag": the public
    // surfaces read the requisition, and saving a draft does not write to it.
    await visitor.reload();
    await expect(
      visitor.getByText(ORIGINAL),
      'a draft is private — the advert candidates read has not moved',
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      visitor.getByText('weekly on-call rota'),
      'and the unpublished wording is nowhere on their page',
    ).toHaveCount(0);

    // ── Published, on purpose ───────────────────────────────────────────────
    await panel.getByRole('button', { name: 'Publish v2' }).click();
    await expect(
      page.getByText('Version 2 is now the published job description.'),
    ).toBeVisible({ timeout: 30_000 });

    await visitor.reload();
    await expect(
      visitor.getByText(DRAFTED),
      'and now, and only now, the candidate reads the new wording',
    ).toBeVisible({ timeout: 30_000 });

    // ── The wording it replaced is still on the record ──────────────────────
    // Marked "Previous", not deleted. Somebody who applied last week applied
    // to that advert, and the company has to be able to say what it said.
    const history = panel.getByRole('listitem');
    await expect(history, 'both versions are kept').toHaveCount(2);
    await expect(panel.getByText('Published', { exact: true })).toBeVisible();
    await expect(panel.getByText('Previous', { exact: true })).toBeVisible();
    await expect(
      panel.getByText('Added the on-call rota.'),
      'with the reason it changed, in the words of whoever changed it',
    ).toBeVisible();
    await expect(
      panel.getByText(/Live since /),
      'and when it went live, which is the question the history exists to answer',
    ).toBeVisible();

    // ── Going back is a publish, not an undo ────────────────────────────────
    // Restoring v1 makes v1 live again and leaves v2 in the history. If it
    // rewrote the past instead, "historical versions cannot be accidentally
    // overwritten" would be false.
    await history
      .filter({ hasText: 'Previous' })
      .getByRole('button', { name: 'Restore' })
      .click();
    await expect(
      page.getByText('Version 1 is now the published job description.'),
    ).toBeVisible({ timeout: 30_000 });

    await visitor.reload();
    await expect(
      visitor.getByText(ORIGINAL),
      'the restored wording is what candidates read again',
    ).toBeVisible({ timeout: 30_000 });
    await expect(history, 'and nothing was removed to get there').toHaveCount(2);
    await expect(
      panel.getByText('Added the on-call rota.'),
      'the rewrite that was rolled back is still in the record, with its note',
    ).toBeVisible();
    await expect(
      panel.getByText(/Live \d+ \w+ \d{4} – \d+ \w+ \d{4}/),
      'and v2 now carries the window it was live for, which is what it was',
    ).toBeVisible();

    await publicView.close();
  });
});
