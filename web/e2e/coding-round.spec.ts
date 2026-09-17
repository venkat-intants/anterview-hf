// A coding round, actually executed.
//
// The only round whose result comes from running the candidate's own code, on a
// sandbox, against tests they cannot see. Everything else in the suite can be
// proved with fixtures; this cannot — either the runner executes Python and
// grades it or it does not, and nothing below the browser tells you which.
//
// Needs a code runner. Locally that is self-hosted Piston (scripts/piston-up.ps1
// and EXECUTION_PROVIDER=piston), so the candidate's code never leaves the
// machine. The spec says so when it is missing rather than failing as a broken
// exam.

import {
  Api,
  createLiveCodingOpening,
  decisionQueue,
  enrolmentFor,
  expect,
  runBackgroundPasses,
  signIn,
  test,
} from './support/fixtures';
import { aCandidate, applyThroughPublicForm, shortlistFromApplicants } from './support/journeys';
import { linkIn, waitForMail } from './support/mail';

const SOLUTION = 'a, b = map(int, input().split())\nprint(a + b)\n';

test.describe('a coding round', () => {
  test.beforeAll(async ({ request }) => {
    const runner = process.env.E2E_PISTON_URL ?? 'http://localhost:2000/api/v2/runtimes';
    const res = await request.get(runner).catch(() => null);
    test.skip(
      !res?.ok(),
      `No code runner at ${runner}. Start one with scripts/piston-up.ps1 and run ` +
        'data_gateway with EXECUTION_PROVIDER=piston (see e2e/README.md).',
    );
  });

  test('runs the candidate’s own code against hidden tests and advances them on the result', async ({
    page,
    browser,
    request,
    tenant,
  }) => {
    test.slow();
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const admin = await Api.as(request, tenant.accounts.super_admin);
    const opening = await createLiveCodingOpening(api, admin, 'E2E Coding Round');
    const candidate = aCandidate('Kabir', 80);

    const candidateContext = await browser.newContext();
    const candidatePage = await candidateContext.newPage();
    await applyThroughPublicForm(candidatePage, opening.id, candidate);
    await runBackgroundPasses(request);

    const enrolment = await enrolmentFor(api, opening.id, candidate.name);
    await signIn(page, tenant.accounts.hr_manager);
    await shortlistFromApplicants(page, candidate, opening.title);

    // ── The candidate opens the emailed link and writes code ────────────────
    const invite = await waitForMail(candidate.email, /assessment|exam/i, 60_000);
    await candidatePage.goto(`/exam#${linkIn(invite, /\/exam#([A-Za-z0-9_-]{16,})/)}`);
    const start = candidatePage.getByRole('button', { name: 'Start exam' });
    await start.waitFor();
    await candidatePage.locator('input[type="checkbox"]').first().check();
    await start.click();

    const editor = candidatePage.locator('[id^="ce-take-"]').first();
    await editor.waitFor();
    await editor.fill(SOLUTION);

    // Running the samples is the candidate checking their own work before
    // committing to it — and the first proof that code really executes.
    await candidatePage.getByRole('button', { name: 'Run samples' }).click();
    await expect(
      candidatePage.getByText(/1\/1|passing/i),
      'the sample test runs and passes against the real runner',
    ).toBeVisible({ timeout: 60_000 });

    // "Submit exam", not "Submit solution": the coding problems are embedded in
    // the exam page, which owns the one button that ends the round. The
    // standalone coding page has its own — a spec that clicks that one waits
    // forever for a button this page never renders.
    await candidatePage.getByRole('button', { name: 'Submit exam' }).click();
    await expect(candidatePage.getByText(/You passed|Not this time/)).toBeVisible({
      timeout: 60_000,
    });
    await expect(
      candidatePage.getByText('You passed'),
      'the hidden tests ran too — a correct solution passes all three',
    ).toBeVisible();
    await candidateContext.close();

    // ── Which moves them, exactly like any other round ──────────────────────
    await expect
      .poll(async () => (await decisionQueue(api, opening.id)).map((r) => r.full_name), {
        message: 'passing the coding round finishes the workflow',
      })
      .toContain(candidate.name);

    const row = (await decisionQueue(api, opening.id)).find(
      (r) => r.full_name === candidate.name,
    )!;
    expect(row.round_results, 'the round produced one result').toHaveLength(1);
    expect(row.round_results[0].passed).toBe(true);
    expect(
      row.round_results[0].graded_by,
      'graded by running the code, not by a person',
    ).not.toBe('human');
    expect(row.held, 'and they were not held — they passed').toBe(false);
    expect(enrolment.id).toBeTruthy();
  });
});
