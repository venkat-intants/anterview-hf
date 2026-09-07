// Tests for the workflow design copilot (D6) and its canvas preview (D7).
//
// This is the one place in the product where a language model shapes something
// that will later be applied to real candidates, so the properties worth
// pinning are all about the gap between "the model said so" and "it happened":
//
//   • the conversation's SUBJECT is sent as surface context, not chosen by the
//     model — the copilot has no way to design a workflow for another opening;
//   • a proposal is previewed, never applied. Nothing commits on arrival, on
//     mount, or on render;
//   • applying fires exactly the proposal's own commit, unedited;
//   • the UI says that applying produces a DRAFT. An HR manager must not be
//     able to conclude from this screen that a chat put a live process in
//     front of candidates;
//   • a published version offers no copilot input at all, because there is
//     nothing it could change.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AgentChatResponse, Proposal } from '../api/agent';
import type { Workflow, WorkflowSummary } from '../api/workflows';

const SETTINGS = {
  auto_score_on_apply: true,
  auto_assign_first_round: true,
  auto_advance_rounds: true,
  reminders_enabled: true,
  shortlist_ats_threshold: 7,
  hold_band: 10,
};

const EMPTY_DRAFT: Workflow = {
  id: 'wf-draft',
  requisition_id: 'req-1',
  version: 1,
  status: 'draft',
  name: null,
  editable: true,
  role_profile_id: null,
  settings: SETTINGS,
  published_at: null,
  rounds: [],
};

const PUBLISHED: Workflow = { ...EMPTY_DRAFT, id: 'wf-live', status: 'published', editable: false };

const VERSIONS: WorkflowSummary[] = [
  {
    id: 'wf-draft',
    version: 1,
    status: 'draft',
    name: null,
    rounds: 0,
    enrolled_candidates: 0,
    published_at: null,
    created_at: '2026-09-01T00:00:00.000Z',
  },
];

/** A whole-process proposal, exactly as the backend emits one. */
const WORKFLOW_PROPOSAL: Proposal = {
  id: 'p-1',
  kind: 'workflow',
  title: 'Hiring process — Backend Engineer',
  summary: '2 rounds · Screening test (60%) → Conversation (60%)',
  commit: {
    method: 'POST',
    path: '/hr/requisitions/req-1/workflows/apply',
    body: {
      name: 'Screen, then interview',
      rounds: [
        {
          title: 'Screening test',
          kind: 'mcq',
          pass_threshold: 60,
          deadline_days: 7,
          criteria: [{ id: 'python', name: 'Python proficiency' }],
        },
        {
          title: 'Conversation',
          kind: 'ai_interview',
          pass_threshold: 60,
          deadline_days: 7,
          criteria: [{ id: 'comms', name: 'Communication' }],
        },
      ],
    },
    label: 'Add to canvas',
  },
  rationale: 'A written screen sizes the field before anyone spends an hour on a call.',
  citations: [],
  risk_note: null,
  created_at: '2026-09-02T00:00:00.000Z',
};

const CHAT_REPLY: AgentChatResponse = {
  agent: 'workflow_copilot',
  reply: 'Two rounds. I left Reliability unmeasured — no round covers it.',
  proposals: [WORKFLOW_PROPOSAL],
  citations: [],
  tools_used: [{ name: 'get_opening_under_design', ok: true, duration_ms: 12 }],
  stop_reason: 'completed',
};

const askAgent = vi.fn();
const getAgentStatus = vi.fn();
const commitProposal = vi.fn();
vi.mock('../api/agent', () => ({
  askAgent: (...a: unknown[]) => askAgent(...a) as unknown,
  getAgentStatus: (...a: unknown[]) => getAgentStatus(...a) as unknown,
  commitProposal: (...a: unknown[]) => commitProposal(...a) as unknown,
}));

const listWorkflows = vi.fn();
const getWorkflow = vi.fn();
const validateWorkflow = vi.fn();
const getRoleModel = vi.fn();
vi.mock('../api/workflows', () => ({
  MAX_ROUNDS: 12,
  MAX_CRITERIA_PER_ROUND: 8,
  EXAM_BACKED_KINDS: ['mcq', 'coding'],
  listWorkflows: (...a: unknown[]) => listWorkflows(...a) as unknown,
  getWorkflow: (...a: unknown[]) => getWorkflow(...a) as unknown,
  validateWorkflow: (...a: unknown[]) => validateWorkflow(...a) as unknown,
  getRoleModel: (...a: unknown[]) => getRoleModel(...a) as unknown,
  addRound: vi.fn(),
  updateRound: vi.fn(),
  removeRound: vi.fn(),
  reorderRounds: vi.fn(),
  setRoundCriteria: vi.fn(),
  updateSettings: vi.fn(),
  startDraft: vi.fn(),
  publishWorkflow: vi.fn(),
  cloneWorkflow: vi.fn(),
  discardDraft: vi.fn(),
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

function panel(): HTMLElement {
  return screen.getByLabelText('Ask the design assistant').closest('div') as HTMLElement;
}

async function ask(text: string): Promise<void> {
  const user = userEvent.setup();
  const box = await screen.findByLabelText('Ask the design assistant');
  await user.type(box, text);
  await user.click(screen.getByLabelText('Send'));
}

beforeEach(() => {
  vi.clearAllMocks();
  getAgentStatus.mockResolvedValue({
    enabled: true,
    model_configured: true,
    console: 'hr_manager',
    surfaces: ['workflow_builder'],
    capabilities: ['read', 'draft'],
    note: '',
  });
  askAgent.mockResolvedValue(CHAT_REPLY);
  commitProposal.mockResolvedValue({});
  getRequisition.mockResolvedValue({
    id: 'req-1',
    title: 'Backend Engineer',
    total_enrolments: 4,
    awaiting_decision: 0,
  });
  listWorkflows.mockResolvedValue(VERSIONS);
  getWorkflow.mockResolvedValue(EMPTY_DRAFT);
  validateWorkflow.mockResolvedValue({
    publishable: false,
    errors: ['A workflow needs at least one round.'],
    warnings: [],
    coverage: [],
  });
  getRoleModel.mockResolvedValue({
    job_title: 'Backend Engineer',
    domain_family: 'software',
    domain_label: 'Software & IT',
    source: 'taxonomy',
    competencies: [],
  });
});

describe('WorkflowCopilot — what it sends', () => {
  it('names the opening as surface context, so the model never picks one', async () => {
    renderBuilder();
    await ask('design something short');

    await waitFor(() => expect(askAgent).toHaveBeenCalledTimes(1));
    expect(askAgent).toHaveBeenCalledWith('design something short', [], {
      surface: 'workflow_builder',
      surfaceContext: { requisition_id: 'req-1' },
    });
  });

  it('replays only the prose, not previously drafted proposals', async () => {
    renderBuilder();
    await ask('first');
    await waitFor(() => expect(askAgent).toHaveBeenCalledTimes(1));
    await ask('second');

    await waitFor(() => expect(askAgent).toHaveBeenCalledTimes(2));
    const history = askAgent.mock.calls[1][1] as { role: string; text: string }[];
    expect(history).toEqual([
      { role: 'user', text: 'first' },
      { role: 'assistant', text: CHAT_REPLY.reply },
    ]);
  });

  it('is offered only when the deployment actually has this assistant', async () => {
    getAgentStatus.mockResolvedValue({
      enabled: true,
      model_configured: true,
      console: 'hr_manager',
      surfaces: [],
      capabilities: ['read', 'draft'],
      note: '',
    });
    renderBuilder();
    await screen.findByText('Backend Engineer');

    expect(screen.queryByLabelText('Ask the design assistant')).toBeNull();
  });
});

describe('ProposalPreview — the gate', () => {
  it('previews the drafted rounds instead of applying them', async () => {
    renderBuilder();
    await ask('design something short');

    const preview = await screen.findByLabelText('Assistant preview');
    expect(within(preview).getByText('Screening test')).toBeTruthy();
    expect(within(preview).getByText('Conversation')).toBeTruthy();
    // The whole point: arriving is not applying.
    expect(commitProposal).not.toHaveBeenCalled();
  });

  it('says the result is a draft nobody can see yet', async () => {
    renderBuilder();
    await ask('design something short');

    const preview = await screen.findByLabelText('Assistant preview');
    expect(within(preview).getByText(/No candidate sees a draft/)).toBeTruthy();
  });

  it('shows what each round would assess and its threshold', async () => {
    renderBuilder();
    await ask('design something short');

    const preview = await screen.findByLabelText('Assistant preview');
    expect(within(preview).getAllByText(/advance at 60%/)).toHaveLength(2);
    expect(within(preview).getByText('Python proficiency')).toBeTruthy();
  });

  it('fires exactly the proposal it was given, unedited', async () => {
    const user = userEvent.setup();
    renderBuilder();
    await ask('design something short');

    const preview = await screen.findByLabelText('Assistant preview');
    await user.click(within(preview).getByText('Add to canvas'));

    await waitFor(() => expect(commitProposal).toHaveBeenCalledTimes(1));
    expect(commitProposal).toHaveBeenCalledWith(WORKFLOW_PROPOSAL);
  });

  it('discards without sending anything', async () => {
    const user = userEvent.setup();
    renderBuilder();
    await ask('design something short');

    const preview = await screen.findByLabelText('Assistant preview');
    await user.click(within(preview).getByText('Discard'));

    await waitFor(() => expect(screen.queryByLabelText('Assistant preview')).toBeNull());
    expect(commitProposal).not.toHaveBeenCalled();
  });

  it('keeps the preview and explains a failed apply, rather than resetting', async () => {
    const user = userEvent.setup();
    commitProposal.mockRejectedValue(
      new Error('This opening already has a draft workflow.'),
    );
    renderBuilder();
    await ask('design something short');

    const preview = await screen.findByLabelText('Assistant preview');
    await user.click(within(preview).getByText('Add to canvas'));

    expect(
      await within(preview).findByText('This opening already has a draft workflow.'),
    ).toBeTruthy();
    expect(screen.getByLabelText('Assistant preview')).toBeTruthy();
  });
});

describe('WorkflowCopilot — a published version', () => {
  it('cannot be talked into changing, because there is nothing to change', async () => {
    listWorkflows.mockResolvedValue([{ ...VERSIONS[0], id: 'wf-live', status: 'published' }]);
    getWorkflow.mockResolvedValue(PUBLISHED);
    renderBuilder();

    const box = await screen.findByLabelText('Ask the design assistant');
    expect(box).toHaveProperty('disabled', true);
    expect(within(panel()).getByPlaceholderText(/clone to edit/)).toBeTruthy();
  });
});
