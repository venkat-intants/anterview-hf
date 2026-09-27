// Leaving an application half-finished and coming back (PH3-B4).
//
// People apply on a phone, on a break, on a borrowed laptop. So the form can be
// saved and picked up later from a link — and the saving is where a DPDP rule
// bites, because a saved draft holds a person's email and CV before they have
// submitted anything. Consent is taken at the FIRST save, not at submit, and
// the server refuses a draft without it (PH3-B4c).
//
// The order here is the order that matters: the save control is visible and
// inert before consent, works after it, the link brings the answers back, and
// the finished application is an ordinary one.

import { expect, test } from './support/fixtures';
import { Api, createLiveMcqOpening } from './support/fixtures';
import { aCandidate } from './support/journeys';
import { makeCvPdf } from './support/pdf';

test.describe('an application saved for later', () => {
  test('cannot be saved before consent, comes back from its link, and finishes normally', async ({
    page,
    request,
    tenant,
  }) => {
    test.slow();
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const admin = await Api.as(request, tenant.accounts.super_admin);
    const opening = await createLiveMcqOpening(api, admin, 'E2E Save And Resume');
    const candidate = aCandidate('Sunita', 79);

    // ── Fill in enough to be worth saving ───────────────────────────────────
    await page.goto(`/apply/${opening.id}`);
    await page.locator('#name').fill(candidate.name);
    await page.locator('#email').fill(candidate.email);
    await page.getByRole('button', { name: 'Continue' }).click();
    await page.getByRole('button', { name: 'Skip' }).click();
    await page.locator('input#cv').setInputFiles({
      name: 'cv.pdf',
      mimeType: 'application/pdf',
      buffer: makeCvPdf([candidate.name, candidate.email, `E2E-SCORE: ${candidate.resumeScore}`]),
    });
    await page.getByRole('button', { name: 'Continue' }).click();

    // ── Saving is offered, and refused, until permission is given ───────────
    // Visible but inert, so the reason it cannot be pressed is the checkbox
    // directly above it — a hidden control would just look broken.
    const save = page.getByRole('button', { name: 'Save and finish later' });
    await expect(save, 'the way back is offered before it can be used').toBeVisible();
    await expect(save, 'but saving stores their email, so not before consent').toBeDisabled();

    const consent = page
      .locator('label')
      .filter({ hasText: 'may store my name, email and CV' })
      .locator('input[type="checkbox"]');
    await consent.check();
    await expect(save, 'and works once they have agreed').toBeEnabled();

    // ── Saved, with the link that brings them back ──────────────────────────
    await save.click();
    await expect(page.getByText('Saved. Keep this link to carry on later.')).toBeVisible();
    const resumeLink = await page.getByLabel('Your resume link').inputValue();
    expect(resumeLink, 'the link carries the draft in its fragment').toMatch(
      /\/apply\/draft#[A-Za-z0-9_-]{16,}/,
    );
    await expect(
      page.getByText(`We have also stored your progress against ${candidate.email}`),
      'and says which address it was saved against, so they know where they are',
    ).toBeVisible();
    await expect(
      page.getByText('This link is the only way back in'),
      'and that losing the link loses the draft — said before they close the tab',
    ).toBeVisible();

    // ── They leave. Really leave: a new browsing context, nothing kept ───────
    const returning = await page.context().browser()!.newContext();
    const later = await returning.newPage();
    await later.goto(new URL(resumeLink).pathname + new URL(resumeLink).hash);

    // ── And their answers are waiting ───────────────────────────────────────
    // What comes back is their PLACE, not their typing: the link opens the
    // application it belongs to, addressed to them, and says how long it lasts.
    // `startDraft` sends the email, the consent and the language — the name and
    // the CV are asked for here instead, under "Check your details", which is
    // also where PH3-B5 has the candidate confirm what was read off the CV.
    await expect(
      later.getByText(`applying as ${candidate.email}`),
      'the link opens their application, addressed to them',
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      later.getByText(/This link works until \d{2}\/\d{2}\/\d{4}/),
      'and says when it stops working, which is the thing they need to know',
    ).toBeVisible();

    // ── Finishing from here is an ordinary application ───────────────────────
    // Two things stand between a draft and an application, and the page names
    // both: a CV, and details confirmed.
    await expect(later.getByText('CV uploaded — still needed')).toBeVisible();
    // Choosing the file uploads it — there is no second press, and waiting for
    // one waits forever, because the button is replaced by the uploaded state.
    await later.locator('input[type="file"]').first().setInputFiles({
      name: 'cv.pdf',
      mimeType: 'application/pdf',
      buffer: makeCvPdf([candidate.name, candidate.email, `E2E-SCORE: ${candidate.resumeScore}`]),
    });
    await expect(
      later.getByRole('button', { name: 'Replace CV' }),
      'the CV is on the server, and can be swapped rather than re-added',
    ).toBeVisible({ timeout: 60_000 });
    await expect(later.getByText('CV uploaded — still needed')).toHaveCount(0);

    await later.getByRole('textbox', { name: 'Full name' }).fill(candidate.name);
    const correct = later.getByRole('button', { name: 'These details are correct' });
    await expect(correct, 'confirming is refused until the details are filled in').toBeEnabled({
      timeout: 60_000,
    });
    await correct.click();

    const submit = later.getByRole('button', { name: 'Submit application' });
    await expect(submit).toBeEnabled({ timeout: 30_000 });
    await submit.click();
    // The draft path has its own words, because it IS a different moment: they
    // finished something they had started, rather than sending a fresh form.
    await expect(
      later.getByText('Thanks — your application is in. We will be in touch by email.'),
    ).toBeVisible({ timeout: 60_000 });

    await returning.close();
  });
});
