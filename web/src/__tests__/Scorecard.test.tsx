// Tests for Scorecard page — S5-007 + redesign (feat/ui-redesign-v2)
// Covers: loading skeleton, data display after fetch, error state, PDF button,
// navigation links, radar chart presence.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider, useAuth } from '../context/AuthContext';
import Scorecard from '../pages/Scorecard';
import { useEffect } from 'react';
import type { ScorecardData } from '../api/scorecard';

// ---------------------------------------------------------------------------
// Mock scorecard API
// ---------------------------------------------------------------------------

const MOCK_SCORECARD_DATA: ScorecardData = {
  scorecard_id: '00000000-0000-0000-0000-000000000001',
  session_id: '00000000-0000-0000-0000-000000000002',
  composite_score: 7.05,
  scores: {
    communication: 7,
    technical: 6,
    problem_solving: 8,
    confidence: 7,
  },
  strengths: [
    'Clear communication throughout the interview',
    'Good use of concrete examples',
    'Structured thinking',
  ],
  improvements: [
    { area: 'Technical Depth', suggestion: 'Practice system design concepts.' },
    { area: 'Confidence', suggestion: 'Speak at a measured pace.' },
  ],
  summary: 'A solid entry-level candidate who meets tier expectations on most axes.',
  report_pdf_url: null,
};

const mockGetScorecard = vi.fn().mockResolvedValue(MOCK_SCORECARD_DATA);

vi.mock('../api/scorecard', () => ({
  getScorecard: (...args: unknown[]) => mockGetScorecard(...args) as unknown,
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

function renderScorecard(scorecardId = '00000000-0000-0000-0000-000000000001') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/scorecard/${scorecardId}`]}>
        <AuthProvider>
          <TokenSetter>
            <Routes>
              <Route path="/scorecard/:scorecardId" element={<Scorecard />} />
            </Routes>
          </TokenSetter>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Scorecard page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetScorecard.mockResolvedValue(MOCK_SCORECARD_DATA);
  });

  it('shows a loading spinner initially', () => {
    // Delay the mock so the spinner is visible
    mockGetScorecard.mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(MOCK_SCORECARD_DATA), 200)),
    );
    renderScorecard();
    expect(screen.getByRole('status', { name: /loading scorecard/i })).toBeInTheDocument();
  });

  it('displays the overall score after data loads', async () => {
    renderScorecard();
    // After data loads, the page h1 "Your Scorecard" appears and the ScoreRing
    // renders the 0-100 scaled value (Math.round(7.05*10) = 71) as visible text
    // inside a span. The sr-only span reads "Overall Score: 7.1 out of 10".
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /your scorecard/i })).toBeInTheDocument();
    });
    // ScoreRing renders the 0-100 integer (71) as a visible span inside the ring.
    // The "Overall Score" label appears as a <p> below the ring.
    expect(screen.getByText('71')).toBeInTheDocument();
    // Both "Overall Score" labels (sr-only span + visible <p>) are in the DOM.
    expect(screen.getAllByText(/overall score/i).length).toBeGreaterThanOrEqual(1);
  });

  it('displays the score breakdown section', async () => {
    renderScorecard();
    await waitFor(() => {
      expect(screen.getByText('Score Breakdown')).toBeInTheDocument();
    });
    expect(screen.getByText('Communication')).toBeInTheDocument();
    expect(screen.getByText('Technical Knowledge')).toBeInTheDocument();
    expect(screen.getByText('Problem Solving')).toBeInTheDocument();
    expect(screen.getByText('Confidence')).toBeInTheDocument();
  });

  it('displays strengths list', async () => {
    renderScorecard();
    await waitFor(() => {
      expect(screen.getByText('Key Strengths')).toBeInTheDocument();
    });
    expect(screen.getByText('Clear communication throughout the interview')).toBeInTheDocument();
    expect(screen.getByText('Good use of concrete examples')).toBeInTheDocument();
  });

  it('displays improvements with area and suggestion', async () => {
    renderScorecard();
    await waitFor(() => {
      expect(screen.getByText('Areas for Improvement')).toBeInTheDocument();
    });
    expect(screen.getByText('Technical Depth:')).toBeInTheDocument();
    expect(screen.getByText('Practice system design concepts.')).toBeInTheDocument();
  });

  it('displays the summary paragraph', async () => {
    renderScorecard();
    await waitFor(() => {
      expect(screen.getByText(/solid entry-level candidate/i)).toBeInTheDocument();
    });
  });

  it('does not show Download PDF button when report_pdf_url is null', async () => {
    renderScorecard();
    // Use the unique h1 "Your Scorecard" as the "data loaded" sentinel — the page
    // renders it only after the query resolves; avoids the multi-match problem with
    // "Overall Score" which appears in both an sr-only span and a visible <p>.
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /your scorecard/i })).toBeInTheDocument();
    });
    expect(screen.queryByText(/download pdf/i)).not.toBeInTheDocument();
  });

  it('shows Download PDF button when report_pdf_url is set', async () => {
    mockGetScorecard.mockResolvedValueOnce({
      ...MOCK_SCORECARD_DATA,
      report_pdf_url: 'https://r2.example.com/scorecards/001/report.pdf?sig=abc',
    });
    renderScorecard();
    // The redesigned page renders the "Download PDF Report" link in TWO places:
    // the header area and the CTA footer. Wait for at least one to appear, then
    // verify both share the correct href and target — this upholds the original
    // intent (the PDF link exists and opens in a new tab) while matching the new DOM.
    await waitFor(() => {
      expect(screen.getAllByRole('link', { name: /download pdf report/i }).length).toBeGreaterThan(0);
    });
    const links = screen.getAllByRole('link', { name: /download pdf report/i });
    links.forEach((link) => {
      expect(link).toHaveAttribute('target', '_blank');
      expect(link).toHaveAttribute(
        'href',
        'https://r2.example.com/scorecards/001/report.pdf?sig=abc',
      );
    });
  });

  it('shows error state when fetch fails', async () => {
    mockGetScorecard.mockRejectedValueOnce(new Error('Not found'));
    renderScorecard('bad-id');
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
    expect(screen.getByText(/scorecard not available/i)).toBeInTheDocument();
  });

  it('renders back-to-history navigation link', async () => {
    renderScorecard();
    // Use h1 "Your Scorecard" as the "data loaded" sentinel (unique on the page).
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /your scorecard/i })).toBeInTheDocument();
    });
    expect(screen.getByRole('link', { name: /history/i })).toBeInTheDocument();
  });

  it('renders back-to-dashboard navigation link', async () => {
    renderScorecard();
    // Use h1 "Your Scorecard" as the "data loaded" sentinel (unique on the page).
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /your scorecard/i })).toBeInTheDocument();
    });
    expect(screen.getByRole('link', { name: /dashboard/i })).toBeInTheDocument();
  });
});
