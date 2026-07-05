// Tests for AdminOverview page
// Covers: KPI tiles render from mock data, loading skeleton, error state.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import AdminOverview from '../pages/admin/AdminOverview';
import type {
  OverviewResponse,
  TrendsResponse,
  ScoreDistributionResponse,
  SystemHealthResponse,
} from '../api/admin';

// ---------------------------------------------------------------------------
// Mock admin API
// ---------------------------------------------------------------------------

const MOCK_OVERVIEW: OverviewResponse = {
  total_candidates: 142,
  total_interviews: 217,
  completed_interviews: 183,
  completion_rate: 0.8433,
  avg_composite_score: 6.74,
  avg_duration_seconds: 682.1,
  interviews_today: 4,
  interviews_last_7d: 31,
  interviews_last_30d: 98,
};

const MOCK_TRENDS: TrendsResponse = {
  items: [
    { date: '2026-05-30', interview_count: 5, avg_composite: 6.5 },
    { date: '2026-05-31', interview_count: 3, avg_composite: 7.1 },
    { date: '2026-06-01', interview_count: 6, avg_composite: 6.8 },
  ],
  date_from: '2026-05-04',
  date_to: '2026-06-02',
};

const MOCK_DIST: ScoreDistributionResponse = {
  buckets: [
    { label: '0-2', count: 4 },
    { label: '2-4', count: 11 },
    { label: '4-6', count: 38 },
    { label: '6-8', count: 97 },
    { label: '8-10', count: 33 },
  ],
  avg_communication: 7.02,
  avg_technical: 6.48,
  avg_problem_solving: 6.75,
  avg_confidence: 6.93,
};

const MOCK_SYSTEM_HEALTH: SystemHealthResponse = {
  overall: 'operational',
  services: [
    { name: 'admin_ops', kind: 'service', status: 'operational', latency_ms: 0, detail: null },
    { name: 'interview_core', kind: 'service', status: 'operational', latency_ms: 24, detail: null },
    { name: 'data_gateway', kind: 'service', status: 'operational', latency_ms: 18, detail: null },
    { name: 'feedback_billing', kind: 'service', status: 'operational', latency_ms: 21, detail: null },
    { name: 'postgres', kind: 'datastore', status: 'operational', latency_ms: null, detail: null },
    { name: 'redis', kind: 'datastore', status: 'operational', latency_ms: null, detail: null },
  ],
  checked_at: '2026-06-25T00:00:00.000Z',
};

const mockGetOverview = vi.fn().mockResolvedValue(MOCK_OVERVIEW);
const mockGetTrends = vi.fn().mockResolvedValue(MOCK_TRENDS);
const mockGetScoreDist = vi.fn().mockResolvedValue(MOCK_DIST);
const mockGetSystemHealth = vi.fn().mockResolvedValue(MOCK_SYSTEM_HEALTH);

vi.mock('../api/admin', () => ({
  getOverview: (...args: unknown[]) => mockGetOverview(...args) as unknown,
  getTrends: (...args: unknown[]) => mockGetTrends(...args) as unknown,
  getScoreDistribution: (...args: unknown[]) => mockGetScoreDist(...args) as unknown,
  getSystemHealth: (...args: unknown[]) => mockGetSystemHealth(...args) as unknown,
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderOverview() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AdminOverview />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('AdminOverview page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetOverview.mockResolvedValue(MOCK_OVERVIEW);
    mockGetTrends.mockResolvedValue(MOCK_TRENDS);
    mockGetScoreDist.mockResolvedValue(MOCK_DIST);
    mockGetSystemHealth.mockResolvedValue(MOCK_SYSTEM_HEALTH);
  });

  // The aurora redesign changed the page h1 from "Platform Overview" to
  // "Admin Overview". The intent is still "verify the page heading renders".
  it('renders the page heading', async () => {
    renderOverview();
    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: /admin overview/i }),
      ).toBeInTheDocument();
    });
  });

  it('shows loading skeleton initially', () => {
    // Keep overview pending
    mockGetOverview.mockImplementation(() => new Promise(() => undefined));
    renderOverview();
    // Data tile container should be present but skeletons shown
    expect(screen.getByTestId('overview-tiles')).toBeInTheDocument();
  });

  it('renders total-candidates tile value after load', async () => {
    renderOverview();
    await waitFor(() => {
      expect(screen.getByText('142')).toBeInTheDocument();
    });
  });

  it('renders total-interviews tile value after load', async () => {
    renderOverview();
    await waitFor(() => {
      expect(screen.getByText('217')).toBeInTheDocument();
    });
  });

  it('renders completed-interviews tile value after load', async () => {
    renderOverview();
    await waitFor(() => {
      expect(screen.getByText('183')).toBeInTheDocument();
    });
  });

  it('renders completion-rate sub-text after load', async () => {
    renderOverview();
    // 0.8433 → Math.round(0.8433 * 100) = 84%
    await waitFor(() => {
      expect(screen.getByText(/84% completion rate/i)).toBeInTheDocument();
    });
  });

  it('renders avg composite score formatted to 2 dp', async () => {
    renderOverview();
    // 6.74 → "6.74"
    await waitFor(() => {
      expect(screen.getByText('6.74')).toBeInTheDocument();
    });
  });

  it('renders activity tile with today label', async () => {
    renderOverview();
    await waitFor(() => {
      // The "Activity" tile has a sub-text containing "today"
      expect(screen.getByText(/today/i)).toBeInTheDocument();
    });
  });

  it('renders 7d and 30d sub-text', async () => {
    renderOverview();
    await waitFor(() => {
      expect(screen.getByText(/31 last 7d/i)).toBeInTheDocument();
      expect(screen.getByText(/98 last 30d/i)).toBeInTheDocument();
    });
  });

  it('renders trend chart section heading', async () => {
    renderOverview();
    await waitFor(() => {
      expect(screen.getByText(/daily interview volume/i)).toBeInTheDocument();
    });
  });

  it('renders score distribution section heading', async () => {
    renderOverview();
    await waitFor(() => {
      expect(screen.getByText(/score distribution/i)).toBeInTheDocument();
    });
  });

  it('shows error state when getOverview rejects', async () => {
    mockGetOverview.mockRejectedValue(new Error('DB connection failed'));
    renderOverview();
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
    expect(screen.getByText(/failed to load overview/i)).toBeInTheDocument();
  });
});
