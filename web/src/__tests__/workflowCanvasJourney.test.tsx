// D1/D3 — the canvas shows the whole journey, and coverage reads as a verdict.

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import WorkflowCanvas from '../components/workflow/WorkflowCanvas';
import { coverageVerdict } from '../lib/workflowLifecycle';
import { ROUND_KIND_META } from '../components/workflow/roundKinds';
import type { Round } from '../api/workflows';

const round = (over: Partial<Round>): Round =>
  ({
    id: 'r', title: 'Round', kind: 'mcq', position: 0, pass_threshold: 60,
    time_limit_seconds: 1800, deadline_days: 5, exam_round_id: 'e', needs_questions: false,
    criteria: [], on_pass_next_round_id: null,
    ...over,
  }) as unknown as Round;

function renderCanvas(rounds: Round[]) {
  return render(
    <WorkflowCanvas
      rounds={rounds}
      selectedId={null}
      editable
      onSelect={vi.fn()}
      onAdd={vi.fn()}
      onRemove={vi.fn()}
      onReorder={vi.fn()}
    />,
  );
}

describe('the canvas journey', () => {
  it('starts at the application, behind a human shortlist gate', () => {
    renderCanvas([round({})]);
    expect(screen.getByText('Candidates apply')).toBeTruthy();
    expect(screen.getByTestId('shortlist-gate').textContent).toMatch(
      /You shortlist — nothing below starts until a person does/,
    );
  });

  it('draws the hold path back to the decision queue', () => {
    renderCanvas([round({})]);
    expect(screen.getByText(/puts a candidate in your decision queue/)).toBeTruthy();
    expect(screen.getByTestId('hold-path').textContent).toMatch(
      /held below a round’s threshold.*Nobody is\s+rejected automatically/s,
    );
  });

  it('says which rounds are automated and which a person decides', () => {
    renderCanvas([
      round({ id: 'a', title: 'Aptitude' }),
      round({ id: 'b', title: 'Final', kind: 'human_review', pass_threshold: null,
              exam_round_id: null, position: 1 }),
    ]);
    expect(screen.getByText('Automated')).toBeTruthy();
    expect(screen.getByText('A person decides')).toBeTruthy();
  });
});

describe('round kinds', () => {
  it('lets a human review carry a checklist, decided by a person', () => {
    expect(ROUND_KIND_META.human_review.supportsCriteria).toBe(true);
    expect(ROUND_KIND_META.human_review.decidedBy).toBe('person');
    expect(ROUND_KIND_META.ai_interview.decidedBy).toBe('system');
  });
});

describe('the coverage verdict', () => {
  it('words the weighted share, and says nothing without a role model', () => {
    expect(coverageVerdict(0.93)).toMatch(/^Strong coverage: the rounds assess 93%/);
    expect(coverageVerdict(0.8)).toMatch(/^Reasonable coverage/);
    expect(coverageVerdict(0.5)).toMatch(/^Thin coverage.*only 50%/);
    expect(coverageVerdict(null)).toBeNull();
  });
});
