// Tests for the PH5 wave-1 governed-metrics screen (HRAnalyticsPage,
// `/hr/analytics`). Every number here is asserted to come straight from the
// mocked server response — the component never derives, sums or re-scales a
// metric client-side, so these fixtures double as a check that no such
// derivation crept in (a rate literally cannot read over 100% here, because
// the mock — standing in for the server — is the only thing that supplies it).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type {
  FunnelGroup,
  FunnelResponse,
  MembersResponse,
  MetricDefinitionsResponse,
} from '../api/metrics';

// ── Mocks ─────────────────────────────────────────────────────────────────────

const getMetricDefinitions = vi.fn();
const getAnalyticsFunnel = vi.fn();
const getAnalyticsMembers = vi.fn();
vi.mock('../api/metrics', async () => {
  const actual = await vi.importActual<typeof import('../api/metrics')>('../api/metrics');
  return {
    ...actual,
    getMetricDefinitions: (...a: unknown[]) => getMetricDefinitions(...a) as unknown,
    getAnalyticsFunnel: (...a: unknown[]) => getAnalyticsFunnel(...a) as unknown,
    getAnalyticsMembers: (...a: unknown[]) => getAnalyticsMembers(...a) as unknown,
  };
});

const listRequisitions = vi.fn();
vi.mock('../api/requisitions', async () => {
  const actual = await vi.importActual<typeof import('../api/requisitions')>('../api/requisitions');
  return {
    ...actual,
    listRequisitions: (...a: unknown[]) => listRequisitions(...a) as unknown,
  };
});

// PH5 wave-1 follow-up (A2) — the "Check-ins due" card inside Quality of hire.
// getEnrolmentCheckins/createCheckin/correctCheckin belong to CheckinSection,
// mounted only once a due row is opened; stubbed here purely so an accidental
// mount never hits the network-call guard (its own behaviour is covered in
// CheckinSection.test.tsx).
const listCheckinsDue = vi.fn();
vi.mock('../api/checkins', () => ({
  listCheckinsDue: (...a: unknown[]) => listCheckinsDue(...a) as unknown,
  getEnrolmentCheckins: () =>
    Promise.resolve({
      checkins: [],
      notice: '',
      window: { start: null, employed_from: null, closes_at: null, open: true },
    }),
  createCheckin: vi.fn(),
  correctCheckin: vi.fn(),
}));

const { mockUseAuth } = vi.hoisted(() => ({ mockUseAuth: vi.fn() }));
vi.mock('../context/AuthContext', () => ({
  useAuth: () => mockUseAuth() as unknown,
}));

vi.mock('../components/CandidateDrawer', () => ({
  default: ({ applicantId }: { applicantId: string | null }) =>
    applicantId ? <div role="dialog">{`drawer ${applicantId}`}</div> : null,
}));

// getHrAnalytics is only touched by the (unchanged) default embeddable panel,
// which this page never renders — mocked anyway so an accidental import never
// hits the real client.
vi.mock('../api/pipeline', () => ({
  getHrAnalytics: vi.fn(),
}));

import { HRAnalyticsPage } from '../pages/hr/HRAnalytics';

// ── Fixtures ──────────────────────────────────────────────────────────────────

const DEFINITIONS: MetricDefinitionsResponse = {
  registry_hash: 'abc123def4567890',
  flags: [
    { name: 'hired', version: 1, description: 'The application reached a hire that stands.' },
  ],
  measures: [{ name: 'days_to_hire', version: 1, description: 'Days from application to hire.' }],
  dimensions: [
    {
      name: 'source',
      version: 1,
      label: 'Source',
      description: 'Where the application came from.',
      values: [
        { key: 'internal', label: 'Internal (HR-added)' },
        { key: 'referral', label: 'Referral' },
        { key: 'unknown', label: 'Unknown / untracked' },
      ],
    },
    {
      name: 'requisition',
      version: 1,
      label: 'Opening',
      description: 'The job opening applied to.',
    },
  ],
  metrics: [
    {
      name: 'applications',
      version: 1,
      label: 'Applications',
      kind: 'count',
      effective_from: '2026-09-22',
      current: true,
      description: 'Every application in the cohort.',
      formula: 'count(applied)',
      cohort_bases: ['application', 'decision'],
      dimensions: ['source', 'requisition'],
      numerator: null,
      denominator: null,
      measure: null,
      buckets: null,
      change_note: null,
      drillable: { numerator: true, denominator: true },
    },
    {
      name: 'application_to_hire',
      version: 1,
      label: 'Application → hire',
      kind: 'rate',
      effective_from: '2026-09-22',
      current: true,
      description: 'Share of applications that were hired.',
      formula: 'count(applied AND hired) / count(applied)',
      cohort_bases: ['application', 'decision'],
      dimensions: ['source', 'requisition'],
      numerator: ['applied@1', 'hired@1'],
      denominator: ['applied@1'],
      measure: null,
      buckets: null,
      change_note: 'Now counts only hires that stand.',
      drillable: { numerator: true, denominator: true },
    },
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
      name: 'time_to_hire_days',
      version: 1,
      label: 'Time to hire',
      kind: 'median',
      effective_from: '2026-09-22',
      current: true,
      description: 'Median days from application to hire.',
      formula: 'median(days_to_hire)',
      cohort_bases: ['hire'],
      dimensions: [],
      numerator: null,
      denominator: null,
      measure: 'days_to_hire@1',
      buckets: null,
      change_note: null,
      drillable: { numerator: false, denominator: false },
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
      description: 'Share of recorded check-ins still employed at 90 days.',
      formula: 'count(retained_90d) / count(checkin_recorded)',
      cohort_bases: ['hire'],
      dimensions: [],
      numerator: ['retained_90d@1'],
      denominator: ['checkin_recorded@1'],
      measure: null,
      buckets: null,
      change_note: null,
      // Aggregate-only (security review) — who "retained" is never a named
      // list; the denominator (checkin_recorded population) stays clickable.
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
  ],
};

function pipelineGroup(overrides: Partial<FunnelGroup> = {}): FunnelGroup {
  return {
    key: null,
    label: 'All',
    in_progress: 7,
    metrics: {
      applications: { metric: 'applications', version: 1, kind: 'count', value: 40 },
      application_to_hire: {
        metric: 'application_to_hire',
        version: 1,
        kind: 'rate',
        value: 12.5,
        numerator: 5,
        denominator: 40,
        suppressed: false,
      },
    },
    ...overrides,
  };
}

function pipelineFunnel(overrides: Partial<FunnelResponse> = {}): FunnelResponse {
  return {
    registry_hash: DEFINITIONS.registry_hash,
    cohort: { basis: 'application', from: null, to: null },
    filters: { requisition_id: null, source: null },
    group_by: null,
    groups: [pipelineGroup()],
    ...overrides,
  };
}

function hireGroup(overrides: Partial<FunnelGroup> = {}): FunnelGroup {
  return {
    key: null,
    label: 'All',
    in_progress: 0,
    metrics: {
      hires: { metric: 'hires', version: 1, kind: 'count', value: 37 },
      time_to_hire_days: {
        metric: 'time_to_hire_days',
        version: 1,
        kind: 'median',
        value: 21,
        n: 37,
        suppressed: false,
      },
      checkin_coverage: {
        metric: 'checkin_coverage',
        version: 1,
        kind: 'rate',
        value: 80.0,
        numerator: 4,
        denominator: 5,
        suppressed: false,
      },
      // Suppressed check-in outcomes — per the contract, value/numerator go
      // null but denominator/n are always present.
      // Denominator deliberately distinct from checkin_coverage's (4 of 5) so
      // tests can tell the two rates' buttons apart unambiguously.
      retention_90d: {
        metric: 'retention_90d',
        version: 1,
        kind: 'rate',
        value: null,
        numerator: null,
        denominator: 6,
        suppressed: true,
      },
      performance_90d: {
        metric: 'performance_90d',
        version: 1,
        kind: 'distribution',
        value: null,
        n: 4,
        suppressed: true,
      },
      hire_interviewer_score: {
        metric: 'hire_interviewer_score',
        version: 1,
        kind: 'mean',
        value: 7.8,
        n: 37,
        suppressed: false,
      },
    },
    ...overrides,
  };
}

function hireFunnel(overrides: Partial<FunnelResponse> = {}): FunnelResponse {
  return {
    registry_hash: DEFINITIONS.registry_hash,
    cohort: { basis: 'hire', from: null, to: null },
    filters: { requisition_id: null, source: null },
    group_by: null,
    groups: [hireGroup()],
    ...overrides,
  };
}

function membersFixture(overrides: Partial<MembersResponse> = {}): MembersResponse {
  return {
    metric: 'hires',
    version: 1,
    part: 'numerator',
    total: 250,
    truncated: true,
    rows: [
      {
        enrolment_id: 'en-1',
        applicant_id: 'ap-1',
        candidate_name: 'Asha Rao',
        requisition_id: 'req-1',
        requisition_title: 'Backend Engineer',
        source: 'referral',
        applied_at: '2026-09-01T00:00:00.000Z',
      },
    ],
    ...overrides,
  };
}

// ── Harness ───────────────────────────────────────────────────────────────────

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <HRAnalyticsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function asHrManager() {
  mockUseAuth.mockReturnValue({ user: { user_id: 'u-1', roles: ['hr_manager'] } });
}

beforeEach(() => {
  vi.clearAllMocks();
  asHrManager();
  getMetricDefinitions.mockResolvedValue(DEFINITIONS);
  listRequisitions.mockResolvedValue([]);
  getAnalyticsFunnel.mockImplementation((opts: { cohort?: string } = {}) =>
    Promise.resolve(opts.cohort === 'hire' ? hireFunnel() : pipelineFunnel()),
  );
  getAnalyticsMembers.mockResolvedValue(membersFixture());
  listCheckinsDue.mockResolvedValue([]);
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('HRAnalyticsPage — the funnel', () => {
  it('renders a rate with its own numerator and denominator, never over 100%', async () => {
    renderPage();

    const pct = await screen.findByText('12.5%');
    expect(pct).toBeInTheDocument();
    // The percentage is exactly what the mock (standing in for the governed
    // server response) supplied — nothing here derives or rescales it, so it
    // structurally cannot exceed 100.
    expect(parseFloat(pct.textContent ?? '0')).toBeLessThanOrEqual(100);
    expect(screen.getAllByText('5').length).toBeGreaterThan(0);
    expect(screen.getAllByText('40').length).toBeGreaterThan(0);
  });

  it('says there are no applications rather than drawing an empty funnel', async () => {
    getAnalyticsFunnel.mockImplementation((opts: { cohort?: string } = {}) =>
      Promise.resolve(
        opts.cohort === 'hire'
          ? hireFunnel({
              groups: [
                hireGroup({
                  metrics: { hires: { metric: 'hires', version: 1, kind: 'count', value: 0 } },
                }),
              ],
            })
          : pipelineFunnel({
              groups: [
                pipelineGroup({
                  in_progress: 0,
                  metrics: {
                    applications: { metric: 'applications', version: 1, kind: 'count', value: 0 },
                  },
                }),
              ],
            }),
      ),
    );
    renderPage();

    expect(await screen.findByText('No applications in this period.')).toBeInTheDocument();
  });

  it('reports a failed funnel fetch instead of hiding it', async () => {
    getAnalyticsFunnel.mockImplementation((opts: { cohort?: string } = {}) =>
      opts.cohort === 'hire' ? Promise.resolve(hireFunnel()) : Promise.reject(new Error('boom')),
    );
    renderPage();

    expect(await screen.findByText('Could not load the funnel just now.')).toBeInTheDocument();
  });

  it('explains the funnel is application-scoped on the hire cohort, rather than claiming there are no applications when there are hires', async () => {
    // hireFunnel()'s default group has 37 hires — the pipeline metrics
    // (applications, screened, …) simply are not computed for this cohort,
    // which used to be misread as "0 applications".
    renderPage();
    await screen.findByText('12.5%');

    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: 'Hire' }));

    expect(
      await screen.findByText(
        'The funnel is measured over applications. Switch to the application or decision cohort to see it.',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText('No applications in this period.')).not.toBeInTheDocument();
  });
});

describe('HRAnalyticsPage — suppression', () => {
  it('shows "too few to compare" for a suppressed check-in outcome, with n visible', async () => {
    renderPage();

    expect(await screen.findByText('37')).toBeInTheDocument(); // hires count, from Quality of hire
    expect(screen.getAllByText('too few to compare').length).toBeGreaterThan(0);
    expect(screen.getByText('(n=4)')).toBeInTheDocument();
  });

  it('never renders a clickable numerator for a suppressed retention_90d (aggregate-only)', async () => {
    renderPage();
    await screen.findByText('37');

    // checkin_coverage stays fully clickable...
    expect(screen.getByRole('button', { name: '4' })).toBeInTheDocument();
    // ...but retention_90d's numerator (who "retained") is never a button —
    // it renders null as a plain em dash, never a named list.
    expect(screen.queryByRole('button', { name: '—' })).not.toBeInTheDocument();
    // A null value (check-in metric) never appears anywhere, tooltip included.
    expect(screen.queryByTitle(/%/)).not.toBeInTheDocument();
  });

  it('says "too few to compare" for a suppressed PIPELINE rate even though it comes back with a real value (policy change)', async () => {
    getAnalyticsFunnel.mockImplementation((opts: { cohort?: string } = {}) =>
      Promise.resolve(
        opts.cohort === 'hire'
          ? hireFunnel()
          : pipelineFunnel({
              groups: [
                pipelineGroup({
                  metrics: {
                    applications: { metric: 'applications', version: 1, kind: 'count', value: 40 },
                    // Suppressed, but — unlike a check-in metric — still
                    // carries its real value/numerator; the UI must not
                    // read `value !== null` as "not suppressed".
                    application_to_hire: {
                      metric: 'application_to_hire',
                      version: 1,
                      kind: 'rate',
                      value: 12.5,
                      numerator: 1,
                      denominator: 8,
                      suppressed: true,
                    },
                  },
                }),
              ],
            }),
      ),
    );
    renderPage();
    await screen.findByText('40');

    // Never the percentage, whatever the value says.
    expect(screen.queryByText('12.5%')).not.toBeInTheDocument();
    expect(screen.getAllByText('too few to compare').length).toBeGreaterThan(0);
    // The raw figure is still visible — numerator/denominator are unaffected
    // by suppression, only drillability is (governed separately by `drillable`).
    expect(screen.getByRole('button', { name: '1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '8' })).toBeInTheDocument();
    // Optional per the review: the withheld figure may surface in a tooltip
    // for a non-null (non-check-in) suppressed value.
    expect(screen.getByTitle('12.5% (1 of 8), too few to compare reliably')).toBeInTheDocument();
  });
});

describe('HRAnalyticsPage — group comparison', () => {
  it('shows "Unknown / untracked" as a normal row, never hidden', async () => {
    getAnalyticsFunnel.mockImplementation((opts: { cohort?: string; group_by?: string } = {}) =>
      Promise.resolve(
        opts.cohort === 'hire'
          ? hireFunnel()
          : pipelineFunnel({
              group_by: 'source',
              groups: [
                pipelineGroup(),
                pipelineGroup({ key: 'referral', label: 'Referral', in_progress: 1 }),
                pipelineGroup({ key: 'unknown', label: 'Unknown / untracked', in_progress: 2 }),
              ],
            }),
      ),
    );
    renderPage();
    await screen.findByText('12.5%');

    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: 'Source' }));

    // "Unknown / untracked" also appears as a <select> option in the source
    // filter — scope to the comparison table to assert the GROUP ROW.
    const table = await screen.findByRole('table');
    expect(within(table).getByText('Unknown / untracked')).toBeInTheDocument();
    expect(within(table).getByText('Referral')).toBeInTheDocument();
  });
});

describe('HRAnalyticsPage — cohort switch', () => {
  it('refetches the funnel with the newly selected cohort basis', async () => {
    renderPage();
    await screen.findByText('12.5%');
    expect(getAnalyticsFunnel).toHaveBeenCalledWith(
      expect.objectContaining({ cohort: 'application' }),
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: 'Decision' }));

    await waitFor(() =>
      expect(getAnalyticsFunnel).toHaveBeenCalledWith(
        expect.objectContaining({ cohort: 'decision' }),
      ),
    );
  });

  it('nudges toward the decision cohort while the application cohort is active', async () => {
    renderPage();
    await screen.findByText('12.5%');
    expect(screen.getByText(/switch to the decision cohort/i)).toBeInTheDocument();
  });
});

describe('HRAnalyticsPage — "How is this calculated?"', () => {
  it('shows the metric@version, formula and change note', async () => {
    renderPage();
    await screen.findByText('12.5%');

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'How is Applications calculated?' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('applications@1', { exact: false })).toBeInTheDocument();
    expect(within(dialog).getByText('count(applied)')).toBeInTheDocument();
  });

  it('shows the change note and a copyable registry hash for a changed metric', async () => {
    renderPage();
    await screen.findByText('12.5%');

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'How is Application → hire calculated?' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Now counts only hires that stand.')).toBeInTheDocument();
    expect(within(dialog).getByText(/registry abc123def4/)).toBeInTheDocument();
  });

  it('focuses inside the dialog on open and closes on Escape (code review — accessibility)', async () => {
    renderPage();
    await screen.findByText('12.5%');

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'How is Applications calculated?' }));

    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(within(dialog).getByLabelText('Close')).toHaveFocus());

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('HRAnalyticsPage — definition versioning', () => {
  it('renders only the current version once a metric has two on file, in the glossary and its dialog', async () => {
    const applicationsV1 = DEFINITIONS.metrics.find((m) => m.name === 'applications');
    if (!applicationsV1) throw new Error('fixture missing "applications"');
    const twoVersions: MetricDefinitionsResponse = {
      ...DEFINITIONS,
      metrics: [
        {
          ...applicationsV1,
          version: 1,
          current: false,
          description: 'SUPERSEDED — the old, pre-v2 wording.',
          formula: 'count(applied) [v1, superseded]',
        },
        {
          ...applicationsV1,
          version: 2,
          current: true,
          description: 'Every application in the cohort.',
          formula: 'count(applied)',
          change_note: 'Wording tightened; the count itself did not change.',
        },
        ...DEFINITIONS.metrics.filter((m) => m.name !== 'applications'),
      ],
    };
    getMetricDefinitions.mockResolvedValue(twoVersions);
    renderPage();
    await screen.findByText('12.5%');

    // One glossary row for "applications" — the current version, not a
    // second row (or the wrong text) for the superseded one.
    const glossary = screen.getByTestId('metric-glossary');
    const glossaryLabels = within(glossary).getAllByText('Applications');
    expect(glossaryLabels).toHaveLength(1);
    expect(within(glossary).queryByText(/SUPERSEDED/)).not.toBeInTheDocument();

    // Its "How is this calculated?" dialog — opened by name alone, same as
    // every other consumer — shows the CURRENT version's text.
    const user = userEvent.setup();
    const glossaryRow = glossaryLabels[0].closest('li');
    if (!glossaryRow) throw new Error('glossary row not found');
    await user.click(within(glossaryRow).getByRole('button', { name: 'How is this calculated?' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('applications@2', { exact: false })).toBeInTheDocument();
    expect(within(dialog).getByText('count(applied)')).toBeInTheDocument();
    expect(within(dialog).queryByText(/SUPERSEDED/)).not.toBeInTheDocument();
  });
});

describe('HRAnalyticsPage — drill-down', () => {
  it('opens the members list and shows the truncation notice', async () => {
    renderPage();
    const hiresCount = await screen.findByText('37');

    const user = userEvent.setup();
    await user.click(hiresCount);

    expect(getAnalyticsMembers).toHaveBeenCalledWith(
      expect.objectContaining({ metric: 'hires', part: 'numerator', cohort: 'hire' }),
    );
    expect(await screen.findByText('Showing 200 of 250')).toBeInTheDocument();
    expect(screen.getByText('Asha Rao')).toBeInTheDocument();
  });

  it('opens the candidate drawer for a row in the drill-down', async () => {
    renderPage();
    const hiresCount = await screen.findByText('37');
    const user = userEvent.setup();
    await user.click(hiresCount);
    await user.click(await screen.findByText('Asha Rao'));

    expect(await screen.findByText('drawer ap-1')).toBeInTheDocument();
  });

  it('focuses inside the drill-down on open and closes on Escape (code review — accessibility)', async () => {
    renderPage();
    const hiresCount = await screen.findByText('37');
    const user = userEvent.setup();
    await user.click(hiresCount);

    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(within(dialog).getByLabelText('Close')).toHaveFocus());

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('HRAnalyticsPage — quality of hire', () => {
  it('shows the persistent quality-signals banner, distinct from pipeline numbers', async () => {
    renderPage();

    expect(
      await screen.findByText(
        'Quality signals are recorded by your team after hiring. They describe outcomes; they never change an application or a decision.',
      ),
    ).toBeInTheDocument();
  });

  it('labels the interviewer score as human-recorded', async () => {
    renderPage();
    // The metric glossary (below) lists every definition by the same label —
    // scope to the Quality of hire card to assert THIS section's usage.
    const card = await screen.findByTestId('quality-of-hire');
    expect(await within(card).findByText('Human interviewer scorecards')).toBeInTheDocument();
  });
});

describe('HRAnalyticsPage — check-ins due', () => {
  it('lists each row — name, opening, started, days since start', async () => {
    listCheckinsDue.mockResolvedValue([
      {
        enrolment_id: 'en-1',
        applicant_id: 'ap-1',
        full_name: 'Priya Menon',
        job_title: 'Backend Engineer',
        employment_start: '2026-06-01T00:00:00.000Z',
        due_at: '2026-08-20T00:00:00.000Z',
        days_since_start: 112,
      },
    ]);
    renderPage();

    expect(await screen.findByText('Check-ins due')).toBeInTheDocument();
    expect(await screen.findByText('Priya Menon')).toBeInTheDocument();
    expect(screen.getByText('Backend Engineer')).toBeInTheDocument();
    expect(screen.getByText('112d ago', { exact: false })).toBeInTheDocument();
  });

  it('says there is nothing due rather than showing an empty list', async () => {
    listCheckinsDue.mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText('No check-ins due.')).toBeInTheDocument();
  });

  it('opens the shared candidate drawer on that applicant, not a separate one', async () => {
    listCheckinsDue.mockResolvedValue([
      {
        enrolment_id: 'en-1',
        applicant_id: 'ap-1',
        full_name: 'Priya Menon',
        job_title: 'Backend Engineer',
        employment_start: '2026-06-01T00:00:00.000Z',
        due_at: '2026-08-20T00:00:00.000Z',
        days_since_start: 112,
      },
    ]);
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByText('Priya Menon'));

    expect(await screen.findByText('drawer ap-1')).toBeInTheDocument();
  });
});

describe('HRAnalyticsPage — the drillable flag controls clickability', () => {
  it('renders a part as plain text (not a button) once the definition says it is not drillable', async () => {
    const restricted: MetricDefinitionsResponse = {
      ...DEFINITIONS,
      metrics: DEFINITIONS.metrics.map((m) =>
        m.name === 'application_to_hire'
          ? { ...m, drillable: { numerator: true, denominator: false } }
          : m,
      ),
    };
    getMetricDefinitions.mockResolvedValue(restricted);
    // A numerator distinct from every other number on screen, so asserting
    // it is a button cannot coincidentally match some OTHER metric's value.
    getAnalyticsFunnel.mockImplementation((opts: { cohort?: string } = {}) =>
      Promise.resolve(
        opts.cohort === 'hire'
          ? hireFunnel()
          : pipelineFunnel({
              groups: [
                pipelineGroup({
                  metrics: {
                    applications: { metric: 'applications', version: 1, kind: 'count', value: 40 },
                    application_to_hire: {
                      metric: 'application_to_hire',
                      version: 1,
                      kind: 'rate',
                      value: 22.5,
                      numerator: 9,
                      denominator: 40,
                      suppressed: false,
                    },
                  },
                }),
              ],
            }),
      ),
    );
    renderPage();

    await screen.findByText('22.5%');
    // The numerator (9) is still a button; the rate's denominator (40) is not
    // — even though the SAME number also appears as the (always-drillable)
    // applications count, which stays a button.
    expect(screen.getByRole('button', { name: '9' })).toBeInTheDocument();
    const fortyButtons = screen.getAllByText('40').filter((el) => el.tagName === 'BUTTON');
    expect(fortyButtons).toHaveLength(1); // only the applications count
  });
});

describe('HRAnalyticsPage — role gating (super_admin sees definitions only)', () => {
  it('shows the metric glossary but never calls the funnel endpoint', async () => {
    mockUseAuth.mockReturnValue({ user: { user_id: 'u-2', roles: ['super_admin'] } });
    renderPage();

    expect(
      await screen.findByText(/Your role sees how these metrics are defined/i),
    ).toBeInTheDocument();
    expect(screen.getByText('Metric glossary')).toBeInTheDocument();
    expect(await screen.findByText('Applications')).toBeInTheDocument();
    await waitFor(() => expect(getMetricDefinitions).toHaveBeenCalled());
    expect(getAnalyticsFunnel).not.toHaveBeenCalled();
    expect(screen.queryByText('The funnel')).not.toBeInTheDocument();
  });
});
