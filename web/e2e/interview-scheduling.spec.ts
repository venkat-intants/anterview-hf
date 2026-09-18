// Scheduling the human interview (PH4-A2, PH4-O5).
//
// Three people, one interview. The interviewer says when they can be booked;
// HR builds the loop and lets the candidate choose; the candidate picks a time
// that fits everyone and cannot then move it themselves. HR is told when a
// time would put someone outside their availability and has to say so on
// purpose to override it — a quiet double-booking is the failure this guards.
//
// One run, in the order it happens.

import {
  Api,
  createLiveReviewOpening,
  enrolmentFor,
  expect,
  REVIEW_ROUND_TITLE,
  runBackgroundPasses,
  signIn,
  test,
} from './support/fixtures';
import { aCandidate, applyThroughPublicForm, shortlistFromApplicants } from './support/journeys';
import { linkIn, waitForMail } from './support/mail';

const PASSWORD = 'E2e-Scheduling-Cand-9';
const LOOP_TITLE = 'Panel interviews';

/** A `datetime-local` value, in the browser's (this machine's) own zone. */
function localInput(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** Two days out, so the 2-hour booking lead never bites whatever the hour. */
function dayAfterTomorrowAt(hour: number): Date {
  const d = new Date();
  d.setDate(d.getDate() + 2);
  d.setHours(hour, 0, 0, 0);
  return d;
}

test.describe('an interview is scheduled', () => {
  test('the interviewer sets availability, HR lets the candidate choose, and the booked time holds', async ({
    page,
    browser,
    request,
    tenant,
  }) => {
    test.slow(); // three people and two background passes
    const api = await Api.as(request, tenant.accounts.hr_manager);
    const admin = await Api.as(request, tenant.accounts.super_admin);
    const opening = await createLiveReviewOpening(api, admin, 'E2E Scheduling');
    const candidate = aCandidate('Kiran', 79);
    const interviewerName = 'E2E Interviewer';

    // ── The candidate applies, and HR moves them onto the review round ───────
    const candidateContext = await browser.newContext();
    const candidatePage = await candidateContext.newPage();
    await applyThroughPublicForm(candidatePage, opening.id, candidate);
    await runBackgroundPasses(request);
    await enrolmentFor(api, opening.id, candidate.name);

    // ── The interviewer says when they can be booked ─────────────────────────
    const interviewerContext = await browser.newContext();
    const interviewerPage = await interviewerContext.newPage();
    await signIn(interviewerPage, tenant.accounts.interviewer);
    await interviewerPage.goto('/interviewer');
    await expect(interviewerPage.getByRole('heading', { name: 'My availability' })).toBeVisible();
    await interviewerPage.locator('#avail-start').fill(localInput(dayAfterTomorrowAt(10)));
    await interviewerPage.locator('#avail-end').fill(localInput(dayAfterTomorrowAt(14)));
    await interviewerPage.getByRole('button', { name: 'Add window' }).click();
    await expect(interviewerPage.getByText('Availability added')).toBeVisible();
    await expect(interviewerPage.getByText('No windows set yet.')).toHaveCount(0);

    await signIn(page, tenant.accounts.hr_manager);
    await shortlistFromApplicants(page, candidate, opening.title);

    // ── HR builds a loop the candidate books themselves ──────────────────────
    await page.goto(`/hr/requisitions/${opening.id}/decisions`);
    await page
      .getByRole('button', { name: `Open details for ${candidate.name}` })
      .first()
      .click();
    const drawer = page.getByRole('dialog', { name: candidate.name });
    await expect(drawer.getByText('No interview loop yet for this application.')).toBeVisible();

    await drawer.getByRole('button', { name: 'New loop' }).click();
    await drawer.getByLabel('Title', { exact: true }).fill(LOOP_TITLE);
    await drawer.getByRole('switch', { name: 'Let the candidate choose times' }).click();
    await drawer.getByRole('button', { name: 'Create loop' }).click();

    const loop = drawer.getByRole('group', { name: `Interview loop: ${LOOP_TITLE}` });
    await expect(loop).toBeVisible();
    await expect(loop.getByText(/candidate chooses times/)).toBeVisible();

    await loop.getByRole('button', { name: 'Add session' }).click();
    await loop.getByLabel('Round').selectOption({ label: REVIEW_ROUND_TITLE });
    await loop.getByRole('checkbox', { name: interviewerName }).check();

    // A fixed time outside the interviewer's window is refused — with the
    // override offered, not taken.
    const start = loop.getByLabel(/^Start time/);
    await start.fill(localInput(dayAfterTomorrowAt(20)));
    await loop.getByRole('button', { name: 'Add session' }).click();
    await expect(loop.getByRole('alert'), 'a time outside availability is flagged').toBeVisible();
    await expect(loop.getByRole('button', { name: 'Schedule anyway' })).toBeVisible();
    await expect(
      loop.getByText('No sessions yet.'),
      'nothing was booked by the refusal',
    ).toBeVisible();

    // Leaving the time blank hands the choice to the candidate.
    await start.fill('');
    await expect(loop.getByRole('alert')).toHaveCount(0);
    await loop.getByRole('button', { name: 'Add session' }).click();
    await expect(
      loop.getByText('No time set yet — waiting on the candidate to choose one.'),
    ).toBeVisible();

    await loop.getByRole('button', { name: 'Send to candidate' }).click();
    await expect(loop.getByText(/sent to the candidate/)).toBeVisible();

    // ── The candidate is asked to choose, and does ───────────────────────────
    await waitForMail(candidate.email, /Choose your interview times/i);

    const confirmation = await waitForMail(candidate.email, /have your application/i);
    const token = linkIn(confirmation, /\/activate#([A-Za-z0-9_-]{16,})/);
    await candidatePage.goto(`/activate#${token}`);
    await candidatePage.locator('#ac-new').fill(PASSWORD);
    await candidatePage.locator('#ac-confirm').fill(PASSWORD);
    await candidatePage.getByRole('button', { name: 'Create my account' }).click();
    await expect(candidatePage.getByRole('heading', { name: 'You’re all set' })).toBeVisible();
    await signIn(candidatePage, { email: candidate.email, password: PASSWORD });
    await candidatePage.goto('/applications');

    const mine = candidatePage.locator('[aria-labelledby="your-interviews-heading"]');
    // The status reads in the tag and again where the time will go.
    await expect(mine.getByText('Waiting for you to choose a time').first()).toBeVisible();
    await expect(mine.getByText(`With ${interviewerName}`)).toBeVisible();
    await mine.getByRole('button', { name: 'Choose a time' }).click();

    // Only times inside the interviewer's window are offered: 10:00–14:00 holds
    // a 45-minute session starting on the 15-minute grid from 10:00 to 13:15.
    const slots = mine.getByRole('button', { name: /^\d{1,2}:\d{2}/ });
    await expect(slots).toHaveCount(14);
    await slots.first().click();
    await expect(candidatePage.getByText('Time booked.')).toBeVisible();
    await expect(mine.getByText('Scheduled', { exact: true }).first()).toBeVisible();

    // Fixed once set: no control on the candidate's side moves or cancels it.
    await expect(mine.getByText('To change this time, contact the hiring team.')).toBeVisible();
    await expect(mine.getByRole('button', { name: /reschedule|cancel/i })).toHaveCount(0);
    await expect(mine.getByRole('button', { name: 'Choose a time' })).toHaveCount(0);
    await candidateContext.close();

    // ── The interviewer sees what they are booked for ────────────────────────
    await interviewerPage.reload();
    const upcoming = interviewerPage.locator('section', {
      has: interviewerPage.getByRole('heading', { name: 'Upcoming interviews' }),
    });
    await expect(upcoming.getByText(candidate.name)).toBeVisible();
    await expect(upcoming.getByText('scheduled', { exact: true }).first()).toBeVisible();
    await expect(upcoming.getByText('Time not set yet')).toHaveCount(0);
    await interviewerContext.close();

    // ── HR sees the booked time, and the load it puts on the panel ───────────
    await page.reload();
    await page
      .getByRole('button', { name: `Open details for ${candidate.name}` })
      .first()
      .click();
    await expect(
      loop.getByText('No time set yet — waiting on the candidate to choose one.'),
      'the candidate’s choice reaches HR',
    ).toHaveCount(0);

    await page.goto('/hr/panel');
    await expect(page.getByRole('heading', { name: 'Interview panel' })).toBeVisible();
    await page.getByRole('button', { name: 'Next 30 days' }).click();
    await expect(page.getByText(interviewerName).first()).toBeVisible();
  });
});
