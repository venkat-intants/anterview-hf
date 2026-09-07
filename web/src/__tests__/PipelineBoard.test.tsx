// The pipeline board.
//
// Most of this file is about what the board must NOT do. The spec is right
// that a board like this invites drag-and-drop, and drag-and-drop here would
// either lie — a display change with no interview behind it — or fabricate the
// event that justifies it. A candidate's stage is derived from what actually
// happened to them, so the board reflects it and never maintains it.
//
// The rest is the ordinary reading: everyone in a column, a count, one score
// rather than three mostly-blank ones, and an empty column that says it is
// empty rather than looking broken.

import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PipelineRow, PipelineStatus } from '../api/pipeline';

import PipelineBoard from '../components/PipelineBoard';

function row(over: Partial<PipelineRow> = {}): PipelineRow {
  return {
    applicant_id: `ap-${Math.random().toString(36).slice(2, 8)}`,
    full_name: 'Asha Rao',
    target_job_title: 'Backend Engineer',
    target_level: 'mid',
    status: 'new' as PipelineStatus,
    ats_overall: null,
    ats_recommendation: null,
    best_exam_percent: null,
    exam_passed: null,
    total_exam_attempts: 0,
    interview_status: null,
    interview_score: null,
    scorecard_id: null,
    ...over,
  } as PipelineRow;
}

function renderBoard(rows: PipelineRow[], onOpen = vi.fn()) {
  const view = render(<PipelineBoard rows={rows} onOpen={onOpen} />);
  return { ...view, onOpen };
}

const column = (name: string): HTMLElement =>
  screen.getByRole('region', { name: 'Candidate pipeline board' }).querySelector(
    `section[aria-labelledby="col-${name}"]`,
  ) as HTMLElement;

describe('PipelineBoard — nothing here moves anybody', () => {
  it('has no draggable cards', () => {
    // The whole point. A stage is derived from what happened; dropping
    // somebody into a column would either lie or fabricate the event.
    const { container } = renderBoard([row()]);
    expect(container.querySelector('[draggable="true"]')).toBeNull();
  });

  it('offers no action that would change a stage', () => {
    renderBoard([row({ status: 'shortlisted' })]);
    for (const label of [/advance/i, /reject/i, /hire/i, /move/i]) {
      expect(screen.queryByRole('button', { name: label })).not.toBeInTheDocument();
    }
  });

  it('says where moving somebody actually happens', () => {
    renderBoard([row()]);
    expect(screen.getByText(/every move is recorded with who made it/)).toBeInTheDocument();
  });

  it('opens the candidate instead', async () => {
    const user = userEvent.setup();
    const { onOpen } = renderBoard([row({ applicant_id: 'ap-1', full_name: 'Asha Rao' })]);
    await user.click(screen.getByRole('button', { name: /Open details for Asha Rao/ }));
    expect(onOpen).toHaveBeenCalledWith('ap-1');
  });
});

describe('PipelineBoard — the columns', () => {
  it('puts each candidate under where they are', () => {
    renderBoard([
      row({ full_name: 'New Person', status: 'new' }),
      row({ full_name: 'Short Lister', status: 'shortlisted' }),
      row({ full_name: 'Interviewee', status: 'interviewed' }),
    ]);
    expect(within(column('new')).getByText('New Person')).toBeInTheDocument();
    expect(within(column('shortlisted')).getByText('Short Lister')).toBeInTheDocument();
    expect(within(column('interviewed')).getByText('Interviewee')).toBeInTheDocument();
  });

  it('puts hired and rejected together under Decided', () => {
    // Both are decided, the board is about who still needs attention, and a
    // column of rejections is a wall nobody scans.
    renderBoard([
      row({ full_name: 'Was Hired', status: 'hired' }),
      row({ full_name: 'Not Progressing', status: 'rejected' }),
    ]);
    const decided = column('decided');
    expect(within(decided).getByText('Was Hired')).toBeInTheDocument();
    expect(within(decided).getByText('Not Progressing')).toBeInTheDocument();
  });

  it('says which outcome it was, since the column cannot', () => {
    renderBoard([row({ full_name: 'Was Hired', status: 'hired' })]);
    expect(within(column('decided')).getByText('hired')).toBeInTheDocument();
  });

  it('does not repeat the status where the heading already says it', () => {
    renderBoard([row({ full_name: 'Short Lister', status: 'shortlisted' })]);
    // The heading says Shortlisted; a tag on every card would be noise.
    expect(within(column('shortlisted')).queryByText('shortlisted')).not.toBeInTheDocument();
  });

  it('counts each column', () => {
    renderBoard([
      row({ status: 'new' }),
      row({ status: 'new' }),
      row({ status: 'shortlisted' }),
    ]);
    expect(within(column('new')).getByText('2')).toBeInTheDocument();
    expect(within(column('shortlisted')).getByText('1')).toBeInTheDocument();
  });

  it('says an empty column is empty rather than looking broken', () => {
    renderBoard([row({ status: 'new' })]);
    expect(within(column('decided')).getByText('Nobody here.')).toBeInTheDocument();
  });

  it('renders every column even with no rows at all', () => {
    renderBoard([]);
    for (const key of ['new', 'shortlisted', 'interviewed', 'decided']) {
      expect(column(key)).toBeTruthy();
    }
  });
});

describe('PipelineBoard — the score on a card', () => {
  it('shows the furthest score a candidate has reached', () => {
    renderBoard([
      row({ ats_overall: 78, best_exam_percent: 64, interview_score: 7.8 }),
    ]);
    expect(screen.getByText('7.8/10')).toBeInTheDocument();
    expect(screen.queryByText('64%')).not.toBeInTheDocument();
  });

  it('falls back to the exam, then the resume', () => {
    const { unmount } = renderBoard([row({ ats_overall: 78, best_exam_percent: 64 })]);
    expect(screen.getByText('64%')).toBeInTheDocument();
    unmount();

    renderBoard([row({ ats_overall: 78 })]);
    expect(screen.getByText('78/100')).toBeInTheDocument();
  });

  it('labels the score, because the scales are not comparable', () => {
    // 78/100 on a CV and 7.8/10 in an interview mean different things, and a
    // bare number invites reading them as if they did not.
    renderBoard([row({ ats_overall: 78 })]);
    expect(screen.getByText('Resume')).toBeInTheDocument();
  });

  it('shows nothing rather than a zero when nobody has scored them', () => {
    const { container } = renderBoard([row()]);
    expect(container.textContent).not.toMatch(/\/100|\/10|%/);
  });
});
