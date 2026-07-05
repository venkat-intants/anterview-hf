// Tests for AdminInterviewDetail page
// Covers: scored path (renders composite score, axes, strengths, improvements, summary)
//         and unscored path (scorecard: null — renders "No scorecard yet" without crashing).
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import AdminInterviewDetail from '../pages/admin/AdminInterviewDetail';
import type { InterviewDetailResponse } from '../api/admin';

// ---------------------------------------------------------------------------
// Mock admin API
// ---------------------------------------------------------------------------

const SCORED_DETAIL: InterviewDetailResponse = {
  session_id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
  candidate_email: 'priya.sharma@example.com',
  candidate_name: 'Priya Sharma',
  candidate_preferred_language: 'en',
  job_title: 'Junior Java Developer',
  status: 'completed',
  language: 'en',
  started_at: '2026-06-01T10:00:00Z',
  completed_at: '2026-06-01T10:12:34Z',
  duration_seconds: 754,
  scorecard: {
    scorecard_id: '00000000-0000-0000-0000-000000000099',
    composite_score: 7.85,
    communication: 8.0,
    technical: 7.5,
    problem_solving: 8.0,
    confidence: 7.75,
    strengths: ['Articulate communicator', 'Strong Java OOP fundamentals'],
    improvements: [{ area: 'Concurrency', suggestion: 'Improve concurrency knowledge' }],
    summary: 'A strong junior candidate.',
  },
};

const UNSCORED_DETAIL: InterviewDetailResponse = {
  session_id: 'cccccccc-cccc-cccc-cccc-cccccccccccc',
  candidate_email: 'ananya.reddy@example.com',
  candidate_name: 'Ananya Reddy',
  candidate_preferred_language: 'te',
  job_title: 'Data Entry Operator',
  status: 'abandoned',
  language: 'te',
  started_at: '2026-05-30T09:30:00Z',
  completed_at: null,
  duration_seconds: null,
  scorecard: null,
};

const mockGetInterviewDetail = vi.fn();
const mockNavigate = vi.fn();

vi.mock('../api/admin', () => ({
  getInterviewDetail: (...args: unknown[]) => mockGetInterviewDetail(...args) as unknown,
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderDetail(sessionId: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/admin/interviews/${sessionId}`]}>
        <Routes>
          <Route path="/admin/interviews/:sessionId" element={<AdminInterviewDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('AdminInterviewDetail page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('scored path', () => {
    beforeEach(() => {
      mockGetInterviewDetail.mockResolvedValue(SCORED_DETAIL);
    });

    it('renders candidate name as page heading', async () => {
      renderDetail('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');
      await waitFor(() => {
        expect(
          screen.getByRole('heading', { name: /priya sharma/i }),
        ).toBeInTheDocument();
      });
    });

    it('renders the composite score value', async () => {
      renderDetail('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');
      // The redesigned UI shows the composite score as "7.85 / 10" below the
      // ScoreRing, so match with a regex rather than exact text equality.
      await waitFor(() => {
        expect(screen.getByText(/7\.85/)).toBeInTheDocument();
      });
    });

    it('renders session metadata — role and language', async () => {
      renderDetail('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');
      await waitFor(() => {
        // The redesigned UI renders job_title in two places: the candidate
        // header subtitle and the "Role" MetaItem card, so use getAllByText.
        expect(screen.getAllByText('Junior Java Developer').length).toBeGreaterThan(0);
        expect(screen.getAllByText('English').length).toBeGreaterThan(0);
      });
    });

    it('renders strengths list', async () => {
      renderDetail('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');
      await waitFor(() => {
        expect(screen.getByText('Articulate communicator')).toBeInTheDocument();
        expect(screen.getByText('Strong Java OOP fundamentals')).toBeInTheDocument();
      });
    });

    it('renders improvements list', async () => {
      renderDetail('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');
      await waitFor(() => {
        expect(screen.getByText(/Improve concurrency knowledge/)).toBeInTheDocument();
      });
    });

    it('renders the summary text', async () => {
      renderDetail('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');
      await waitFor(() => {
        expect(screen.getByText('A strong junior candidate.')).toBeInTheDocument();
      });
    });

    it('renders the status badge', async () => {
      renderDetail('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');
      await waitFor(() => {
        // Multiple elements may display "Completed" (badge + MetaRow label)
        expect(screen.getAllByText('Completed').length).toBeGreaterThan(0);
      });
    });
  });

  describe('unscored path (scorecard: null)', () => {
    beforeEach(() => {
      mockGetInterviewDetail.mockResolvedValue(UNSCORED_DETAIL);
    });

    it('renders without crashing and shows candidate name', async () => {
      renderDetail('cccccccc-cccc-cccc-cccc-cccccccccccc');
      await waitFor(() => {
        expect(
          screen.getByRole('heading', { name: /ananya reddy/i }),
        ).toBeInTheDocument();
      });
    });

    it('shows "No scorecard yet" message when scorecard is null', async () => {
      renderDetail('cccccccc-cccc-cccc-cccc-cccccccccccc');
      await waitFor(() => {
        expect(screen.getByText(/no scorecard yet/i)).toBeInTheDocument();
      });
    });

    it('does not render the composite score section', async () => {
      renderDetail('cccccccc-cccc-cccc-cccc-cccccccccccc');
      await waitFor(() => {
        expect(screen.getByText(/no scorecard yet/i)).toBeInTheDocument();
      });
      // "Overall Score" heading is only rendered inside the scored branch
      expect(screen.queryByText(/overall score/i)).not.toBeInTheDocument();
    });

    it('renders the Abandoned status badge', async () => {
      renderDetail('cccccccc-cccc-cccc-cccc-cccccccccccc');
      await waitFor(() => {
        expect(screen.getByText('Abandoned')).toBeInTheDocument();
      });
    });
  });

  describe('error path', () => {
    it('renders error state when getInterviewDetail rejects', async () => {
      mockGetInterviewDetail.mockRejectedValue(new Error('Not found'));
      renderDetail('nonexistent-session');
      await waitFor(() => {
        expect(screen.getByRole('alert')).toBeInTheDocument();
      });
      expect(screen.getByText(/interview not found/i)).toBeInTheDocument();
    });
  });
});
