// Tests for the candidate's own applications page.
//
// The page is a read-only list, so most of what is worth testing is judgement
// rather than mechanics:
//
//   • it shows PROGRESS and never an evaluation. The API does not send scores,
//     and this page must not invent a proxy for one — no "you scored", no
//     traffic-light on the person.
//   • "Round 0 of 4" is not a sentence, and neither is "Round 2 of ". The
//     progress line only appears when both halves are real.
//   • the empty state is read by two different people, and the one who needs
//     help is the applicant whose account was never linked to their
//     application. It has to tell them their application is safe and what to
//     do — a bare "nothing here" would read as "we lost it".
//   • closed applications are kept and separated, not hidden. "Did I ever hear
//     back about that one?" is the question this page exists to answer.
//   • the history is fetched only when asked for.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { MyApplication, MyApplicationDetail } from '../api/applications';
import type { MyRediscoveryCompany } from '../api/rediscovery';
import { ApiError } from '../api/client';

const listMyApplications = vi.fn();
const getMyApplication = vi.fn();
const mintMyInterviewLink = vi.fn();
const mintMyTaskLink = vi.fn();
const mintMyExamLink = vi.fn();
vi.mock('../api/applications', () => ({
  listMyApplications: (...a: unknown[]) => listMyApplications(...a) as unknown,
  getMyApplication: (...a: unknown[]) => getMyApplication(...a) as unknown,
  mintMyInterviewLink: (...a: unknown[]) => mintMyInterviewLink(...a) as unknown,
  mintMyTaskLink: (...a: unknown[]) => mintMyTaskLink(...a) as unknown,
  mintMyExamLink: (...a: unknown[]) => mintMyExamLink(...a) as unknown,
}));

// Starting an interview navigates the tab. jsdom's location is not writable, so
// the assign it calls is stubbed and asserted on instead.
const assign = vi.fn();
Object.defineProperty(window, 'location', {
  writable: true,
  value: { ...window.location, assign },
});

const toastError = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: vi.fn(),
  },
}));

// PH4-A2 — "Your interviews" always mounts above the applications list.
// Mocked to an empty list so every existing page test stays deterministic;
// its own behaviour is covered in YourInterviews.test.tsx.
const listMyInterviewLoops = vi.fn();
vi.mock('../api/scheduling', () => ({
  listMyInterviewLoops: (...a: unknown[]) => listMyInterviewLoops(...a) as unknown,
}));

// PH5-E3 — "Your rediscovery" (YourRediscovery.tsx) also mounts above the
// applications list unconditionally. Defaulted to an empty company list so
// every existing test on this page stays exactly as deterministic as before
// this feature existed; its own behaviour is covered below in "Applications —
// rediscovery". `rediscoveryErrorMessage` is real (via importActual) rather
// than reimplemented, on the CorpusDocuments.test.tsx precedent.
const listMyRediscovery = vi.fn();
const setMyRediscovery = vi.fn();
vi.mock('../api/rediscovery', async () => {
  const actual = await vi.importActual<typeof import('../api/rediscovery')>('../api/rediscovery');
  return {
    ...actual,
    listMyRediscovery: (...a: unknown[]) => listMyRediscovery(...a) as unknown,
    setMyRediscovery: (...a: unknown[]) => setMyRediscovery(...a) as unknown,
  };
});

import Applications from '../pages/Applications';

function app(over: Partial<MyApplication> = {}): MyApplication {
  return {
    id: 'enr-1',
    job_title: 'Backend Engineer',
    company_name: 'Acme Test Co',
    applied_at: '2026-09-01T10:00:00Z',
    updated_at: '2026-09-02T10:00:00Z',
    stage: 'Application received',
    next_step: 'The hiring team is reviewing applications. Nothing to do for now.',
    closed: false,
    current_round_title: null,
    current_round_kind: null,
    round_number: null,
    total_rounds: null,
    // No waiting invitation is the ordinary case; the invite tests set it.
    interview_invite_id: null,
    interview_scheduled_at: null,
    // PH4-D4 — no open task submission is the ordinary case; the task tests
    // set it.
    task_submission_id: null,
    task_due_at: null,
    task_status: null,
    // Likewise no waiting assessment; the assessment tests set it.
    exam_assignment_id: null,
    exam_in_progress: false,
    exam_expires_at: null,
    exam_scheduled_at: null,
    ...over,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Applications />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listMyInterviewLoops.mockResolvedValue([]);
  listMyRediscovery.mockResolvedValue({ companies: [], consent_months: 12 });
  listMyApplications.mockResolvedValue([app()]);
  getMyApplication.mockResolvedValue({
    ...app(),
    history: [
      { stage: 'Application received', occurred_at: '2026-09-01T10:00:00Z', by_a_person: false },
    ],
  } satisfies MyApplicationDetail);
});

describe('Applications', () => {
  it('lists the role, the company and the stage', async () => {
    renderPage();
    expect(await screen.findByText('Backend Engineer')).toBeInTheDocument();
    expect(screen.getByText(/Acme Test Co/)).toBeInTheDocument();
    expect(screen.getByText('Application received')).toBeInTheDocument();
  });

  it('tells the candidate what happens next', async () => {
    renderPage();
    expect(await screen.findByText(/Nothing to do for now/)).toBeInTheDocument();
  });

  it('shows how far through the rounds they are', async () => {
    listMyApplications.mockResolvedValue([
      app({
        stage: 'Shortlisted',
        current_round_title: 'Coding Round',
        current_round_kind: 'coding',
        round_number: 2,
        total_rounds: 4,
      }),
    ]);
    renderPage();
    expect(await screen.findByText('Round 2 of 4 · Coding Round')).toBeInTheDocument();
  });

  it('omits the progress line entirely before a round is assigned', async () => {
    renderPage();
    await screen.findByText('Backend Engineer');
    // Not "Round 0", not "Round of" — absent.
    expect(screen.queryByText(/Round/)).not.toBeInTheDocument();
  });

  it('names the round without a count when the count is unknown', async () => {
    listMyApplications.mockResolvedValue([
      app({ current_round_title: 'Coding Round', round_number: null, total_rounds: null }),
    ]);
    renderPage();
    expect(await screen.findByText('Coding Round')).toBeInTheDocument();
  });

  it('never shows a score or a threshold', async () => {
    // held → "Under review" server-side. The page must not undo that, and must
    // not put a number on the person either.
    listMyApplications.mockResolvedValue([
      app({ stage: 'Under review', next_step: 'The hiring team is reviewing your application.' }),
    ]);
    const { container } = renderPage();
    await screen.findByText('Under review');
    const text = container.textContent ?? '';
    expect(text).not.toMatch(/score/i);
    expect(text).not.toMatch(/threshold/i);
    expect(text).not.toMatch(/\b\d{1,3}\s*%/);
    expect(text).not.toMatch(/\bheld\b/i);
  });

  it('separates closed applications from live ones', async () => {
    listMyApplications.mockResolvedValue([
      app({ id: 'live', job_title: 'Backend Engineer' }),
      app({ id: 'done', job_title: 'Data Analyst', stage: 'Not progressing', closed: true }),
    ]);
    renderPage();
    expect(await screen.findByText('Closed')).toBeInTheDocument();
    // Kept, not hidden.
    expect(screen.getByText('Data Analyst')).toBeInTheDocument();
    expect(screen.getByText('Backend Engineer')).toBeInTheDocument();
  });

  it('does not show a Closed heading when nothing is closed', async () => {
    renderPage();
    await screen.findByText('Backend Engineer');
    expect(screen.queryByText('Closed')).not.toBeInTheDocument();
  });

  it('fetches the history only when it is asked for', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    expect(getMyApplication).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /Show history/ }));
    await waitFor(() => expect(getMyApplication).toHaveBeenCalledWith('enr-1'));
  });

  it('says who moved the candidate, when a person did', async () => {
    getMyApplication.mockResolvedValue({
      ...app(),
      history: [
        { stage: 'Application received', occurred_at: '2026-09-01T10:00:00Z', by_a_person: false },
        { stage: 'Shortlisted', occurred_at: '2026-09-02T10:00:00Z', by_a_person: true },
      ],
    } satisfies MyApplicationDetail);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Backend Engineer');
    await user.click(screen.getByRole('button', { name: /Show history/ }));

    expect(await screen.findByText(/by the hiring team/)).toBeInTheDocument();
  });

  it('reassures an applicant whose account was never linked', async () => {
    listMyApplications.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText('No applications yet')).toBeInTheDocument();
    // The load-bearing sentence: their application exists even though this
    // page is empty, and the activation email is what connects the two.
    expect(screen.getByText(/application is safe/i)).toBeInTheDocument();
    expect(screen.getByText(/confirmation email/i)).toBeInTheDocument();
  });

  it('does not dress a failed load up as an empty list', async () => {
    // The bug this test was written to catch: on error `data` is undefined, so
    // the list is [] and the empty state rendered — telling an applicant they
    // had applied to nothing because a fetch failed.
    listMyApplications.mockRejectedValue(new Error('Network down'));
    renderPage();
    expect(await screen.findByText('Could not load your applications')).toBeInTheDocument();
    expect(screen.queryByText('No applications yet')).not.toBeInTheDocument();
  });

  it('reports the failure once, not once per render', async () => {
    listMyApplications.mockRejectedValue(new Error('Network down'));
    renderPage();
    await waitFor(() => expect(toastError).toHaveBeenCalledWith('Network down'));
    expect(toastError).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// The interview a candidate was invited to
// ---------------------------------------------------------------------------
// The invitation reached the candidate only as an email. Filtered, mistyped or
// caught by a local mail sink, it left an interview that existed and could not
// be started — while looking healthy from the hiring side, because the invite
// really had been minted and queued. This is the route that needs no mail.

describe('Applications — a waiting interview', () => {
  it('offers to start it', async () => {
    listMyApplications.mockResolvedValue([app({ interview_invite_id: 'inv-1' })]);
    renderPage();

    expect(await screen.findByText('Your interview is ready')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Start interview' })).toBeTruthy();
  });

  it('shows nothing when no invitation is waiting', async () => {
    // Which is most of an application's life. An always-present block would
    // train people to ignore the one time it matters.
    listMyApplications.mockResolvedValue([app()]);
    renderPage();

    await screen.findByText('Backend Engineer');
    expect(screen.queryByText('Your interview is ready')).toBeNull();
  });

  it('does not ask for a link until the candidate presses the button', async () => {
    // Minting ROTATES the token, so any previously issued link stops working.
    // Fetching on render would silently break the emailed link of every
    // candidate who merely looked at this page.
    listMyApplications.mockResolvedValue([app({ interview_invite_id: 'inv-1' })]);
    renderPage();

    await screen.findByText('Your interview is ready');
    expect(mintMyInterviewLink).not.toHaveBeenCalled();
  });

  it('sends the candidate to the link it is given', async () => {
    mintMyInterviewLink.mockResolvedValue({
      interview_url: `${window.location.origin}/interview-invite#tok`,
      expires_at: '2026-09-11T00:00:00Z',
    });
    listMyApplications.mockResolvedValue([app({ interview_invite_id: 'inv-1' })]);
    renderPage();

    await userEvent.click(await screen.findByRole('button', { name: 'Start interview' }));

    await vi.waitFor(() => expect(mintMyInterviewLink).toHaveBeenCalledWith('inv-1'));
    await vi.waitFor(() =>
      expect(assign).toHaveBeenCalledWith(`${window.location.origin}/interview-invite#tok`),
    );
  });

  it('explains a failure instead of appearing to do nothing', async () => {
    mintMyInterviewLink.mockRejectedValue(new Error('Invitation has expired.'));
    listMyApplications.mockResolvedValue([app({ interview_invite_id: 'inv-1' })]);
    renderPage();

    await userEvent.click(await screen.findByRole('button', { name: 'Start interview' }));
    expect(await screen.findByText('Invitation has expired.')).toBeTruthy();
  });

  it('shows the scheduled time when there is one', async () => {
    listMyApplications.mockResolvedValue([
      app({ interview_invite_id: 'inv-1', interview_scheduled_at: '2026-09-10T09:00:00Z' }),
    ]);
    renderPage();

    expect(await screen.findByText(/Scheduled for/)).toBeTruthy();
  });
});

// PH4-D4 — the same reasoning as "a waiting interview" above, and the same
// security review finding as YourOffers.tsx's "Open offer": the link carries
// a fresh bearer token in its fragment, so it must be checked same-origin
// before this ever navigates to it, never handed to `window.location.assign`
// unchecked (safeUrl.ts's own header names this exact mistake).
describe('Applications — a waiting task', () => {
  it('offers to open it', async () => {
    listMyApplications.mockResolvedValue([app({ task_submission_id: 'sub-1' })]);
    renderPage();

    expect(await screen.findByText('Your task is ready')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Open task' })).toBeTruthy();
  });

  it('sends the candidate to a same-origin /task link', async () => {
    mintMyTaskLink.mockResolvedValue({ url: `${window.location.origin}/task#tok` });
    listMyApplications.mockResolvedValue([app({ task_submission_id: 'sub-1' })]);
    renderPage();

    await userEvent.click(await screen.findByRole('button', { name: 'Open task' }));

    await vi.waitFor(() => expect(mintMyTaskLink).toHaveBeenCalledWith('sub-1'));
    await vi.waitFor(() =>
      expect(assign).toHaveBeenCalledWith(`${window.location.origin}/task#tok`),
    );
  });

  it('refuses a cross-origin link instead of navigating to it', async () => {
    mintMyTaskLink.mockResolvedValue({ url: 'https://evil.example.com/task#tok' });
    listMyApplications.mockResolvedValue([app({ task_submission_id: 'sub-1' })]);
    renderPage();

    await userEvent.click(await screen.findByRole('button', { name: 'Open task' }));

    await vi.waitFor(() => expect(mintMyTaskLink).toHaveBeenCalledWith('sub-1'));
    expect(await screen.findByText('Could not open your task.')).toBeTruthy();
    expect(assign).not.toHaveBeenCalled();
  });

  it('refuses a same-origin link to the wrong path', async () => {
    mintMyTaskLink.mockResolvedValue({ url: `${window.location.origin}/offer#tok` });
    listMyApplications.mockResolvedValue([app({ task_submission_id: 'sub-1' })]);
    renderPage();

    await userEvent.click(await screen.findByRole('button', { name: 'Open task' }));

    expect(await screen.findByText('Could not open your task.')).toBeTruthy();
    expect(assign).not.toHaveBeenCalled();
  });
});

// The assessment a candidate was sent
// ---------------------------------------------------------------------------
// The same gap as the interview, one round earlier: the card named the round —
// "Round 1 of 1 · Aptitude" — and the only way into it was the emailed link.

describe('Applications — a waiting assessment', () => {
  const waiting = { exam_assignment_id: 'asn-1', exam_expires_at: '2026-09-25T00:00:00Z' };

  it('offers to start it, with the date it closes', async () => {
    listMyApplications.mockResolvedValue([app(waiting)]);
    renderPage();

    expect(await screen.findByText('Your assessment is ready')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Start assessment' })).toBeTruthy();
    expect(screen.getByText(/Take it before/)).toBeTruthy();
  });

  it('offers to resume once begun', async () => {
    listMyApplications.mockResolvedValue([app({ ...waiting, exam_in_progress: true })]);
    renderPage();

    expect(await screen.findByText('Your assessment is in progress')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Resume assessment' })).toBeTruthy();
    expect(screen.getByText(/pick up where you left off/)).toBeTruthy();
  });

  it('names the window for a scheduled round, not the link expiry', async () => {
    // A scheduled round opens at a time and shuts a short while later, both
    // long before the link expires. Showing the expiry states a deadline the
    // candidate does not have — and missing the real one costs the attempt.
    listMyApplications.mockResolvedValue([
      app({ ...waiting, exam_scheduled_at: '2026-09-22T09:00:00Z' }),
    ]);
    renderPage();

    expect(await screen.findByText(/This round opens on/)).toBeTruthy();
    expect(screen.queryByText(/Take it before/)).toBeNull();
  });

  it('shows nothing when no assessment is waiting', async () => {
    listMyApplications.mockResolvedValue([app()]);
    renderPage();

    await screen.findByText('Backend Engineer');
    expect(screen.queryByTestId('exam-cta')).toBeNull();
  });

  it('does not ask for a link until the candidate presses the button', async () => {
    // Minting rotates the token. Doing it on render would break the emailed link
    // of every candidate who merely opened this page.
    listMyApplications.mockResolvedValue([app(waiting)]);
    renderPage();

    await screen.findByText('Your assessment is ready');
    expect(mintMyExamLink).not.toHaveBeenCalled();
  });

  it('sends the candidate to the link it is given', async () => {
    mintMyExamLink.mockResolvedValue({
      exam_url: `${window.location.origin}/exam#tok`,
      expires_at: '2026-09-25T00:00:00Z',
    });
    listMyApplications.mockResolvedValue([app(waiting)]);
    renderPage();

    await userEvent.click(await screen.findByRole('button', { name: 'Start assessment' }));

    // false: an unprompted first press may not close a tab someone is sitting
    // their assessment in.
    await vi.waitFor(() => expect(mintMyExamLink).toHaveBeenCalledWith('asn-1', false));
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith(`${window.location.origin}/exam#tok`));
  });

  it('asks before closing an assessment open in another tab', async () => {
    // The server answers 409 rather than rotating, because rotating kills that
    // tab — it 404s from then on while the clock keeps running. A second tab
    // on this page, a back button or a double click would each have been
    // enough to lose a timed assessment to a press nobody thought about.
    const conflict = new ApiError('This assessment is already open in another tab.', 409);
    mintMyExamLink.mockRejectedValueOnce(conflict).mockResolvedValueOnce({
      exam_url: `${window.location.origin}/exam#tok2`,
      expires_at: '2026-09-25T00:00:00Z',
    });
    listMyApplications.mockResolvedValue([app({ ...waiting, exam_in_progress: true })]);
    renderPage();

    await userEvent.click(await screen.findByRole('button', { name: 'Resume assessment' }));

    // The question, and nothing opened.
    expect(await screen.findByText(/already open in another tab/)).toBeTruthy();
    expect(assign).not.toHaveBeenCalled();

    // The answer, and only now may it close the other tab.
    await userEvent.click(screen.getByRole('button', { name: 'Open it here' }));
    await vi.waitFor(() => expect(mintMyExamLink).toHaveBeenLastCalledWith('asn-1', true));
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith(`${window.location.origin}/exam#tok2`));
  });

  it('explains a failure instead of appearing to do nothing', async () => {
    mintMyExamLink.mockRejectedValue(new Error('No assessment is waiting on this application.'));
    listMyApplications.mockResolvedValue([app(waiting)]);
    renderPage();

    await userEvent.click(await screen.findByRole('button', { name: 'Start assessment' }));
    expect(await screen.findByText('No assessment is waiting on this application.')).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// PH5-E3 (D5-1) — the candidate's own rediscovery opt-in, one row per company.
// ---------------------------------------------------------------------------

function rediscoveryCompany(over: Partial<MyRediscoveryCompany> = {}): MyRediscoveryCompany {
  return {
    company_id: 'co-1',
    company_name: 'Acme Test Co',
    applicant_id: 'app-1',
    state: 'off',
    ...over,
  };
}

describe('Applications — rediscovery', () => {
  it('shows nothing when the candidate has no company to opt into', async () => {
    listMyRediscovery.mockResolvedValue({ companies: [], consent_months: 12 });
    renderPage();
    await screen.findByText('Backend Engineer');
    expect(screen.queryByText('Future openings')).not.toBeInTheDocument();
  });

  it('shows the toggle for a company, off by default', async () => {
    listMyRediscovery.mockResolvedValue({ companies: [rediscoveryCompany()], consent_months: 12 });
    renderPage();

    expect(
      await screen.findByText('Keep my profile for future openings at Acme Test Co'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Turn on' })).toBeInTheDocument();
  });

  it('turns on with a single press — no confirm needed to opt in', async () => {
    const user = userEvent.setup();
    listMyRediscovery.mockResolvedValue({ companies: [rediscoveryCompany()], consent_months: 12 });
    setMyRediscovery.mockResolvedValue(
      rediscoveryCompany({ state: 'on', opted_in_at: '2026-09-26T00:00:00Z', expires_at: '2027-09-26T00:00:00Z' }),
    );
    renderPage();

    await user.click(await screen.findByRole('button', { name: 'Turn on' }));

    await waitFor(() => expect(setMyRediscovery).toHaveBeenCalledWith('co-1', true));
    expect(await screen.findByRole('button', { name: 'Turn off' })).toBeInTheDocument();
  });

  it('requires a second press to turn off — a single click only shows the confirm', async () => {
    const user = userEvent.setup();
    listMyRediscovery.mockResolvedValue({
      companies: [rediscoveryCompany({ state: 'on', expires_at: '2027-09-26T00:00:00Z' })],
      consent_months: 12,
    });
    renderPage();

    await user.click(await screen.findByRole('button', { name: 'Turn off' }));

    // The first press only reveals the confirm — it must NOT have called the API yet.
    expect(setMyRediscovery).not.toHaveBeenCalled();
    expect(screen.getByText('Turn this off?')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Yes, turn off' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Yes, turn off' }));
    await waitFor(() => expect(setMyRediscovery).toHaveBeenCalledWith('co-1', false));
  });

  it('cancels the turn-off confirm without calling the API', async () => {
    const user = userEvent.setup();
    listMyRediscovery.mockResolvedValue({
      companies: [rediscoveryCompany({ state: 'on' })],
      consent_months: 12,
    });
    renderPage();

    await user.click(await screen.findByRole('button', { name: 'Turn off' }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByText('Turn this off?')).not.toBeInTheDocument();
    expect(setMyRediscovery).not.toHaveBeenCalled();
  });

  it('names what is kept, for how long, in the plain-language notice', async () => {
    listMyRediscovery.mockResolvedValue({ companies: [rediscoveryCompany()], consent_months: 12 });
    renderPage();
    await screen.findByText('Keep my profile for future openings at Acme Test Co');
    expect(screen.getByText(/for up to 12 months/)).toBeInTheDocument();
    expect(screen.getByText(/turn it off at any time/)).toBeInTheDocument();
  });
});
