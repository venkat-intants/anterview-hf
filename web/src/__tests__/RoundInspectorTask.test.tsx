// RoundInspector — PH4-D4, the workflow builder accepting the two new round
// kinds (job_simulation, portfolio): the kind picker offers them, and
// choosing one swaps in TaskEditor — no exam picker, no interview kit, and
// (for a portfolio) its own artifact settings.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Round } from '../api/workflows';
import type { TaskConfig } from '../api/jobTasks';

const jobTasksApi = {
  getRoundTask: vi.fn(),
  putRoundTask: vi.fn(),
  listRoundTaskMaterials: vi.fn(),
  addRoundTaskMaterial: vi.fn(),
  removeRoundTaskMaterial: vi.fn(),
  downloadRoundTaskMaterial: vi.fn(),
};
vi.mock('../api/jobTasks', async () => {
  const actual = await vi.importActual<typeof import('../api/jobTasks')>('../api/jobTasks');
  return {
    ...actual,
    getRoundTask: (...a: unknown[]) => jobTasksApi.getRoundTask(...a) as unknown,
    putRoundTask: (...a: unknown[]) => jobTasksApi.putRoundTask(...a) as unknown,
    listRoundTaskMaterials: (...a: unknown[]) =>
      jobTasksApi.listRoundTaskMaterials(...a) as unknown,
    addRoundTaskMaterial: (...a: unknown[]) => jobTasksApi.addRoundTaskMaterial(...a) as unknown,
    removeRoundTaskMaterial: (...a: unknown[]) =>
      jobTasksApi.removeRoundTaskMaterial(...a) as unknown,
    downloadRoundTaskMaterial: (...a: unknown[]) =>
      jobTasksApi.downloadRoundTaskMaterial(...a) as unknown,
  };
});
vi.mock('../api/exams', () => ({
  listExams: vi.fn(() => Promise.resolve([])),
  getStructure: vi.fn(() => Promise.resolve(null)),
}));
vi.mock('../api/scorecards', () => ({
  getRoundKit: vi.fn(),
  updateRoundKit: vi.fn(),
}));
vi.mock('../api/stageSla', () => ({
  getStages: vi.fn(() => Promise.resolve([])),
  listStageOwners: vi.fn(() => Promise.resolve([])),
  setStage: vi.fn(),
}));
vi.mock('../lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import RoundInspector from '../components/workflow/RoundInspector';

function baseRound(over: Partial<Round> = {}): Round {
  return {
    id: 'round-1',
    position: 0,
    title: 'Take-home simulation',
    kind: 'job_simulation',
    pass_threshold: null,
    time_limit_seconds: null,
    deadline_days: 7,
    on_pass_next_round_id: null,
    on_fail_next_round_id: null,
    fast_track_min_percent: null,
    on_fast_track_next_round_id: null,
    exam_round_id: null,
    needs_questions: false,
    criteria: [],
    ...over,
  };
}

function renderInspector(round: Round, editable = true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <RoundInspector
          round={round}
          allRounds={[round]}
          workflowId="wf-1"
          roleModel={undefined}
          editable={editable}
          saving={false}
          onPatch={vi.fn()}
          onCriteria={vi.fn()}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const EMPTY_CONFIG: TaskConfig | null = null;

beforeEach(() => {
  vi.clearAllMocks();
  jobTasksApi.getRoundTask.mockResolvedValue(EMPTY_CONFIG);
  jobTasksApi.listRoundTaskMaterials.mockResolvedValue([]);
});

describe('RoundInspector — round kind picker', () => {
  it('offers job simulation and portfolio as round kinds', () => {
    renderInspector(baseRound());
    const select = screen.getByLabelText('Round type');
    expect(within(select).getByText('Job simulation')).toBeInTheDocument();
    expect(within(select).getByText('Portfolio')).toBeInTheDocument();
  });
});

describe('RoundInspector — TaskEditor for job_simulation', () => {
  it('shows the task editor instead of an exam picker or an interview kit', async () => {
    renderInspector(baseRound({ kind: 'job_simulation' }));

    expect(await screen.findByLabelText('Brief')).toBeInTheDocument();
    expect(screen.queryByText('Take questions from')).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/instructions for interviewers/i)).not.toBeInTheDocument();
  });

  it('saves the brief and items', async () => {
    const user = userEvent.setup();
    jobTasksApi.putRoundTask.mockResolvedValue({
      round_id: 'round-1',
      kind: 'job_simulation',
      brief: 'Design a feature.',
      brief_translations: null,
      items: [],
      min_artifacts: null,
      max_artifacts: null,
      allow_files: true,
      allow_links: true,
      allowed_link_domains: null,
    });
    renderInspector(baseRound({ kind: 'job_simulation' }));

    const brief = await screen.findByLabelText('Brief');
    await user.type(brief, 'Design a feature.');

    // A job simulation needs at least one item — the server refuses to save
    // one with none, and Save stays disabled client-side for the same reason.
    const saveButton = screen.getByRole('button', { name: /save task/i });
    expect(saveButton).toBeDisabled();
    await user.click(screen.getByRole('button', { name: /add item/i }));
    await user.type(screen.getByLabelText('Prompt'), 'Describe your approach.');
    expect(saveButton).toBeEnabled();

    await user.click(saveButton);

    await waitFor(() => expect(jobTasksApi.putRoundTask).toHaveBeenCalledTimes(1));
    const [roundId, body] = jobTasksApi.putRoundTask.mock.calls[0] as [string, { brief: string }];
    expect(roundId).toBe('round-1');
    expect(body.brief).toBe('Design a feature.');
  });

  it('never offers a "file" answer type, which no candidate endpoint can fulfil', async () => {
    renderInspector(baseRound({ kind: 'job_simulation' }));
    await screen.findByLabelText('Brief');
    await userEvent.setup().click(screen.getByRole('button', { name: /add item/i }));

    const typeSelect = await screen.findByLabelText('Answer type');
    expect(within(typeSelect).queryByText(/file/i)).not.toBeInTheDocument();
  });
});

describe('RoundInspector — TaskEditor for portfolio', () => {
  it('shows artifact settings a job simulation does not', async () => {
    renderInspector(baseRound({ kind: 'portfolio', title: 'Design portfolio' }));

    expect(await screen.findByText('Portfolio settings')).toBeInTheDocument();
    expect(screen.getByText('Minimum artifacts')).toBeInTheDocument();
    expect(screen.getByText('Maximum artifacts')).toBeInTheDocument();
  });
});

describe('RoundInspector — a published (non-editable) task round', () => {
  it('is read-only and offers no save', async () => {
    jobTasksApi.getRoundTask.mockResolvedValue({
      round_id: 'round-1',
      kind: 'job_simulation',
      brief: 'Design a feature.',
      brief_translations: null,
      items: [],
      min_artifacts: null,
      max_artifacts: null,
      allow_files: true,
      allow_links: true,
      allowed_link_domains: null,
    });
    renderInspector(baseRound({ kind: 'job_simulation' }), false);

    const brief = await screen.findByLabelText('Brief');
    expect(brief).toBeDisabled();
    expect(screen.queryByRole('button', { name: /save task/i })).not.toBeInTheDocument();
    expect(
      screen.getByText(/is fixed — its task configuration cannot change here/i),
    ).toBeInTheDocument();
  });
});
