// Smoke tests for Dashboard page
// The Dashboard renders inside AppShell when in the real app, but in tests
// we render it directly (no AppShell) to keep the scope tight.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider, useAuth } from '../context/AuthContext';
import Dashboard from '../pages/Dashboard';
import { useEffect } from 'react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../lib/i18n';

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

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

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

describe('Dashboard page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // Aurora redesign: the hero heading changed from "Welcome, <name>" to
  // "Let's get you hired, <firstName>." (h1 inside a GlassCard). The intent
  // is unchanged — verify the user is personally greeted after login.
  it('renders welcome message after load', async () => {
    renderDashboard();
    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: /let's get you hired/i }),
      ).toBeInTheDocument();
    });
  });

  // Aurora redesign: the email is no longer shown on the Dashboard surface.
  // The user's first name now appears in the h1 greeting — verify that the
  // authenticated user's first name ("Test") is present in the heading.
  it('displays user email', async () => {
    renderDashboard();
    await waitFor(() => {
      // The h1 greeting reads "Let's get you hired, Test." — the first name
      // is derived from full_name supplied by the getMe mock.
      expect(
        screen.getByRole('heading', { name: /test/i }),
      ).toBeInTheDocument();
    });
  });

  it('shows the Start Interview quick-action button', async () => {
    renderDashboard();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /start interview/i })).toBeInTheDocument();
    });
  });

  // Aurora redesign: explicit role badges were removed from the Dashboard.
  // Candidate-specific content is now indicated by the "Interview readiness"
  // section, which only renders for authenticated candidates. Verify that
  // heading is present to assert role-appropriate UI loaded correctly.
  it('displays the candidate role badge', async () => {
    renderDashboard();
    await waitFor(() => {
      // t('dashboard.readinessTitle') = 'Interview readiness'
      expect(screen.getByText(/interview readiness/i)).toBeInTheDocument();
    });
  });

  it('navigates to /start when Start Interview is clicked', async () => {
    const user = userEvent.setup();
    renderDashboard();
    const startBtn = await screen.findByRole('button', { name: /start interview/i });
    await user.click(startBtn);
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/start');
    });
  });

  // Aurora redesign: the "Interviews" StatCard value goes through AnimatedNumber
  // which relies on IntersectionObserver to count up. Because jsdom stubs
  // IntersectionObserver as a no-op, the animated span stays at its initial
  // "—" placeholder value even after data loads. Instead we assert on the
  // readiness-card description sentence, which is plain (non-animated) text
  // that includes the interview count once sessions data has resolved.
  // t('dashboard.readinessDesc', { count: 1 }) → "Based on 1 attempt(s) across your sessions"
  it('shows interviews-taken stat after data loads', async () => {
    renderDashboard();
    await waitFor(() => {
      expect(
        screen.getByText(/based on 1 attempt/i),
      ).toBeInTheDocument();
    });
  });

  // Aurora redesign: the average score is no longer shown as "7.0 / 10".
  // composite_score 7.05 × 10 = Math.round(70.5) = 71.
  // The ScoreRing renders the 0–100 readiness score (71) directly as a
  // numeric <span> — this is NOT routed through AnimatedNumber, so it is
  // present as plain DOM text as soon as the scorecards query resolves.
  it('shows average score stat after data loads', async () => {
    renderDashboard();
    // composite_score: 7.05 → Math.round(7.05 * 10) = 71 (ScoreRing 0–100 scale)
    await waitFor(() => {
      expect(screen.getByText('71')).toBeInTheDocument();
    });
  });

  it('shows recent session job title after data loads', async () => {
    renderDashboard();
    await waitFor(() => {
      expect(screen.getAllByText(/junior java developer/i).length).toBeGreaterThan(0);
    });
  });
});
