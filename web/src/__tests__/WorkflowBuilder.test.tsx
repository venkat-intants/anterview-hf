// Tests for the visual workflow builder (Group D).
//
// The builder authors the thing that then runs unattended, so the properties
// worth pinning are the ones whose failure would be invisible until a real
// candidate hit them:
//
//   • a PUBLISHED workflow cannot be edited from this screen. People are
//     part-way through it; changing a threshold or a rubric underneath them
//     would assess them against a standard nobody agreed to. The only route
//     back to editing is a clone, and the original must keep running.
//   • picking a competency freezes its ANCHORS AND PROBES onto the round, not
//     just its name. A rubric stored as bare labels cannot be reproduced at
//     interview time, which is the whole point of freezing it (C8).
//   • reordering sends the complete order. The server positions rounds from
//     this list, and a partial one silently reshuffles the chain.
//   • publishing is not a stray click, because it starts emailing real people.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { RoleModel, ValidationReport, Workflow, WorkflowSummary } from '../api/workflows';

const SETTINGS = {
  auto_score_on_apply: true,
  auto_assign_first_round: true,
  auto_advance_rounds: true,
  reminders_enabled: true,
  shortlist_ats_threshold: 7,
  hold_band: 10,
  auto_shortlist: false,
};

const DRAFT: Workflow = {
  id: 'wf-draft',
  requisition_id: 'req-1',
  version: 2,
  status: 'draft',
  name: 'Technical hire',
  editable: true,
  review_status: 'draft',
  role_profile_id: null,
  settings: SETTINGS,
  published_at: null,
  rounds: [
    {
      id: 'r-mcq',
      position: 0,
      title: 'Fundamentals',
      kind: 'mcq',
      pass_threshold: 60,
      time_limit_seconds: null,
      deadline_days: 7,
      auto_advance: null,
      on_pass_next_round_id: 'r-ai',
      on_fail_next_round_id: null,
      fast_track_min_percent: null,
      on_fast_track_next_round_id: null,
      exam_round_id: null,
      needs_questions: true,
      criteria: [],
    },
    {
      id: 'r-ai',
      position: 1,
      title: 'Conversation',
      kind: 'ai_interview',
      pass_threshold: 60,
      time_limit_seconds: null,
      deadline_days: 7,
      auto_advance: null,
      on_pass_next_round_id: null,
      on_fail_next_round_id: null,
      fast_track_min_percent: null,
      on_fast_track_next_round_id: null,
      exam_round_id: null,
      needs_questions: false,
      criteria: [],
    },
  ],
};

const PUBLISHED: Workflow = {
  ...DRAFT,
  id: 'wf-live',
  version: 1,
  status: 'published',
  editable: false,
  review_status: 'approved',
  published_at: '2026-08-01T00:00:00.000Z',
};

const VERSIONS_DRAFT: WorkflowSummary[] = [
  {
    id: 'wf-draft',
    version: 2,
    status: 'draft',
    name: 'Technical hire',
    rounds: 2,
    enrolled_candidates: 0,
    published_at: null,
    created_at: '2026-09-01T00:00:00.000Z',
  },
];

const VERSIONS_LIVE: WorkflowSummary[] = [
  {
    id: 'wf-live',
    version: 1,
    status: 'published',
    name: 'Technical hire',
    rounds: 2,
    enrolled_candidates: 4,
    published_at: '2026-08-01T00:00:00.000Z',
    created_at: '2026-08-01T00:00:00.000Z',
  },
];

const ROLE_MODEL: RoleModel = {
  job_title: 'Backend Engineer',
  domain_family: 'software',
  domain_label: 'Software & IT',
  source: 'taxonomy',
  competencies: [
    {
      id: 'python',
      name: 'Python proficiency',
      kind: 'technical',
      weight: 0.4,
      anchors: { low: 'cannot write a loop', mid: 'working scripts', high: 'idiomatic, tested' },
      probes: ['walk me through something you built'],
    },
    {
      id: 'comms',
      name: 'Communication',
      kind: 'communication',
      weight: 0.3,
      anchors: { low: 'hard to follow', mid: 'clear enough', high: 'explains to a non-expert' },
      probes: ['explain a decision to a non-engineer'],
    },
  ],
};

const REPORT_BLOCKED: ValidationReport = {
  publishable: false,
  errors: ['Fundamentals: a mcq round needs questions before publishing.'],
  warnings: ["'Communication' carries weight 0.30 in this role but no round assesses it"],
  coverage: [
    {
      competency_id: 'python',
      competency_name: 'Python proficiency',
      profile_weight: 0.4,
      times_assessed: 1,
      assessed_in: ['Conversation'],
    },
    {
      competency_id: 'comms',
      competency_name: 'Communication',
      profile_weight: 0.3,
      times_assessed: 0,
      assessed_in: [],
    },
  ],
};

const listWorkflows = vi.fn();
const getWorkflow = vi.fn();
const validateWorkflow = vi.fn();
const getRoleModel = vi.fn();
const addRound = vi.fn();
const updateRound = vi.fn();
const removeRound = vi.fn();
const reorderRounds = vi.fn();
const setRoundCriteria = vi.fn();
const updateSettings = vi.fn();
const startDraft = vi.fn();
const listWorkflowTemplates = vi.fn();
const createFromTemplate = vi.fn();
const publishWorkflow = vi.fn();
const cloneWorkflow = vi.fn();
const discardDraft = vi.fn();

// The real constants (MAX_ROUNDS, the round-kind sets) come from the module
// itself, so this test fails if they change -- a hard-coded copy here would
// keep asserting against a stale duplicate. Only the network calls are stubbed.
vi.mock('../api/workflows', async () => ({
  ...(await vi.importActual<typeof import('../api/workflows')>('../api/workflows')),
  listWorkflows: (...a: unknown[]) => listWorkflows(...a) as unknown,
  getWorkflow: (...a: unknown[]) => getWorkflow(...a) as unknown,
  validateWorkflow: (...a: unknown[]) => validateWorkflow(...a) as unknown,
  getRoleModel: (...a: unknown[]) => getRoleModel(...a) as unknown,
  addRound: (...a: unknown[]) => addRound(...a) as unknown,
  updateRound: (...a: unknown[]) => updateRound(...a) as unknown,
  removeRound: (...a: unknown[]) => removeRound(...a) as unknown,
  reorderRounds: (...a: unknown[]) => reorderRounds(...a) as unknown,
  setRoundCriteria: (...a: unknown[]) => setRoundCriteria(...a) as unknown,
  updateSettings: (...a: unknown[]) => updateSettings(...a) as unknown,
  startDraft: (...a: unknown[]) => startDraft(...a) as unknown,
  listWorkflowTemplates: (...a: unknown[]) => listWorkflowTemplates(...a) as unknown,
  createFromTemplate: (...a: unknown[]) => createFromTemplate(...a) as unknown,
  publishWorkflow: (...a: unknown[]) => publishWorkflow(...a) as unknown,
  cloneWorkflow: (...a: unknown[]) => cloneWorkflow(...a) as unknown,
  discardDraft: (...a: unknown[]) => discardDraft(...a) as unknown,
}));

const getRequisition = vi.fn();
// Spread the real module rather than replacing it: the page also uses
// updateRequisition and EMPLOYMENT_TYPE_LABELS now, and a bare factory blanks
// everything it does not list.
vi.mock('../api/requisitions', async () => {
  const actual = await vi.importActual<typeof import('../api/requisitions')>('../api/requisitions');
  return {
    ...actual,
    getRequisition: (...a: unknown[]) => getRequisition(...a) as unknown,
    updateRequisition: () => Promise.resolve(undefined),
  };
});
// The builder page now also renders the question editor. Passing the real
// module through and overriding only the calls keeps its constants (kind
// labels, CHOICE_KINDS) working — a bare factory would blank them.
vi.mock('../api/questions', async () => {
  const actual = await vi.importActual<typeof import('../api/questions')>('../api/questions');
  return {
    ...actual,
    listQuestions: () => Promise.resolve([]),
    addQuestion: () => Promise.resolve([]),
    updateQuestion: () => Promise.resolve([]),
    retireQuestion: () => Promise.resolve(undefined),
    reorderQuestions: () => Promise.resolve([]),
    listAnswers: () => Promise.resolve([]),
  };
});

const listExams = vi.fn();
const getStructure = vi.fn();
vi.mock('../api/exams', () => ({
  listExams: (...a: unknown[]) => listExams(...a) as unknown,
  getStructure: (...a: unknown[]) => getStructure(...a) as unknown,
}));

// PH4-O6 / O2 — ReviewPanel and DryRunPanel are always mounted for a draft
// workflow now, so every test that renders one needs these.
const getReview = vi.fn();
const submitForReview = vi.fn();
const withdrawReview = vi.fn();
const reopenForEdits = vi.fn();
const runSimulation = vi.fn();
const getSimulation = vi.fn();
vi.mock('../api/workflowReview', () => ({
  getReview: (...a: unknown[]) => getReview(...a) as unknown,
  submitForReview: (...a: unknown[]) => submitForReview(...a) as unknown,
  withdrawReview: (...a: unknown[]) => withdrawReview(...a) as unknown,
  reopenForEdits: (...a: unknown[]) => reopenForEdits(...a) as unknown,
  runSimulation: (...a: unknown[]) => runSimulation(...a) as unknown,
  getSimulation: (...a: unknown[]) => getSimulation(...a) as unknown,
  reviewErrorDetail: (err: unknown) => {
    const detail = (err as { detail?: unknown } | null)?.detail;
    if (detail && typeof detail === 'object' && 'message' in detail) return detail;
    return null;
  },
}));

// PH4-O1 — StageSettings (round + final-decision) always mounts too.
const getStages = vi.fn();
const listStageOwners = vi.fn();
const setStage = vi.fn();
vi.mock('../api/stageSla', () => ({
  getStages: (...a: unknown[]) => getStages(...a) as unknown,
  listStageOwners: (...a: unknown[]) => listStageOwners(...a) as unknown,
  setStage: (...a: unknown[]) => setStage(...a) as unknown,
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
    info: vi.fn(),
    warning: vi.fn(),
  },
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useParams: () => ({ requisitionId: 'req-1' }) };
});

import WorkflowBuilder from '../pages/hr/WorkflowBuilder';

function renderBuilder() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <WorkflowBuilder />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getRequisition.mockResolvedValue({
    id: 'req-1',
    title: 'Backend Engineer',
    total_enrolments: 12,
    awaiting_decision: 0,
  });
  listWorkflows.mockResolvedValue(VERSIONS_DRAFT);
  getWorkflow.mockResolvedValue(DRAFT);
  validateWorkflow.mockResolvedValue(REPORT_BLOCKED);
  getRoleModel.mockResolvedValue(ROLE_MODEL);
  addRound.mockResolvedValue(DRAFT);
  updateRound.mockResolvedValue(DRAFT);
  removeRound.mockResolvedValue(DRAFT);
  reorderRounds.mockResolvedValue(DRAFT);
  setRoundCriteria.mockResolvedValue(DRAFT);
  publishWorkflow.mockResolvedValue({ ...PUBLISHED, validation: REPORT_BLOCKED });
  cloneWorkflow.mockResolvedValue(DRAFT);
  listExams.mockResolvedValue([]);
  getStructure.mockResolvedValue({ exam_id: 'e1', rounds: [] });

  getReview.mockResolvedValue({
    review_status: 'draft',
    submitted_at: null,
    submitted_by_name: null,
    reviewed_at: null,
    reviewed_by_name: null,
    note: null,
    history: [],
  });
  getSimulation.mockResolvedValue(null);
  getStages.mockResolvedValue([]);
  listStageOwners.mockResolvedValue([]);
});

describe('WorkflowBuilder - the canvas', () => {
  it('renders the rounds in order with what each still needs', async () => {
    renderBuilder();
    await screen.findByText('Fundamentals');

    // By role, not text: the coverage panel below also names every round a
    // competency is assessed in, which can include "Conversation" too.
    expect(screen.getByRole('button', { name: /^Conversation/ })).toBeTruthy();
    // needs_questions surfaced on the node itself, not only in the panel —
    // the gap belongs next to the round that has it.
    expect(screen.getByText('No questions attached')).toBeTruthy();
  });

  it('says what happens at the end, because that is the most misread part', async () => {
    renderBuilder();
    await screen.findByText('Fundamentals');

    expect(screen.getByText(/puts a candidate in your decision queue/)).toBeTruthy();
  });

  it('sends the complete new order when a round moves', async () => {
    const user = userEvent.setup();
    renderBuilder();
    await screen.findByText('Fundamentals');

    await user.click(screen.getByLabelText('Move Conversation earlier'));

    await waitFor(() => expect(reorderRounds).toHaveBeenCalledTimes(1));
    expect(reorderRounds).toHaveBeenCalledWith('wf-draft', ['r-ai', 'r-mcq']);
  });
});

describe('WorkflowBuilder - a published version is immutable', () => {
  beforeEach(() => {
    listWorkflows.mockResolvedValue(VERSIONS_LIVE);
    getWorkflow.mockResolvedValue(PUBLISHED);
  });

  it('offers no way to add, remove or reorder', async () => {
    renderBuilder();
    await screen.findByText('Fundamentals');

    expect(screen.queryByText('Add a round')).toBeNull();
    expect(screen.queryByLabelText('Remove Conversation')).toBeNull();
    expect(screen.queryByLabelText('Move Conversation earlier')).toBeNull();
  });

  it('explains that editing means a new version and leaves this one running', async () => {
    renderBuilder();
    await screen.findByText('Fundamentals');

    expect(screen.getByText(/creates version 2/)).toBeTruthy();
    expect(screen.queryByText('Publish')).toBeNull();
    expect(screen.getByText('Edit as new version')).toBeTruthy();
  });

  it('clones rather than unlocking', async () => {
    const user = userEvent.setup();
    renderBuilder();
    await screen.findByText('Fundamentals');

    await user.click(screen.getByText('Edit as new version'));

    await waitFor(() => expect(cloneWorkflow).toHaveBeenCalledWith('wf-live'));
    expect(toastSuccess).toHaveBeenCalledWith('Editing as version 2 - version 1 stays live');
  });
});

describe('WorkflowBuilder - freezing what a round assesses', () => {
  it('copies the anchors and probes onto the round, not just the name', async () => {
    const user = userEvent.setup();
    renderBuilder();
    await screen.findByText('Conversation');

    await user.click(screen.getByRole('button', { name: /^Conversation/ }));
    // By label, not text: the coverage panel also names every competency.
    await user.click(await screen.findByLabelText('Python proficiency'));

    await waitFor(() => expect(setRoundCriteria).toHaveBeenCalledTimes(1));
    expect(setRoundCriteria).toHaveBeenCalledWith('wf-draft', 'r-ai', [
      {
        id: 'python',
        name: 'Python proficiency',
        kind: 'technical',
        weight: 0.4,
        anchors: {
          low: 'cannot write a loop',
          mid: 'working scripts',
          high: 'idiomatic, tested',
        },
        probes: ['walk me through something you built'],
      },
    ]);
  });

  it('says plainly that a threshold does not reject anyone', async () => {
    const user = userEvent.setup();
    renderBuilder();
    await screen.findByText('Conversation');

    await user.click(screen.getByRole('button', { name: /^Conversation/ }));

    expect(await screen.findByText(/does not reject anyone/)).toBeTruthy();
  });
});

describe('WorkflowBuilder - coverage', () => {
  it('names the competency nothing measures', async () => {
    renderBuilder();
    await screen.findByText('Fundamentals');

    // The blocker banner under the track owns the validation errors; the
    // Coverage panel must not repeat them (it keeps only coverage facts).
    expect(await screen.findByTestId('submit-blockers')).toBeTruthy();
    expect(screen.queryByText('1 thing to fix before this can go live.')).toBeNull();
    const covered = screen.getByText('Communication').closest('li') as HTMLElement;
    expect(within(covered).getByText('not assessed')).toBeTruthy();
  });
});

describe('WorkflowBuilder - publishing', () => {
  const REPORT_READY: ValidationReport = { ...REPORT_BLOCKED, publishable: true, errors: [] };
  const APPROVED_DRAFT: Workflow = { ...DRAFT, review_status: 'approved' };

  it('offers no Publish button until a company super admin has approved this version', async () => {
    // Default DRAFT fixture — review_status: 'draft'.
    renderBuilder();
    await screen.findByText('Fundamentals');

    expect(screen.queryByText('Publish')).toBeNull();
    // Review is no longer an inspector tab: it is the track plus a sheet.
    expect(screen.queryByRole('tab', { name: 'Review' })).toBeNull();
    expect(screen.getByTestId('workflow-state')).toBeTruthy();
  });

  it('asks before publishing, because publishing emails real people', async () => {
    const user = userEvent.setup();
    validateWorkflow.mockResolvedValue(REPORT_READY);
    getWorkflow.mockResolvedValue(APPROVED_DRAFT);
    getReview.mockResolvedValue({
      review_status: 'approved',
      submitted_at: null,
      submitted_by_name: null,
      reviewed_at: null,
      reviewed_by_name: 'A Reviewer',
      note: null,
      history: [],
    });
    renderBuilder();
    await screen.findByText('Fundamentals');
    await waitFor(() =>
      expect((screen.getByText('Publish').closest('button') as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );

    await user.click(screen.getByText('Publish'));

    expect(publishWorkflow).not.toHaveBeenCalled();
    expect(screen.getByText('Publish version 2')).toBeTruthy();

    await user.click(screen.getByText('Publish version 2'));
    await waitFor(() => expect(publishWorkflow).toHaveBeenCalledWith('wf-draft'));
  });

  it('rechecks so a refusal lands in the panel rather than a toast that scrolls away', async () => {
    const user = userEvent.setup();
    // Validate said ready, the server disagreed at publish time (a race, or a
    // check only publish runs) — the panel must pick up the new reasons.
    validateWorkflow.mockResolvedValue(REPORT_READY);
    publishWorkflow.mockRejectedValue(new Error('This workflow is not ready to publish'));
    getWorkflow.mockResolvedValue(APPROVED_DRAFT);
    getReview.mockResolvedValue({
      review_status: 'approved',
      submitted_at: null,
      submitted_by_name: null,
      reviewed_at: null,
      reviewed_by_name: 'A Reviewer',
      note: null,
      history: [],
    });
    renderBuilder();
    await screen.findByText('Fundamentals');
    await waitFor(() =>
      expect((screen.getByText('Publish').closest('button') as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );

    const before = validateWorkflow.mock.calls.length;
    await user.click(screen.getByText('Publish'));
    await user.click(screen.getByText('Publish version 2'));

    await waitFor(() => expect(validateWorkflow.mock.calls.length).toBeGreaterThan(before));
    expect(toastError).toHaveBeenCalledWith('This workflow is not ready to publish');
  });

  it('disables the confirm button while the publish is in flight (HR-M3)', async () => {
    const user = userEvent.setup();
    validateWorkflow.mockResolvedValue(REPORT_READY);
    publishWorkflow.mockImplementation(() => new Promise(() => undefined));
    getWorkflow.mockResolvedValue(APPROVED_DRAFT);
    getReview.mockResolvedValue({
      review_status: 'approved',
      submitted_at: null,
      submitted_by_name: null,
      reviewed_at: null,
      reviewed_by_name: 'A Reviewer',
      note: null,
      history: [],
    });
    renderBuilder();
    await screen.findByText('Fundamentals');
    await waitFor(() =>
      expect((screen.getByText('Publish').closest('button') as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );

    await user.click(screen.getByText('Publish'));
    const confirm = screen.getByTestId('confirm-publish');
    await user.click(confirm);
    await waitFor(() => expect(confirm).toBeDisabled());
    await user.click(confirm);
    expect(publishWorkflow).toHaveBeenCalledTimes(1);
  });

  it('disables the header Publish button when validation still finds issues, and says why', async () => {
    // Approved, but validation (re-checked live) still finds the coverage gap —
    // the header must not let this be clicked regardless.
    getWorkflow.mockResolvedValue(APPROVED_DRAFT);
    getReview.mockResolvedValue({
      review_status: 'approved',
      submitted_at: null,
      submitted_by_name: null,
      reviewed_at: null,
      reviewed_by_name: 'A Reviewer',
      note: null,
      history: [],
    });
    renderBuilder();
    await screen.findByText('Fundamentals');
    await screen.findByText('1 thing to fix before this can go live.');

    const header = screen.getByText('Publish').closest('button');
    expect(header).toBeDisabled();
    expect(header).toHaveAttribute('title', 'Fix 1 issue before publishing.');
  });
});

describe('WorkflowBuilder - attaching exam questions', () => {
  it('offers only published exams, and only rounds that are published and have questions', async () => {
    const user = userEvent.setup();
    listExams.mockResolvedValue([
      { id: 'e1', title: 'Backend fundamentals', status: 'published', question_count: 12 },
    ]);
    const section = (n: number) => ({
      id: `s-${n}`,
      round_id: 'x',
      title: 'S',
      kind: 'mcq',
      time_limit_seconds: null,
      position: 0,
      question_count: n,
    });
    const round = (
      id: string,
      title: string,
      status: 'draft' | 'published',
      questions: number,
    ) => ({
      id,
      title,
      round_number: 1,
      pass_threshold: 60,
      time_limit_seconds: null,
      advances_to_interview: false,
      status,
      position: 0,
      sections: [section(questions)],
    });
    getStructure.mockResolvedValue({
      exam_id: 'e1',
      rounds: [
        round('er-ready', 'Ready round', 'published', 10),
        round('er-draft', 'Draft round', 'draft', 5),
        round('er-empty', 'Empty round', 'published', 0),
      ],
    });
    renderBuilder();
    await screen.findByText('Conversation');

    await user.click(screen.getByRole('button', { name: /^Fundamentals/ }));
    await waitFor(() => expect(listExams).toHaveBeenCalledWith('published'));

    await user.selectOptions(await screen.findByLabelText('Take questions from'), 'e1');

    const ready = (await screen.findByText('Ready round')).closest('button') as HTMLButtonElement;
    const draft = screen.getByText('Draft round').closest('button') as HTMLButtonElement;
    const empty = screen.getByText('Empty round').closest('button') as HTMLButtonElement;

    expect(ready.disabled).toBe(false);
    expect(within(ready).getByText('Published')).toBeTruthy();
    expect(within(ready).getByText('10 questions')).toBeTruthy();

    expect(draft.disabled).toBe(true);
    expect(within(draft).getByText('Draft')).toBeTruthy();
    expect(screen.getByText(/Publish this round in the exam editor first/)).toBeTruthy();

    expect(empty.disabled).toBe(true);
    expect(within(empty).getByText('0 questions')).toBeTruthy();
    expect(screen.getByText(/Add questions to this round in the exam editor first/)).toBeTruthy();
    expect(screen.getAllByRole('link', { name: /Open the exam editor/ })).toHaveLength(2);

    await user.click(draft);
    expect(updateRound).not.toHaveBeenCalled();
  });
});

describe('WorkflowBuilder - starting from nothing', () => {
  const TEMPLATES = [
    {
      key: 'technical',
      name: 'Technical',
      description: 'Aptitude, code, conversation, review.',
      recommended: true,
      rounds: [
        {
          title: 'Aptitude',
          kind: 'mcq',
          pass_threshold: 60,
          time_limit_seconds: 1800,
          deadline_days: 5,
          auto_advance: null,
          competencies: ['Python', 'Software Lifecycle'],
        },
        {
          title: 'Human Review',
          kind: 'human_review',
          pass_threshold: null,
          time_limit_seconds: null,
          deadline_days: 5,
          auto_advance: null,
          competencies: ['Ownership'],
        },
      ],
    },
    {
      key: 'interview_only',
      name: 'Interview only',
      description: 'Straight to the conversation.',
      recommended: false,
      rounds: [
        {
          title: 'AI Interview',
          kind: 'ai_interview',
          pass_threshold: 60,
          time_limit_seconds: null,
          deadline_days: 7,
          auto_advance: null,
          competencies: ['Communication'],
        },
      ],
    },
  ];

  it('offers templates built for this role, marking the one that suits it', async () => {
    listWorkflows.mockResolvedValue([]);
    listWorkflowTemplates.mockResolvedValue(TEMPLATES);
    renderBuilder();

    expect(await screen.findByText('Give this opening a process')).toBeTruthy();
    expect(await screen.findByText('Technical')).toBeTruthy();
    expect(screen.getByText('Suits this role')).toBeTruthy();
    // What it would create, for this role: the competencies each round assesses.
    expect(screen.getByText(/Python, Software Lifecycle/)).toBeTruthy();
    expect(screen.getByText('Start from an empty canvas instead')).toBeTruthy();
  });

  it('says the templates could not load, and still offers the empty canvas', async () => {
    listWorkflows.mockResolvedValue([]);
    listWorkflowTemplates.mockRejectedValue(new Error('offline'));
    renderBuilder();

    expect(await screen.findByText('Could not load the templates for this role.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
    expect(screen.getByText('Start from an empty canvas instead')).toBeTruthy();
  });

  it('creates a template as one draft on the server, not round by round', async () => {
    const user = userEvent.setup();
    listWorkflows.mockResolvedValue([]);
    listWorkflowTemplates.mockResolvedValue(TEMPLATES);
    createFromTemplate.mockResolvedValue({ ...DRAFT });
    renderBuilder();

    await user.click(await screen.findByRole('button', { name: /interview only template/i }));

    await waitFor(() =>
      expect(createFromTemplate).toHaveBeenCalledWith(expect.any(String), 'interview_only'),
    );
    // One transaction: the rounds, their competencies and settings together.
    expect(addRound).not.toHaveBeenCalled();
  });
});

// ── UX round 3: the state-driven primary, the track, the blockers ───────────

const REPORT_READY_FOR_STATES: ValidationReport = { ...REPORT_BLOCKED, publishable: true, errors: [] };

function reviewState(over: Record<string, unknown>) {
  return {
    review_status: 'draft',
    submitted_at: null,
    submitted_by_name: null,
    reviewed_at: null,
    reviewed_by_name: null,
    note: null,
    history: [],
    ...over,
  };
}

const SIM_FRESH = {
  simulation_id: 's1',
  workflow_id: 'wf-draft',
  version: 2,
  fingerprint: 'f',
  created_at: '2026-10-01T00:00:00.000Z',
  stale: false,
  run_by_name: 'Asha',
  status: 'passed',
  errors: 0,
  warnings: 0,
  rounds: [],
  workflow_findings: [],
  scenarios: [],
};

function primaries(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>('[data-primary="true"]'));
}

describe('WorkflowBuilder - one primary action, always the next step', () => {
  it('draft with no dry run: Run dry run', async () => {
    validateWorkflow.mockResolvedValue(REPORT_READY_FOR_STATES);
    renderBuilder();
    await screen.findByText('Fundamentals');
    await waitFor(() => expect(primaries()).toHaveLength(1));
    expect(primaries()[0]).toHaveTextContent('Run dry run');
  });

  it('draft with a fresh dry run: Submit for review', async () => {
    validateWorkflow.mockResolvedValue(REPORT_READY_FOR_STATES);
    getSimulation.mockResolvedValue(SIM_FRESH);
    renderBuilder();
    await screen.findByText('Fundamentals');
    await waitFor(() => expect(primaries()[0]).toHaveTextContent('Submit for review'));
    expect(primaries()).toHaveLength(1);
  });

  it('a stale dry run does not count: Run dry run again', async () => {
    validateWorkflow.mockResolvedValue(REPORT_READY_FOR_STATES);
    getSimulation.mockResolvedValue({ ...SIM_FRESH, stale: true });
    renderBuilder();
    await screen.findByText('Fundamentals');
    await waitFor(() => expect(primaries()).toHaveLength(1));
    expect(primaries()[0]).toHaveTextContent('Run dry run');
  });

  it('an edit makes the dry run stale, so the primary goes back to Run dry run', async () => {
    // Without invalidating the simulation after a round edit, the header kept
    // reading "Submit for review" off a dry run the edit had just outdated.
    const user = userEvent.setup();
    validateWorkflow.mockResolvedValue(REPORT_READY_FOR_STATES);
    getSimulation.mockResolvedValue(SIM_FRESH);
    renderBuilder();
    await screen.findByText('Fundamentals');
    await waitFor(() => expect(primaries()[0]).toHaveTextContent('Submit for review'));

    // The server now reports the stored run as stale.
    getSimulation.mockResolvedValue({ ...SIM_FRESH, stale: true });
    await user.click(screen.getByLabelText('Move Conversation earlier'));

    await waitFor(() => expect(primaries()[0]).toHaveTextContent('Run dry run'));
    expect(primaries()).toHaveLength(1);
  });

  it('changes requested: Submit for review again, with the reviewer note on the track', async () => {
    validateWorkflow.mockResolvedValue(REPORT_READY_FOR_STATES);
    getSimulation.mockResolvedValue(SIM_FRESH);
    getWorkflow.mockResolvedValue({ ...DRAFT, review_status: 'changes_requested' });
    getReview.mockResolvedValue(
      reviewState({
        review_status: 'changes_requested',
        reviewed_by_name: 'Meena',
        reviewed_at: '2026-10-02T00:00:00.000Z',
        note: 'Add a coding round',
      }),
    );
    renderBuilder();
    await screen.findByText('Fundamentals');
    await waitFor(() => expect(primaries()[0]).toHaveTextContent('Submit for review'));
    expect(primaries()).toHaveLength(1);
    const track = screen.getByTestId('workflow-state');
    await waitFor(() => expect(track).toHaveTextContent('Add a coding round'));
    expect(track).toHaveTextContent('Sent back');
  });

  it('in review: no primary, and the track says who it is waiting on', async () => {
    validateWorkflow.mockResolvedValue(REPORT_READY_FOR_STATES);
    getWorkflow.mockResolvedValue({ ...DRAFT, review_status: 'in_review', editable: false });
    renderBuilder();
    await screen.findByText('Fundamentals');
    expect(primaries()).toHaveLength(0);
    expect(screen.getByTestId('workflow-state')).toHaveTextContent('Waiting on a company super admin');
  });

  it('approved: Publish', async () => {
    validateWorkflow.mockResolvedValue(REPORT_READY_FOR_STATES);
    getWorkflow.mockResolvedValue({ ...DRAFT, review_status: 'approved', editable: false });
    renderBuilder();
    await screen.findByText('Fundamentals');
    expect(primaries()).toHaveLength(1);
    expect(primaries()[0]).toHaveTextContent('Publish');
    expect(primaries()[0]).toHaveAttribute('data-testid', 'publish-workflow');
  });

  it('live: Edit as new version', async () => {
    listWorkflows.mockResolvedValue(VERSIONS_LIVE);
    getWorkflow.mockResolvedValue(PUBLISHED);
    renderBuilder();
    await screen.findByText('Fundamentals');
    expect(primaries()).toHaveLength(1);
    expect(primaries()[0]).toHaveTextContent('Edit as new version');
    expect(primaries()[0]).toHaveAttribute('data-testid', 'edit-as-new-version');
  });

  it('live while a draft already exists: no Edit as new version, which the server would refuse', async () => {
    listWorkflows.mockResolvedValue([...VERSIONS_DRAFT, ...VERSIONS_LIVE]);
    getWorkflow.mockImplementation((id: string) =>
      Promise.resolve(id === 'wf-live' ? PUBLISHED : DRAFT),
    );
    const user = userEvent.setup();
    renderBuilder();
    await screen.findByText('Fundamentals');
    // Switch to the live version (the draft is the default).
    await user.selectOptions(screen.getByRole('combobox', { name: 'Workflow version' }), 'wf-live');
    await waitFor(() => expect(screen.getByTestId('workflow-state')).toHaveTextContent('Published'));
    expect(screen.queryByTestId('edit-as-new-version')).not.toBeInTheDocument();
    expect(primaries()).toHaveLength(0);
  });

  it('archived: no primary, and the track says Archived rather than implying it is live', async () => {
    const ARCHIVED = { ...VERSIONS_LIVE[0], id: 'wf-old', version: 1, status: 'archived' as const };
    listWorkflows.mockResolvedValue([{ ...VERSIONS_LIVE[0], id: 'wf-live', version: 2 }, ARCHIVED]);
    getWorkflow.mockImplementation((id: string) =>
      Promise.resolve(
        id === 'wf-old' ? { ...PUBLISHED, id: 'wf-old', status: 'archived' as const } : PUBLISHED,
      ),
    );
    const user = userEvent.setup();
    renderBuilder();
    await screen.findByText('Fundamentals');
    await user.selectOptions(screen.getByRole('combobox', { name: 'Workflow version' }), 'wf-old');
    await waitFor(() =>
      expect(screen.getByTestId('workflow-state')).toHaveTextContent('Archived, no longer live'),
    );
    expect(screen.queryByTestId('edit-as-new-version')).not.toBeInTheDocument();
    expect(primaries()).toHaveLength(0);
  });

  it('an empty draft offers no primary', async () => {
    getWorkflow.mockResolvedValue({ ...DRAFT, rounds: [] });
    renderBuilder();
    await waitFor(() => expect(screen.getByTestId('workflow-state')).toBeTruthy());
    expect(primaries()).toHaveLength(0);
  });
});

describe('WorkflowBuilder - blockers beside the action', () => {
  it('lists the server errors, each linking to its round', async () => {
    const user = userEvent.setup();
    getSimulation.mockResolvedValue(SIM_FRESH);
    renderBuilder();
    await screen.findByText('Fundamentals');

    const blockers = await screen.findByTestId('submit-blockers');
    expect(blockers).toHaveTextContent('Fundamentals: a mcq round needs questions before publishing.');

    await user.click(within(blockers).getByRole('button', { name: 'Go to Fundamentals' }));
    // The round's inspector opens, with its "needs an exam" state and a way out.
    expect(await screen.findByTestId('needs-exam')).toBeTruthy();
    expect(screen.getByRole('link', { name: /Go to Exams/ })).toHaveAttribute(
      'href',
      '/hr/exams?returnTo=' + encodeURIComponent('/hr/requisitions/req-1/workflow'),
    );
  });

  it('opens the submit step with Submit disabled while blockers stand', async () => {
    const user = userEvent.setup();
    getSimulation.mockResolvedValue(SIM_FRESH);
    renderBuilder();
    await screen.findByText('Fundamentals');

    await user.click(await screen.findByRole('button', { name: 'Submit for review' }));
    const review = await screen.findByTestId('review-panel');
    const submit = within(review).getByRole('button', { name: 'Submit for review' });
    expect(submit).toBeDisabled();
    expect(review).toHaveTextContent('Fix 1 issue before submitting.');
    expect(submitForReview).not.toHaveBeenCalled();
  });

  it('shows no blockers once the version is ready', async () => {
    validateWorkflow.mockResolvedValue(REPORT_READY_FOR_STATES);
    renderBuilder();
    await screen.findByText('Fundamentals');
    await waitFor(() => expect(validateWorkflow).toHaveBeenCalled());
    expect(screen.queryByTestId('submit-blockers')).toBeNull();
  });
});

describe('WorkflowBuilder - the opening setup panels have moved', () => {
  it('renders none of the four panels, and points at the opening Setup tab', async () => {
    renderBuilder();
    await screen.findByText('Fundamentals');

    // Posting, JD versions, application questions, budget & approval.
    expect(screen.queryByText(/Job posting/i)).toBeNull();
    expect(screen.queryByText(/Application questions/i)).toBeNull();
    expect(screen.queryByText(/Submit for approval/i)).toBeNull();
    expect(screen.queryByText(/Job description versions/i)).toBeNull();

    const link = screen.getByRole('link', { name: 'Setup tab' });
    expect(link).toHaveAttribute('href', '/hr/requisitions/req-1?tab=setup');
  });
});

// ── Auto-shortlist — removing the human shortlist gate ─────────────────────
//
// The FIRST of the workflow's two human gates became optional (migration
// a2c4e6f8b0d3). These pin the builder's half: the switch needs a bar, the
// switch is genuinely disabled without one (keyboard included), and the canvas
// stops claiming "nothing starts until a person does" once it is no longer true.

async function openAutomation(user: ReturnType<typeof userEvent.setup>) {
  renderBuilder();
  await user.click(await screen.findByRole('tab', { name: 'Automation' }));
}

describe('WorkflowBuilder - auto-shortlist', () => {
  it('is off by default, and says a person confirms every shortlist', async () => {
    const user = userEvent.setup();
    await openAutomation(user);

    const sw = screen.getByRole('switch', { name: 'Shortlist without my confirmation' });
    expect(sw).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByText(/you confirm every shortlist/i)).toBeInTheDocument();
  });

  it('turns on with a bar set, sending exactly that setting', async () => {
    const user = userEvent.setup();
    updateSettings.mockResolvedValue({ ...DRAFT, settings: { ...SETTINGS, auto_shortlist: true } });
    await openAutomation(user);

    await user.click(screen.getByRole('switch', { name: 'Shortlist without my confirmation' }));

    await waitFor(() =>
      expect(updateSettings).toHaveBeenCalledWith('wf-draft', { auto_shortlist: true }),
    );
  });

  it('is GENUINELY disabled without a bar - keyboard included', async () => {
    // The old pattern wrapped switches in pointer-events-none, which stops the
    // mouse and nothing else: Tab + Space still flipped them. Asserted on the
    // disabled attribute AND on a keyboard activation, since either alone would
    // pass against a mouse-only block.
    getWorkflow.mockResolvedValue({
      ...DRAFT,
      settings: { ...SETTINGS, shortlist_ats_threshold: null },
    });
    const user = userEvent.setup();
    await openAutomation(user);

    const sw = screen.getByRole('switch', { name: 'Shortlist without my confirmation' });
    expect(sw).toBeDisabled();
    sw.focus();
    await user.keyboard(' ');
    await user.keyboard('{Enter}');
    expect(updateSettings).not.toHaveBeenCalled();
    expect(screen.getByText(/every applicant would be shortlisted/i)).toBeInTheDocument();
  });

  it('can still be turned OFF even if the bar is gone', async () => {
    // Never trap a setting in the state the server would refuse: on with no bar
    // is unrepresentable, but if one arrived it must be switchable off.
    getWorkflow.mockResolvedValue({
      ...DRAFT,
      settings: { ...SETTINGS, shortlist_ats_threshold: null, auto_shortlist: true },
    });
    const user = userEvent.setup();
    await openAutomation(user);

    expect(
      screen.getByRole('switch', { name: 'Shortlist without my confirmation' }),
    ).not.toBeDisabled();
  });

  it('tells HR what it will do, in numbers, once on', async () => {
    getWorkflow.mockResolvedValue({ ...DRAFT, settings: { ...SETTINGS, auto_shortlist: true } });
    const user = userEvent.setup();
    await openAutomation(user);

    // Bar 7 is 70/100 — the scale the score is actually on.
    expect(
      screen.getByText(/scored 70\/100 or higher and confirmed their email start round one/i),
    ).toBeInTheDocument();
    // Who it does NOT apply to — HR must not read it as "everyone above the bar".
    expect(screen.getByText(/uploaded by HR, or not yet confirmed/i)).toBeInTheDocument();
    expect(screen.getByText(/super admin sees it when approving/i)).toBeInTheDocument();
    expect(screen.getByText('Shortlist automatically at ATS score')).toBeInTheDocument();
  });

  it('the canvas banner no longer claims a person gates the shortlist', async () => {
    // "Nothing below starts until a person does" would be false on the one
    // screen HR uses to understand their own process.
    getWorkflow.mockResolvedValue({ ...DRAFT, settings: { ...SETTINGS, auto_shortlist: true } });
    renderBuilder();

    const gate = await screen.findByTestId('shortlist-gate');
    expect(gate).toHaveAttribute('data-mode', 'auto');
    expect(gate).toHaveTextContent(/shortlisted automatically/i);
    expect(gate).toHaveTextContent(/70\/100/);
    expect(gate).not.toHaveTextContent(/until a person does/i);
  });

  it('the canvas banner keeps the human gate when it is off', async () => {
    renderBuilder();
    const gate = await screen.findByTestId('shortlist-gate');
    expect(gate).toHaveAttribute('data-mode', 'manual');
    expect(gate).toHaveTextContent(/you shortlist/i);
  });
});

// ── HR-H2 / HR-L7: number settings save once, on the console's ATS scale ────

describe('WorkflowBuilder - automation number fields', () => {
  it('shows the ATS bar out of 100 and saves it once, on blur, in tenths', async () => {
    const user = userEvent.setup();
    updateSettings.mockResolvedValue({ ...DRAFT, settings: { ...SETTINGS, shortlist_ats_threshold: 8 } });
    await openAutomation(user);

    const field = screen.getByLabelText('Suggest shortlisting at ATS score');
    expect(field).toHaveValue(70);
    expect(screen.getByText('/ 100')).toBeInTheDocument();
    await user.clear(field);
    await user.type(field, '80');
    // Nothing is sent while typing: "8" on the way to "80" never reaches the server.
    expect(updateSettings).not.toHaveBeenCalled();
    await user.tab();

    await waitFor(() =>
      expect(updateSettings).toHaveBeenCalledWith('wf-draft', { shortlist_ats_threshold: 8 }),
    );
    expect(updateSettings).toHaveBeenCalledTimes(1);
  });

  it('refuses a value off the scale before saving it', async () => {
    const user = userEvent.setup();
    await openAutomation(user);

    const field = screen.getByLabelText('Suggest shortlisting at ATS score');
    await user.clear(field);
    await user.type(field, '85{Enter}');

    expect(await screen.findByText(/enter 10, 20 and so on up to 100/i)).toBeInTheDocument();
    expect(field).toHaveAttribute('aria-invalid', 'true');
    expect(updateSettings).not.toHaveBeenCalled();
  });

  it('lets the near-miss band be cleared to retype, and saves only the final value', async () => {
    const user = userEvent.setup();
    await openAutomation(user);

    const field = screen.getByLabelText('Mark near misses within');
    await user.clear(field);
    await user.type(field, '15');
    await user.keyboard('{Enter}');

    await waitFor(() => expect(updateSettings).toHaveBeenCalledWith('wf-draft', { hold_band: 15 }));
    expect(updateSettings).toHaveBeenCalledTimes(1);
  });

  it('puts the saved value back when the server refuses the save', async () => {
    const user = userEvent.setup();
    updateSettings.mockRejectedValueOnce(new Error('hold_band refused'));
    await openAutomation(user);

    const field = screen.getByLabelText('Mark near misses within');
    await user.clear(field);
    await user.type(field, '15');
    await user.keyboard('{Enter}');

    await waitFor(() => expect(updateSettings).toHaveBeenCalledWith('wf-draft', { hold_band: 15 }));
    // The saved band is still 10; the field must not keep showing 15 as if kept.
    await waitFor(() => expect(field).toHaveValue(10));
  });
});

// ── Review fixes: the toggle mirrors every rule the server enforces ─────────
//
// The server refuses auto-shortlist without a bar of at least 1, and without
// "Send the first round automatically" on. A switch that let HR try either
// would just produce a failed save — so the UI states the rule and disables
// the switch instead.

describe('WorkflowBuilder - auto-shortlist follows the server rules', () => {
  it('is disabled while first-round auto-assign is off, and says why', async () => {
    // Shortlisting automatically and then starting nothing would strand the
    // applicant: the shortlist was the step a person used to see.
    getWorkflow.mockResolvedValue({
      ...DRAFT,
      settings: { ...SETTINGS, auto_assign_first_round: false },
    });
    const user = userEvent.setup();
    await openAutomation(user);

    expect(
      screen.getByRole('switch', { name: 'Shortlist without my confirmation' }),
    ).toBeDisabled();
    expect(screen.getByText(/left with nothing started/i)).toBeInTheDocument();
  });

  it('treats a bar of 0 as no bar', async () => {
    // `score >= 0` admits everyone — including an applicant the scorer failed
    // on and recorded as 0.
    getWorkflow.mockResolvedValue({
      ...DRAFT,
      settings: { ...SETTINGS, shortlist_ats_threshold: 0 },
    });
    const user = userEvent.setup();
    await openAutomation(user);

    expect(
      screen.getByRole('switch', { name: 'Shortlist without my confirmation' }),
    ).toBeDisabled();
    expect(screen.getByText(/at least 1/i)).toBeInTheDocument();
  });

  it('the candidate preview tells HR what an applicant above the bar will see', async () => {
    getWorkflow.mockResolvedValue({ ...DRAFT, settings: { ...SETTINGS, auto_shortlist: true } });
    const user = userEvent.setup();
    renderBuilder();
    await user.click(await screen.findByRole('button', { name: 'Preview as a candidate' }));

    // The preview used to promise "nothing more happens until you shortlist
    // them" whatever the setting — false on an auto-shortlisting version.
    expect(await screen.findByText(/start round one on their own/i)).toBeInTheDocument();
  });
});

describe('WorkflowBuilder - discarding a draft (HR-M2)', () => {
  it('asks before deleting the draft', async () => {
    const user = userEvent.setup();
    discardDraft.mockResolvedValue(undefined);
    renderBuilder();
    await screen.findByText('Fundamentals');

    await user.click(screen.getByRole('button', { name: 'Discard draft' }));
    expect(discardDraft).not.toHaveBeenCalled();
    const armed = screen.getByRole('group', { name: 'Confirm: Discard draft' });
    await user.click(within(armed).getByRole('button', { name: 'Discard draft' }));
    await waitFor(() => expect(discardDraft).toHaveBeenCalledWith('wf-draft'));
  });
});

describe('WorkflowBuilder - a failed check is not an empty one', () => {
  it('says the coverage check failed instead of asking for a round', async () => {
    validateWorkflow.mockRejectedValue(new Error('offline'));
    renderBuilder();
    await screen.findByText('Fundamentals');
    expect(await screen.findByText('Could not check what this process measures.')).toBeTruthy();
    expect(screen.queryByText('Add a round to see what this process measures.')).toBeNull();
  });
});
