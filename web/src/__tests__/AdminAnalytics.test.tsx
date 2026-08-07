// Tests for the platform analytics page (FE-2).
//
// The `admin` role's console. Every panel here is a chart, and recharts renders
// nothing measurable in jsdom (it sizes off a ResponsiveContainer that has no
// layout), so these assert what surrounds the charts: the page mounts, each
// panel resolves to data / empty / error rather than a blank card, and a failed
// fetch is reported instead of being swallowed into an empty chart that reads
// as "no interviews happened".

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type {
  ByRoleItem,
  ByLanguageItem,
  ScoreDistributionResponse,
} from '../api/admin';

const BY_ROLE: ByRoleItem[] = [
  {
    job_id: 'j-1',
    job_title: 'Backend Engineer',
    interview_count: 42,
    avg_composite: 7.1,
    avg_communication: 7.4,
    avg_technical: 6.8,
    avg_problem_solving: 7.0,
    avg_confidence: 7.2,
  },
];

const BY_LANG: ByLanguageItem[] = [
  { language: 'en', interview_count: 30, avg_composite: 7.2 },
  { language: 'hi', interview_count: 12, avg_composite: 6.9 },
];

const DIST: ScoreDistributionResponse = {
  buckets: [
    { label: '0-2', count: 1 },
    { label: '8-10', count: 9 },
  ],
  avg_communication: 7.4,
  avg_technical: 6.8,
  avg_problem_solving: 7.02,
  avg_confidence: 7.2,
};

const getByRole = vi.fn();
const getByLanguage = vi.fn();
const getScoreDistribution = vi.fn();
vi.mock('../api/admin', () => ({
  getByRole: (...a: unknown[]) => getByRole(...a) as unknown,
  getByLanguage: (...a: unknown[]) => getByLanguage(...a) as unknown,
  getScoreDistribution: (...a: unknown[]) => getScoreDistribution(...a) as unknown,
}));

const toastError = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  },
}));

import AdminAnalytics from '../pages/admin/AdminAnalytics';

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AdminAnalytics />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getByRole.mockResolvedValue(BY_ROLE);
  getByLanguage.mockResolvedValue(BY_LANG);
  getScoreDistribution.mockResolvedValue(DIST);
});

describe('AdminAnalytics', () => {
  it('renders the page and every panel heading', async () => {
    renderPage();

    expect(screen.getByRole('heading', { name: /^analytics$/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /interviews by role/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /language mix/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /score distribution/i })).toBeInTheDocument();
    await waitFor(() => expect(getByRole).toHaveBeenCalled());
  });

  it('fetches all three aggregates exactly once', async () => {
    renderPage();

    await waitFor(() => expect(getScoreDistribution).toHaveBeenCalled());
    expect(getByRole).toHaveBeenCalledTimes(1);
    expect(getByLanguage).toHaveBeenCalledTimes(1);
    expect(getScoreDistribution).toHaveBeenCalledTimes(1);
  });

  it('says there is no data rather than drawing an empty chart', async () => {
    getByRole.mockResolvedValue([]);
    getByLanguage.mockResolvedValue([]);
    getScoreDistribution.mockResolvedValue({ ...DIST, buckets: [] });
    renderPage();

    await waitFor(() =>
      expect(screen.getAllByText(/no data available yet/i).length).toBeGreaterThan(0),
    );
  });

  it('reports a failed aggregate instead of showing it as zero interviews', async () => {
    // A silently-empty chart here reads as "nobody interviewed this month",
    // which is the wrong conclusion to hand an operator.
    getByRole.mockRejectedValue(new Error('analytics warehouse unavailable'));
    renderPage();

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('analytics warehouse unavailable'),
    );
  });
});
