// RoundInspector — "After a candidate passes", the per-round auto-advance toggle.
//
// This control had no test at all, which is how its explanatory copy came to
// describe a product that did not exist: the first wording promised HR "you are
// notified, and the candidate waits until you release them onward" when the
// runner notified nobody and a release moved nobody.
//
// That copy is load-bearing, not decoration. HR chooses "Hold for my review"
// BECAUSE of what this paragraph says will happen, on rounds that real
// candidates are sitting in. So the wording is asserted here the way behaviour
// is, and the three-state control is asserted to stay three-state: NULL means
// "follow the workflow", which is a genuinely different answer from "automatic"
// — collapsing them would freeze every existing round at whatever the workflow
// said on migration day.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Round } from '../api/workflows';

vi.mock('../api/scorecards', () => ({
  getRoundKit: vi.fn(() => Promise.resolve(null)),
  updateRoundKit: vi.fn(),
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

function round(over: Partial<Round> = {}): Round {
  return {
    id: 'round-1', position: 0, title: 'Trade test', kind: 'mcq', pass_threshold: 60,
    time_limit_seconds: null, deadline_days: 7, auto_advance: null,
    on_pass_next_round_id: null, on_fail_next_round_id: null,
    fast_track_min_percent: null, on_fast_track_next_round_id: null,
    exam_round_id: 'e1', needs_questions: false, criteria: [],
    ...over,
  };
}

function renderInspector(r: Round, onPatch = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <RoundInspector
          round={r}
          allRounds={[r]}
          workflowId="wf-1"
          roleModel={undefined}
          editable
          saving={false}
          onPatch={onPatch}
          onCriteria={vi.fn()}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return onPatch;
}

function select(): HTMLSelectElement {
  return screen.getByLabelText(/after a candidate passes/i);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('RoundInspector — the per-round auto-advance toggle', () => {
  it('shows the workflow default as its own third state, not as "automatic"', () => {
    renderInspector(round({ auto_advance: null }));
    expect(select().value).toBe('inherit');
  });

  it.each([
    [true, 'auto'],
    [false, 'manual'],
  ])('renders an override of %s as %s', (value, expected) => {
    renderInspector(round({ auto_advance: value }));
    expect(select().value).toBe(expected);
  });

  it('patches null — not false — when HR goes back to the workflow default', async () => {
    // The distinction the tri-state exists for: "no opinion" must not be
    // written as "hold", or changing the workflow default would stop reaching
    // this round.
    const onPatch = renderInspector(round({ auto_advance: false }));
    await userEvent.selectOptions(select(), 'inherit');
    expect(onPatch).toHaveBeenCalledWith({ auto_advance: null });
  });

  it.each([
    ['auto', true],
    ['manual', false],
  ])('patches %s as %s', async (option, expected) => {
    const onPatch = renderInspector(round({ auto_advance: null }));
    await userEvent.selectOptions(select(), option);
    expect(onPatch).toHaveBeenCalledWith({ auto_advance: expected });
  });
});

describe('RoundInspector — what the hold copy promises', () => {
  // Each clause below is a claim about workflow_runner. If one is removed there,
  // this test is the thing that says so.
  it('promises the notification the runner now sends', () => {
    renderInspector(round({ auto_advance: false }));
    expect(screen.getByTestId('hold-behaviour-copy')).toHaveTextContent(/you are notified/i);
  });

  it('names the next round as the destination when there is one', () => {
    renderInspector(round({ auto_advance: false, on_pass_next_round_id: 'round-2' }));
    const copy = screen.getByTestId('hold-behaviour-copy').textContent ?? '';
    expect(copy).toMatch(/moves them on to the next round/i);
    expect(copy).not.toMatch(/final decision exactly as/i);
  });

  it('names the final decision when this is the last round', () => {
    renderInspector(round({ auto_advance: false, on_pass_next_round_id: null }));
    const copy = screen.getByTestId('hold-behaviour-copy').textContent ?? '';
    expect(copy).toMatch(/moves them on to the final decision/i);
  });

  it('never lets the setting read as a rejection', () => {
    // A "held" badge reads as a rejection to most people, and DPDP scrutiny of
    // automated decision-making is exactly why it must not be one. This setting
    // decides who MOVES PEOPLE ON; ending a candidacy goes through the decision
    // that demands a reason and a reason code.
    renderInspector(round({ auto_advance: false }));
    const copy = screen.getByTestId('hold-behaviour-copy').textContent ?? '';
    expect(copy).toMatch(/nobody is rejected by this setting/i);
    expect(copy).toMatch(/asks you for a reason/i);
  });

  it('no longer warns that a released candidate stays put', () => {
    // The interim copy said so truthfully; release_hold now advances them, so
    // the warning would send HR to move someone the runner has already moved.
    renderInspector(round({ auto_advance: false, on_pass_next_round_id: 'round-2' }));
    expect(screen.queryByText(/does not move them into the next round/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/you will need to advance them/i)).not.toBeInTheDocument();
  });
});
