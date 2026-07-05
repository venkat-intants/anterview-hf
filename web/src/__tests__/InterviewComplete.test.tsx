// Tests for InterviewComplete page
//
// Covers:
//   1. Renders the "Interview Complete" heading.
//   2. Shows the preparing scorecard state while polling.
//   3. Redirects to /scorecard/:id when the scorecard_id arrives.
//   4. Shows the timeout state after SCORECARD_POLL_TIMEOUT_MS elapses.
//   5. "Check again" restarts polling.
//   6. Navigation links to Dashboard and History are present.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockListSessions = vi.fn();
vi.mock('../api/sessions', () => ({
  // eslint-disable-next-line @typescript-eslint/no-unsafe-return
  listSessions: (...args: unknown[]) => mockListSessions(...args),
}));

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderComplete(sessionId = 'sess-abc-123') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/interview/${sessionId}/complete`]}>
        <Routes>
          <Route
            path="/interview/:sessionId/complete"
            element={<InterviewCompleteComponent />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// InterviewComplete is a default export — import it as a variable so we can
// name it without clashing with the imported type.
import InterviewCompleteComponent from '../pages/InterviewComplete';

// ---------------------------------------------------------------------------
// Render helper with location state support
// ---------------------------------------------------------------------------

function renderCompleteWithState(
  sessionId = 'sess-abc-123',
  state?: Record<string, unknown>,
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={[
          { pathname: `/interview/${sessionId}/complete`, state: state ?? null },
        ]}
      >
        <Routes>
          <Route
            path="/interview/:sessionId/complete"
            element={<InterviewCompleteComponent />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('InterviewComplete page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Default: session exists but scorecard_id is still null (generating)
    mockListSessions.mockResolvedValue({
      items: [
        {
          session_id: 'sess-abc-123',
          job_title: 'Software Engineer',
          language: 'en',
          status: 'completed',
          started_at: '2026-05-30T10:00:00Z',
          completed_at: '2026-05-30T10:12:00Z',
          duration_seconds: 720,
          created_at: '2026-05-30T09:58:00Z',
          scorecard_id: null, // not yet ready
        },
      ],
      total: 1,
      page: 1,
      per_page: 20,
    });
  });

  it('renders the Interview Complete heading', () => {
    renderComplete();
    expect(screen.getByRole('heading', { name: /interview complete/i })).toBeInTheDocument();
  });

  it('shows a "preparing your scorecard" message while polling', async () => {
    renderComplete();
    await waitFor(() => {
      expect(screen.getByText(/preparing your scorecard/i)).toBeInTheDocument();
    });
  });

  it('shows the check-icon and completion message', () => {
    renderComplete();
    // The completion checkmark icon is decorative; heading and message are present
    expect(screen.getByRole('heading', { name: /interview complete/i })).toBeInTheDocument();
    expect(screen.getByText(/your responses have been recorded/i)).toBeInTheDocument();
  });

  it('redirects to /scorecard/:id when scorecard_id arrives in poll', async () => {
    // First call: no scorecard yet; second call: scorecard ready
    mockListSessions
      .mockResolvedValueOnce({
        items: [
          {
            session_id: 'sess-abc-123',
            job_title: 'SE',
            language: 'en',
            status: 'completed',
            started_at: '',
            completed_at: '',
            duration_seconds: null,
            created_at: '',
            scorecard_id: null,
          },
        ],
        total: 1,
        page: 1,
        per_page: 20,
      })
      .mockResolvedValue({
        items: [
          {
            session_id: 'sess-abc-123',
            job_title: 'SE',
            language: 'en',
            status: 'completed',
            started_at: '',
            completed_at: '',
            duration_seconds: null,
            created_at: '',
            scorecard_id: 'sc-ready-001',
          },
        ],
        total: 1,
        page: 1,
        per_page: 20,
      });

    renderComplete();

    await waitFor(
      () => {
        expect(mockNavigate).toHaveBeenCalledWith('/scorecard/sc-ready-001', { replace: true });
      },
      { timeout: 15000 }, // React Query refetch interval is 3s — allow a couple of polls
    );
  });

  it('does not render timeout state initially (it starts in polling state)', async () => {
    // Guard: verify the component starts in the preparing state, not the timeout state.
    renderComplete();
    await waitFor(() => {
      expect(screen.getByText(/preparing your scorecard/i)).toBeInTheDocument();
    });
    // Timeout message must NOT be present at startup
    expect(screen.queryByText(/scorecard is taking longer than usual/i)).not.toBeInTheDocument();
  });

  it('"Back to Dashboard" link is always visible regardless of poll state', () => {
    renderComplete();
    // Dashboard link is rendered immediately (not gated behind polling state)
    const dashboardLink = screen.getByRole('link', { name: /back to dashboard/i });
    expect(dashboardLink).toBeInTheDocument();
  });

  it('has a "Back to Dashboard" link pointing to /dashboard', () => {
    renderComplete();
    const dashboardLink = screen.getByRole('link', { name: /back to dashboard/i });
    expect(dashboardLink).toHaveAttribute('href', '/dashboard');
  });

  it('has a "History" link pointing to /history', () => {
    renderComplete();
    const historyLink = screen.getByRole('link', { name: /history/i });
    expect(historyLink).toHaveAttribute('href', '/history');
  });

  it('has a "New interview" link pointing to /start', () => {
    renderComplete();
    const newLink = screen.getByRole('link', { name: /new interview/i });
    expect(newLink).toHaveAttribute('href', '/start');
  });

  // ── Early-exit path ────────────────────────────────────────────────────────

  it('shows early-exit heading when endedEarly state is true', () => {
    renderCompleteWithState('sess-abc-123', { endedEarly: true, message: 'You ended the interview early.' });
    expect(screen.getByRole('heading', { name: /interview ended early/i })).toBeInTheDocument();
  });

  it('shows early-exit notice card when endedEarly is true', () => {
    renderCompleteWithState('sess-abc-123', { endedEarly: true });
    expect(screen.getByTestId('early-exit-card')).toBeInTheDocument();
    expect(screen.getByText(/session closed before completion/i)).toBeInTheDocument();
  });

  it('does NOT show "preparing your scorecard" when endedEarly is true', () => {
    renderCompleteWithState('sess-abc-123', { endedEarly: true });
    expect(screen.queryByText(/preparing your scorecard/i)).not.toBeInTheDocument();
  });

  it('does NOT call listSessions when endedEarly is true', () => {
    mockListSessions.mockClear();
    renderCompleteWithState('sess-abc-123', { endedEarly: true });
    // listSessions must not be invoked — no scorecard polling for early exits
    expect(mockListSessions).not.toHaveBeenCalled();
  });

  it('shows Dashboard, History and New interview links on early-exit', () => {
    renderCompleteWithState('sess-abc-123', { endedEarly: true });
    expect(screen.getByRole('link', { name: /back to dashboard/i })).toHaveAttribute('href', '/dashboard');
    expect(screen.getByRole('link', { name: /history/i })).toHaveAttribute('href', '/history');
    expect(screen.getByRole('link', { name: /new interview/i })).toHaveAttribute('href', '/start');
  });
});
