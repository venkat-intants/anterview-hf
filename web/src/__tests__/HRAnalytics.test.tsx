// Tests for the embeddable HR analytics PANEL (FE-2) — the default export used
// by HRPipeline, still backed by the unchanged `GET /hr/analytics`
// (application_progress). PH5 wave 1 does not touch this data path; see
// HRAnalyticsPage.test.tsx for the new governed-metrics standalone screen.
//
// The funnel bars are the only chart on this page backed by a real API; the
// other three are declared empty until their endpoints ship. That distinction
// is what these tests protect — an empty state that quietly turns into a chart
// of zeros would read as "we screened nobody" rather than "no data yet".

import { analyticsDefaults } from './analyticsFixture';
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
  ...analyticsDefaults(),
  // Governed conversion — deliberately NOT derivable from `funnel` above by
  // simple division (e.g. shortlisted/applied would read 50.0% too, but
  // that coincidence is not what the assertions rely on: the panel reads
  // these fields verbatim, it does not compute them).
  conversion: {
    applied: 40,
    ever_shortlisted: 20,
    ever_sat_exam: 16,
    ever_interviewed: 10,
    ever_hired: 5,
    pct_shortlisted: 50.0,
    pct_sat_exam: 40.0,
    pct_interviewed: 25.0,
    pct_hired: 12.5,
  },
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
  ...analyticsDefaults(),
};

const getHrAnalytics = vi.fn();
vi.mock('../api/pipeline', () => ({
  getHrAnalytics: (...a: unknown[]) => getHrAnalytics(...a) as unknown,
}));

import HRAnalytics from '../pages/hr/HRAnalytics';

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

  it('renders the governed conversion rates as sent, never a client-computed one', async () => {
    renderWith(<HRAnalytics />);

    await screen.findByText('40');
    // FULL's conversion block (below) sets these explicitly; asserting the
    // exact figures pins that the subtitle reads them as-is rather than
    // recomputing anything from `funnel`.
    expect(screen.getByText('50.0%')).toBeInTheDocument(); // shortlist
    expect(screen.getByText('40.0%')).toBeInTheDocument(); // sat exam
    expect(screen.getByText('25.0%')).toBeInTheDocument(); // interviewed
    expect(screen.getByText('12.5%')).toBeInTheDocument(); // hire
  });

  it('shows the governed hire rate even when funnel counts would divide to over 100%', async () => {
    // hired (5) > interview_completed (2): the old client math (hired ÷
    // interviews) would have read 250%. The governed conversion.pct_hired
    // — computed over ALL applications, not the funnel's interview count —
    // is unaffected, because nothing here divides the funnel's own numbers.
    getHrAnalytics.mockResolvedValue({
      ...FULL,
      funnel: { ...FULL.funnel, hired: 5, interview_completed: 2 },
    });
    renderWith(<HRAnalytics />);

    await screen.findByText('40');
    expect(screen.queryByText(/250%/)).not.toBeInTheDocument();
    expect(screen.getByText('12.5%')).toBeInTheDocument();
  });

  it('shows a dash and "not enough data yet" for a null governed rate, never a computed fallback', async () => {
    getHrAnalytics.mockResolvedValue({
      ...FULL,
      conversion: {
        applied: 0,
        ever_shortlisted: 0,
        ever_sat_exam: 0,
        ever_interviewed: 0,
        ever_hired: 0,
        pct_shortlisted: null,
        pct_sat_exam: null,
        pct_interviewed: null,
        pct_hired: null,
      },
    });
    renderWith(<HRAnalytics />);

    await screen.findByText('40');
    expect(screen.getAllByText('—')).toHaveLength(4);
    expect(screen.getAllByText('(not enough data yet)')).toHaveLength(4);
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
      ...analyticsDefaults(),
    });
    renderWith(<HRAnalytics />);

    expect(await screen.findByText(/avg ATS score/i)).toBeInTheDocument();
    expect(screen.queryByText(/avg exam score/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/avg interview score/i)).not.toBeInTheDocument();
  });
});
