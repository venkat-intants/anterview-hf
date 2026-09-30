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
import { linkIn, waitForMail } from './support/mail';

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

    // ── 3. They apply again, and the screen gives NOTHING away ──────────────
    // A refusal naming the date used to appear right here — and that was an
    // oracle. This endpoint is anonymous and accepts any address, so anyone
    // holding the public link could type an address and learn that a named
    // person had applied, had been REJECTED, and roughly when. The reply is
    // now the same one a live application gets.
    await applyThroughPublicForm(candidatePage, opening.id, candidate, {
      expectReceived: false,
    });
    await expect(
      candidatePage.getByText('You have already applied for this role'),
      'indistinguishable from a live application, on purpose',
    ).toBeVisible({ timeout: 60_000 });
    const onScreen = await candidatePage.locator('body').innerText();
    for (const leak of ['reject', 'turned down', 'not able to consider', 'until 20']) {
      expect(
        onScreen.toLowerCase(),
        `an anonymous caller is never told "${leak}"`,
      ).not.toContain(leak.toLowerCase());
    }

    // The date is not lost — it goes to the ADDRESS, which is the only place
    // it is the candidate's to read.
    const notice = await waitForMail(candidate.email, /About your application/i, 60_000);
    expect(
      notice.text,
      'the person who owns the address is told when they may apply',
    ).toMatch(/not able to consider a new application until \d{4}-\d{2}-\d{2}/);

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

    // ── 5. They apply again — and it does NOT go straight in ────────────────
    // This step used to assert "Application received" and pass while the
    // enrolment stayed rejected, HR saw nothing and the override went unspent:
    // it proved the form accepted a submission, not that criterion 9 works.
    // A reapplication is staged, because this endpoint is anonymous and takes
    // any address, so acting on it would let a stranger move a real person's
    // application.
    await applyThroughPublicForm(candidatePage, opening.id, candidate, {
      expectReceived: false,
    });
    await expect(
      candidatePage.getByText('One more step — check your email'),
      'the candidate is told it is waiting, not that it is in',
    ).toBeVisible({ timeout: 60_000 });
    await expect(
      candidatePage.getByText(/Nothing is sent to the hiring team until you follow that link/),
    ).toBeVisible();

    // ── 6. The link proves the address, and THEN it reaches HR ──────────────
    const confirmMail = await waitForMail(
      candidate.email, /Confirm your application/i, 60_000,
    );
    expect(
      confirmMail.text,
      'the email must not tell whoever reads that inbox that this person was rejected',
    ).not.toMatch(/reject|turned down/i);
    const confirmUrl = linkIn(confirmMail, /(https?:\/\/\S*\/reapply#\S+)/);

    await candidatePage.goto(new URL(confirmUrl).pathname + new URL(confirmUrl).hash);
    await candidatePage.getByRole('button', { name: /Confirm my application/i }).click();
    await expect(
      candidatePage.getByText(/your application is with the hiring team again/i),
    ).toBeVisible({ timeout: 60_000 });

    // ── 7. And HR's own view says they are back in ──────────────────────────
    // This is the whole of criterion 9: "authorized users can override
    // cooldown restrictions" is only true if the person actually re-enters the
    // pipeline. Read through HR's authenticated API for the opening rather
    // than by scanning the applicants list — that list is semantic-search
    // only, with no name filter, so a match there would depend on how many
    // other candidates happen to exist.
    await runBackgroundPasses(request);
    const enrolments = await api.get<{ full_name: string; status: string }[]>(
      `/hr/requisitions/${opening.id}/enrolments`,
    );
    const theirs = enrolments.filter((e) => e.full_name === candidate.name);
    expect(theirs, 'HR sees exactly one application for this person (D-06)').toHaveLength(1);
    expect(
      theirs[0].status,
      'the rejection was forgiven and they are live again, not still rejected',
    ).toBe('new');

    await candidateContext.close();
  });
});
