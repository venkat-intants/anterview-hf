// Tests for the HR analytics panel and its standalone page (FE-2).
//
// The funnel bars are the only chart on this page backed by a real API; the
// other three are declared empty until their endpoints ship. That distinction
// is what these tests protect — an empty state that quietly turns into a chart
// of zeros would read as "we screened nobody" rather than "no data yet".

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { HrAnalytics as HrAnalyticsData } from '../api/pipeline';

const FULL: HrAnalyticsData = {
  funnel: {
    total_applicants: 40,
    shortlisted: 20,
    exam_taken: 16,
    exam_passed: 8,
    interview_completed: 4,
    interview_invited: 6,
    hired: 2,
    rejected: 10,
  },
  averages: { avg_ats: 66.4, avg_exam_percent: 71.8, avg_interview_composite: 7.42 },
};

const EMPTY: HrAnalyticsData = {
  funnel: {
    total_applicants: 0,
    shortlisted: 0,
    exam_taken: 0,
    exam_passed: 0,
    interview_completed: 0,
    interview_invited: 0,
    hired: 0,
    rejected: 0,
  },
  averages: { avg_ats: null, avg_exam_percent: null, avg_interview_composite: null },
};

const getHrAnalytics = vi.fn();
vi.mock('../api/pipeline', () => ({
  getHrAnalytics: (...a: unknown[]) => getHrAnalytics(...a) as unknown,
}));

import HRAnalytics, { HRAnalyticsPage } from '../pages/hr/HRAnalytics';

function renderWith(node: JSX.Element) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  getHrAnalytics.mockResolvedValue(FULL);
});

describe('HRAnalytics — funnel', () => {
  it('draws one bar per stage with the live counts', async () => {
    renderWith(<HRAnalytics />);

    expect(await screen.findByText('40')).toBeInTheDocument();
    for (const label of ['Applied', 'Shortlisted', 'Exam passed', 'Interviewed', 'Hired']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it('computes each conversion rate against the right denominator', async () => {
    renderWith(<HRAnalytics />);

    await screen.findByText('40');
    // shortlist 20/40, pass 8/16, hire 2/4 — the pass rate is over exams TAKEN,
    // not over all applicants, which is the mistake worth pinning.
    expect(screen.getAllByText('50%')).toHaveLength(3);
  });

  it('says there is no pipeline data rather than drawing a chart of zeros', async () => {
    getHrAnalytics.mockResolvedValue(EMPTY);
    renderWith(<HRAnalytics />);

    expect(await screen.findByText(/no pipeline data yet/i)).toBeInTheDocument();
  });

  it('declares the not-yet-backed charts empty instead of faking them', async () => {
    renderWith(<HRAnalytics />);

    expect(await screen.findByText(/no language data yet/i)).toBeInTheDocument();
    expect(screen.getByText(/no score data yet/i)).toBeInTheDocument();
    expect(screen.getByText(/no trend data yet/i)).toBeInTheDocument();
  });
});

describe('HRAnalytics — averages summary', () => {
  it('rounds the percentage averages and keeps the interview score to 1dp', async () => {
    renderWith(<HRAnalytics />);

    expect(await screen.findByText('66')).toBeInTheDocument(); // 66.4 ATS
    expect(screen.getByText('72%')).toBeInTheDocument(); // 71.8 exam
    expect(screen.getByText('7.4/10')).toBeInTheDocument(); // 7.42 interview
  });

  it('omits an average that the server reported as null', async () => {
    getHrAnalytics.mockResolvedValue({
      ...FULL,
      averages: { avg_ats: 66.4, avg_exam_percent: null, avg_interview_composite: null },
    });
    renderWith(<HRAnalytics />);

    expect(await screen.findByText(/avg ATS score/i)).toBeInTheDocument();
    expect(screen.queryByText(/avg exam score/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/avg interview score/i)).not.toBeInTheDocument();
  });
});

describe('HRAnalyticsPage — standalone route', () => {
  it('wraps the same panel in a titled page', async () => {
    renderWith(<HRAnalyticsPage />);

    expect(screen.getByRole('heading', { name: /^analytics$/i })).toBeInTheDocument();
    // Same panel, one fetch — the page is a shell, not a second data path.
    expect(await screen.findByText('Hiring funnel')).toBeInTheDocument();
    expect(getHrAnalytics).toHaveBeenCalledTimes(1);
  });
});
