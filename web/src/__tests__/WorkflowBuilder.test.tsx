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
import type {
  RoleModel,
  ValidationReport,
  Workflow,
  WorkflowSummary,
} from '../api/workflows';

const SETTINGS = {
  auto_score_on_apply: true,
  auto_assign_first_round: true,
  auto_advance_rounds: true,
  reminders_enabled: true,
  shortlist_ats_threshold: 7,
  hold_band: 10,
};

const DRAFT: Workflow = {
  id: 'wf-draft',
  requisition_id: 'req-1',
  version: 2,
  status: 'draft',
  name: 'Technical hire',
  editable: true,
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
      on_pass_next_round_id: 'r-ai',
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
      on_pass_next_round_id: null,
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
const publishWorkflow = vi.fn();
const cloneWorkflow = vi.fn();
const discardDraft = vi.fn();

vi.mock('../api/workflows', () => ({
  MAX_ROUNDS: 12,
  MAX_CRITERIA_PER_ROUND: 8,
  EXAM_BACKED_KINDS: ['mcq', 'coding'],
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
  publishWorkflow: (...a: unknown[]) => publishWorkflow(...a) as unknown,
  cloneWorkflow: (...a: unknown[]) => cloneWorkflow(...a) as unknown,
  discardDraft: (...a: unknown[]) => discardDraft(...a) as unknown,
}));

const getRequisition = vi.fn();
// Spread the real module rather than replacing it: the page also uses
// updateRequisition and EMPLOYMENT_TYPE_LABELS now, and a bare factory blanks
// everything it does not list.
vi.mock('../api/requisitions', async () => {
  const actual = await vi.importActual<typeof import('../api/requisitions')>(
    '../api/requisitions',
  );
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
  const actual = await vi.importActual<typeof import('../api/questions')>(
    '../api/questions',
  );
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


vi.mock('../api/exams', () => ({
  listExams: () => Promise.resolve([]),
  getStructure: () => Promise.resolve({ exam_id: 'e1', rounds: [] }),
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
});

describe('WorkflowBuilder — the canvas', () => {
  it('renders the rounds in order with what each still needs', async () => {
    renderBuilder();
    await screen.findByText('Fundamentals');

    expect(screen.getByText('Conversation')).toBeTruthy();
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

describe('WorkflowBuilder — a published version is immutable', () => {
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
    expect(toastSuccess).toHaveBeenCalledWith(
      'Editing as version 2 — version 1 stays live',
    );
  });
});

describe('WorkflowBuilder — freezing what a round assesses', () => {
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

describe('WorkflowBuilder — coverage', () => {
  it('names the competency nothing measures', async () => {
    renderBuilder();
    await screen.findByText('Fundamentals');

    expect(await screen.findByText('1 thing to fix before this can go live.')).toBeTruthy();
    const covered = screen.getByText('Communication').closest('li') as HTMLElement;
    expect(within(covered).getByText('not assessed')).toBeTruthy();
  });
});

describe('WorkflowBuilder — publishing', () => {
  it('asks before publishing, because publishing emails real people', async () => {
    const user = userEvent.setup();
    renderBuilder();
    await screen.findByText('Fundamentals');

    await user.click(screen.getByText('Publish'));

    expect(publishWorkflow).not.toHaveBeenCalled();
    expect(screen.getByText('Publish version 2')).toBeTruthy();

    await user.click(screen.getByText('Publish version 2'));
    await waitFor(() => expect(publishWorkflow).toHaveBeenCalledWith('wf-draft'));
  });

  it('rechecks so a refusal lands in the panel rather than a toast that scrolls away', async () => {
    const user = userEvent.setup();
    publishWorkflow.mockRejectedValue(new Error('This workflow is not ready to publish'));
    renderBuilder();
    await screen.findByText('Fundamentals');

    const before = validateWorkflow.mock.calls.length;
    await user.click(screen.getByText('Publish'));
    await user.click(screen.getByText('Publish version 2'));

    await waitFor(() => expect(validateWorkflow.mock.calls.length).toBeGreaterThan(before));
    expect(toastError).toHaveBeenCalledWith('This workflow is not ready to publish');
  });
});

describe('WorkflowBuilder — starting from nothing', () => {
  it('offers templates and a blank canvas when the opening has no workflow', async () => {
    listWorkflows.mockResolvedValue([]);
    renderBuilder();

    expect(await screen.findByText('Give this opening a process')).toBeTruthy();
    expect(screen.getByText('Technical hire')).toBeTruthy();
    expect(screen.getByText('Start from an empty canvas instead')).toBeTruthy();
  });

  it('builds a template one round at a time, in order', async () => {
    const user = userEvent.setup();
    listWorkflows.mockResolvedValue([]);
    startDraft.mockResolvedValue({ ...DRAFT, rounds: [] });
    renderBuilder();
    await screen.findByText('Give this opening a process');

    await user.click(screen.getByText('Interview only'));

    await waitFor(() => expect(addRound).toHaveBeenCalledTimes(2));
    // Sequential, and in the template's order — positions come from insert
    // order, so a parallel fan-out would race for them.
    expect(addRound.mock.calls[0][1]).toMatchObject({ kind: 'ai_interview' });
    expect(addRound.mock.calls[1][1]).toMatchObject({ kind: 'human_review' });
  });
});
