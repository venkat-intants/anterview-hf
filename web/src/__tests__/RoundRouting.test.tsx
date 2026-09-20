// RoundRouting — PH4-O3. Where a result on a round actually sends a candidate.
//
// The pass branch is read-only (it is the add/reorder chain). Below-threshold
// and fast-track are the two branches this panel edits, and fast-track is a
// pair — clearing or setting either clears/sets both in one patch, because a
// half-set fast-track is the exact state the server's validator rejects.

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import RoundRouting from '../components/workflow/RoundRouting';
import type { Round } from '../api/workflows';

function round(over: Partial<Round> = {}): Round {
  return {
    id: 'r-a', position: 0, title: 'Aptitude', kind: 'mcq', pass_threshold: 60,
    time_limit_seconds: null, deadline_days: 7, on_pass_next_round_id: 'r-b',
    on_fail_next_round_id: null, fast_track_min_percent: null,
    on_fast_track_next_round_id: null, exam_round_id: 'e1', needs_questions: false,
    criteria: [],
    ...over,
  };
}

const OTHERS = [
  { id: 'r-b', title: 'Conversation' },
  { id: 'r-c', title: 'Final review' },
];

describe('RoundRouting — the pass branch', () => {
  it('reads the chain, never lets it be edited here', () => {
    render(
      <RoundRouting round={round()} otherRounds={OTHERS} editable errors={[]} onPatch={vi.fn()} />,
    );
    expect(screen.getByText('Conversation', { selector: 'span.font-medium' })).toBeTruthy();
    expect(screen.queryByLabelText(/if they pass/i)).toBeNull();
  });

  it('says "Final decision" when there is nothing after it', () => {
    render(
      <RoundRouting
        round={round({ on_pass_next_round_id: null })}
        otherRounds={OTHERS}
        editable
        errors={[]}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.getByText('Final decision')).toBeTruthy();
  });
});

describe('RoundRouting — below the threshold', () => {
  it('offers every other round, plus holding for a person', async () => {
    const user = userEvent.setup();
    const onPatch = vi.fn();
    render(
      <RoundRouting round={round()} otherRounds={OTHERS} editable errors={[]} onPatch={onPatch} />,
    );

    const select = screen.getByLabelText(/if below the threshold/i);
    await user.selectOptions(select, 'r-c');
    expect(onPatch).toHaveBeenCalledWith({ on_fail_next_round_id: 'r-c' });
  });

  it('words it "if not passed" for a human review, which has no threshold', () => {
    render(
      <RoundRouting
        round={round({ kind: 'human_review', pass_threshold: null })}
        otherRounds={OTHERS}
        editable
        errors={[]}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.getByLabelText(/if not passed/i)).toBeTruthy();
    expect(screen.queryByLabelText(/if below the threshold/i)).toBeNull();
  });
});

describe('RoundRouting — fast-track is a pair', () => {
  it('is hidden for a human review round, which cannot fast-track anyone', () => {
    render(
      <RoundRouting
        round={round({ kind: 'human_review', pass_threshold: null })}
        otherRounds={OTHERS}
        editable
        errors={[]}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.queryByLabelText(/fast-track this round/i)).toBeNull();
    expect(screen.getByText(/cannot fast-track anyone/)).toBeTruthy();
  });

  it('sets both the score and the destination together when switched on', async () => {
    const user = userEvent.setup();
    const onPatch = vi.fn();
    render(
      <RoundRouting round={round()} otherRounds={OTHERS} editable errors={[]} onPatch={onPatch} />,
    );

    await user.click(screen.getByLabelText(/fast-track aptitude/i));
    expect(onPatch).toHaveBeenCalledTimes(1);
    const call = onPatch.mock.calls[0][0] as Record<string, unknown>;
    expect(call.fast_track_min_percent).not.toBeNull();
    expect(call.on_fast_track_next_round_id).not.toBeNull();
  });

  it('clears both together when switched off', async () => {
    const user = userEvent.setup();
    const onPatch = vi.fn();
    render(
      <RoundRouting
        round={round({ fast_track_min_percent: 90, on_fast_track_next_round_id: 'r-b' })}
        otherRounds={OTHERS}
        editable
        errors={[]}
        onPatch={onPatch}
      />,
    );

    await user.click(screen.getByLabelText(/fast-track aptitude/i));
    expect(onPatch).toHaveBeenCalledWith({
      fast_track_min_percent: null,
      on_fast_track_next_round_id: null,
    });
  });

  it('edits the score and destination once a fast-track exists', async () => {
    const user = userEvent.setup();
    const onPatch = vi.fn();
    render(
      <RoundRouting
        round={round({ fast_track_min_percent: 90, on_fast_track_next_round_id: 'r-b' })}
        otherRounds={OTHERS}
        editable
        errors={[]}
        onPatch={onPatch}
      />,
    );

    await user.selectOptions(screen.getByLabelText(/fast-track destination/i), 'r-c');
    expect(onPatch).toHaveBeenCalledWith({ on_fast_track_next_round_id: 'r-c' });
  });
});

describe('RoundRouting — locked and validation', () => {
  it('disables every control when the workflow is not editable', () => {
    render(
      <RoundRouting
        round={round({ fast_track_min_percent: 90, on_fast_track_next_round_id: 'r-b' })}
        otherRounds={OTHERS}
        editable={false}
        errors={[]}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.getByLabelText(/if below the threshold/i)).toBeDisabled();
    expect(screen.getByLabelText(/fast-track score/i)).toBeDisabled();
    expect(screen.getByLabelText(/fast-track destination/i)).toBeDisabled();
  });

  it('shows validation errors from the server', () => {
    render(
      <RoundRouting
        round={round()}
        otherRounds={OTHERS}
        editable
        errors={['Aptitude: the fast-track score must be above the advance threshold.']}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.getByText(/fast-track score must be above/)).toBeTruthy();
  });
});
