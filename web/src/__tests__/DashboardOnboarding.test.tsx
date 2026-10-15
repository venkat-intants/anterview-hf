// The onboarding → dashboard handoff.
//
// Three behaviours worth locking down, the first two of which were broken in a
// way that only showed up as a flicker:
//
//   1. A user who still owes onboarding AND has no application must never see
//      the dashboard. The redirect existed before, but the page painted first
//      and was then yanked away.
//   2. Once a practice plan exists, the dashboard speaks about THEIR role.
//   3. An account that already has applications is NOT sent to the practice
//      wizard: someone who activated to follow an application lands on it. The
//      redirect waits for the applications answer rather than guessing, so
//      neither the dashboard nor the wizard flashes while it loads.
//
// Rendered without AppShell, like Dashboard.test.tsx, to keep the scope tight.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect } from 'react';
import { I18nextProvider } from 'react-i18next';

import { AuthProvider, useAuth } from '../context/AuthContext';
import Dashboard from '../pages/Dashboard';
import i18n from '../lib/i18n';
import { getOnboardingStatus, getPracticePlan } from '../api/onboarding';

vi.mock('../api/auth', () => ({
  getMe: vi.fn().mockResolvedValue({
    user_id: '11111111-1111-1111-1111-111111111111',
    full_name: 'Asha Rao',
    email: 'asha@intants.com',
    roles: ['candidate'],
    has_resume: false,
  }),
  logout: vi.fn().mockResolvedValue({ ok: true }),
}));

vi.mock('../api/resume', () => ({
  uploadResume: vi.fn(),
  getCurrentResume: vi.fn().mockRejectedValue(new Error('No resume')),
}));

vi.mock('../api/sessions', () => ({
  listSessions: vi
    .fn()
    .mockResolvedValue({ items: [], total: 0, page: 1, per_page: 3 }),
}));

vi.mock('../api/scorecard', () => ({
  listScorecards: vi
    .fn()
    .mockResolvedValue({ items: [], total: 0, page: 1, per_page: 20 }),
}));

vi.mock('../api/onboarding', async () => {
  const actual =
    await vi.importActual<typeof import('../api/onboarding')>('../api/onboarding');
  return { ...actual, getOnboardingStatus: vi.fn(), getPracticePlan: vi.fn() };
});

const listMyApplications = vi.fn();
vi.mock('../api/applications', () => ({
  listMyApplications: (...a: unknown[]) => listMyApplications(...a) as unknown,
  mintMyExamLink: vi.fn(),
  mintMyInterviewLink: vi.fn(),
  mintMyTaskLink: vi.fn(),
}));
vi.mock('../api/scheduling', () => ({ listMyInterviewLoops: vi.fn().mockResolvedValue([]) }));
vi.mock('../api/offers', () => ({
  listMyOffers: vi.fn().mockResolvedValue([]),
  getMyOfferLink: vi.fn(),
}));

const APPLICATION = {
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
};

const NOT_ONBOARDED = {
  applicable: true,
  seen: false,
  completed: false,
  skipped: false,
  full_name: 'Asha Rao',
  goal: null,
  target_role: null,
  target_level: null,
  preferred_language: 'en',
};

const EMPTY_PLAN = {
  ready: false,
  competencies: [],
  interviews_completed: 0,
  never_probed: [],
  target_role: null,
  target_level: null,
  domain_family: null,
  domain_label: null,
  what_this_person_does: null,
  readiness: null,
  last_interview_at: null,
  focus_competency_id: null,
  focus_competency_name: null,
};

const SEEN_STATUS = {
  applicable: true,
  seen: true,
  completed: true,
  skipped: false,
  full_name: 'Asha Rao',
  goal: 'campus_placement' as const,
  target_role: 'CNC Machine Operator',
  target_level: 'entry' as const,
  preferred_language: 'en',
};

const READY_PLAN = {
  ready: true,
  target_role: 'CNC Machine Operator',
  target_level: 'entry' as const,
  domain_family: 'manufacturing',
  domain_label: 'Mechanical & Manufacturing',
  what_this_person_does: 'Sets up and runs CNC machines to spec.',
  competencies: [
    {
      id: 'machine_setup',
      name: 'Machine Setup',
      kind: 'technical',
      weight: 0.4,
      target: 'Sets up a job from a drawing unaided.',
      score: 4.2,
      attempts: 2,
      trend: -0.4,
    },
    {
      id: 'safety',
      name: 'Shop-floor Safety',
      kind: 'technical',
      weight: 0.6,
      target: 'Names the hazards before touching the machine.',
      score: 7.1,
      attempts: 2,
      trend: 0.3,
    },
  ],
  readiness: 58,
  interviews_completed: 2,
  last_interview_at: '2026-08-01T10:00:00Z',
  focus_competency_id: 'machine_setup',
  focus_competency_name: 'Machine Setup',
  never_probed: [],
};

function TokenSetter({ children }: { children: React.ReactNode }) {
  const { setAuth } = useAuth();
  useEffect(() => {
    setAuth('mock-access-token', {
      user_id: '11111111-1111-1111-1111-111111111111',
      full_name: 'Asha Rao',
      email: 'asha@intants.com',
      roles: ['candidate'],
    });
  }, [setAuth]);
  return <>{children}</>;
}

function renderAt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/dashboard']}>
          <AuthProvider>
            <TokenSetter>
              <Routes>
                <Route path="/dashboard" element={<Dashboard />} />
                <Route path="/onboarding" element={<h1>Onboarding wizard</h1>} />
              </Routes>
            </TokenSetter>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

describe('Dashboard ↔ onboarding handoff', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // A practice-only account unless a test says otherwise.
    listMyApplications.mockResolvedValue([]);
  });

  it('sends a not-yet-onboarded user to the wizard without painting the dashboard', async () => {
    vi.mocked(getOnboardingStatus).mockResolvedValue({
      ...SEEN_STATUS,
      seen: false,
      completed: false,
      goal: null,
      target_role: null,
      target_level: null,
    });
    vi.mocked(getPracticePlan).mockResolvedValue({
      ready: false,
      competencies: [],
      interviews_completed: 0,
      never_probed: [],
      target_role: null,
      target_level: null,
      domain_family: null,
      domain_label: null,
      what_this_person_does: null,
      readiness: null,
      last_interview_at: null,
      focus_competency_id: null,
      focus_competency_name: null,
    });

    renderAt();

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /onboarding wizard/i })).toBeInTheDocument();
    });
    // The flash this guards against: the marketing banner must never have been
    // rendered on the way through.
    expect(screen.queryByText(/practice like it's real/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /let's get you hired/i })).not.toBeInTheDocument();
  });

  it('personalizes the dashboard and drops the marketing banner once a plan exists', async () => {
    vi.mocked(getOnboardingStatus).mockResolvedValue(SEEN_STATUS);
    vi.mocked(getPracticePlan).mockResolvedValue(READY_PLAN);

    renderAt();

    // Hero speaks about their role and points at their weakest competency.
    await waitFor(() => {
      expect(
        screen.getByText(
          /practising for cnc machine operator.*weakest area right now is machine setup/i,
        ),
      ).toBeInTheDocument();
    });
    // Domain label replaces the generic "Welcome back" eyebrow.
    expect(screen.getAllByText(/mechanical & manufacturing/i).length).toBeGreaterThan(0);
    // Goal-aware line — they said "campus placement".
    expect(screen.getByText(/placement season rewards reps/i)).toBeInTheDocument();
    // Acquisition banner is gone; it has no job once a plan exists.
    expect(screen.queryByText(/practice like it's real/i)).not.toBeInTheDocument();
  });

  it('sends a user who skipped personalization to their first practice, and still offers to personalize', async () => {
    vi.mocked(getOnboardingStatus).mockResolvedValue({
      ...SEEN_STATUS,
      completed: false,
      skipped: true,
      goal: null,
      target_role: null,
      target_level: null,
    });
    vi.mocked(getPracticePlan).mockResolvedValue(EMPTY_PLAN);

    renderAt();

    // The promo is gone for good. No interview yet, so the next step is the
    // first one...
    const strip = await screen.findByTestId('next-step');
    expect(screen.queryByText(/practice like it's real/i)).not.toBeInTheDocument();
    expect(within(strip).getByRole('heading', { name: 'Start your first practice interview' })).toBeInTheDocument();
    expect(within(strip).getByRole('link', { name: 'Start practice' })).toHaveAttribute('href', '/start');
    // ...and personalising is a quieter, separate offer on the plan card.
    expect(screen.getByRole('link', { name: /personalize/i })).toHaveAttribute('href', '/onboarding');
  });

  it('carries exactly one practice call to action once a plan exists', async () => {
    vi.mocked(getOnboardingStatus).mockResolvedValue(SEEN_STATUS);
    vi.mocked(getPracticePlan).mockResolvedValue(READY_PLAN);

    renderAt();

    const strip = await screen.findByTestId('next-step');
    expect(within(strip).getByRole('link', { name: 'Start practice' })).toHaveAttribute('href', '/start');
    // The plan card is on the page, but its own start button is not.
    expect(screen.getByText('Your practice plan')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /practise machine setup|start a mock interview/i })).not
      .toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: /start practice|practise|mock interview/i })).toHaveLength(1);
  });

  it('leaves the plan card’s start button as the practice call to action when an application is the next step', async () => {
    vi.mocked(getOnboardingStatus).mockResolvedValue(SEEN_STATUS);
    vi.mocked(getPracticePlan).mockResolvedValue(READY_PLAN);
    listMyApplications.mockResolvedValue([APPLICATION]);

    renderAt();

    await screen.findByRole('heading', { name: 'Nothing needed from you right now' });
    // One practice call to action, and it is the plan card's (outline, not primary).
    expect(screen.getAllByRole('link', { name: /practise machine setup/i })).toHaveLength(1);
  });

  describe('an account that owes onboarding', () => {
    beforeEach(() => {
      vi.mocked(getOnboardingStatus).mockResolvedValue(NOT_ONBOARDED);
      vi.mocked(getPracticePlan).mockResolvedValue(EMPTY_PLAN);
    });

    it('is sent to the wizard when it has no applications', async () => {
      listMyApplications.mockResolvedValue([]);
      renderAt();
      expect(await screen.findByRole('heading', { name: /onboarding wizard/i })).toBeInTheDocument();
    });

    it('is NOT sent there when it has applications', async () => {
      // Someone who activated their account to follow an application should
      // land on it, not be asked what job they are practising for.
      listMyApplications.mockResolvedValue([APPLICATION]);
      renderAt();
      expect(
        await screen.findByRole('heading', { name: 'Nothing needed from you right now' }),
      ).toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: /onboarding wizard/i })).not.toBeInTheDocument();
    });

    it('paints neither the dashboard nor the wizard while the applications are still loading', async () => {
      let release: (apps: unknown[]) => void = () => undefined;
      listMyApplications.mockReturnValue(
        new Promise<unknown[]>((resolve) => {
          release = resolve;
        }),
      );
      renderAt();

      await screen.findByTestId('dashboard-skeleton');
      expect(screen.queryByRole('heading', { name: /onboarding wizard/i })).not.toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: /let's get you hired/i })).not.toBeInTheDocument();

      release([APPLICATION]);
      expect(
        await screen.findByRole('heading', { name: 'Nothing needed from you right now' }),
      ).toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: /onboarding wizard/i })).not.toBeInTheDocument();
    });

    it('does NOT send a real applicant to the wizard because the applications call failed', async () => {
      // A failed request is "unknown", not "none". Redirecting here dropped an
      // applicant with a pending assessment into the practice wizard.
      listMyApplications.mockRejectedValue(new Error('down'));
      renderAt();
      expect(await screen.findByTestId('next-step')).toBeInTheDocument();
      expect(screen.getByRole('heading', { name: 'We could not check your applications' })).toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: /onboarding wizard/i })).not.toBeInTheDocument();
    });
  });
});
