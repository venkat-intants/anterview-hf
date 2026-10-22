// E1 — the per-opening dashboard.
//
// What is pinned:
//   • the funnel is the PUBLISHED workflow's rounds, so two openings with
//     different workflows show different rounds;
//   • held candidates are visible, by name, and never presented as rejected;
//   • "needs attention" lists this opening's problems, with severity in words;
//   • automated activity is told apart from what a person did;
//   • the steps that wait for HR on purpose are listed as such;
//   • hires read against the target, timing and scores are shown as summaries;
//   • the hard-coded status list is gone.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { RequisitionDashboard } from '../api/requisitions';

const mocks = vi.hoisted(() => {
  class UnresolvedCandidatesError extends Error {
    readonly unresolved: number;
    constructor(unresolved: number, message: string) {
      super(message);
      this.unresolved = unresolved;
    }
  }
  return {
    getRequisitionDashboard: vi.fn(),
    // The Setup tab reads these; the Pipeline tests never reach them.
    getRequisition: vi.fn(),
    getJdHistory: vi.fn(() =>
      Promise.resolve({ requisition_id: 'req-1', published_version_id: null, versions: [] }),
    ),
    getPublishSchedule: vi.fn(() =>
      Promise.resolve({
        requisition_id: 'req-1', publish_at: null, published_at: null,
        public_apply_enabled: true, tolerance: '',
      }),
    ),
    setRequisitionStatus: vi.fn(),
    updateRequisition: vi.fn(),
    listTeam: vi.fn(() => Promise.resolve([])),
    closingDateToIso: (d: string) => (d ? `${d}T23:59:59.000Z` : null),
    UnresolvedCandidatesError,
  };
});
// The Setup tab mounts real panels that use the module's pure helpers, so only
// the network calls are replaced.
vi.mock('../api/requisitions', async () => ({
  ...(await vi.importActual<typeof import('../api/requisitions')>('../api/requisitions')),
  ...mocks,
}));
vi.mock('../api/workflows', () => ({
  listWorkflows: vi.fn(() => Promise.resolve([])),
  getWorkflow: vi.fn(),
  validateWorkflow: vi.fn(),
}));
vi.mock('../api/workflowReview', () => ({ getSimulation: vi.fn() }));
vi.mock('../api/offers', () => ({
  listDocumentRequirements: vi.fn(() => Promise.resolve([])),
}));
const getCompanyRequisitionDashboard = vi.fn();
vi.mock('../api/companyBoard', () => ({
  getCompanyRequisitionDashboard: (...a: unknown[]) =>
    getCompanyRequisitionDashboard(...a) as unknown,
}));

import RequisitionDashboardPage from '../pages/hr/RequisitionDashboard';

const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

function dash(over: Partial<RequisitionDashboard> = {}): RequisitionDashboard {
  return {
    requisition: {
      id: 'req-1', title: 'Python Developer', level: 'mid', status: 'open', jd_text: null,
      target_hires: 4, closes_at: null, owner_user_id: null, from_backfill: false,
      public_apply_enabled: true, created_at: new Date().toISOString(), total_enrolments: 12,
      approval_status: 'approved',
      hired: 1, awaiting_decision: 3, funnel: [], department: 'Engineering',
      location: 'Hyderabad', employment_type: null, experience_min_years: null,
      experience_max_years: null, salary_min: null, salary_max: null, salary_currency: null,
      salary_visible: false, responsibilities: [], required_skills: [], nice_to_have_skills: [],
    },
    rounds: [
      { round_id: 'r1', position: 0, title: 'Technical Test', kind: 'mcq', at_this_round: 3,
        attempted: 8, passed: 5, pass_rate: 63 },
      { round_id: 'r2', position: 1, title: 'AI Interview', kind: 'ai_interview',
        at_this_round: 1, attempted: 5, passed: 4, pass_rate: 80 },
    ],
    has_published_workflow: true,
    median_days_in_stage: 3,
    still_being_read: 0,
    progress: {
      applications: 12, in_progress: 4, awaiting_decision: 3, held: 2, hired: 1, rejected: 1,
      not_started: 2, on_older_version: 0, target_hires: 4,
    },
    workflow_state: { published_version: 2, draft_version: 3 },
    stage_timing: [
      { key: 'shortlisted', label: 'Shortlisted', median_days: 1.5, count: 9 },
      { key: 'r1', label: 'Reached Technical Test', median_days: 2, count: 8 },
      { key: 'hired', label: 'Hired', median_days: null, count: 0 },
    ],
    scores: { avg_ats: 71.4, scored_applications: 11, avg_composite: 64.2, assessed_candidates: 8 },
    held_pool: [
      { enrolment_id: 'e1', applicant_id: 'a1', full_name: 'Asha Rao',
        held_reason: 'Scored 54% against 60% on Technical Test', round_title: 'Technical Test',
        ats_overall: 70, held_days: 4 },
    ],
    attention: [
      { key: 'decision_backlog', severity: 'critical', title: '1 candidate waiting on a decision',
        body: 'The longest has waited 15 days.', link: '/hr/requisitions/req-1/decisions' },
      { key: 'links_expiring', severity: 'warning',
        title: '2 assessment links expire within 48 hours', body: 'Not started yet.', link: null },
    ],
    manual_steps: [
      { key: 'ready_to_shortlist', count: 2, label: 'Scored at or above the 7/10 bar',
        link: '/hr/applicants' },
      { key: 'held', count: 2, label: 'Held below a round threshold',
        link: '/hr/requisitions/req-1/decisions' },
    ],
    activity: [
      { occurred_at: minutesAgo(5), automated: true, actor: null, candidate: 'Ravi K',
        enrolment_id: 'e2', from_status: 'shortlisted', to_status: 'shortlisted',
        from_round: 'Technical Test', to_round: 'AI Interview',
        reason: 'advanced from Technical Test to AI Interview' },
      { occurred_at: minutesAgo(90), automated: false, actor: 'Meera HR', candidate: 'Asha Rao',
        enrolment_id: 'e1', from_status: 'interviewed', to_status: 'hired', from_round: null,
        to_round: null, reason: 'Strong panel' },
    ],
    activity_summary: { automated_7d: 14, manual_7d: 3, last_automated_at: minutesAgo(5) },
    ...over,
  };
}

function renderPage(readOnly = false, search = '') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/hr/requisitions/req-1${search}`]}>
        <Routes>
          <Route
            path="/hr/requisitions/:requisitionId"
            element={<RequisitionDashboardPage readOnly={readOnly} />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  mocks.getRequisitionDashboard.mockResolvedValue(dash());
});

describe('the per-opening dashboard (E1)', () => {
  it('shows status, key job information and the workflow state', async () => {
    renderPage();
    expect(await screen.findByRole('heading', { name: 'Python Developer' })).toBeTruthy();
    expect(screen.getByText('Hyderabad')).toBeTruthy();
    expect(screen.getByText('Engineering')).toBeTruthy();
    expect(screen.getByTestId('workflow-state').textContent).toBe('workflow v2 live · v3 draft');
    // The status pill is in words, not the raw lowercase value (audit X12).
    expect(screen.getByText('Open')).toBeTruthy();
    expect(screen.queryByText('open')).toBeNull();
  });

  it('names the opening in the breadcrumb, not a generic "Opening dashboard" (HR-L8)', async () => {
    renderPage();
    await screen.findByRole('heading', { name: 'Python Developer' });
    const current = document.querySelector('nav [aria-current="page"]');
    expect(current?.textContent).toBe('Python Developer');
    expect(screen.queryByText('Opening dashboard')).toBeNull();
  });

  it('reads hires against the target and counts who is where', async () => {
    renderPage();
    await screen.findByRole('heading', { name: 'Python Developer' });
    expect(screen.getByText('1 of 4')).toBeTruthy();
    expect(screen.getByLabelText('Hiring progress: 1 of 4')).toBeTruthy();
    expect(screen.getByText('In a round now')).toBeTruthy();
    expect(screen.getByText('Held, not rejected')).toBeTruthy();
  });

  it("draws the funnel from the published workflow's rounds", async () => {
    renderPage();
    const funnel = await screen.findByTestId('funnel');
    expect(within(funnel).getByText('1. Technical Test')).toBeTruthy();
    expect(within(funnel).getByText('2. AI Interview')).toBeTruthy();
  });

  it('a different workflow shows different rounds', async () => {
    mocks.getRequisitionDashboard.mockResolvedValue(
      dash({
        rounds: [
          { round_id: 'x', position: 0, title: 'Portfolio Review', kind: 'human_review',
            at_this_round: 2, attempted: 0, passed: 0, pass_rate: null },
        ],
      }),
    );
    renderPage();
    const funnel = await screen.findByTestId('funnel');
    expect(within(funnel).getByText('1. Portfolio Review')).toBeTruthy();
    expect(within(funnel).queryByText(/Technical Test/)).toBeNull();
  });

  it('keeps held candidates visible, by name, and never as rejected', async () => {
    renderPage();
    const pool = await screen.findByTestId('held-pool');
    expect(within(pool).getByText('Asha Rao')).toBeTruthy();
    expect(within(pool).getByText(/Held, not rejected/)).toBeTruthy();
    expect(within(pool).getByText('Scored 54% against 60% on Technical Test')).toBeTruthy();
    expect(within(pool).queryByText(/^rejected$/i)).toBeNull();
  });

  it("lists this opening's problems, worst first, with severity in words", async () => {
    renderPage();
    const panel = await screen.findByTestId('needs-attention');
    const titles = within(panel).getAllByText(/waiting on a decision|expire within/);
    expect(titles[0].textContent).toMatch(/Critical: 1 candidate waiting on a decision/);
    expect(within(panel).getByText(/2 assessment links expire within 48 hours/)).toBeTruthy();
  });

  it('says so when nothing needs attention', async () => {
    mocks.getRequisitionDashboard.mockResolvedValue(dash({ attention: [] }));
    renderPage();
    expect(
      await screen.findByText('Nothing in this opening needs attention right now.'),
    ).toBeTruthy();
  });

  it('tells automated activity apart from what a person did, and when', async () => {
    renderPage();
    const activity = await screen.findByTestId('activity');
    // Activity is a record: closed by default, under a summary of the newest line.
    const toggle = within(activity).getByRole('button', { name: /Activity/ });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(within(activity).getByTestId('collapsible-summary').textContent).toMatch(/events? · latest: /);
    await userEvent.click(toggle);
    expect(within(activity).getByText('Ravi K moved to AI Interview')).toBeTruthy();
    expect(within(activity).getByText('Automated')).toBeTruthy();
    expect(within(activity).getByText('Meera HR')).toBeTruthy();
    expect(within(activity).getByText(/14 automated · 3 by people/)).toBeTruthy();
    expect(within(activity).getByText(/automation last ran 5 min ago/)).toBeTruthy();
  });

  it('lists the steps that wait for HR on purpose', async () => {
    renderPage();
    const steps = await screen.findByTestId('manual-steps');
    expect(within(steps).getByText('Waiting on you, by design')).toBeTruthy();
    expect(within(steps).getByText('Scored at or above the 7/10 bar')).toBeTruthy();
  });

  it('shows median time to each stage and scores as summaries', async () => {
    renderPage();
    const timing = await screen.findByTestId('timing');
    expect(within(timing).getByText('Reached Technical Test')).toBeTruthy();
    expect(within(timing).getByText('nobody yet')).toBeTruthy();
    expect(within(timing).getByText('71/100')).toBeTruthy();
    // An assessed-round average is out of 100 like every other percent score.
    expect(within(timing).getByText('64/100')).toBeTruthy();
  });

  it('links to the workflow editor and the decision queue, and drops the status list', async () => {
    renderPage();
    await screen.findByRole('heading', { name: 'Python Developer' });
    expect(screen.getByRole('link', { name: 'Workflow' }).getAttribute('href')).toBe(
      '/hr/requisitions/req-1/workflow',
    );
    expect(screen.getByRole('link', { name: 'Decisions' }).getAttribute('href')).toBe(
      '/hr/requisitions/req-1/decisions',
    );
    expect(screen.queryByText('By stage')).toBeNull();
    expect(screen.getByText(/refreshes on its own/)).toBeTruthy();
  });

  it("gives the company super admin the same numbers, with nothing to change (E3)", async () => {
    getCompanyRequisitionDashboard.mockResolvedValue(dash());
    renderPage(true);
    expect(await screen.findByRole('heading', { name: 'Python Developer' })).toBeTruthy();
    expect(getCompanyRequisitionDashboard).toHaveBeenCalledWith('req-1');
    expect(mocks.getRequisitionDashboard).not.toHaveBeenCalled();
    expect(screen.getByText('Read-only')).toBeTruthy();
    expect(screen.getByTestId('held-pool')).toBeTruthy();
    for (const control of ['Close', 'Pause', 'Workflow', 'Decisions']) {
      expect(screen.queryByRole('button', { name: control })).toBeNull();
      expect(screen.queryByRole('link', { name: control })).toBeNull();
    }
    expect(screen.queryByRole('switch')).toBeNull();
    expect(screen.queryByText(/Review them in the decision queue/)).toBeNull();
    const panel = screen.getByTestId('needs-attention');
    expect(panel.querySelector('a')).toBeNull();
    expect(screen.getByRole('link', { name: /Hiring board/ }).getAttribute('href')).toBe(
      '/superadmin/board',
    );
    // AD-F17: the decisions are HR's, so the read-only view does not say "you".
    expect(screen.queryByText(/your decision/i)).toBeNull();
    expect(screen.queryByText(/waiting on you/i)).toBeNull();
    expect(screen.getByText("Held for HR's decision")).toBeTruthy();
    expect(screen.getByText("Awaiting HR's decision")).toBeTruthy();
  });

  it('sends the super admin back to Approvals from an opening still awaiting approval', async () => {
    const pending = dash();
    getCompanyRequisitionDashboard.mockResolvedValue({
      ...pending,
      requisition: { ...pending.requisition, approval_status: 'pending_approval' },
    });
    renderPage(true);
    expect(await screen.findByRole('heading', { name: 'Python Developer' })).toBeTruthy();
    expect(screen.getByRole('link', { name: /Approvals/ }).getAttribute('href')).toBe(
      '/superadmin/approvals',
    );
  });

  it('keeps "your decision" wording for HR', async () => {
    renderPage();
    expect(await screen.findByText('Held for your decision')).toBeTruthy();
  });

  it('warns that the public link waits for a published workflow', async () => {
    mocks.getRequisitionDashboard.mockResolvedValue(
      dash({ has_published_workflow: false, rounds: [] }),
    );
    // The apply switch now lives in the Go live stage of the Setup tab.
    renderPage(false, '?tab=setup');
    await userEvent.click(await screen.findByRole('button', { name: /Go live/ }));
    expect((await screen.findByTestId('apply-needs-workflow')).textContent).toMatch(
      /until a workflow is published/,
    );
  });
});
