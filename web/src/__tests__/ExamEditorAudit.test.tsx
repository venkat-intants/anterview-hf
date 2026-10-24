// Exam editor, audit wave 2 (exams area). Rendered, not source-asserted: each
// test drives the control a person would use and checks what reaches the API.
//
//   E1  the spreadsheet import goes to the section it was made from
//   E2  a taken round offers Duplicate, never an Unpublish the server refuses
//   E3  deleting a question / section / round / revoking a link needs a confirm
//   E4  a locked round's grading settings cannot be edited; a refused pass mark
//       does not stay on screen
//   E5  a locked round offers no delete
//   E6  reordering reports failures, and "first" is the first in the list
//   E7  a part-failed "Add N questions" keeps only the unsaved drafts
//   E11 exam settings can be changed and the exam closed
//   E12 who already holds a live link is shown, and rotating it is warned about
//   E13 the applicant list pages instead of stopping at 200
//   E14 a failed load is an error with a retry, not a skeleton for ever
//   E23 removing the option marked correct clears the mark
//   E26 the composer tabs are real tabs (tabpanel wiring, arrow keys)
//   H1  icon-only cancel buttons have a name

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import type { Assignment, ExamDetail, ExamQuestion, Round } from '../api/exams';
import type { PipelineRow } from '../api/pipeline';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const api = vi.hoisted(() => ({
  getExam: vi.fn(),
  getStructure: vi.fn(),
  updateExam: vi.fn(),
  generateQuestions: vi.fn(),
  importSectionQuestions: vi.fn(),
  downloadQuestionTemplate: vi.fn(),
  createRound: vi.fn(),
  updateRound: vi.fn(),
  deleteRound: vi.fn(),
  reorderRounds: vi.fn(),
  duplicateRound: vi.fn(),
  createSection: vi.fn(),
  updateSection: vi.fn(),
  deleteSection: vi.fn(),
  listSectionQuestions: vi.fn(),
  addSectionQuestion: vi.fn(),
  deleteSectionQuestion: vi.fn(),
  listAssignments: vi.fn(),
  assignExam: vi.fn(),
  revokeAssignment: vi.fn(),
}));
vi.mock('../api/exams', () => {
  const out: Record<string, unknown> = { CODING_LANGUAGES: ['python'] };
  for (const name of Object.keys(api)) {
    out[name] = (...a: unknown[]) =>
      (api as Record<string, (...x: unknown[]) => unknown>)[name](...a);
  }
  return out;
});

const getPipeline = vi.fn();
vi.mock('../api/pipeline', () => ({
  getPipeline: (...a: unknown[]) => getPipeline(...a) as unknown,
}));

vi.mock('../api/questionBanks', () => ({
  saveExamQuestionToBank: vi.fn(),
  listQuestionBanks: vi.fn().mockResolvedValue([]),
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

import ExamEditor from '../pages/hr/ExamEditor';
import { McqSection } from '../components/hr/exam/McqSection';
import { RoundPanel } from '../components/hr/exam/RoundPanel';
import { RoundAssignPanel } from '../components/hr/exam/RoundAssignPanel';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const EXAM: ExamDetail = {
  id: 'e-1',
  title: 'Backend screening',
  description: null,
  target_job_title: null,
  pass_threshold: 60,
  time_limit_seconds: null,
  allow_retake: false,
  auto_advance_on_pass: false,
  status: 'draft',
  kind: 'mcq',
  created_at: '2026-10-01T10:00:00.000Z',
  attempt_count: 0,
  questions: [],
};

function round(over: Partial<Round> = {}): Round {
  return {
    id: 'r-1',
    title: 'Round 1',
    round_number: 1,
    pass_threshold: 60,
    time_limit_seconds: null,
    advances_to_interview: false,
    status: 'draft',
    position: 1,
    sections: [],
    camera_proctoring_required: false,
    attempt_count: 0,
    ...over,
  };
}

const QUESTION: ExamQuestion = {
  id: 'q-1',
  prompt: 'What is 2 + 2?',
  options: ['3', '4'],
  correct_index: 1,
  points: 1,
  position: 0,
};

function newClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function wrap(ui: ReactElement, client = newClient()) {
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/hr/exams/e-1']}>
        <Routes>
          <Route path="/hr/exams/:examId" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function roundPanel(r: Round, over: Partial<Parameters<typeof RoundPanel>[0]> = {}) {
  const props = {
    examId: 'e-1',
    round: r,
    totalRounds: 2,
    isFirst: true,
    isLast: false,
    onDelete: vi.fn(),
    deleting: false,
    onMove: vi.fn(),
    reordering: false,
    ...over,
  };
  wrap(<RoundPanel {...props} />);
  return props;
}

beforeEach(() => {
  vi.clearAllMocks();
  api.getExam.mockResolvedValue(EXAM);
  api.listSectionQuestions.mockResolvedValue([]);
  api.listAssignments.mockResolvedValue([]);
  getPipeline.mockResolvedValue({ items: [], count: 0, limit: 200, offset: 0 });
});

// ---------------------------------------------------------------------------
// McqSection
// ---------------------------------------------------------------------------

describe('MCQ section', () => {
  it('imports a spreadsheet into THIS section (E1)', async () => {
    api.importSectionQuestions.mockResolvedValue({ added: 2, errors: [], questions: [] });
    const user = userEvent.setup();
    wrap(<McqSection examId="e-1" sectionId="s-2" locked={false} />);

    await user.click(screen.getByRole('tab', { name: /spreadsheet/i }));
    const file = new File(['Question,Option A'], 'q.csv', { type: 'text/csv' });
    await user.upload(screen.getByLabelText(/choose spreadsheet file/i), file);

    await waitFor(() => expect(api.importSectionQuestions).toHaveBeenCalled());
    expect(api.importSectionQuestions.mock.calls[0].slice(0, 2)).toEqual(['e-1', 's-2']);
    expect(await screen.findByText(/up to 2 MB and 1,000 rows/)).toBeInTheDocument();
    expect(toastSuccess).toHaveBeenCalledWith('2 questions imported');
  });

  it('asks before deleting a question (E3)', async () => {
    api.listSectionQuestions.mockResolvedValue([QUESTION]);
    api.deleteSectionQuestion.mockResolvedValue(undefined);
    const user = userEvent.setup();
    wrap(<McqSection examId="e-1" sectionId="s-1" locked={false} />);

    await user.click(await screen.findByRole('button', { name: 'Delete question 1' }));
    expect(api.deleteSectionQuestion).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Delete question' }));

    await waitFor(() =>
      expect(api.deleteSectionQuestion).toHaveBeenCalledWith('e-1', 's-1', 'q-1'),
    );
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith('Question deleted'));
  });

  it('keeps only the unsaved drafts when an "Add N questions" fails part-way (E7)', async () => {
    api.generateQuestions.mockResolvedValue({
      questions: [
        { prompt: 'First draft', options: ['a', 'b'], correct_index: 0, points: 1 },
        { prompt: 'Second draft', options: ['a', 'b'], correct_index: 0, points: 1 },
        { prompt: 'Third draft', options: ['a', 'b'], correct_index: 0, points: 1 },
      ],
    });
    api.addSectionQuestion
      .mockResolvedValueOnce({ ...QUESTION, id: 'n-1' })
      .mockRejectedValueOnce(new TypeError('Failed to fetch'));
    const user = userEvent.setup();
    wrap(<McqSection examId="e-1" sectionId="s-1" locked={false} />);

    await user.click(screen.getByRole('tab', { name: /^ai$/i }));
    await user.type(screen.getByLabelText('AI topic'), 'arithmetic');
    await user.click(screen.getByRole('button', { name: 'Generate' }));
    await user.click(await screen.findByRole('button', { name: 'Add 3 questions' }));

    // The saved first draft is gone; the failed one and the untried one stay,
    // so pressing Add again cannot save "First draft" twice.
    expect(await screen.findByRole('button', { name: 'Add 2 questions' })).toBeInTheDocument();
    expect(screen.queryByText(/First draft/)).not.toBeInTheDocument();
    expect(screen.getByText(/Second draft/)).toBeInTheDocument();
    expect(screen.getByText(/Third draft/)).toBeInTheDocument();
    expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/1 question were added/));
  });

  it('clears the correct mark when its option is removed (E23)', async () => {
    const user = userEvent.setup();
    wrap(<McqSection examId="e-1" sectionId="s-1" locked={false} />);

    await user.type(screen.getByLabelText('Question prompt'), 'Pick one');
    for (const [i, text] of ['a', 'b', 'c'].entries()) {
      await user.type(screen.getByLabelText(`Option ${i + 1}`), text);
    }
    // Option 1 is marked correct by default; removing it used to move the
    // mark silently onto what had been option 2.
    await user.click(screen.getByRole('button', { name: 'Remove option 1' }));
    await user.type(screen.getByLabelText('Option 3'), 'd');
    await user.click(screen.getByRole('button', { name: 'Add question' }));

    expect(toastError).toHaveBeenCalledWith('Mark which option is correct.');
    expect(api.addSectionQuestion).not.toHaveBeenCalled();
  });

  it('wires the composer tabs to their panels and moves with the arrow keys (E26)', async () => {
    const user = userEvent.setup();
    wrap(<McqSection examId="e-1" sectionId="s-1" locked={false} />);

    const manual = screen.getByRole('tab', { name: /manual/i });
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', manual.id);
    expect(manual).toHaveAttribute('aria-controls', screen.getByRole('tabpanel').id);

    manual.focus();
    await user.keyboard('{ArrowRight}');
    const ai = screen.getByRole('tab', { name: /^ai$/i });
    expect(ai).toHaveAttribute('aria-selected', 'true');
    expect(ai).toHaveFocus();
    expect(manual).toHaveAttribute('tabindex', '-1');
  });
});

// ---------------------------------------------------------------------------
// RoundPanel
// ---------------------------------------------------------------------------

describe('round panel', () => {
  it('offers Duplicate, not Unpublish, on a taken round, and freezes its settings (E2, E4, E5)', () => {
    roundPanel(round({ status: 'published', attempt_count: 2 }));

    expect(screen.getByText(/this round has been taken/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Unpublish' })).not.toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /duplicate round/i }).length).toBeGreaterThan(0);
    expect(screen.getByLabelText('Pass threshold')).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: /advances to interview/i })).toBeDisabled();
    expect(screen.queryByRole('button', { name: /delete round/i })).not.toBeInTheDocument();
  });

  it('still offers Unpublish on a published round nobody has taken', () => {
    roundPanel(round({ status: 'published', attempt_count: 0 }));
    expect(screen.getAllByRole('button', { name: 'Unpublish' }).length).toBeGreaterThan(0);
  });

  it('puts the saved pass mark back when a save is refused (E4)', async () => {
    api.updateRound.mockRejectedValue(new Error('nope'));
    const user = userEvent.setup();
    roundPanel(round());

    const input = screen.getByLabelText('Pass threshold');
    await user.clear(input);
    await user.type(input, '80');
    await user.tab();

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    await waitFor(() => expect(input).toHaveValue(60));
  });

  it('keeps a pass mark of 0 (E16)', async () => {
    api.updateRound.mockResolvedValue(round({ pass_threshold: 0 }));
    const user = userEvent.setup();
    roundPanel(round());

    const input = screen.getByLabelText('Pass threshold');
    await user.clear(input);
    await user.type(input, '0');
    await user.tab();

    await waitFor(() =>
      expect(api.updateRound).toHaveBeenCalledWith('e-1', 'r-1', { pass_threshold: 0 }),
    );
  });

  it('asks before deleting a draft round (E3)', async () => {
    const user = userEvent.setup();
    const props = roundPanel(round());

    await user.click(screen.getByRole('button', { name: 'Delete round Round 1' }));
    expect(props.onDelete).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Delete round' }));
    expect(props.onDelete).toHaveBeenCalledWith('r-1');
  });

  it('can clear a draft round time limit (E11)', async () => {
    api.updateRound.mockResolvedValue(round());
    const user = userEvent.setup();
    roundPanel(round({ time_limit_seconds: 600 }));

    const input = screen.getByLabelText(/round time limit in minutes/i);
    expect(input).toHaveValue(10);
    await user.clear(input);
    await user.tab();

    await waitFor(() =>
      expect(api.updateRound).toHaveBeenCalledWith('e-1', 'r-1', { time_limit_seconds: null }),
    );
  });

  it('refuses a time limit of 0 instead of clearing the limit (re-audit)', async () => {
    const user = userEvent.setup();
    roundPanel(round({ time_limit_seconds: 600 }));

    const input = screen.getByLabelText(/round time limit in minutes/i);
    await user.clear(input);
    await user.type(input, '0');
    await user.tab();

    expect(toastError).toHaveBeenCalledWith(
      'Enter a time limit of 1 minute or more, or leave it blank for no limit.',
    );
    expect(api.updateRound).not.toHaveBeenCalled();
    expect(input).toHaveValue(10);
  });

  it('puts the blank back when 0 is typed into a round with no limit (re-audit)', async () => {
    const user = userEvent.setup();
    roundPanel(round({ time_limit_seconds: null }));

    const input = screen.getByLabelText(/round time limit in minutes/i);
    await user.type(input, '0');
    await user.tab();

    expect(toastError).toHaveBeenCalled();
    expect(api.updateRound).not.toHaveBeenCalled();
    expect(input).toHaveValue(null);
  });

  it('cannot submit a section time limit of 0: the field refuses it (min 1)', async () => {
    // The section forms submit, so the input's own min={1} stops 0 before any
    // request; only the round field, saved on blur, needed parseMinutes.
    const user = userEvent.setup();
    roundPanel(round());

    await user.click(screen.getByRole('button', { name: /add section/i }));
    const form = screen.getByRole('form', { name: 'New section' });
    const field = within(form).getByLabelText<HTMLInputElement>('Section time limit in minutes');
    await user.type(field, '0');
    await user.click(within(form).getByRole('button', { name: /add section/i }));

    expect(field.validity.rangeUnderflow).toBe(true);
    expect(api.createSection).not.toHaveBeenCalled();
  });

  it('adds a coding section with its title and time limit, then closes the form', async () => {
    api.createSection.mockResolvedValue({ id: 's-9' });
    const user = userEvent.setup();
    roundPanel(round());

    await user.click(screen.getByRole('button', { name: /add section/i }));
    const form = screen.getByRole('form', { name: 'New section' });
    await user.click(within(form).getByRole('button', { name: 'Coding' }));
    await user.type(within(form).getByLabelText('Section title'), 'Algorithms');
    await user.type(within(form).getByLabelText('Section time limit in minutes'), '15');
    await user.click(within(form).getByRole('button', { name: /add section/i }));

    await waitFor(() =>
      expect(api.createSection).toHaveBeenCalledWith('e-1', 'r-1', {
        title: 'Algorithms',
        kind: 'coding',
        time_limit_seconds: 900,
      }),
    );
    await waitFor(() =>
      expect(screen.queryByRole('form', { name: 'New section' })).not.toBeInTheDocument(),
    );
    expect(toastSuccess).toHaveBeenCalledWith('Section added');
  });

  it('names the icon-only cancel in the rename form (H1)', async () => {
    const user = userEvent.setup();
    roundPanel(round());

    await user.click(screen.getByRole('button', { name: 'Rename round Round 1' }));
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
  });

  it('asks before deleting a section (E3)', async () => {
    api.deleteSection.mockResolvedValue(undefined);
    const user = userEvent.setup();
    roundPanel(
      round({
        sections: [
          {
            id: 's-1',
            round_id: 'r-1',
            title: 'Basics',
            kind: 'mcq',
            time_limit_seconds: null,
            position: 1,
            question_count: 0,
          },
        ],
      }),
    );

    await user.click(screen.getByRole('button', { name: 'Delete section Basics' }));
    expect(api.deleteSection).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Delete section' }));
    await waitFor(() => expect(api.deleteSection).toHaveBeenCalledWith('e-1', 'r-1', 's-1'));
  });

  it("keeps the round's Publish as the only primary button (re-audit, one primary per view)", async () => {
    const user = userEvent.setup();
    roundPanel(round());
    await user.click(screen.getByRole('button', { name: /add section/i }));

    const primaries = screen.getAllByRole('button').filter(isPrimary);
    expect(primaries.map((b) => b.textContent?.trim())).toEqual(['Publish']);
  });
});

/** The default (primary) Button variant: its class list has a bare bg-primary. */
function isPrimary(el: HTMLElement): boolean {
  return el.className.split(/\s+/).includes('bg-primary');
}

describe('composer and assign actions are secondary (re-audit, one primary per view)', () => {
  it('draws every composer action as outline, on each tab', async () => {
    const user = userEvent.setup();
    wrap(<McqSection examId="e-1" sectionId="s-1" locked={false} />);

    expect(screen.getByRole('button', { name: 'Add question' })).not.toSatisfy(isPrimary);
    await user.click(screen.getByRole('tab', { name: /^ai$/i }));
    expect(screen.getAllByRole('button').filter(isPrimary)).toEqual([]);
    await user.click(screen.getByRole('tab', { name: /spreadsheet/i }));
    expect(screen.getAllByRole('button').filter(isPrimary)).toEqual([]);
  });

  it('draws Send links as outline', async () => {
    getPipeline.mockResolvedValue({ items: [row({})], count: 1, limit: 200, offset: 0 });
    wrap(<RoundAssignPanel examId="e-1" roundId="r-1" isPublished isFirstRound />);

    const send = await screen.findByRole('button', { name: /send links/i });
    expect(send).not.toSatisfy(isPrimary);
    expect(send.className).toContain('border-input');
  });
});

// ---------------------------------------------------------------------------
// Assign panel
// ---------------------------------------------------------------------------

function row(over: Partial<PipelineRow>): PipelineRow {
  return {
    applicant_id: 'ap-1',
    enrolment_id: null,
    requisition_id: null,
    opening_title: null,
    full_name: 'Asha',
    target_job_title: null,
    target_level: null,
    status: 'new',
    ats_overall: null,
    ats_recommendation: null,
    best_exam_percent: null,
    exam_passed: null,
    total_exam_attempts: 0,
    interview_status: null,
    ...over,
  } as PipelineRow;
}

const LIVE: Assignment = {
  assignment_id: 'as-1',
  applicant_id: 'ap-1',
  applicant_name: 'Asha',
  status: 'invited',
  expires_at: '2026-12-01T00:00:00.000Z',
  consumed_at: null,
  created_at: '2026-10-01T00:00:00.000Z',
  round_id: 'r-1',
  scheduled_at: null,
};

describe('assign panel', () => {
  it('marks who already holds a live link and warns before replacing it (E12)', async () => {
    getPipeline.mockResolvedValue({
      items: [row({}), row({ applicant_id: 'ap-2', full_name: 'Bala' })],
      count: 2,
      limit: 200,
      offset: 0,
    });
    api.listAssignments.mockResolvedValue([
      LIVE,
      { ...LIVE, assignment_id: 'as-x', round_id: 'r-2' },
    ]);
    const user = userEvent.setup();
    wrap(<RoundAssignPanel examId="e-1" roundId="r-1" isPublished isFirstRound />);

    const group = await screen.findByRole('group', { name: /select applicants to assign/i });
    const asha = (await within(group).findByText('Asha')).closest('label') as HTMLElement;
    expect(within(asha).getByText('Link sent')).toBeInTheDocument();
    // Listed under this round's "Assigned" (it matched nothing before).
    expect(screen.getByRole('button', { name: 'Revoke link for Asha' })).toBeInTheDocument();

    await user.click(within(asha).getByRole('checkbox'));
    expect(screen.getByText(/already has a live link to this round/i)).toBeInTheDocument();
  });

  it('asks before revoking a link (E3)', async () => {
    api.listAssignments.mockResolvedValue([LIVE]);
    api.revokeAssignment.mockResolvedValue({ ...LIVE, status: 'revoked' });
    const user = userEvent.setup();
    wrap(<RoundAssignPanel examId="e-1" roundId="r-1" isPublished isFirstRound />);

    await user.click(await screen.findByRole('button', { name: 'Revoke link for Asha' }));
    expect(api.revokeAssignment).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Revoke link' }));
    await waitFor(() => expect(api.revokeAssignment).toHaveBeenCalledWith('e-1', 'as-1'));
  });

  it('says the links were emailed (E12)', async () => {
    getPipeline.mockResolvedValue({ items: [row({})], count: 1, limit: 200, offset: 0 });
    api.assignExam.mockResolvedValue([
      {
        assignment_id: 'as-9',
        applicant_id: 'ap-1',
        applicant_name: 'Asha',
        magic_link: 'https://x.test/exam#t',
        expires_at: '2026-12-01T00:00:00.000Z',
        status: 'invited',
      },
    ]);
    const user = userEvent.setup();
    wrap(<RoundAssignPanel examId="e-1" roundId="r-1" isPublished isFirstRound />);

    await user.click(await screen.findByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: /send links \(1\)/i }));

    expect(await screen.findByText(/emailed to each candidate/i)).toBeInTheDocument();
  });

  it('pages past the first 200 applications (E13)', async () => {
    getPipeline
      .mockResolvedValueOnce({ items: [row({})], count: 2, limit: 200, offset: 0 })
      .mockResolvedValueOnce({
        items: [row({ applicant_id: 'ap-2', full_name: 'Bala' })],
        count: 2,
        limit: 200,
        offset: 1,
      });
    const user = userEvent.setup();
    wrap(<RoundAssignPanel examId="e-1" roundId="r-1" isPublished isFirstRound />);

    expect(await screen.findByText(/showing 1 of 2 applications/i)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Load more' }));

    expect(await screen.findByText('Bala')).toBeInTheDocument();
    expect(getPipeline).toHaveBeenLastCalledWith({ limit: 200, offset: 1 });
  });
});

// ---------------------------------------------------------------------------
// The page
// ---------------------------------------------------------------------------

describe('exam editor page', () => {
  it('shows an error with a retry when the structure cannot load (E14)', async () => {
    api.getStructure.mockRejectedValue(new Error('boom'));
    wrap(<ExamEditor />);

    expect(await screen.findByText('Could not load this exam.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });

  it('reorders by list position and reports a failed move (E6)', async () => {
    // Positions are not compacted after a delete: 2 and 5, not 1 and 2.
    api.getStructure.mockResolvedValue({
      exam_id: 'e-1',
      rounds: [
        round({ id: 'r-a', title: 'Alpha', position: 2, round_number: 2 }),
        round({ id: 'r-b', title: 'Beta', position: 5, round_number: 5 }),
      ],
    });
    api.reorderRounds.mockRejectedValue(new Error('nope'));
    const user = userEvent.setup();
    wrap(<ExamEditor />);

    expect(await screen.findByRole('button', { name: 'Move round Alpha up' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Move round Beta down' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Move round Alpha down' }));

    await waitFor(() => expect(api.reorderRounds).toHaveBeenCalledWith('e-1', ['r-b', 'r-a']));
    // Reported, not an unhandled rejection.
    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1));
  });

  it('changes exam settings and closes the exam behind a confirm (E11)', async () => {
    api.getStructure.mockResolvedValue({
      exam_id: 'e-1',
      rounds: [round({ status: 'published' })],
    });
    api.updateExam.mockResolvedValue({ ...EXAM, status: 'closed' });
    const user = userEvent.setup();
    wrap(<ExamEditor />);

    await user.click(await screen.findByRole('button', { name: /exam settings/i }));
    await user.click(screen.getByRole('switch', { name: 'Allow retake' }));
    await waitFor(() => expect(api.updateExam).toHaveBeenCalledWith('e-1', { allow_retake: true }));

    await user.click(screen.getByRole('button', { name: 'Close exam' }));
    const group = screen.getByRole('group', { name: /confirm: close exam/i });
    await user.click(within(group).getByRole('button', { name: 'Close exam' }));
    await waitFor(() => expect(api.updateExam).toHaveBeenCalledWith('e-1', { status: 'closed' }));
  });

  it('reports Saving only for the edits made on this exam (E22)', async () => {
    api.getStructure.mockResolvedValue({ exam_id: 'e-1', rounds: [round()] });
    const client = newClient();
    wrap(<ExamEditor />, client);
    expect(await screen.findByText('All changes saved')).toBeInTheDocument();

    // An unrelated mutation that never settles (a Save-to-bank, another panel).
    void client
      .getMutationCache()
      .build(client, { mutationFn: () => new Promise<void>(() => undefined) })
      .execute(undefined);

    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByText('All changes saved')).toBeInTheDocument();
    expect(screen.queryByText('Saving…')).not.toBeInTheDocument();
  });
});
