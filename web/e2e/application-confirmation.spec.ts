// Checking what was read off a CV before it becomes an application (PH3-B5).
//
// A parser reads a name, a phone number and two profile links off a PDF and is
// sometimes wrong — a maiden name, a nickname, an old number. So the candidate
// is shown what was read and asked to correct it, and the criterion that
// matters is the sixth: "only confirmed information is used". That is a claim
// about what HR ends up looking at, which makes it a claim no unit test can
// settle — the CV, the correction and the applicant record are three different
// systems apart.
//
// So: a CV that says one thing, a candidate who says another, and the HR
// console at the end of it. Their answer has to be the one that survives.
//
// The other half is the one the acceptance doc calls out by name: a candidate
// who chose Hindi once read the advert in English and then met the
// confirmation screen in Hindi. Both are localised now, and a browser is the
// only place that is observable.

import { expect, test } from './support/fixtures';
import { Api, createLiveMcqOpening, signIn } from './support/fixtures';
import { aCandidate, submitRespectingTheRateLimit } from './support/journeys';
import { makeCvPdf } from './support/pdf';

/** The name the CV carries — deliberately not the name they will give us. */
const CV_NAME_SUFFIX = 'Sharma';
const CORRECTED_SUFFIX = 'Venkatesan';
const CV_PHONE = '+91 98200 11223';
const CV_LINKEDIN = 'linkedin.com/in/e2e-parsed-profile';

test.describe('the details read off a CV', () => {
  test('are shown, can be corrected, and it is the correction that reaches HR', async ({
    page,
    browser,
    request,
    tenant,
  }) => {
    test.slow();
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const admin = await Api.as(request, tenant.accounts.super_admin);
    const opening = await createLiveMcqOpening(api, admin, 'E2E Confirmation');
    const candidate = aCandidate('Meera', 81);
    const firstName = candidate.name.split(' ')[0];
    const cvName = `${firstName} ${CV_NAME_SUFFIX}`;
    const correctedName = `${firstName} ${CORRECTED_SUFFIX}`;

    // ── A CV with a name on it, and a candidate who is going to disagree ────
    await page.goto(`/apply/${opening.id}`);
    await page.locator('#name').fill(cvName);
    await page.locator('#email').fill(candidate.email);
    await page.getByRole('button', { name: 'Continue' }).click();
    await page.getByRole('button', { name: 'Skip' }).click();
    await page.locator('input#cv').setInputFiles({
      name: 'cv.pdf',
      mimeType: 'application/pdf',
      buffer: makeCvPdf([
        cvName,
        CV_PHONE,
        CV_LINKEDIN,
        candidate.email,
        `E2E-SCORE: ${candidate.resumeScore}`,
      ]),
    });
    await page.getByRole('button', { name: 'Continue' }).click();

    // The confirmation screen is reached by saving and coming back, which is
    // the path that holds a CV long enough to have read anything off it.
    await page
      .locator('label')
      .filter({ hasText: 'may store my name, email and CV' })
      .locator('input[type="checkbox"]')
      .check();
    await page.getByRole('button', { name: 'Save and finish later' }).click();
    await expect(page.getByText('Saved. Keep this link to carry on later.')).toBeVisible({
      timeout: 60_000,
    });
    // The link appears as soon as the draft exists — before the answers and
    // the CV have finished travelling to it, deliberately, so a tab closed
    // mid-upload still leaves the person their way back in. So wait for the
    // carry-over to finish before following it, exactly as the page asks a
    // real person to.
    await expect(
      page.getByText('Still saving your CV'),
      'the page says it is still working rather than looking finished',
    ).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText('Still saving your CV')).toHaveCount(0, {
      timeout: 60_000,
    });
    const resumeLink = await page.getByLabel('Your resume link').inputValue();
    const draft = new URL(resumeLink);

    await page.goto(draft.pathname + draft.hash);
    await expect(page.getByText('Check your details')).toBeVisible({ timeout: 60_000 });

    // ── What the parser read is on the screen, and said to be from the CV ───
    // Not presented as fact. "We read these from your CV. Please correct
    // anything that is wrong" is the difference between asking someone to
    // check something and telling them what they are called.
    await expect(page.getByText('We read these from your CV')).toBeVisible();
    const name = page.getByRole('textbox', { name: 'Full name' });
    await expect(name, 'the name off the CV is filled in for them').toHaveValue(cvName, {
      timeout: 30_000,
    });
    await expect(
      page.getByRole('textbox', { name: /^Phone/ }),
      'and the phone number, which is the field people most often need to fix',
    ).toHaveValue(CV_PHONE);
    await expect(page.getByRole('textbox', { name: /^LinkedIn/ })).toHaveValue(
      new RegExp(CV_LINKEDIN),
    );

    // ── Nothing can be sent before they have confirmed ──────────────────────
    await expect(
      page.getByText('Details confirmed — still needed'),
      'a CV alone is not an application — someone has to have checked it',
    ).toBeVisible();
    await expect(page.getByRole('button', { name: 'Submit application' })).toBeDisabled();

    // ── They correct it, and the CV keeps saying what it said ───────────────
    await name.fill(correctedName);
    await expect(
      page.getByText(`Your CV says “${cvName}”.`),
      'the CV is quoted, not overwritten — correcting a form is not a claim the CV said something else',
    ).toBeVisible();

    const confirm = page.getByRole('button', { name: 'These details are correct' });
    await expect(confirm).toBeEnabled({ timeout: 30_000 });
    await confirm.click();
    await expect(page.getByText('Details confirmed — still needed')).toHaveCount(0);

    const submit = page.getByRole('button', { name: 'Submit application' });
    await expect(submit).toBeEnabled({ timeout: 30_000 });
    // The draft door counts against the same `public_apply_submit` window as
    // the one-shot one, so this goes through the shared helper too. Not because
    // this spec has been seen to trip it, but because it submits on the same
    // budget as the two that have — fixing only the doors that have failed so
    // far is what made this take two rounds.
    await submitRespectingTheRateLimit(
      page,
      submit,
      'Thanks — we have your application',
    );
    await expect(
      page.getByText('Thanks — we have your application'),
    ).toBeVisible({ timeout: 60_000 });

    // ── And HR is looking at the name they gave us ──────────────────────────
    // The whole story in one assertion: the parser's answer lost to the
    // person's, which is what "only confirmed information is used" means once
    // it stops being a sentence in a document.
    const hrContext = await browser.newContext();
    const hrPage = await hrContext.newPage();
    await signIn(hrPage, tenant.accounts.hr_manager);
    await hrPage.goto('/hr/applicants');
    await expect(
      hrPage.getByText(correctedName),
      'HR sees the name the candidate gave, not the one on the PDF',
    ).toBeVisible({ timeout: 60_000 });
    await expect(
      hrPage.getByText(cvName),
      'and never the one the parser guessed',
    ).toHaveCount(0);

    await hrContext.close();
  });
});
