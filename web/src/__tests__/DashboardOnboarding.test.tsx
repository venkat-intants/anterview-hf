// The onboarding → dashboard handoff.
//
// Two behaviours worth locking down, both of which were broken in a way that
// only showed up as a flicker:
//
//   1. A user who still owes onboarding must never see the dashboard. The
//      redirect existed before, but the page painted first and was then yanked
//      away — a generic marketing banner flashed at the one user least served
//      by it.
//   2. Once a practice plan exists, the dashboard speaks about THEIR role and
//      drops the acquisition banner.
//
// Rendered without AppShell, like Dashboard.test.tsx, to keep the scope tight.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
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

  it('keeps the marketing banner for a user who skipped personalization', async () => {
    vi.mocked(getOnboardingStatus).mockResolvedValue({
      ...SEEN_STATUS,
      completed: false,
      skipped: true,
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
      expect(screen.getByText(/practice like it's real/i)).toBeInTheDocument();
    });
  });
});
