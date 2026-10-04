// The anonymous apply door tells a stranger nothing (PH3-B4b, AR-10).
//
// The property: `POST /apply/{id}` takes an email address as a form field with
// no login at all, so whatever it answers is answered to whoever typed the
// address. It must therefore answer IDENTICALLY whether that address has a live
// application here, was rejected and is inside the waiting period, was rejected
// and a person has since allowed a reapplication, or has never applied.
// Sixteen rounds of review on that one property found separate ways it leaked:
// the refusal's date, the response fields, the ids, the draft row left
// readable, what a second submission read back, the work the endpoint did, the
// identity lookup's timing, and twice a crafted field value that turned one
// state into a 503.
//
// WHY THIS IS A BROWSER TEST AND NOT ANOTHER API ONE. The backend side is
// pinned by the integration matrix in `test_ph3_cooldown_indistinguishable.py`,
// which compares status and body across five states. None of those tests render
// anything. The screen is a second place the leak can come back: `Submitted` in
// `PublicApply.tsx` renders `apply.received`, `result.message` and
// `apply.reviewNote`, and the comment above it records that
// `awaiting_confirmation`, `already_applied` and the ids were each removed
// after a review found them telling a stranger a named person had been turned
// down. A future change that branches that panel on anything — a field, a
// status, a count — reopens the channel without touching the API those tests
// measure.
//
// AND WHY IT COMPARES SCREENS RATHER THAN LOOKING FOR WORDS.
// `reapply-cooldown.spec.ts` asserts the refused screen contains none of
// 'reject', 'turned down', 'not able to consider', 'until 20'. That is a
// denylist, and a denylist only catches the phrasings somebody thought of —
// the same assert-a-proxy-for-the-property mistake that round 16 found in four
// separate guards on this branch. This asserts the effect: the rendered screen
// is the SAME, so there is no wording left to enumerate.
//
// ONE ADDRESS, FOUR STATES, IN ORDER. The first version of this spec used four
// different people and seven submissions, and failed on the seventh with "Too
// many requests": `POST /apply/{id}` carries `rate_limit("public_apply_submit",
// 6)`, a fixed 60-second window per client IP shared by both doors. Separate
// browser contexts do not help — the limiter keys on the address the request
// comes from, and every context here is localhost.
//
// Walking ONE candidate through the states in sequence needs four submissions
// instead of seven, which fits inside the product's real limit without anyone
// relaxing it for a test. It is also the stronger design, and that is the
// reason to keep it rather than merely the reason it started: the email, the
// typed name and the uploaded CV are now literally the same bytes in all four
// probes, so the backend's knowledge of that address is the only thing that
// differs between the screens being compared. Nothing has to be normalised
// away afterwards.
//
// THIS TEST HAS BEEN SEEN TO FAIL, which is the only reason to believe it.
// A green assertion that four screens are equal is also what you get if all
// four probes land in the SAME state — a broken setup reads exactly like a
// perfect product. So the reply was mutated at one exit and the test re-run:
// `public_apply.py`'s refusal exit (`return await _reply(name, ...)`, the one
// the live-application and in-cooldown states return through; the accepted
// states return through the one at the end of the one-shot handler) was given
// its own sentence, "Thanks — we already have an application from you."
//
// The test failed with `Expected: 1, Received: 2`, and the four screens split
// 2/2 exactly along those two code paths — never-applied and allowed-to-reapply
// on one wording, live-application and in-cooldown on the other. That is worth
// more than the red tick: it proves the setup below really does drive four
// different backend paths, because if it did not, the mutation would have
// produced one screen and the test would have passed while asserting nothing.
//
// THE FIFTH STATE IS NOT HERE. "Rejected, and the waiting period has since
// elapsed" needs a rejection backdated past the window. A browser cannot do
// that and no test hook offers it — `/test-hooks` exposes `reconcile` and
// `reminders`, nothing else — and `cooldown_days: 0` is not a substitute,
// because the backend returns at `if not cooldown_days` before it ever reads
// the ledger, so it exercises a different path. That state stays covered by the
// integration matrix, which backdates in SQL.

import {
  Api,
  createLiveMcqOpening,
  expect,
  runBackgroundPasses,
  signIn,
  test,
} from './support/fixtures';
import type { Page } from '@playwright/test';
import { aCandidate, applyThroughPublicForm } from './support/journeys';
import type { Candidate } from './support/journeys';

/**
 * Everything the candidate can read after submitting.
 *
 * Whitespace is collapsed and nothing else is normalised. In particular dates
 * are NOT masked, though masking them would be the obvious way to keep a
 * footer from making this flaky: the very first leak this branch fixed was a
 * refusal that named the date the candidate could reapply, so a `<date>`
 * substitution here would blind the test to the one regression it most exists
 * to catch. One candidate and one opening means nothing else on the screen
 * varies between probes anyway — and if that stops being true, the fix is to
 * assert on the panel rather than to start erasing content.
 */
async function renderedScreen(page: Page): Promise<string> {
  const body = await page.locator('body').innerText();
  return body.replace(/\s+/g, ' ').trim();
}

test.describe('the anonymous apply door', () => {
  test('answers the same way whatever it knows about the address', async ({
    page,
    browser,
    request,
    tenant,
  }) => {
    test.slow();
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const admin = await Api.as(request, tenant.accounts.super_admin);
    const opening = await createLiveMcqOpening(api, admin, 'E2E Apply Indistinguishable');

    // A waiting period, so a rejection actually produces a refusal. Set over
    // the API rather than through the screen: `reapply-cooldown.spec.ts`
    // already proves the screen sets it, and driving it again here would let
    // this test fail for a reason that is not its subject.
    const set = await api.patchRaw(`/hr/requisitions/${opening.id}`, {
      reapply_cooldown_days: 30,
    });
    expect(set.status, `could not set the waiting period: ${set.body}`).toBe(200);

    const who = aCandidate('Probe', 80);
    const screens = new Map<string, string>();

    /** Submit as this one person, from a clean browser, and keep the screen. */
    const probe = async (state: string, candidate: Candidate): Promise<void> => {
      const context = await browser.newContext();
      const probePage = await context.newPage();
      await applyThroughPublicForm(probePage, opening.id, candidate, {
        expectReceived: false,
      });
      // Waiting on the shared heading is not an assumption about which state
      // this is: all four are supposed to answer through this same panel. If
      // one ever stops reaching it, that failure is itself the finding — which
      // is why the message names the state.
      await expect(
        probePage.getByText('Application received'),
        `${state}: the shared reply never rendered`,
      ).toBeVisible({ timeout: 60_000 });
      screens.set(state, await renderedScreen(probePage));
      await context.close();
    };

    // ── 1. Never applied here ─────────────────────────────────────────────
    // Has to be first: the moment this submission lands, the address has a
    // live application and this state is gone for good.
    await probe('never applied here', who);
    await runBackgroundPasses(request);

    // ── 2. A live application ─────────────────────────────────────────────
    // The same address again, with its first application still open. This is
    // the state whose reply used to carry `already_applied`.
    await probe('live application', who);

    // ── 3. Rejected, inside the waiting period ────────────────────────────
    await signIn(page, tenant.accounts.hr_manager);
    await page.goto('/hr/applicants');
    const row = page.getByRole('button', { name: `Open details for ${who.name}` });
    await row.first().waitFor();
    await row.first().click();
    const drawer = page.getByRole('dialog', { name: `${who.name} applicant details` });
    await drawer.getByRole('button', { name: 'Reject', exact: true }).click();
    await drawer.getByLabel('Reason', { exact: true }).selectOption({ index: 1 });
    await drawer.getByLabel(/^why/i).fill('Not enough production experience.');
    // Wait on the WRITE, not on a change in the drawer: the drawer stays open
    // after a successful reject (see `Applicants.test.tsx`), so there is no
    // reliable visual cue — and the next step navigates a browser away, which
    // would abort a PATCH still in flight.
    const rejected = page.waitForResponse(
      (r) =>
        /\/hr\/applicants\/[^/]+$/.test(new URL(r.url()).pathname) &&
        r.request().method() === 'PATCH',
    );
    await drawer.getByRole('button', { name: /confirm reject/i }).click();
    const rejectResponse = await rejected;
    expect(
      rejectResponse.status(),
      `rejecting ${who.name} failed: ${await rejectResponse.text()}`,
    ).toBe(200);

    await probe('inside the waiting period', who);

    // ── 4. Rejected, but a person has allowed a reapplication ─────────────
    await page.goto('/hr/applicants');
    const againRow = page.getByRole('button', { name: `Open details for ${who.name}` });
    await againRow.first().waitFor();
    await againRow.first().click();
    const again = page.getByRole('dialog', { name: `${who.name} applicant details` });
    await again
      .getByRole('button', { name: new RegExp(`let ${who.name} reapply`, 'i') })
      .click();
    await again
      .getByRole('button', { name: new RegExp(`confirm ${who.name} may reapply`, 'i') })
      .click();
    await expect(again.getByText(/may reapply now/i)).toBeVisible();

    await probe('allowed to reapply', who);

    // ── The whole point ───────────────────────────────────────────────────
    expect(screens.size, 'every state should have been probed').toBe(4);
    const distinct = new Set(screens.values());
    expect(
      distinct.size,
      'the four states render different screens, so a stranger who types an ' +
        'address into the public form learns something about whoever owns it:\n' +
        [...screens]
          .map(([state, text]) => `    ${state}\n        ${text.slice(0, 300)}`)
          .join('\n'),
    ).toBe(1);

    // A screen identical because it is BLANK would satisfy the assertion above.
    const [onlyScreen] = distinct;
    expect(
      onlyScreen,
      'the one shared screen should still say the thing it exists to say',
    ).toContain('Application received');
  });
});
