// Tests for the candidate Dashboard page.
// The Dashboard renders inside AppShell when in the real app, but in tests
// we render it directly (no AppShell) to keep the scope tight.
//
// What is worth pinning here is the page's contract, not its pixels:
//   - the greeting carries no button, and the page has exactly ONE next step,
//     taken from the head of the candidate's action queue;
//   - rendering never mints a link (minting rotates the token — a render that
//     did it would kill the emailed link of everyone who looked);
//   - the 409 "open it here?" question for an assessment open elsewhere is kept;
//   - interviews and offers are only fetched when there is an application;
//   - the statistics are one group, drawn only when there is something to count.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider, useAuth } from '../context/AuthContext';
import Dashboard from '../pages/Dashboard';
import { useEffect } from 'react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../lib/i18n';
import { ApiError } from '../api/client';
import { listSessions } from '../api/sessions';
import { listScorecards } from '../api/scorecard';
import type { MyApplication } from '../api/applications';

vi.mock('../api/auth', () => ({
  getMe: vi.fn().mockResolvedValue({
    user_id: '11111111-1111-1111-1111-111111111111',
    full_name: 'Test Candidate',
    email: 'test@intants.com',
    roles: ['candidate'],
    has_resume: false,
  }),
  logout: vi.fn().mockResolvedValue({ ok: true }),
}));

vi.mock('../api/resume', () => ({
  uploadResume: vi.fn().mockResolvedValue({
    message: 'ok',
    resume_id: 'new-id',
    resume_s3_key: 'resumes/test.pdf',
    text_length: 1000,
  }),
  getCurrentResume: vi.fn().mockRejectedValue(new Error('No resume')),
}));

vi.mock('../api/sessions', () => ({
  listSessions: vi.fn().mockResolvedValue({
    items: [
      {
        session_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
        job_title: 'Junior Java Developer',
        language: 'en',
        status: 'completed',
        started_at: '2026-05-29T10:00:00Z',
        completed_at: '2026-05-29T10:12:34Z',
        duration_seconds: 754,
        created_at: '2026-05-29T09:58:00Z',
        scorecard_id: '00000000-0000-0000-0000-000000000001',
      },
    ],
    total: 1,
    page: 1,
    per_page: 3,
  }),
}));

// Onboarding + practice plan gate what the Dashboard renders at all: it holds a
// skeleton until the status resolves. Mocked explicitly so these tests exercise
// the already-onboarded, no-target-role path deterministically instead of
// leaning on a network call failing fast.
vi.mock('../api/onboarding', async () => {
  const actual =
    await vi.importActual<typeof import('../api/onboarding')>('../api/onboarding');
  return {
    ...actual,
    getOnboardingStatus: vi.fn().mockResolvedValue({
      applicable: true,
      seen: true,
      completed: false,
      skipped: true,
      full_name: 'Test Candidate',
      goal: null,
      target_role: null,
      target_level: null,
      preferred_language: 'en',
    }),
    getPracticePlan: vi.fn().mockResolvedValue({
      ready: false,
      competencies: [],
      interviews_completed: 0,
      never_probed: [],
    }),
  };
});

vi.mock('../api/scorecard', () => ({
  listScorecards: vi.fn().mockResolvedValue({
    items: [
      {
        scorecard_id: '00000000-0000-0000-0000-000000000001',
        session_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
        composite_score: 7.05,
        created_at: '2026-05-29T10:13:00Z',
        summary: 'Solid candidate.',
        job_title: 'Junior Java Developer',
      },
    ],
    total: 1,
    page: 1,
    per_page: 20,
  }),
}));

// The candidate's applications, interview loops and offers. Applications default
// to none (a practice-only account); the application tests set them.
const listMyApplications = vi.fn();
const mintMyExamLink = vi.fn();
const mintMyInterviewLink = vi.fn();
const mintMyTaskLink = vi.fn();
vi.mock('../api/applications', () => ({
  listMyApplications: (...a: unknown[]) => listMyApplications(...a) as unknown,
  mintMyExamLink: (...a: unknown[]) => mintMyExamLink(...a) as unknown,
  mintMyInterviewLink: (...a: unknown[]) => mintMyInterviewLink(...a) as unknown,
  mintMyTaskLink: (...a: unknown[]) => mintMyTaskLink(...a) as unknown,
}));
const listMyInterviewLoops = vi.fn();
vi.mock('../api/scheduling', () => ({
  listMyInterviewLoops: (...a: unknown[]) => listMyInterviewLoops(...a) as unknown,
}));
const listMyOffers = vi.fn();
const getMyOfferLink = vi.fn();
vi.mock('../api/offers', () => ({
  listMyOffers: (...a: unknown[]) => listMyOffers(...a) as unknown,
  getMyOfferLink: (...a: unknown[]) => getMyOfferLink(...a) as unknown,
}));

// Starting something navigates the tab. jsdom's location is not writable, so the
// assign it calls is stubbed and asserted on instead.
const assign = vi.fn();
Object.defineProperty(window, 'location', {
  writable: true,
  value: { ...window.location, assign },
});

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

function application(over: Partial<MyApplication> = {}): MyApplication {
  return {
    id: 'app-1',
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
    interview_invite_id: null,
    interview_scheduled_at: null,
    task_submission_id: null,
    task_due_at: null,
    task_status: null,
    exam_assignment_id: null,
    exam_in_progress: false,
    exam_expires_at: null,
    exam_scheduled_at: null,
    ...over,
  };
}

// Properly sets auth state via effect before Dashboard renders
function TokenSetter({ children }: { children: React.ReactNode }) {
  const { setAuth } = useAuth();
  useEffect(() => {
    setAuth('mock-access-token', {
      user_id: '11111111-1111-1111-1111-111111111111',
      full_name: 'Test Candidate',
      email: 'test@intants.com',
      roles: ['candidate'],
    });
  }, [setAuth]);
  return <>{children}</>;
}

function renderDashboard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <AuthProvider>
            <TokenSetter>
              <Dashboard />
            </TokenSetter>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listMyApplications.mockResolvedValue([]);
  listMyInterviewLoops.mockResolvedValue([]);
  listMyOffers.mockResolvedValue([]);
});

describe('Dashboard page', () => {
  // The hero heading is "Let's get you hired, <firstName>." — verify the user is
  // personally greeted after login.
  it('renders welcome message after load', async () => {
    renderDashboard();
    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: /let's get you hired/i }),
      ).toBeInTheDocument();
    });
  });

  // The user's first name appears in the h1 greeting.
  it('greets the user by first name', async () => {
    renderDashboard();
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /test/i })).toBeInTheDocument();
    });
  });

  it('puts no button in the greeting', async () => {
    const { container } = renderDashboard();
    await screen.findByRole('heading', { name: /let's get you hired/i });
    const header = container.querySelector('header');
    expect(header).not.toBeNull();
    expect(within(header as HTMLElement).queryAllByRole('button')).toEqual([]);
    expect(within(header as HTMLElement).queryAllByRole('link')).toEqual([]);
  });

  it('shows exactly one next step', async () => {
    renderDashboard();
    await screen.findByTestId('next-step');
    expect(screen.getAllByTestId('next-step')).toHaveLength(1);
    expect(screen.getByText('Your next step')).toBeInTheDocument();
  });

  it('does not render the promo banner or the static "Next steps" card', async () => {
    renderDashboard();
    await screen.findByTestId('next-step');
    expect(screen.queryByText(/practice like it's real/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /^next steps$/i })).not.toBeInTheDocument();
  });

  it('keeps the resume status and upload, in the aside', async () => {
    renderDashboard();
    const aside = await screen.findByRole('complementary', { name: /your resume/i });
    expect(within(aside).getByText(/no resume on file/i)).toBeInTheDocument();
  });

  it('names the resume on file once, and still offers an upload', async () => {
    const { getCurrentResume } = await import('../api/resume');
    vi.mocked(getCurrentResume).mockResolvedValue({
      resume_id: 'r1',
      filename: 'asha-rao-cv.pdf',
      resume_s3_key: 'resumes/r1.pdf',
      text_length: 1200,
      uploaded_at: '2026-05-01T10:00:00Z',
      created_at: '2026-05-01T10:00:00Z',
      download_url: null,
    });
    try {
      renderDashboard();
      const aside = await screen.findByRole('complementary', { name: /your resume/i });
      await within(aside).findByText('asha-rao-cv.pdf');
      expect(within(aside).getAllByText('asha-rao-cv.pdf')).toHaveLength(1);
      // The upload zone is still there to replace it.
      expect(aside.querySelector('input[type="file"]')).not.toBeNull();
    } finally {
      vi.mocked(getCurrentResume).mockRejectedValue(new Error('No resume'));
    }
  });

  // A practice-only account: the next step IS the practice call to action, so
  // the page carries one. This account has a past interview and no target role,
  // so the step is to set one, and the plan card does not repeat it.
  it('offers a practice-only account one practice step, and does not repeat it', async () => {
    renderDashboard();
    const link = await screen.findByRole('link', { name: 'Set target role' });
    expect(link).toHaveAttribute('href', '/onboarding');
    expect(screen.queryByRole('link', { name: /personalize/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /start a mock interview/i })).not.toBeInTheDocument();
  });

  it('does not fetch interviews or offers for an account with no applications', async () => {
    renderDashboard();
    await screen.findByTestId('next-step');
    expect(listMyApplications).toHaveBeenCalledTimes(1);
    expect(listMyInterviewLoops).not.toHaveBeenCalled();
    expect(listMyOffers).not.toHaveBeenCalled();
    expect(screen.queryByTestId('dashboard-applications')).not.toBeInTheDocument();
  });

  // The recent-interviews feed shows the job title once sessions resolve.
  it('shows recent session job title after data loads', async () => {
    renderDashboard();
    await waitFor(() => {
      expect(screen.getAllByText(/junior java developer/i).length).toBeGreaterThan(0);
    });
  });

  // The numbers are ONE group next to the interviews they summarise. 7.05 × 10
  // rounds to 71 on the 0–100 scale; the sole scored interview is also the best.
  it('shows the statistics as one group once there is something to count', async () => {
    renderDashboard();
    const stats = await screen.findByTestId('practice-stats');
    expect(within(stats).getByText('Interviews')).toBeInTheDocument();
    expect(within(stats).getByText('Avg score')).toBeInTheDocument();
    expect(within(stats).getAllByText('71')).toHaveLength(2); // average and best
  });

  it('draws no statistics at all before the first interview', async () => {
    vi.mocked(listSessions).mockResolvedValueOnce({ items: [], total: 0, page: 1, per_page: 100 });
    vi.mocked(listScorecards).mockResolvedValueOnce({ items: [], total: 0, page: 1, per_page: 20 });
    renderDashboard();
    await screen.findByText(/no practice interviews yet/i);
    expect(screen.queryByTestId('practice-stats')).not.toBeInTheDocument();
  });

  it('offers no button in the empty recent-interviews list', async () => {
    vi.mocked(listSessions).mockResolvedValueOnce({ items: [], total: 0, page: 1, per_page: 100 });
    renderDashboard();
    const empty = await screen.findByText(/no practice interviews yet/i);
    expect(within(empty.closest('section') as HTMLElement).queryAllByRole('button')).toEqual([]);
    expect(
      within(empty.closest('section') as HTMLElement).queryByRole('link', { name: /start/i }),
    ).not.toBeInTheDocument();
  });
});

describe('Dashboard - an account with applications', () => {
  const waiting = application({
    exam_assignment_id: 'asn-1',
    exam_expires_at: '2026-10-25T00:00:00Z',
  });

  it('leads with what is waiting on the candidate, with its role and company', async () => {
    listMyApplications.mockResolvedValue([application({ id: 'other' }), waiting]);
    renderDashboard();
    const strip = await screen.findByTestId('next-step');
    expect(within(strip).getByRole('heading', { name: 'Take your assessment' })).toBeInTheDocument();
    expect(within(strip).getByText('Backend Engineer · Acme Test Co')).toBeInTheDocument();
    expect(within(strip).getByRole('button', { name: 'Start assessment' })).toBeInTheDocument();
    expect(screen.getAllByTestId('next-step')).toHaveLength(1);
  });

  it('never asks for a link until the button is pressed', async () => {
    // Minting rotates the token and kills the emailed link. A render must not.
    listMyApplications.mockResolvedValue([waiting]);
    renderDashboard();
    await screen.findByRole('button', { name: 'Start assessment' });
    expect(mintMyExamLink).not.toHaveBeenCalled();
    expect(mintMyInterviewLink).not.toHaveBeenCalled();
    expect(mintMyTaskLink).not.toHaveBeenCalled();
    expect(getMyOfferLink).not.toHaveBeenCalled();
  });

  it('opens the link it is given, on press, in this tab', async () => {
    mintMyExamLink.mockResolvedValue({
      exam_url: `${window.location.origin}/exam#tok`,
      expires_at: '2026-10-25T00:00:00Z',
    });
    listMyApplications.mockResolvedValue([waiting]);
    renderDashboard();

    await userEvent.click(await screen.findByRole('button', { name: 'Start assessment' }));

    // false: an unprompted first press may not close a tab someone is sitting
    // their assessment in.
    await waitFor(() => expect(mintMyExamLink).toHaveBeenCalledWith('asn-1', false));
    await waitFor(() => expect(assign).toHaveBeenCalledWith(`${window.location.origin}/exam#tok`));
  });

  it('asks before closing an assessment open in another tab', async () => {
    const conflict = new ApiError('This assessment is already open in another tab.', 409);
    mintMyExamLink.mockRejectedValueOnce(conflict).mockResolvedValueOnce({
      exam_url: `${window.location.origin}/exam#tok2`,
      expires_at: '2026-10-25T00:00:00Z',
    });
    listMyApplications.mockResolvedValue([{ ...waiting, exam_in_progress: true }]);
    renderDashboard();

    await userEvent.click(await screen.findByRole('button', { name: 'Resume assessment' }));

    // The question, and nothing opened.
    expect(await screen.findByText(/already open in another tab/)).toBeInTheDocument();
    expect(assign).not.toHaveBeenCalled();

    // The answer, and only now may it close the other tab.
    await userEvent.click(screen.getByRole('button', { name: 'Open it here' }));
    await waitFor(() => expect(mintMyExamLink).toHaveBeenLastCalledWith('asn-1', true));
    await waitFor(() => expect(assign).toHaveBeenCalledWith(`${window.location.origin}/exam#tok2`));
  });

  it('explains a failed press instead of appearing to do nothing', async () => {
    mintMyExamLink.mockRejectedValue(new Error('No assessment is waiting on this application.'));
    listMyApplications.mockResolvedValue([waiting]);
    renderDashboard();

    await userEvent.click(await screen.findByRole('button', { name: 'Start assessment' }));
    expect(
      await screen.findByText('No assessment is waiting on this application.'),
    ).toBeInTheDocument();
  });

  it('counts the other things waiting, and links them to the applications page', async () => {
    listMyApplications.mockResolvedValue([
      waiting,
      application({ id: 'b', interview_invite_id: 'inv-1' }),
      application({ id: 'c', task_submission_id: 'sub-1' }),
    ]);
    renderDashboard();
    const more = await screen.findByRole('link', { name: '2 more waiting' });
    expect(more).toHaveAttribute('href', '/applications');
  });

  it('says nothing is needed when the applications are all with the hiring team', async () => {
    listMyApplications.mockResolvedValue([application()]);
    renderDashboard();
    const strip = await screen.findByTestId('next-step');
    expect(within(strip).getByText('Nothing needed from you right now')).toBeInTheDocument();
    expect(within(strip).queryAllByRole('button')).toEqual([]);
    expect(within(strip).queryAllByRole('link')).toEqual([]);
  });

  it('lists each application with whose move it is', async () => {
    listMyApplications.mockResolvedValue([
      application({ id: 'quiet', job_title: 'Sales Associate' }),
      { ...waiting, job_title: 'Data Analyst', round_number: 1, total_rounds: 3 },
      application({ id: 'done', job_title: 'Clerk', closed: true, stage: 'Not progressing' }),
    ]);
    renderDashboard();
    const card = await screen.findByTestId('dashboard-applications');
    const rows = within(card).getAllByRole('listitem');
    // Your move first, then with the team, then closed.
    expect(rows.map((r) => within(r).getByText(/Data Analyst|Sales Associate|Clerk/).textContent)).toEqual([
      'Data Analyst',
      'Sales Associate',
      'Clerk',
    ]);
    expect(within(rows[0]).getByText('Your move')).toBeInTheDocument();
    expect(within(rows[0]).getByText(/Round 1 of 3/)).toBeInTheDocument();
    expect(within(rows[1]).getByText('With the hiring team')).toBeInTheDocument();
    expect(within(rows[2]).getByText('Closed')).toBeInTheDocument();
  });

  it('fetches interviews and offers once, and only because there is an application', async () => {
    listMyApplications.mockResolvedValue([application()]);
    renderDashboard();
    await screen.findByTestId('dashboard-applications');
    await waitFor(() => expect(listMyInterviewLoops).toHaveBeenCalledTimes(1));
    expect(listMyOffers).toHaveBeenCalledTimes(1);
  });

  it('puts an unanswered offer in the queue even with no other application activity', async () => {
    listMyApplications.mockResolvedValue([application({ id: 'o' })]);
    listMyOffers.mockResolvedValue([
      {
        id: 'off-1',
        status: 'sent',
        company: 'Acme Test Co',
        job_title: 'Backend Engineer',
        expires_at: '2026-10-20T00:00:00Z',
        application_id: 'o',
      },
    ]);
    renderDashboard();
    const strip = await screen.findByTestId('next-step');
    await waitFor(() =>
      expect(within(strip).getByRole('heading', { name: 'Respond to your offer' })).toBeInTheDocument(),
    );
    expect(getMyOfferLink).not.toHaveBeenCalled();
  });
});

describe('Dashboard - a failed request is not an empty queue', () => {
  const failedTitle = 'We could not check your applications';

  it('does not say "start practising" when the applications call fails; it offers a retry', async () => {
    listMyApplications.mockRejectedValueOnce(new Error('down'));
    renderDashboard();
    const strip = await screen.findByTestId('next-step');
    expect(within(strip).getByRole('heading', { name: failedTitle })).toBeInTheDocument();
    expect(within(strip).queryByRole('link', { name: /practice|target role/i })).not.toBeInTheDocument();
    expect(screen.queryByText('Nothing needed from you right now')).not.toBeInTheDocument();

    listMyApplications.mockResolvedValue([application()]);
    await userEvent.click(within(strip).getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Nothing needed from you right now')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: failedTitle })).not.toBeInTheDocument();
  });

  it('does not say "nothing needed" when the offers call fails', async () => {
    listMyApplications.mockResolvedValue([application()]);
    listMyOffers.mockRejectedValueOnce(new Error('down'));
    renderDashboard();
    const strip = await screen.findByTestId('next-step');
    await waitFor(() =>
      expect(within(strip).getByRole('heading', { name: failedTitle })).toBeInTheDocument(),
    );
    expect(screen.queryByText('Nothing needed from you right now')).not.toBeInTheDocument();

    // Retry re-runs only the call that failed.
    listMyOffers.mockResolvedValue([]);
    await userEvent.click(within(strip).getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Nothing needed from you right now')).toBeInTheDocument();
    expect(listMyApplications).toHaveBeenCalledTimes(1);
  });

  it('does not say "nothing needed" when the interviews call fails', async () => {
    listMyApplications.mockResolvedValue([application()]);
    listMyInterviewLoops.mockRejectedValueOnce(new Error('down'));
    renderDashboard();
    expect(await screen.findByRole('heading', { name: failedTitle })).toBeInTheDocument();
    expect(screen.queryByText('Nothing needed from you right now')).not.toBeInTheDocument();
  });
});

// Audit CA-L1: the only action on a failed /me used to sign the person out.
describe('Dashboard - profile load failure', () => {
  it('retries first and keeps sign-out as the secondary action', async () => {
    const { getMe, logout } = await import('../api/auth');
    vi.mocked(getMe).mockRejectedValueOnce(new Error('upstream 502'));
    renderDashboard();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not load your profile.');
    expect(alert).not.toHaveTextContent('upstream 502');

    await userEvent.click(within(alert).getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(vi.mocked(getMe)).toHaveBeenCalledTimes(2));
    expect(vi.mocked(logout)).not.toHaveBeenCalled();
  });

  it('can still sign out from the error', async () => {
    const { getMe, logout } = await import('../api/auth');
    vi.mocked(getMe).mockRejectedValueOnce(new Error('upstream 502'));
    renderDashboard();
    const alert = await screen.findByRole('alert');
    await userEvent.click(within(alert).getByRole('button', { name: 'Sign out' }));
    await waitFor(() => expect(vi.mocked(logout)).toHaveBeenCalled());
  });
});
