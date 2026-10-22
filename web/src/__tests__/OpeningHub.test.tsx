// The opening hub: Setup rail + Pipeline (UX round 3, stream A).
//
// What is pinned:
//   • the default tab follows the data, and `?tab=` overrides it;
//   • only the dashboard query polls, and nothing Setup-only is fetched while
//     Pipeline is the active tab (the DB is billed by wake-ups);
//   • every control that existed before the move is still reachable by role and
//     name, in the stage it now lives in;
//   • a gated control is disabled with its reason beside it (aria-describedby),
//     and clicking it never reaches the server;
//   • the header carries the approval pill and it follows a panel's submit.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type {
  ApprovalStatus,
  Requisition,
  RequisitionDashboard,
} from '../api/requisitions';

const m = vi.hoisted(() => ({
  getRequisitionDashboard: vi.fn(),
  getRequisition: vi.fn(),
  getJdHistory: vi.fn(),
  getPublishSchedule: vi.fn(),
  listWorkflows: vi.fn(),
  getWorkflow: vi.fn(),
  validateWorkflow: vi.fn(),
  getSimulation: vi.fn(),
  listDocumentRequirements: vi.fn(),
  updateRequisition: vi.fn(),
  submitRequisitionForApproval: vi.fn(),
  listTeam: vi.fn(),
  listQuestions: vi.fn(),
  getCompanyRequisitionDashboard: vi.fn(),
}));

vi.mock('../api/requisitions', async () => {
  const actual = await vi.importActual<typeof import('../api/requisitions')>('../api/requisitions');
  return {
    ...actual,
    getRequisitionDashboard: m.getRequisitionDashboard,
    getRequisition: m.getRequisition,
    getJdHistory: m.getJdHistory,
    getPublishSchedule: m.getPublishSchedule,
    updateRequisition: m.updateRequisition,
    submitRequisitionForApproval: m.submitRequisitionForApproval,
    listTeam: m.listTeam,
  };
});
vi.mock('../api/workflows', async () => {
  const actual = await vi.importActual<typeof import('../api/workflows')>('../api/workflows');
  return {
    ...actual,
    listWorkflows: m.listWorkflows,
    getWorkflow: m.getWorkflow,
    validateWorkflow: m.validateWorkflow,
  };
});
vi.mock('../api/workflowReview', async () => {
  const actual = await vi.importActual<typeof import('../api/workflowReview')>('../api/workflowReview');
  return { ...actual, getSimulation: m.getSimulation };
});
vi.mock('../api/offers', async () => {
  const actual = await vi.importActual<typeof import('../api/offers')>('../api/offers');
  return { ...actual, listDocumentRequirements: m.listDocumentRequirements };
});
vi.mock('../api/questions', async () => {
  const actual = await vi.importActual<typeof import('../api/questions')>('../api/questions');
  return { ...actual, listQuestions: m.listQuestions };
});
vi.mock('../api/companyBoard', () => ({
  getCompanyRequisitionDashboard: m.getCompanyRequisitionDashboard,
}));
vi.mock('../lib/toast', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

import RequisitionDashboardPage from '../pages/hr/RequisitionDashboard';

function opening(over: Partial<Requisition> = {}): Requisition {
  return {
    id: 'req-1', title: 'Python Developer', level: 'mid', status: 'open', jd_text: null,
    target_hires: 4, closes_at: null, owner_user_id: null, from_backfill: false,
    public_apply_enabled: false, created_at: '2026-09-01T10:00:00Z', total_enrolments: 12,
    approval_status: 'draft', hired: 1, awaiting_decision: 3, funnel: [], department: 'Engineering',
    location: 'Hyderabad', employment_type: null, experience_min_years: null,
    experience_max_years: null, salary_min: null, salary_max: null, salary_currency: null,
    salary_visible: false, responsibilities: [], required_skills: [], nice_to_have_skills: [],
    ...over,
  };
}

function dash(
  req: Partial<Requisition> = {},
  over: Partial<RequisitionDashboard> = {},
): RequisitionDashboard {
  return {
    requisition: opening(req),
    rounds: [],
    has_published_workflow: false,
    median_days_in_stage: null,
    still_being_read: 0,
    progress: {
      applications: 12, in_progress: 4, awaiting_decision: 3, held: 0, hired: 1, rejected: 1,
      not_started: 2, on_older_version: 0, target_hires: 4,
    },
    workflow_state: { published_version: null, draft_version: null },
    stage_timing: [],
    scores: { avg_ats: null, scored_applications: 0, avg_composite: null, assessed_candidates: 0 },
    held_pool: [],
    attention: [],
    manual_steps: [],
    activity: [],
    activity_summary: { automated_7d: 0, manual_7d: 0, last_automated_at: null },
    ...over,
  };
}

let client: QueryClient;

function renderHub(search = '', readOnly = false) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
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

const setupOnlyFetchers = [
  m.getRequisition,
  m.getJdHistory,
  m.getPublishSchedule,
  m.listWorkflows,
  m.listDocumentRequirements,
  m.getWorkflow,
  m.validateWorkflow,
  m.getSimulation,
];

function useData(d: RequisitionDashboard, record: Requisition = d.requisition) {
  m.getRequisitionDashboard.mockResolvedValue(d);
  m.getCompanyRequisitionDashboard.mockResolvedValue(d);
  m.getRequisition.mockResolvedValue(record);
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  m.getJdHistory.mockResolvedValue({ requisition_id: 'req-1', published_version_id: null, versions: [] });
  m.getPublishSchedule.mockResolvedValue({
    requisition_id: 'req-1', publish_at: null, published_at: null,
    public_apply_enabled: false, tolerance: 'About a minute.',
  });
  m.listWorkflows.mockResolvedValue([]);
  m.listDocumentRequirements.mockResolvedValue([]);
  m.listTeam.mockResolvedValue([]);
  m.listQuestions.mockResolvedValue([]);
  m.getSimulation.mockResolvedValue(null);
  useData(dash());
});

const tab = (name: string) => screen.findByRole('tab', { name });

describe('which tab opens', () => {
  it('Pipeline when anyone has applied', async () => {
    renderHub();
    expect((await tab('Pipeline')).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByTestId('funnel')).toBeTruthy();
  });

  it('Setup for an opening nobody has entered', async () => {
    useData(dash({ total_enrolments: 0 }));
    renderHub();
    expect((await tab('Setup')).getAttribute('aria-selected')).toBe('true');
    expect(await screen.findByRole('navigation', { name: 'Opening setup' })).toBeTruthy();
  });

  it('?tab= wins over the default, both ways', async () => {
    renderHub('?tab=setup');
    expect((await tab('Setup')).getAttribute('aria-selected')).toBe('true');
    expect(screen.queryByTestId('funnel')).toBeNull();
  });

  it('switching tabs changes what is shown', async () => {
    renderHub();
    await userEvent.click(await tab('Setup'));
    expect(await screen.findByRole('navigation', { name: 'Opening setup' })).toBeTruthy();
    expect(screen.queryByTestId('funnel')).toBeNull();
    await userEvent.click(screen.getByRole('tab', { name: 'Pipeline' }));
    expect(await screen.findByTestId('funnel')).toBeTruthy();
  });
});

describe('request discipline', () => {
  const polling = () =>
    client
      .getQueryCache()
      .getAll()
      .filter((q) => q.observers.some((o) => Boolean(o.options.refetchInterval)));

  it('fetches nothing Setup-only while Pipeline is the active tab, and only the dashboard polls', async () => {
    renderHub();
    await screen.findByTestId('funnel');
    // let anything wrongly enabled get a chance to fire
    await new Promise((r) => setTimeout(r, 30));
    for (const fn of setupOnlyFetchers) expect(fn).not.toHaveBeenCalled();
    expect(polling()).toHaveLength(1);
    expect(polling()[0].queryKey).toEqual(['hr', 'requisition-dashboard', 'req-1']);
  });

  it('fetches them once Setup opens, and still only the dashboard polls', async () => {
    renderHub('?tab=setup');
    await waitFor(() => expect(m.getJdHistory).toHaveBeenCalled());
    await waitFor(() => expect(m.getPublishSchedule).toHaveBeenCalled());
    await waitFor(() => expect(m.listDocumentRequirements).toHaveBeenCalled());
    expect(m.getRequisition).toHaveBeenCalled();
    expect(polling()).toHaveLength(1);
  });

  it('asks for workflow detail only when the dashboard says a version exists', async () => {
    renderHub('?tab=setup');
    await waitFor(() => expect(m.getJdHistory).toHaveBeenCalled());
    expect(m.listWorkflows).not.toHaveBeenCalled();
  });

  it('the read-only viewer makes no HR-only request at all', async () => {
    renderHub('?tab=setup', true);
    await screen.findByRole('navigation', { name: 'Opening setup' });
    await new Promise((r) => setTimeout(r, 30));
    for (const fn of setupOnlyFetchers) expect(fn).not.toHaveBeenCalled();
    expect(m.getRequisitionDashboard).not.toHaveBeenCalled();
  });
});

describe('the open stage is not swapped under the user', () => {
  const publishedJd = {
    requisition_id: 'req-1', published_version_id: 'j1',
    versions: [{ id: 'j1', version: 1, status: 'published' }],
  };

  it('mounts no stage until the facts that decide the default have loaded', async () => {
    // With a published JD the default is the hiring process. Before the JD
    // answered, the default was unknown and stage 1 (four panels, their
    // requests) mounted only to be replaced.
    useData(dash({ total_enrolments: 0 }));
    let release: (v: typeof publishedJd) => void = () => undefined;
    m.getJdHistory.mockReturnValue(
      new Promise<typeof publishedJd>((resolve) => {
        release = resolve;
      }),
    );
    renderHub();
    await screen.findByTestId('setup-loading');
    await new Promise((r) => setTimeout(r, 30));
    expect(m.listQuestions).not.toHaveBeenCalled();
    expect(screen.queryByLabelText('Owner')).toBeNull();

    release(publishedJd);
    expect(await screen.findByTestId('stage-process')).toBeTruthy();
    expect(screen.queryByTestId('setup-loading')).toBeNull();
    expect(m.listQuestions).not.toHaveBeenCalled();
  });

  it('keeps the shown stage when a poll or a finished stage would change the default', async () => {
    useData(dash({ total_enrolments: 0 }));
    m.getJdHistory.mockResolvedValue(publishedJd);
    renderHub();
    expect(await screen.findByTestId('stage-process')).toBeTruthy();

    // The next poll says a workflow is now live, so the process stage is done and
    // the default would move on to the approval.
    const polled = dash(
      { total_enrolments: 0, approval_status: 'approved' },
      { has_published_workflow: true, workflow_state: { published_version: 2, draft_version: null } },
    );
    m.getRequisitionDashboard.mockResolvedValue(polled);
    m.listWorkflows.mockResolvedValue([{ id: 'w2', version: 2, status: 'published' }]);
    m.getWorkflow.mockResolvedValue({
      id: 'w2', requisition_id: 'req-1', version: 2, status: 'published', review_status: 'approved',
      rounds: [{ id: 'r1', kind: 'mcq', needs_questions: false, exam_round_id: 'e1' }],
    });
    await client.invalidateQueries({ queryKey: ['hr', 'requisition-dashboard', 'req-1'] });
    await waitFor(() => expect(screen.getByTestId('workflow-state').textContent).toMatch(/v2 live/));

    expect(screen.getByTestId('stage-process')).toBeTruthy();
  });

  it('follows the user once they pick a stage', async () => {
    useData(dash({ total_enrolments: 0 }));
    m.getJdHistory.mockResolvedValue(publishedJd);
    renderHub();
    expect(await screen.findByTestId('stage-process')).toBeTruthy();
    await userEvent.click(await screen.findByRole('button', { name: /Budget and approval/ }));
    // Out of view, but kept (hidden) so nothing typed there is lost.
    await waitFor(() => expect(screen.getByTestId('stage-process').closest('[hidden]')).not.toBeNull());
    expect(await screen.findByLabelText('Budget amount')).toBeTruthy();
  });
});

describe('unsaved typing survives leaving a stage or the tab', () => {
  const stageBtn = (name: RegExp) => screen.findByRole('button', { name });
  beforeEach(() => useData(dash({ total_enrolments: 0, approval_status: 'draft' })));

  it('keeps the budget typed in stage 3 across another stage and back', async () => {
    renderHub('?tab=setup');
    await userEvent.click(await stageBtn(/Budget and approval/));
    const amount = await screen.findByLabelText<HTMLInputElement>('Budget amount');
    await userEvent.type(amount, '4200000');
    await userEvent.click(await stageBtn(/Hiring process/));
    await screen.findByRole('link', { name: 'Open the workflow builder' });
    await userEvent.click(await stageBtn(/Budget and approval/));
    expect((await screen.findByLabelText<HTMLInputElement>('Budget amount')).value).toBe('4200000');
  });

  it('keeps stage 1 typing across a different stage', async () => {
    renderHub('?tab=setup');
    await stageBtn(/Role and posting/);
    const hires = await screen.findByLabelText<HTMLInputElement>('Hires wanted');
    await userEvent.clear(hires);
    await userEvent.type(hires, '9');
    await userEvent.click(await stageBtn(/Hiring process/));
    await userEvent.click(await stageBtn(/Role and posting/));
    expect((await screen.findByLabelText<HTMLInputElement>('Hires wanted')).value).toBe('9');
  });

  it('keeps stage 1 typing across a trip to the Pipeline tab', async () => {
    renderHub('?tab=setup');
    await stageBtn(/Role and posting/);
    const hires = await screen.findByLabelText<HTMLInputElement>('Hires wanted');
    await userEvent.clear(hires);
    await userEvent.type(hires, '9');
    await userEvent.click(screen.getByRole('tab', { name: 'Pipeline' }));
    expect(await screen.findByTestId('funnel')).toBeTruthy();
    await userEvent.click(screen.getByRole('tab', { name: 'Setup' }));
    expect((await screen.findByLabelText<HTMLInputElement>('Hires wanted')).value).toBe('9');
  });

  it('does not mount a stage nobody has opened', async () => {
    renderHub('?tab=setup');
    await stageBtn(/Role and posting/);
    await screen.findByLabelText('Owner');
    expect(screen.queryByTestId('stage-panel-approval')).toBeNull();
    expect(screen.queryByTestId('stage-panel-documents')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Add requirement' })).toBeNull();
  });
});

describe('the rail tells the truth', () => {
  it('shows only real facts for a published JD, a live workflow and an approval', async () => {
    m.getJdHistory.mockResolvedValue({
      requisition_id: 'req-1', published_version_id: 'j2',
      versions: [{ id: 'j2', version: 2, status: 'published' }],
    });
    m.listWorkflows.mockResolvedValue([{ id: 'w2', version: 2, status: 'published' }]);
    m.getWorkflow.mockResolvedValue({
      id: 'w2', requisition_id: 'req-1', version: 2, status: 'published', review_status: 'approved',
      rounds: [{ id: 'r1', kind: 'mcq', needs_questions: false, exam_round_id: 'e1' }],
    });
    useData(
      dash(
        { approval_status: 'approved', approval_decided_by_name: 'Meera S' },
        { has_published_workflow: true, workflow_state: { published_version: 2, draft_version: null } },
      ),
    );
    renderHub('?tab=setup');

    const rail = await screen.findByRole('navigation', { name: 'Opening setup' });
    await waitFor(() => expect(within(rail).getByText(/JD v2 published/)).toBeTruthy());
    const items = within(rail).getAllByRole('listitem').filter((li) => li.querySelector('button'));
    const byTitle = (t: string) => items.find((li) => li.textContent?.includes(t));
    expect(byTitle('Role and posting')?.textContent).toMatch(/Done/);
    expect(byTitle('Hiring process')?.textContent).toMatch(/Done/);
    expect(byTitle('Budget and approval')?.textContent).toMatch(/Done/);
    // preboarding is never ticked
    expect(byTitle('Preboarding documents')?.textContent).toMatch(/Optional/);
    expect(byTitle('Preboarding documents')?.textContent).not.toMatch(/Done/);
  });

  it('puts the approval beside the status in the header', async () => {
    useData(dash({ approval_status: 'pending_approval' }));
    renderHub();
    expect((await screen.findByTestId('approval-state')).textContent).toBe('Awaiting approval');
  });

  it('keeps the header pill in step with a submit made in the panel', async () => {
    useData(dash({ total_enrolments: 0 }));
    // After the submit the record query is invalidated and now answers "pending".
    m.getRequisition
      .mockResolvedValueOnce(opening({ total_enrolments: 0 }))
      .mockResolvedValue(opening({ total_enrolments: 0, approval_status: 'pending_approval' }));
    m.submitRequisitionForApproval.mockResolvedValue(opening({ approval_status: 'pending_approval' }));
    renderHub('?tab=setup');

    expect((await screen.findByTestId('approval-state')).textContent).toBe('Not submitted');
    await userEvent.click(await screen.findByRole('button', { name: /Budget and approval/ }));
    await userEvent.click(await screen.findByRole('button', { name: 'Submit for approval' }));
    await waitFor(() =>
      expect(screen.getByTestId('approval-state').textContent).toBe('Awaiting approval'),
    );
  });
});

describe('one primary action, and it is the next step', () => {
  it('on Setup the header primary names the next stage; Decisions steps back', async () => {
    useData(dash({ total_enrolments: 0 }));
    renderHub();
    const primary = await screen.findByRole('button', { name: 'Write the job description' });
    expect(primary).toBeTruthy();
    const decisions = screen.getByRole('link', { name: 'Decisions' });
    expect(decisions.className).toMatch(/border-input/); // the outline variant
    expect(primary.className).not.toMatch(/border-input/);
  });

  it('its action opens that stage', async () => {
    useData(dash({ total_enrolments: 0 }));
    m.getJdHistory.mockResolvedValue({
      requisition_id: 'req-1', published_version_id: 'j1',
      versions: [{ id: 'j1', version: 1, status: 'published' }],
    });
    renderHub();
    // JD is done, so the next stage is the hiring process: a link into the builder
    const link = await screen.findByRole('link', { name: 'Build the hiring process' });
    expect(link.getAttribute('href')).toBe('/hr/requisitions/req-1/workflow');
  });

  it('on Pipeline, Decisions is the primary', async () => {
    renderHub();
    const decisions = await screen.findByRole('link', { name: 'Decisions' });
    expect(decisions.className).not.toMatch(/border-input/);
  });
});

describe('a gated control is disabled with its reason beside it', () => {
  async function openGoLive() {
    renderHub('?tab=setup');
    await userEvent.click(await screen.findByRole('button', { name: /Go live/ }));
    return screen.findByRole('switch', { name: 'Accept public applications' });
  }

  it('Accept applications on an unapproved opening cannot be clicked into a 409', async () => {
    useData(dash({ total_enrolments: 0, approval_status: 'draft' }));
    const sw = await openGoLive();
    expect((sw as HTMLButtonElement).disabled).toBe(true);
    const reasonId = sw.getAttribute('aria-describedby');
    expect(reasonId).toBeTruthy();
    expect(document.getElementById(reasonId as string)?.textContent).toMatch(/until the opening is approved/);
    await userEvent.click(sw);
    expect(m.updateRequisition).not.toHaveBeenCalled();
  });

  it('the schedule is disabled for the same reason, with the reason attached', async () => {
    useData(dash({ total_enrolments: 0, approval_status: 'pending_approval' }));
    await openGoLive();
    const at = await screen.findByLabelText('Go live at');
    expect((at as HTMLInputElement).disabled).toBe(true);
    const reason = document.getElementById(at.getAttribute('aria-describedby') as string);
    expect(reason?.textContent).toMatch(/needs budget approval/i);
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Schedule' }).disabled).toBe(true);
  });

  it('the stage says what is missing, as text', async () => {
    useData(dash({ total_enrolments: 0, approval_status: 'draft' }));
    renderHub('?tab=setup');
    const rail = await screen.findByRole('navigation', { name: 'Opening setup' });
    expect(within(rail).getByText(/Needs a live hiring process \(stage 2\) and budget approval \(stage 3\)/)).toBeTruthy();
  });

  it('is enabled once the opening is approved, and can still be switched off while gated states clear', async () => {
    useData(dash({ total_enrolments: 0, approval_status: 'approved' }));
    const sw = await openGoLive();
    expect((sw as HTMLButtonElement).disabled).toBe(false);
    expect(sw.getAttribute('aria-describedby')).toBeNull();
  });
});

describe('inventory: every control that existed before is reachable, by role and name', () => {
  const stageButton = (name: RegExp) => screen.findByRole('button', { name });

  beforeEach(() => useData(dash({ total_enrolments: 0, approval_status: 'draft' })));

  it('header', async () => {
    renderHub('?tab=setup');
    expect((await screen.findByRole('link', { name: 'Workflow' })).getAttribute('href')).toBe(
      '/hr/requisitions/req-1/workflow',
    );
    expect(screen.getByRole('button', { name: 'Pause' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Close' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Decisions' }).getAttribute('href')).toBe(
      '/hr/requisitions/req-1/decisions',
    );
    expect(screen.getByRole('link', { name: /Openings/ }).getAttribute('href')).toBe('/hr/requisitions');
    expect(screen.getByTestId('workflow-state')).toBeTruthy();
  });

  it('stage 1: four sub-steps, one panel in view at a time, each reachable directly', async () => {
    renderHub('?tab=setup');
    await stageButton(/Role and posting/);
    const steps = await screen.findByRole('navigation', { name: 'Role and posting steps' });
    const stepBtn = (name: RegExp) => within(steps).getByRole('button', { name });

    // 1 Opening details — the first, so it is the one in view.
    expect(stepBtn(/Opening details/).getAttribute('aria-current')).toBe('step');
    expect(await screen.findByLabelText('Owner')).toBeTruthy();
    expect(screen.getByLabelText('Hires wanted')).toBeTruthy();
    expect(screen.getByLabelText('Closes on')).toBeTruthy();
    // ...and the others are not on screen yet.
    expect(screen.queryByRole('heading', { name: 'The posting' })).toBeNull();

    // 2 Posting, reached directly.
    await userEvent.click(stepBtn(/Posting/));
    expect(await screen.findByRole('heading', { name: 'The posting' })).toBeTruthy();
    expect(stepBtn(/Posting/).getAttribute('aria-current')).toBe('step');
    // The first panel is kept (hidden), so nothing typed there is lost.
    expect(screen.getByLabelText('Owner').closest('[hidden]')).not.toBeNull();

    // 3 Job description, reached directly.
    await userEvent.click(stepBtn(/Job description/));
    expect(await screen.findByRole('heading', { name: 'Job description' })).toBeTruthy();

    // 4 Application questions, reached directly.
    await userEvent.click(stepBtn(/Application questions/));
    expect(await screen.findByRole('heading', { name: 'Application questions' })).toBeTruthy();
  });

  it('stage 1: Next and Back walk the sub-steps in order', async () => {
    renderHub('?tab=setup');
    await stageButton(/Role and posting/);
    await screen.findByLabelText('Owner');
    expect(screen.queryByRole('button', { name: /^Back:/ })).toBeNull();

    await userEvent.click(screen.getByRole('button', { name: 'Next: Posting' }));
    expect(await screen.findByRole('heading', { name: 'The posting' })).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Next: Job description' }));
    expect(await screen.findByRole('heading', { name: 'Job description' })).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Next: Application questions' }));
    expect(await screen.findByRole('heading', { name: 'Application questions' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^Next:/ })).toBeNull();

    await userEvent.click(screen.getByRole('button', { name: 'Back: Job description' }));
    expect(await screen.findByRole('heading', { name: 'Job description' })).toBeTruthy();
  });

  it('stage 1: what was typed in a sub-step survives going to another and back', async () => {
    renderHub('?tab=setup');
    await stageButton(/Role and posting/);
    const hires = await screen.findByLabelText('Hires wanted');
    await userEvent.clear(hires);
    await userEvent.type(hires, '7');
    await userEvent.click(screen.getByRole('button', { name: 'Next: Posting' }));
    await screen.findByRole('heading', { name: 'The posting' });
    await userEvent.click(screen.getByRole('button', { name: 'Back: Opening details' }));
    expect((await screen.findByLabelText<HTMLInputElement>('Hires wanted')).value).toBe('7');
  });

  it('stage 1: a sub-step nobody has opened has not asked the server for anything', async () => {
    renderHub('?tab=setup');
    await stageButton(/Role and posting/);
    await screen.findByLabelText('Owner');
    await new Promise((r) => setTimeout(r, 30));
    expect(m.listQuestions).not.toHaveBeenCalled();
  });

  it('stage 2: the way into the workflow builder', async () => {
    renderHub('?tab=setup');
    await userEvent.click(await stageButton(/Hiring process/));
    expect(
      (await screen.findByRole('link', { name: 'Open the workflow builder' })).getAttribute('href'),
    ).toBe('/hr/requisitions/req-1/workflow');
  });

  it('stage 3: budget and the approval submit', async () => {
    renderHub('?tab=setup');
    await userEvent.click(await stageButton(/Budget and approval/));
    expect(await screen.findByLabelText('Budget amount')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Save budget' })).toBeTruthy();
    expect(screen.getByLabelText(/Note for your company admin/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Submit for approval' })).toBeTruthy();
    // the schedule belongs to stage 4 now
    expect(screen.queryByLabelText('Go live at')).toBeNull();
  });

  it('stage 4: the apply switch, the link and the schedule', async () => {
    useData(dash({ total_enrolments: 0, approval_status: 'approved', public_apply_enabled: true }, {
      has_published_workflow: true, workflow_state: { published_version: 1, draft_version: null },
    }));
    renderHub('?tab=setup');
    await userEvent.click(await stageButton(/Go live/));
    expect(await screen.findByRole('switch', { name: 'Accept public applications' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Copy the application link' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open the application page' })).toBeTruthy();
    expect(await screen.findByLabelText('Go live at')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Schedule' })).toBeTruthy();
  });

  it('stage 5: preboarding documents', async () => {
    renderHub('?tab=setup');
    await userEvent.click(await stageButton(/Preboarding documents/));
    expect(await screen.findByRole('button', { name: 'Add requirement' })).toBeTruthy();
  });

  it('the Pipeline tab keeps its landmarks on the moved elements', async () => {
    useData(dash({}, {
      attention: [{ key: 'a', severity: 'warning', title: 'T', body: 'B', link: null }],
      manual_steps: [{ key: 's', count: 1, label: 'Held below a round threshold', link: null }],
      stage_timing: [{ key: 'k', label: 'Shortlisted', median_days: 1, count: 1 }],
    }));
    renderHub();
    for (const id of ['funnel', 'held-pool', 'needs-attention', 'manual-steps', 'timing', 'activity']) {
      expect(await screen.findByTestId(id)).toBeTruthy();
    }
    expect(screen.getByRole('list', { name: 'Pipeline summary' })).toBeTruthy();
  });

  it('Pipeline: below lg, process and timing fold into one closed section; at lg+ they stay in the aside', async () => {
    useData(dash({}, { manual_steps: [{ key: 's', count: 1, label: 'Held below a round threshold', link: null }] }));
    // desktop (jsdom has no matchMedia): the aside is just there
    const { unmount } = renderHub();
    expect(await screen.findByTestId('manual-steps')).toBeTruthy();
    expect(screen.queryByTestId('pipeline-aside')).toBeNull();
    unmount();

    window.matchMedia = vi.fn().mockImplementation((q: string) => ({
      matches: false, media: q, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    })) as unknown as typeof window.matchMedia;
    try {
      renderHub();
      const aside = await screen.findByTestId('pipeline-aside');
      expect(within(aside).getByRole('button', { name: /Process, timing and applications/ }).getAttribute('aria-expanded')).toBe('false');
      expect(screen.queryByTestId('manual-steps')).toBeNull();
      await userEvent.click(within(aside).getByRole('button', { name: /Process, timing and applications/ }));
      expect(await screen.findByTestId('manual-steps')).toBeTruthy();
      expect(screen.getByTestId('timing')).toBeTruthy();
    } finally {
      // @ts-expect-error — back to jsdom's "no matchMedia"
      delete window.matchMedia;
    }
  });

  it('Pipeline hands off to Setup for the apply switch', async () => {
    renderHub('?tab=pipeline');
    await userEvent.click(await screen.findByRole('button', { name: 'Open Setup' }));
    expect((await tab('Setup')).getAttribute('aria-selected')).toBe('true');
    expect(await screen.findByRole('switch', { name: 'Accept public applications' })).toBeTruthy();
  });
});

describe('the read-only view', () => {
  it('has the rail with summaries and nothing to press', async () => {
    useData(dash({ total_enrolments: 0, approval_status: 'pending_approval', submitted_by_name: 'Asha R' }));
    renderHub('?tab=setup', true);
    const rail = await screen.findByRole('navigation', { name: 'Opening setup' });
    expect(within(rail).getByText(/Waiting for your company admin/)).toBeTruthy();
    // Still awaiting approval, so not on the hiring board: back to Approvals (AD-F17).
    expect(screen.getByRole('link', { name: /Approvals/ }).getAttribute('href')).toBe('/superadmin/approvals');
    expect(screen.getByText('Read-only')).toBeTruthy();
    expect(screen.queryByRole('switch')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Submit for approval' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Open the workflow builder' })).toBeNull();
  });

  it('shows the approval pill too', async () => {
    useData(dash({ approval_status: 'rejected' }));
    renderHub('', true);
    expect((await screen.findByTestId('approval-state')).textContent).toBe('Changes requested');
  });
});

describe('approval statuses are all worded', () => {
  const words: [ApprovalStatus, string][] = [
    ['draft', 'Not submitted'],
    ['pending_approval', 'Awaiting approval'],
    ['approved', 'Approved'],
    ['rejected', 'Changes requested'],
  ];
  it.each(words)('%s', async (status, word) => {
    useData(dash({ approval_status: status }));
    renderHub();
    expect((await screen.findByTestId('approval-state')).textContent).toBe(word);
  });
});
