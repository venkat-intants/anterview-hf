// An opening that goes live by itself (PH3-B4a), and the promise the careers
// board makes about everything on it (PH3-B0).
//
// A recruiter wants the advert to appear at nine on Monday without being at a
// desk at nine on Monday. Everything about that is only true if the opening
// really does appear — so this spec sets a schedule and WAITS for it, which
// makes it the slowest in the suite and the only proof that criterion 12 is
// about the product rather than about a unit test.
//
// Two things get asserted that a unit test structurally cannot:
//
//   the console never shows a scheduled time without the sentence saying how
//   precise it is. The publisher is an interval loop; "09:00" on its own is a
//   promise the architecture does not make, and the honesty lives in the UI.
//
//   what the careers board lists and what the apply page accepts are the same
//   set. They were once two hand-written copies of one predicate that had
//   already drifted, so the board advertised openings that 404'd on click —
//   the one failure a job board cannot have. That is PH3-B0, and it is only
//   observable by reading the board and then clicking what is on it.

import { expect, test } from './support/fixtures';
import { Api, createLiveMcqOpening, signIn } from './support/fixtures';

/** A datetime-local value `minutes` from now, in the browser's own timezone. */
function localInputValue(minutes: number): string {
  const t = new Date(Date.now() + minutes * 60_000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())}` +
    `T${pad(t.getHours())}:${pad(t.getMinutes())}`
  );
}

test.describe('an opening scheduled to go live', () => {
  test('stays shut until its time, can be called off, and publishes itself when it arrives', async ({
    page,
    browser,
    request,
    tenant,
  }) => {
    test.slow();
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const admin = await Api.as(request, tenant.accounts.super_admin);
    const opening = await createLiveMcqOpening(api, admin, 'E2E Scheduled');
    // Approved and ready, but not yet advertised — which is the state every
    // opening is in on the day before it is meant to appear.
    await api.patch(`/hr/requisitions/${opening.id}`, { public_apply_enabled: false });

    const publicView = await browser.newContext();
    const visitor = await publicView.newPage();
    const board = `/careers/${tenant.company.slug}`;

    // ── Before: not on the board, and not open ──────────────────────────────
    await visitor.goto(board);
    await expect(
      visitor.getByRole('heading', { level: 1 }),
      'the board itself is up — this is a board with an absence on it, not a broken page',
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      visitor.getByRole('link', { name: new RegExp(opening.title) }),
      'an unpublished opening is not advertised',
    ).toHaveCount(0);

    await visitor.goto(`/apply/${opening.id}`);
    await expect(
      visitor.getByText('This opening is not accepting applications'),
      'and cannot be applied to by anyone who has the link',
    ).toBeVisible({ timeout: 30_000 });

    // ── A schedule is set, and it says how precise it is ────────────────────
    await signIn(page, tenant.accounts.hr_manager);
    await page.goto(`/hr/requisitions/${opening.id}/workflow`);
    const panel = page.getByRole('region', { name: 'Budget & approval' });

    await panel.getByLabel('Go live at').fill(localInputValue(30));
    await panel.getByRole('button', { name: 'Schedule' }).click();
    await expect(page.getByText('Scheduled.')).toBeVisible({ timeout: 30_000 });
    await expect(panel.getByText(/^Scheduled for /)).toBeVisible();
    await expect(
      panel.getByText(/go live within .* of the chosen time/),
      'never a time on its own: the loop cannot promise a clock trigger, and the screen says so',
    ).toBeVisible();

    // ── Called off ──────────────────────────────────────────────────────────
    // A cancelled schedule must not publish. Worth its own step because the
    // failure is silent and a fortnight late.
    await panel.getByRole('button', { name: 'Cancel scheduled publication' }).click();
    await expect(page.getByText('Scheduled publication cancelled.')).toBeVisible({
      timeout: 30_000,
    });
    await expect(panel.getByText(/^Scheduled for /)).toHaveCount(0);
    await expect(
      panel.getByLabel('Go live at'),
      'and the form comes back, so it can simply be set again',
    ).toBeVisible();

    // ── Set for real, and waited for ────────────────────────────────────────
    // A minute ahead: the input's granularity is minutes, and the publisher
    // sleeps until the next schedule falls due rather than on a fixed beat.
    await panel.getByLabel('Go live at').fill(localInputValue(1));
    await panel.getByRole('button', { name: 'Schedule' }).click();
    await expect(page.getByText('Scheduled.')).toBeVisible({ timeout: 30_000 });

    // Nothing here tells the publisher to run. It is a loop that wakes on its
    // own, which is the point — a clock trigger cannot fire in a container
    // that is asleep, and this deployment's demo Space sleeps.
    await expect(async () => {
      await visitor.goto(`/apply/${opening.id}`);
      await expect(visitor.getByRole('button', { name: 'Continue' })).toBeVisible({
        timeout: 10_000,
      });
    }).toPass({ timeout: 180_000, intervals: [5_000] });

    // ── And the board and the apply page now agree ──────────────────────────
    // PH3-B0. The board lists it because the same predicate that just let the
    // apply page open is the one the board asks. Clicking through from the
    // board is the check that used to fail.
    await visitor.goto(board);
    const listing = visitor.getByRole('link', { name: new RegExp(opening.title) }).first();
    await expect(listing, 'a published opening is advertised').toBeVisible({ timeout: 30_000 });
    await listing.click();
    await expect(
      visitor.getByRole('button', { name: 'Continue' }),
      'and what the board advertises can be applied to — not a 404 on click',
    ).toBeVisible({ timeout: 30_000 });

    // ── HR sees it as published, not merely as scheduled ────────────────────
    await page.reload();
    await expect(
      panel.getByText(/^Scheduled for /),
      'the request is spent once it has been carried out',
    ).toHaveCount(0);

    await publicView.close();
  });
});
