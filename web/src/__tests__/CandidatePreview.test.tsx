// CandidatePreview - the workflow as the candidate lives it.
//
// Pinned here: the glossary words (docs/UI-CONVENTIONS.md). The written MCQ
// round is an "exam" and the coding round a "coding round"; "assessment" is
// the AI panel's word and "coding test" is retired (audit E3).

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { Round } from '../api/workflows';
import CandidatePreview from '../components/workflow/CandidatePreview';

function round(over: Partial<Round>): Round {
  return {
    id: 'r-1',
    position: 0,
    title: 'Fundamentals',
    kind: 'mcq',
    pass_threshold: 60,
    time_limit_seconds: null,
    deadline_days: 5,
    auto_advance: null,
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

describe('CandidatePreview - glossary words', () => {
  it('calls an MCQ round an exam and a coding round a coding round', () => {
    const { container } = render(
      <CandidatePreview
        title="Backend Engineer"
        rounds={[
          round({ id: 'r-mcq', position: 0, kind: 'mcq', title: 'Fundamentals' }),
          round({ id: 'r-code', position: 1, kind: 'coding', title: 'Practical' }),
        ]}
      />,
    );
    expect(screen.getByText(/private link to the exam, in their language/)).toBeInTheDocument();
    expect(
      screen.getByText(/private link to the coding round, in their language/),
    ).toBeInTheDocument();
    expect(screen.getByText('Coding round')).toBeInTheDocument();
    const text = container.textContent ?? '';
    expect(text).not.toMatch(/coding test/i);
    expect(text).not.toMatch(/assessment/i);
  });
});
