// One person, two openings (D-06 / D-06a).
//
// An applicant is a PERSON within a company, and applying to a second opening
// does not make them a second person. Everything downstream depends on that
// holding: their history, their consent record, and the promise that a decision
// on one opening is a decision about that opening only. So this applies the
// same human to two openings and checks both halves — one applicant with two
// applications, and acting on one leaving the other exactly where it was.

import {
  Api,
  createLiveMcqOpening,
  enrolmentFor,
  expect,
  runBackgroundPasses,
  signIn,
  test,
} from './support/fixtures';
import { aCandidate, applyThroughPublicForm } from './support/journeys';

test.describe('the same person applying twice', () => {
  test('is one applicant with two applications, and a move on one leaves the other alone', async ({
    page,
    browser,
    request,
    tenant,
  }) => {
    test.slow();
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const admin = await Api.as(request, tenant.accounts.super_admin);
    const backend = await createLiveMcqOpening(api, admin, 'E2E Two Openings Backend');
    const platform = await createLiveMcqOpening(api, admin, 'E2E Two Openings Platform');
    const candidate = aCandidate('Sunita', 79);

    // ── The same person, the same address, both openings ────────────────────
    const candidateContext = await browser.newContext();
    const candidatePage = await candidateContext.newPage();
    await applyThroughPublicForm(candidatePage, backend.id, candidate);
    await applyThroughPublicForm(candidatePage, platform.id, candidate);
    await candidateContext.close();
    await runBackgroundPasses(request);

    const onBackend = await enrolmentFor(api, backend.id, candidate.name);
    const onPlatform = await enrolmentFor(api, platform.id, candidate.name);
    expect(
      onBackend.applicant_id,
      'one person, not two — the second application joins the applicant who already exists',
    ).toBe(onPlatform.applicant_id);
    expect(onBackend.id, 'but two applications, one per opening').not.toBe(onPlatform.id);

    // ── HR sees one person, listed once, with both applications ─────────────
    await signIn(page, tenant.accounts.hr_manager);
    await page.goto('/hr/applicants');
    await expect(
      page.getByRole('button', { name: `Open details for ${candidate.name}` }),
      'listed once, however many openings they applied to',
    ).toHaveCount(1);
    await page.getByRole('button', { name: `Open details for ${candidate.name}` }).click();

    // Both applications are in the drawer, each with its own action — asserted
    // on the per-opening controls rather than on the titles, which also appear
    // inside the resume-match summary.
    const drawer = page.getByRole('dialog', { name: `${candidate.name} applicant details` });
    await drawer.waitFor();
    await expect(
      drawer.getByRole('button', { name: `Shortlist for ${backend.title}` }),
    ).toBeVisible();
    await expect(
      drawer.getByRole('button', { name: `Shortlist for ${platform.title}` }),
    ).toBeVisible();

    // ── Shortlisting for one opening is about that opening ──────────────────
    await drawer.getByRole('button', { name: `Shortlist for ${backend.title}` }).click();
    await expect
      .poll(async () => (await enrolmentFor(api, backend.id, candidate.name)).status, {
        message: 'the opening they were shortlisted for moves',
      })
      .toBe('shortlisted');

    const untouched = await enrolmentFor(api, platform.id, candidate.name);
    expect(
      untouched.status,
      'and the other application is exactly where the candidate left it',
    ).toBe('new');
    expect(untouched.current_round_id, 'no round was started on it either').toBeNull();
  });
});
