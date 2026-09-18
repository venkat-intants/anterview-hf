// Offer lifecycle and preboarding, end to end (PH4-A3, PH4-A4).
//
// A hire is not the end of the workflow — it is the start of a second one,
// with its own approval gate (D4-2, separation of duties), its own secure
// candidate-facing link, and its own second factor for documents. One run,
// in the order it happens: HR creates the offer, the super admin approves
// it, HR sends it, the candidate accepts and uploads a document, HR rejects
// it, the candidate re-uploads, HR verifies it and closes preboarding out,
// then prepares the signed HRMS handoff.
//
// Each one-time code email says what it is for — accept, decline, or open the
// documents — so the spec reads each by its own subject.

import {
  Api,
  createLiveMcqOpening,
  decisionQueue,
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
import { linkIn, waitForMail, waitForNewerMail } from './support/mail';
import { makeCvPdf } from './support/pdf';

// No candidate password anywhere in this journey — acceptance and documents
// run entirely on the offer link (URL fragment) and its emailed one-time
// codes, by design (see api/publicOffer.ts). Nothing here needs the
// generated-per-run-password convention other specs follow.

/** The six digits an offer_code email carries, on their own line. */
function codeIn(text: string): string {
  const match = text.match(/\b(\d{6})\b/);
  if (!match) throw new Error(`No 6-digit code found in: ${text}`);
  return match[1];
}

test.describe('an offer moves from creation to preboarding complete', () => {
  test('HR creates it, the super admin approves it, the candidate accepts and uploads, HR reviews and exports', async ({
    page,
    browser,
    request,
    tenant,
  }) => {
    test.slow(); // three people, an approval gate and two round trips through email
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const admin = await Api.as(request, tenant.accounts.super_admin);
    const opening = await createLiveMcqOpening(api, admin, 'E2E Offer');
    const candidate = aCandidate('Ananya', 88);

    // ── The candidate applies and passes, so there is a decision to make ────
    const candidateContext = await browser.newContext();
    const candidatePage = await candidateContext.newPage();
    await applyThroughPublicForm(candidatePage, opening.id, candidate);
    await runBackgroundPasses(request);

    await signIn(page, tenant.accounts.hr_manager);
    await shortlistFromApplicants(page, candidate, opening.title);

    const verdict = await sitTheExam(candidatePage, candidate, { answerCorrectly: true });
    expect(verdict).toContain('You passed');

    await expect
      .poll(async () => (await decisionQueue(api, opening.id)).map((r) => r.full_name), {
        message: 'a candidate who finished the workflow reaches the decision queue',
      })
      .toContain(candidate.name);

    // ── 1. HR hires them (D-05: a person, with a reason) ────────────────────
    await page.goto(`/hr/requisitions/${opening.id}/decisions`);
    await page.getByRole('button', { name: 'Hire', exact: true }).click();
    await page
      .getByLabel('Reason', { exact: true })
      .selectOption({ label: 'Skills / competency fit' });
    await page.getByLabel('Why (recorded against your name)').fill('Strong aptitude result');
    await page.getByRole('button', { name: 'Confirm' }).click();
    await expect
      .poll(async () => (await decisionQueue(api, opening.id)).map((r) => r.full_name))
      .not.toContain(candidate.name);

    // ── HR sets up what preboarding needs, on the opening itself ────────────
    await page.goto(`/hr/requisitions/${opening.id}`);
    await page.getByRole('button', { name: 'Add requirement' }).click();
    await page.getByLabel('Name').fill('PAN card');
    await page.getByLabel('Kind').selectOption('tax');
    await page.getByRole('button', { name: 'Add requirement' }).click();
    await expect(page.getByText('PAN card')).toBeVisible();

    // ── 2. HR creates the offer from the candidate's own drawer ─────────────
    await page.goto('/hr/pipeline');
    await page.getByRole('tab', { name: 'Decided' }).click();
    await page.getByRole('button', { name: `Open details for ${candidate.name}` }).click();
    const drawer = page.getByRole('dialog', { name: candidate.name });
    await drawer.getByRole('button', { name: 'Create offer' }).click();
    await drawer.getByLabel('Base salary').fill('1200000');
    await drawer.getByRole('button', { name: 'Create offer' }).click();
    await expect(drawer.getByText('draft')).toBeVisible();

    await drawer.getByRole('link', { name: new RegExp(opening.title) }).click();
    await expect(page.getByRole('heading', { name: new RegExp(candidate.name) })).toBeVisible();
    await page.getByRole('button', { name: 'Submit for approval' }).click();
    await expect(page.getByText('pending approval')).toBeVisible();
    const offerDetailUrl = page.url();

    // ── 3. The super admin approves it — a different account from HR's ──────
    const adminContext = await browser.newContext();
    const adminPage = await adminContext.newPage();
    await signIn(adminPage, tenant.accounts.super_admin);
    await adminPage.goto('/superadmin/offer-approvals');
    await adminPage.getByRole('link', { name: new RegExp(candidate.name) }).click();
    await adminPage.getByRole('button', { name: 'Approve' }).click();
    await adminPage.getByRole('button', { name: 'Confirm approval' }).click();
    await expect(adminPage.getByText(/nothing for you to decide/)).toBeVisible();
    await adminContext.close();

    // ── 4. HR sends the approved offer ───────────────────────────────────────
    await page.goto(offerDetailUrl);
    await expect(page.getByText('approved')).toBeVisible();
    await page.getByRole('button', { name: 'Send to candidate' }).click();
    await expect(page.getByText('sent', { exact: true })).toBeVisible();

    // ── 5. The candidate opens the emailed link, requests a code, accepts ───
    const offerMail = await waitForMail(candidate.email, /your offer/i, 60_000);
    const offerToken = linkIn(offerMail, /\/offer#([A-Za-z0-9_-]{16,})/);
    await candidatePage.goto(`/offer#${offerToken}`);
    await expect(candidatePage.getByRole('heading', { name: opening.title })).toBeVisible();

    await candidatePage.getByRole('button', { name: 'Accept offer' }).click();
    await candidatePage.getByRole('button', { name: 'Send me a code' }).click();
    const acceptCodeMail = await waitForMail(candidate.email, /code to accept the offer/i);
    await candidatePage.getByLabel(/enter the code/i).fill(codeIn(acceptCodeMail.text));
    await candidatePage.getByLabel(/full name/i).fill(candidate.name);
    await candidatePage.getByRole('button', { name: 'Confirm acceptance' }).click();
    await expect(candidatePage.getByText('Offer accepted')).toBeVisible();

    // ── 6. The candidate opens documents with a second code, uploads a PDF ──
    await candidatePage.getByRole('button', { name: 'Get a code' }).click();
    const docsCodeMail = await waitForMail(candidate.email, /code to open your documents/i);
    await candidatePage.getByLabel(/enter the code/i).fill(codeIn(docsCodeMail.text));
    await candidatePage.getByRole('button', { name: 'Continue' }).click();
    await expect(candidatePage.getByText('PAN card')).toBeVisible();

    const panRow = candidatePage.getByRole('group', { name: 'PAN card' });
    await panRow.locator('input[type="file"]').setInputFiles({
      name: 'pan.pdf',
      mimeType: 'application/pdf',
      buffer: makeCvPdf([candidate.name, 'PAN: E2E1234F']),
    });
    await expect(panRow.getByText(/submitted/i)).toBeVisible();

    // ── 7. HR rejects it with a reason ───────────────────────────────────────
    await page.goto(offerDetailUrl);
    await expect(page.getByText('PAN card')).toBeVisible();
    await page.getByRole('button', { name: 'Reject' }).click();
    await page.getByLabel(/tell the candidate/i).fill('The scan is unreadable');
    await page.getByRole('button', { name: /Confirm — Reject/ }).click();
    await expect(page.getByText(/rejected/i).first()).toBeVisible();

    // ── 8. The candidate re-uploads — a fresh visit to the same emailed link,
    //      since the offer token was stripped from THIS tab's address bar the
    //      first time the page loaded (security review: never linger in
    //      browser history), and the documents session lives only in memory
    //      for the life of one page load. ─────────────────────────────────
    await candidatePage.goto(`/offer#${offerToken}`);
    await expect(candidatePage.getByText('Offer accepted')).toBeVisible();
    await candidatePage.getByRole('button', { name: 'Get a code' }).click();
    const secondDocsCodeMail = await waitForNewerMail(
      candidate.email,
      /code to open your documents/i,
      docsCodeMail,
    );
    await candidatePage.getByLabel(/enter the code/i).fill(codeIn(secondDocsCodeMail.text));
    await candidatePage.getByRole('button', { name: 'Continue' }).click();
    await expect(candidatePage.getByText(/the scan is unreadable/i)).toBeVisible();

    const panRowAgain = candidatePage.getByRole('group', { name: 'PAN card' });
    await panRowAgain.locator('input[type="file"]').setInputFiles({
      name: 'pan-v2.pdf',
      mimeType: 'application/pdf',
      buffer: makeCvPdf([candidate.name, 'PAN: E2E1234F', 'clear scan']),
    });
    await expect(panRowAgain.getByText(/submitted/i)).toBeVisible();

    // ── 9. HR verifies it and marks preboarding complete ────────────────────
    await page.goto(offerDetailUrl);
    await expect(page.getByText(/submitted/i).first()).toBeVisible();
    await page.getByRole('button', { name: 'Verify' }).click();
    await page.getByRole('button', { name: /Confirm — Verify/ }).click();
    await expect(page.getByText('Verified')).toBeVisible();
    await page.getByRole('button', { name: 'Mark preboarding complete' }).click();
    await expect(page.getByText(/Preboarding completed on/)).toBeVisible();

    // ── 10. HR prepares the signed HRMS export ──────────────────────────────
    await page.getByRole('button', { name: 'Prepare HRMS export' }).click();
    await expect(page.getByText(/key_id/)).toBeVisible();
    await expect(page.getByText(/"schema"/)).toBeVisible();

    // The offer's own outcome is recorded beside the decision, never in place
    // of it — the hire itself is untouched by anything the offer flow did.
    const decided = await enrolmentFor(api, opening.id, candidate.name);
    expect(decided.status).toBe('hired');

    await candidateContext.close();
  });
});
