// RoundInspector — the interview kit editor (PH4-A5).
//
// The one rule worth pinning here: a kit stays editable after the workflow is
// published. Criteria are frozen with the version; the kit is guidance for the
// people interviewing candidates on that version, and the backend accepts kit
// writes on a published workflow for exactly that reason. The first build
// disabled the editor with the rest of a published round, which meant HR could
// not change a kit for anyone already in the pipeline.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { InterviewKit } from '../api/interviewer';
import type { Round } from '../api/workflows';

const api = {
  getRoundKit: vi.fn(),
  updateRoundKit: vi.fn(),
};
vi.mock('../api/scorecards', () => ({
  getRoundKit: (...a: unknown[]) => api.getRoundKit(...a) as unknown,
  updateRoundKit: (...a: unknown[]) => api.updateRoundKit(...a) as unknown,
}));
vi.mock('../api/exams', () => ({
  listExams: vi.fn(() => Promise.resolve([])),
  getStructure: vi.fn(() => Promise.resolve(null)),
}));
vi.mock('../api/stageSla', () => ({
  getStages: vi.fn(() => Promise.resolve([])),
  listStageOwners: vi.fn(() => Promise.resolve([])),
  setStage: vi.fn(),
}));
vi.mock('../lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import RoundInspector from '../components/workflow/RoundInspector';

const ROUND: Round = {
  id: 'round-1',
  position: 0,
  title: 'Technical interview',
  kind: 'human_review',
  pass_threshold: null,
  time_limit_seconds: null,
  deadline_days: 7,
  auto_advance: null,
  on_pass_next_round_id: null,
  on_fail_next_round_id: null,
  fast_track_min_percent: null,
  on_fast_track_next_round_id: null,
  exam_round_id: null,
  needs_questions: false,
  criteria: [
    {
      id: 'system_design',
      name: 'System Design',
      kind: null,
      weight: 1,
      anchors: null,
      probes: ['Why this approach?'],
    },
  ],
};

const KIT: InterviewKit = {
  round_title: 'Technical interview',
  instructions: '45 minutes.',
  interviewer_notes_from_hr: null,
  has_custom_kit: true,
  updated_at: null,
  criteria: [
    {
      competency_id: 'system_design',
      competency_name: 'System Design',
      weight: 1,
      anchors: null,
      frozen_probes: ['Why this approach?'],
      what_to_evaluate: [],
      look_for: [],
      probes: [],
    },
  ],
};

function renderInspector(editable: boolean) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <RoundInspector
          round={ROUND}
          allRounds={[ROUND]}
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

describe('RoundInspector - interview kit', () => {
  beforeEach(() => {
    api.getRoundKit.mockReset().mockResolvedValue(KIT);
    api.updateRoundKit.mockReset().mockResolvedValue(KIT);
  });

  it('keeps the kit editable on a published (non-editable) workflow', async () => {
    renderInspector(false);
    const instructions = await screen.findByLabelText(/instructions for interviewers/i);
    await waitFor(() => expect(instructions).toHaveValue('45 minutes.'));
    expect(instructions).toBeEnabled();

    await userEvent.clear(instructions);
    await userEvent.type(instructions, '60 minutes.');
    await userEvent.click(screen.getByRole('button', { name: /save interview kit/i }));

    await waitFor(() => expect(api.updateRoundKit).toHaveBeenCalledTimes(1));
    const [roundId, body] = api.updateRoundKit.mock.calls[0] as [string, { instructions: string }];
    expect(roundId).toBe('round-1');
    expect(body.instructions).toBe('60 minutes.');
  });

  it('copies the server limits: 8000 characters of instructions and notes', async () => {
    renderInspector(false);
    const instructions = await screen.findByLabelText(/instructions for interviewers/i);
    expect(instructions).toHaveAttribute('maxLength', '8000');
    expect(screen.getByLabelText(/notes for interviewers/i)).toHaveAttribute('maxLength', '8000');
  });

  it('says a list is over 10 lines or 300 characters beside it, and holds Save', async () => {
    renderInspector(false);
    const evaluate = await screen.findByLabelText(/what to evaluate/i);
    const save = screen.getByRole('button', { name: /save interview kit/i });
    expect(screen.getAllByText(/up to 10 lines of 300 characters/).length).toBeGreaterThan(0);

    const eleven = Array.from({ length: 11 }, (_, i) => `Point ${i + 1}`).join('\n');
    fireEvent.change(evaluate, { target: { value: eleven } });
    expect(screen.getByText('Keep it to 10 lines. This has 11.')).toBeInTheDocument();
    expect(save).toBeDisabled();

    // Blank lines are dropped before sending, so they do not count.
    const tenWithBlanks = Array.from({ length: 10 }, (_, i) => `Point ${i + 1}`).join('\n\n');
    fireEvent.change(evaluate, { target: { value: tenWithBlanks } });
    expect(screen.queryByText(/Keep it to 10 lines/)).toBeNull();
    expect(save).toBeEnabled();

    fireEvent.change(evaluate, { target: { value: `ok\n${'x'.repeat(301)}` } });
    expect(screen.getByText('Line 2 is over 300 characters.')).toBeInTheDocument();
    expect(save).toBeDisabled();

    // The line is named by where it sits in the textarea, blanks included:
    // here it is the third line the author sees, not the second one sent.
    fireEvent.change(evaluate, {
      target: {
        value: `ok

${'x'.repeat(301)}`,
      },
    });
    expect(screen.getByText('Line 3 is over 300 characters.')).toBeInTheDocument();
    expect(save).toBeDisabled();
    expect(api.updateRoundKit).not.toHaveBeenCalled();
  });

  it('never offers the kit on a round a person does not interview', () => {
    api.getRoundKit.mockClear();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <RoundInspector
            round={{ ...ROUND, kind: 'mcq' }}
            allRounds={[{ ...ROUND, kind: 'mcq' }]}
            workflowId="wf-1"
            roleModel={undefined}
            editable={false}
            saving={false}
            onPatch={vi.fn()}
            onCriteria={vi.fn()}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.queryByLabelText(/instructions for interviewers/i)).toBeNull();
    expect(api.getRoundKit).not.toHaveBeenCalled();
  });
});
