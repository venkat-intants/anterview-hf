// OutcomeSignalsSection — PH5-E4. "Interview scores and later outcomes
// (signals)": the banner, band rows (never the "All" row), the cohort
// toggle, and the drill-down carrying `score_band`/`interviewer_score`/
// `evidence_href`.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type {
  MembersResponse,
  MetricDefinitionsResponse,
  OutcomeSignalsResponse,
} from '../api/metrics';

const getOutcomeSignals = vi.fn();
const getAnalyticsMembers = vi.fn();
vi.mock('../api/metrics', async () => {
  const actual = await vi.importActual<typeof import('../api/metrics')>('../api/metrics');
  return {
    ...actual,
    getOutcomeSignals: (...a: unknown[]) => getOutcomeSignals(...a) as unknown,
    getAnalyticsMembers: (...a: unknown[]) => getAnalyticsMembers(...a) as unknown,
  };
});

import OutcomeSignalsSection from '../components/hr/analytics/OutcomeSignalsSection';

const DEFINITIONS: MetricDefinitionsResponse = {
  registry_hash: 'abc123def4567890',
  flags: [],
  measures: [],
  dimensions: [],
  metrics: [
    {
      name: 'hires',
      version: 1,
      label: 'Hires',
      kind: 'count',
      effective_from: '2026-09-22',
      current: true,
      description: 'People hired in this period.',
      formula: 'count(hired)',
      cohort_bases: ['hire'],
      dimensions: ['source', 'requisition'],
      numerator: null,
      denominator: null,
      measure: null,
      buckets: null,
      change_note: null,
      drillable: { numerator: true, denominator: true },
    },
    {
      name: 'checkin_coverage',
      version: 1,
      label: 'Check-in coverage',
      kind: 'rate',
      effective_from: '2026-09-22',
      current: true,
      description: 'Share of due check-ins recorded.',
      formula: 'count(checkin_recorded) / count(checkin_due)',
      cohort_bases: ['hire'],
      dimensions: [],
      numerator: ['checkin_recorded@1'],
      denominator: ['checkin_due@1'],
      measure: null,
      buckets: null,
      change_note: null,
      drillable: { numerator: true, denominator: true },
    },
    {
      name: 'retention_90d',
      version: 1,
      label: 'Retention (90 day)',
      kind: 'rate',
      effective_from: '2026-09-22',
      current: true,
      description: 'Share still employed at 90 days.',
      formula: 'count(retained_90d) / count(checkin_recorded)',
      cohort_bases: ['hire'],
      dimensions: [],
      numerator: ['retained_90d@1'],
      denominator: ['checkin_recorded@1'],
      measure: null,
      buckets: null,
      change_note: null,
      drillable: { numerator: false, denominator: true },
    },
    {
      name: 'performance_90d',
      version: 1,
      label: 'Performance mix (90 day)',
      kind: 'distribution',
      effective_from: '2026-09-22',
      current: true,
      description: 'Below/meets/exceeds among recorded check-ins.',
      formula: 'distribution(perf_*) within checkin_recorded',
      cohort_bases: ['hire'],
      dimensions: [],
      numerator: null,
      denominator: null,
      measure: null,
      buckets: ['below', 'meets', 'exceeds'],
      change_note: null,
      drillable: { numerator: false, denominator: false },
    },
    {
      name: 'hire_interviewer_score',
      version: 1,
      label: 'Interviewer score',
      kind: 'mean',
      effective_from: '2026-09-22',
      current: true,
      description: 'Mean human interviewer score for the hire.',
      formula: 'mean(hire_interviewer_score)',
      cohort_bases: ['hire'],
      dimensions: [],
      numerator: null,
      denominator: null,
      measure: 'hire_interviewer_score@1',
      buckets: null,
      change_note: null,
      drillable: { numerator: false, denominator: false },
    },
    {
      name: 'application_to_hire',
      version: 1,
      label: 'Application → hire',
      kind: 'rate',
      effective_from: '2026-09-22',
      current: true,
      description: 'Share of decided applications that were hired.',
      formula: 'count(applied AND hired) / count(applied)',
      cohort_bases: ['application', 'decision'],
      dimensions: ['source', 'requisition', 'interviewer_score_band'],
      numerator: ['applied@1', 'hired@1'],
      denominator: ['applied@1'],
      measure: null,
      buckets: null,
      change_note: null,
      drillable: { numerator: true, denominator: true },
    },
  ],
};

function outcomeSignals(over: Partial<OutcomeSignalsResponse> = {}): OutcomeSignalsResponse {
  return {
    registry_hash: 'abc123def4567890',
    cohort: { basis: 'hire', from: '2025-09-22', to: '2026-09-22' },
    filters: { requisition_id: null, source: null },
    signal: {
      dimension: 'interviewer_score_band',
      version: 1,
      measure: 'hire_interviewer_score@1',
      bands: [
        { key: 'below_3', label: 'Below 3' },
        { key: '3_to_4', label: '3 to under 4' },
        { key: '4_plus', label: '4 and above' },
        { key: 'none', label: 'No human scorecard' },
      ],
    },
    groups: [
      { key: null, label: 'All', metrics: {} },
      {
        key: '4_plus',
        label: '4 and above',
        metrics: {
          hires: { metric: 'hires', version: 1, kind: 'count', value: 9 },
          checkin_coverage: {
            metric: 'checkin_coverage',
            version: 1,
            kind: 'rate',
            value: 87.5,
            numerator: 7,
            denominator: 8,
            suppressed: false,
          },
          retention_90d: {
            metric: 'retention_90d',
            version: 1,
            kind: 'rate',
            value: 85.7,
            numerator: 6,
            denominator: 7,
            suppressed: false,
          },
          performance_90d: {
            metric: 'performance_90d',
            version: 1,
            kind: 'distribution',
            value: null,
            n: 3,
            suppressed: true,
          },
          hire_interviewer_score: {
            metric: 'hire_interviewer_score',
            version: 1,
            kind: 'mean',
            value: 4.3,
            n: 9,
            suppressed: false,
          },
        },
      },
    ],
    guardrails: {
      evaluation_signal: 'human interviewer scorecards only',
      min_group: 5,
      changes_candidate_status: false,
    },
    ...over,
  };
}

function membersFixture(over: Partial<MembersResponse> = {}): MembersResponse {
  return {
    metric: 'hires',
    version: 1,
    part: 'numerator',
    total: 1,
    truncated: false,
    rows: [
      {
        enrolment_id: 'en-1',
        applicant_id: 'ap-1',
        candidate_name: 'Asha Rao',
        requisition_id: 'req-1',
        requisition_title: 'Backend Engineer',
        source: 'referral',
        applied_at: '2026-06-01T00:00:00.000Z',
        interviewer_score: 4.3,
        evidence_href: '/hr/enrolments/en-1/evidence',
      },
    ],
    ...over,
  };
}

function renderSection(onOpenCandidate = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <OutcomeSignalsSection
          filters={{}}
          definitions={DEFINITIONS}
          onOpenCandidate={onOpenCandidate}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getOutcomeSignals.mockResolvedValue(outcomeSignals());
  getAnalyticsMembers.mockResolvedValue(membersFixture());
});

describe('OutcomeSignalsSection — banner and title', () => {
  it('titles the section and states the DPDP-safe banner', async () => {
    renderSection();
    expect(
      await screen.findByText('Interview scores and later outcomes (signals)'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Signals about past hires. They never change any candidate's status or scores. Only human interviewer scores are used; AI interview scores are deleted after 90 days and are never compared with outcomes.",
      ),
    ).toBeInTheDocument();
  });
});

describe('OutcomeSignalsSection — rows are bands, never "All"', () => {
  it('shows the band row and never a row for the overall "All" group', async () => {
    renderSection();
    expect(await screen.findByText('4 and above')).toBeInTheDocument();
    expect(screen.queryByRole('cell', { name: 'All' })).not.toBeInTheDocument();
  });

  it('renders "too few to compare" for a suppressed distribution', async () => {
    renderSection();
    await screen.findByText('4 and above');
    expect(screen.getByText('too few to compare')).toBeInTheDocument();
  });
});

describe('OutcomeSignalsSection — cohort toggle', () => {
  it('refetches with cohort=decision on toggle', async () => {
    getOutcomeSignals.mockImplementation((opts: { cohort?: string } = {}) =>
      Promise.resolve(
        outcomeSignals({
          cohort: {
            basis: (opts.cohort as 'hire' | 'decision') ?? 'hire',
            from: '2025-09-22',
            to: '2026-09-22',
          },
        }),
      ),
    );
    const user = userEvent.setup();
    renderSection();
    await screen.findByText('4 and above');

    await user.click(screen.getByRole('tab', { name: 'Decision date' }));

    expect(getOutcomeSignals).toHaveBeenLastCalledWith(
      expect.objectContaining({ cohort: 'decision' }),
    );
  });
});

describe('OutcomeSignalsSection — drill-down', () => {
  it('drills into a band cell with score_band, and the row carries the interviewer score and an evidence-trail link', async () => {
    const user = userEvent.setup();
    renderSection();

    const hiresCount = await screen.findByText('9');
    await user.click(hiresCount);

    expect(getAnalyticsMembers).toHaveBeenCalledWith(
      expect.objectContaining({ metric: 'hires', part: 'numerator', score_band: '4_plus' }),
    );
    expect(await screen.findByText('Asha Rao')).toBeInTheDocument();
    expect(screen.getByText('Interviewer score 4.3')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Evidence trail' })).toHaveAttribute(
      'href',
      '/hr/enrolments/en-1/evidence',
    );
  });

  it('opens the candidate drawer callback for a drill-down row', async () => {
    const onOpenCandidate = vi.fn();
    const user = userEvent.setup();
    renderSection(onOpenCandidate);

    await user.click(await screen.findByText('9'));
    await user.click(await screen.findByText('Asha Rao'));

    expect(onOpenCandidate).toHaveBeenCalledWith({ applicant_id: 'ap-1', enrolment_id: 'en-1' });
  });
});
