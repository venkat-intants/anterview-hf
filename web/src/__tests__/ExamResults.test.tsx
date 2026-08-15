// Tests for the HR exam results table and the attempt drill-down (FE-2).
//
// These two pages are how an HR manager decides who advances past the written
// round. Both were untested. The properties pinned here are the ones a reader
// of the table would act on: pass/fail, an in-progress attempt not being read
// as a fail, and the per-question breakdown naming the right correct answer.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AttemptResult, ExamDetail, AttemptBreakdown } from '../api/exams';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const EXAM: ExamDetail = {
  id: 'e-1',
  title: 'Python Fundamentals Screening',
  description: null,
  target_job_title: null,
  pass_threshold: 60,
  time_limit_seconds: null,
  allow_retake: false,
  auto_advance_on_pass: false,
  status: 'published',
  kind: 'mcq',
  created_at: '2026-07-01T10:00:00.000Z',
  attempt_count: 3,
  questions: [
    {
      id: 'q-1',
      prompt: 'What does len() return?',
      options: ['the length', 'the type', 'the id'],
      correct_index: 0,
      points: 1,
      position: 0,
    },
    {
      id: 'q-2',
      prompt: 'Which keyword defines a generator?',
      options: ['return', 'yield', 'await'],
      correct_index: 1,
      points: 2,
      position: 1,
    },
  ],
};

const PASSED: AttemptResult = {
  attempt_id: 'a-pass',
  applicant_id: 'ap-1',
  applicant_name: 'Bhavya Nair',
  score_raw: 3,
  score_max: 3,
  score_percent: 100,
  passed: true,
  status: 'submitted',
  submitted_at: '2026-08-01T10:00:00.000Z',
  attempt_no: 1,
};

const FAILED: AttemptResult = {
  attempt_id: 'a-fail',
  applicant_id: 'ap-2',
  applicant_name: 'Chetan Iyer',
  score_raw: 1,
  score_max: 3,
  score_percent: 33,
  passed: false,
  status: 'submitted',
  submitted_at: '2026-08-02T10:00:00.000Z',
  attempt_no: 1,
};

const IN_PROGRESS: AttemptResult = {
  attempt_id: 'a-open',
  applicant_id: 'ap-3',
  applicant_name: 'Deepa Menon',
  score_raw: null,
  score_max: null,
  score_percent: null,
  passed: null,
  status: 'in_progress',
  submitted_at: null,
  attempt_no: 1,
};

const BREAKDOWN: AttemptBreakdown = {
  attempt_id: 'a-fail',
  score_percent: 33,
  passed: false,
  per_question: { 'q-1': true, 'q-2': false },
  coding: {},
};

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const getExam = vi.fn();
const listAttempts = vi.fn();
const getAttemptBreakdown = vi.fn();
vi.mock('../api/exams', () => ({
  getExam: (...a: unknown[]) => getExam(...a) as unknown,
  listAttempts: (...a: unknown[]) => listAttempts(...a) as unknown,
  getAttemptBreakdown: (...a: unknown[]) => getAttemptBreakdown(...a) as unknown,
}));

import ExamResults from '../pages/hr/ExamResults';
import ExamAttemptDetail from '../pages/hr/ExamAttemptDetail';

function renderResults() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/hr/exams/e-1/results']}>
        <Routes>
          <Route path="/hr/exams/:examId/results" element={<ExamResults />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderAttempt(attemptId = 'a-fail') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/hr/exams/e-1/attempts/${attemptId}`]}>
        <Routes>
          <Route path="/hr/exams/:examId/attempts/:attemptId" element={<ExamAttemptDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getExam.mockResolvedValue(EXAM);
  listAttempts.mockResolvedValue([PASSED, FAILED, IN_PROGRESS]);
  getAttemptBreakdown.mockResolvedValue(BREAKDOWN);
});

// ---------------------------------------------------------------------------

describe('ExamResults', () => {
  it('titles the page with the exam and summarises the cohort', async () => {
    renderResults();

    expect(
      await screen.findByRole('heading', { name: /python fundamentals screening · results/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/3 attempts · 1 passed · pass ≥ 60%/)).toBeInTheDocument();
  });

  it('shows one row per attempt with its own outcome', async () => {
    renderResults();

    // Scoped per row: "100%" also appears in the Top-score tile, and an
    // unscoped match would pass even if both rows showed the same score.
    const rowFor = (name: string): HTMLElement =>
      screen.getByText(name).parentElement!.parentElement!;

    await screen.findByText('Bhavya Nair');
    expect(within(rowFor('Bhavya Nair')).getByText('100%')).toBeInTheDocument();
    expect(within(rowFor('Bhavya Nair')).getByText('Passed')).toBeInTheDocument();
    expect(within(rowFor('Chetan Iyer')).getByText('33%')).toBeInTheDocument();
    expect(within(rowFor('Chetan Iyer')).getByText('Failed')).toBeInTheDocument();
  });

  it('never renders an unfinished attempt as a failure', async () => {
    // The candidate is still sitting the exam. Reading that as "Failed" would
    // get them rejected on the strength of a half-answered paper.
    renderResults();

    await screen.findByText('Deepa Menon');
    expect(screen.getByText('In progress')).toBeInTheDocument();
    // Its score cell is a dash, not a 0%.
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('averages only the attempts that actually have a score', async () => {
    // (100 + 33) / 2 = 66.5 → 67. Including the in-progress attempt as 0 would
    // give 44 and make a healthy cohort look like a failing one.
    renderResults();

    await screen.findByText('Bhavya Nair');

    // Assert the AVERAGE, which is what the test is named for. It previously
    // asserted the pass count and the threshold caption instead — both true
    // whichever divisor the average used, so the arithmetic this test exists to
    // pin was never checked and the 44-vs-67 bug would have shipped green.
    expect(screen.getByText('67%')).toBeInTheDocument();
    expect(screen.queryByText('44%')).not.toBeInTheDocument();

    // Kept: they scope the number above to the right tile.
    expect(screen.getByText('1 passed')).toBeInTheDocument();
    expect(screen.getByText(/≥ 60% threshold/)).toBeInTheDocument();
  });

  it('hides the CSV export when there is nothing to export', async () => {
    listAttempts.mockResolvedValue([]);
    renderResults();

    expect(await screen.findByText(/no attempts yet/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /export results as csv/i })).not.toBeInTheDocument();
  });

  it('links each row to its own attempt detail', async () => {
    renderResults();

    await screen.findByText('Chetan Iyer');
    const hrefs = screen.getAllByRole('link', { name: /view/i }).map((a) => a.getAttribute('href'));
    expect(hrefs).toContain('/hr/exams/e-1/attempts/a-fail');
    expect(hrefs).toContain('/hr/exams/e-1/attempts/a-pass');
  });
});

describe('ExamAttemptDetail', () => {
  it('names the candidate and their attempt number', async () => {
    renderAttempt();

    expect(await screen.findByRole('heading', { name: 'Chetan Iyer' })).toBeInTheDocument();
    expect(
      screen.getByText(/python fundamentals screening · attempt #1/i),
    ).toBeInTheDocument();
  });

  it('shows the correct answer only for the questions that were wrong', async () => {
    renderAttempt();

    // q-2 was wrong: the reviewer needs to see what the right answer was.
    expect(await screen.findByText(/correct answer: yield/i)).toBeInTheDocument();
    // q-1 was right: printing its answer is noise, and reads as a mistake.
    expect(screen.queryByText(/correct answer: the length/i)).not.toBeInTheDocument();
  });

  it('renders the real question prompts rather than numbered placeholders', async () => {
    renderAttempt();

    expect(await screen.findByText(/what does len\(\) return\?/i)).toBeInTheDocument();
    expect(screen.getByText(/which keyword defines a generator\?/i)).toBeInTheDocument();
  });

  it('awards zero points for a wrong answer and full points for a right one', async () => {
    renderAttempt();

    const mcq = await screen.findByText(/multiple choice · 1\/2 correct/i);
    const card = mcq.parentElement as HTMLElement;
    expect(within(card).getByText('1/1 pts')).toBeInTheDocument();
    expect(within(card).getByText('0/2 pts')).toBeInTheDocument();
  });

  it('explains an unloadable attempt instead of rendering an empty shell', async () => {
    getAttemptBreakdown.mockRejectedValue(new Error('404'));
    renderAttempt();

    expect(await screen.findByText(/could not load this attempt/i)).toBeInTheDocument();
  });
});
