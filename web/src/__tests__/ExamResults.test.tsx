// Tests for the HR exam results table and the attempt drill-down (FE-2).
//
// These two pages are how an HR manager decides who advances past the written
// round. Both were untested. The properties pinned here are the ones a reader
// of the table would act on: pass/fail, an in-progress attempt not being read
// as a fail, and the per-question breakdown naming the right correct answer.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
// The camera-proctoring summary ExamAttemptDetail also renders is covered in
// its own AttemptProctoringSummary.test.tsx — stub it to a quiet "nothing to
// report" fixture here so it never errors against this file's fixtures.
const getAttemptProctoring = vi.fn();
const listCodingQuestions = vi.fn();
vi.mock('../api/exams', () => ({
  getExam: (...a: unknown[]) => getExam(...a) as unknown,
  listAttempts: (...a: unknown[]) => listAttempts(...a) as unknown,
  getAttemptBreakdown: (...a: unknown[]) => getAttemptBreakdown(...a) as unknown,
  getAttemptProctoring: (...a: unknown[]) => getAttemptProctoring(...a) as unknown,
  listCodingQuestions: (...a: unknown[]) => listCodingQuestions(...a) as unknown,
  CODING_LANGUAGES: ['python', 'javascript'],
}));

// PH4-D3 — ExamResults' "Similarity signals" column. Counts only, sourced
// from the exam-wide signal list; the content lives behind the attempt's own
// Code evidence tab, not here.
const listSimilaritySignals = vi.fn();
vi.mock('../api/codeEvidence', () => ({
  listSimilaritySignals: (...a: unknown[]) => listSimilaritySignals(...a) as unknown,
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
  getAttemptProctoring.mockResolvedValue({
    camera_in_use: false,
    integrity_score: null,
    counts: {},
    events: [],
  });
  listSimilaritySignals.mockResolvedValue([]);
});

// ---------------------------------------------------------------------------

describe('ExamResults', () => {
  it('titles the page with the exam and summarises the cohort', async () => {
    renderResults();

    expect(
      await screen.findByRole('heading', { name: /python fundamentals screening · results/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/3 attempts · 1 passed · pass mark 60%/)).toBeInTheDocument();
  });

  it('shows one row per attempt with its own outcome', async () => {
    renderResults();

    // Scoped per row: "100%" also appears in the Top-score tile, and an
    // unscoped match would pass even if both rows showed the same score.
    const rowFor = (name: string): HTMLElement =>
      screen.getByText(name).closest('tr') as HTMLElement;

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
    expect(screen.getAllByText('-').length).toBeGreaterThan(0);
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
    expect(screen.getByText('of 2 graded')).toBeInTheDocument();
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

describe('ExamResults - rounds (audit E8, E9, E27)', () => {
  const R1_FAIL: AttemptResult = {
    ...FAILED,
    score_percent: 65,
    round_id: 'r-2',
    round_number: 2,
    round_title: 'Coding',
    round_pass_threshold: 75,
  };
  const R1_PASS: AttemptResult = {
    ...PASSED,
    round_id: 'r-1',
    round_number: 1,
    round_title: 'Aptitude',
    round_pass_threshold: 60,
  };

  it('shows each attempt with its round and that round pass mark', async () => {
    listAttempts.mockResolvedValue([R1_PASS, R1_FAIL]);
    renderResults();

    const row = (await screen.findByText('Chetan Iyer')).closest('tr') as HTMLElement;
    // A 65% "Failed" makes sense next to ITS round's 75%, not the exam's 60%.
    expect(within(row).getByText('Round 2 · Coding')).toBeInTheDocument();
    expect(within(row).getByText('pass mark 75%')).toBeInTheDocument();
    expect(screen.getByText(/each round has its own pass mark/)).toBeInTheDocument();
    expect(screen.queryByText(/pass ≥/)).not.toBeInTheDocument();
  });

  it('computes the pass rate over graded attempts only', async () => {
    // 1 passed of 2 graded = 50%; dividing by all 3 would read 33%.
    renderResults();

    await screen.findByText('Bhavya Nair');
    expect(screen.getByText('50%')).toBeInTheDocument();
    expect(screen.getByText('of 2 graded')).toBeInTheDocument();
  });

  it('never calls an ungraded finished attempt a fail', async () => {
    listAttempts.mockResolvedValue([{ ...FAILED, passed: null }]);
    renderResults();

    const row = (await screen.findByText('Chetan Iyer')).closest('tr') as HTMLElement;
    expect(within(row).getByText('Not graded')).toBeInTheDocument();
    expect(within(row).queryByText('Failed')).not.toBeInTheDocument();
  });

  it('renders View as one link, not a button inside a link', async () => {
    renderResults();

    const link = await screen.findByRole('link', { name: 'View attempt by Chetan Iyer' });
    expect(link.querySelector('button')).toBeNull();
    expect(link.closest('button')).toBeNull();
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

  it('uses the round pass mark and says an in-progress attempt is not graded (E8, E24)', async () => {
    listAttempts.mockResolvedValue([
      { ...IN_PROGRESS, attempt_id: 'a-fail', round_number: 2, round_pass_threshold: 80 },
    ]);
    renderAttempt();

    expect(await screen.findByText('80%')).toBeInTheDocument();
    expect(screen.getByText('not graded yet')).toBeInTheDocument();
    expect(screen.queryByText('did not pass')).not.toBeInTheDocument();
  });

  it('names a coding question by its prompt (E24)', async () => {
    getAttemptBreakdown.mockResolvedValue({
      ...BREAKDOWN,
      per_question: {},
      coding: { 'cq-1': { points: 10, raw: 10, language: 'python' } },
    });
    listCodingQuestions.mockResolvedValue([
      {
        id: 'cq-1',
        prompt: 'Reverse a linked list\nInput: a list',
        allowed_languages: ['python'],
        starter_code: null,
        reference_solution: null,
        test_cases: [],
        time_limit_ms: 1000,
        points: 10,
        position: 0,
      },
    ]);
    renderAttempt();

    expect(await screen.findByText('Reverse a linked list')).toBeInTheDocument();
  });

  it('explains an unloadable attempt instead of rendering an empty shell', async () => {
    getAttemptBreakdown.mockRejectedValue(new Error('404'));
    renderAttempt();

    expect(await screen.findByText(/could not load this attempt/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// PH4-D3 — "Similarity signals" column. Counts only.
// ---------------------------------------------------------------------------

describe('ExamResults - similarity signals column', () => {
  const CODING_EXAM: ExamDetail = { ...EXAM, id: 'e-2', kind: 'coding', questions: [] };
  const CODED_A: AttemptResult = { ...PASSED, attempt_id: 'a-coded-1' };
  const CODED_B: AttemptResult = { ...FAILED, attempt_id: 'a-coded-2' };

  function renderCodingResults() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/hr/exams/e-2/results']}>
          <Routes>
            <Route path="/hr/exams/:examId/results" element={<ExamResults />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it('does not show a similarity column for an MCQ exam', async () => {
    renderResults();
    await screen.findByText('Bhavya Nair');
    expect(screen.queryByText('Similarity signals')).not.toBeInTheDocument();
    expect(listSimilaritySignals).not.toHaveBeenCalled();
  });

  it('shows a count-only column for a coding exam, never the compared content', async () => {
    getExam.mockResolvedValue(CODING_EXAM);
    listAttempts.mockResolvedValue([CODED_A, CODED_B]);
    listSimilaritySignals.mockResolvedValue([
      {
        id: 'sig-1',
        coding_question_id: 'q-1',
        attempt_low_id: 'a-coded-1',
        attempt_high_id: 'a-coded-2',
        reference_kind: 'submission',
        containment_low: 0.9,
        containment_high: 0.8,
        jaccard: 0.7,
        shared_fingerprints: 40,
        created_at: '2026-08-05T00:00:00.000Z',
      },
    ]);
    renderCodingResults();

    expect(await screen.findByText('Similarity signals')).toBeInTheDocument();
    // One signal touches both attempts — each gets a count of 1.
    const rowFor = (name: string): HTMLElement => screen.getByText(name).closest('tr') as HTMLElement;
    await screen.findByText('Bhavya Nair');
    expect(within(rowFor('Bhavya Nair')).getByText('1')).toBeInTheDocument();
    expect(within(rowFor('Chetan Iyer')).getByText('1')).toBeInTheDocument();

    // Never the compared content on this screen — no containment/jaccard figures.
    expect(screen.queryByText(/containment/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/jaccard/i)).not.toBeInTheDocument();

    // Labelled as automated and unreviewed, per the checklist.
    expect(
      screen.getByText(/automated and unreviewed.*not evidence of misconduct on its own/i),
    ).toBeInTheDocument();
  });

  it('shows a dash rather than zero for an attempt with no signals', async () => {
    getExam.mockResolvedValue(CODING_EXAM);
    listAttempts.mockResolvedValue([CODED_A]);
    listSimilaritySignals.mockResolvedValue([]);
    renderCodingResults();

    await screen.findByText('Bhavya Nair');
    const row = screen.getByText('Bhavya Nair').parentElement!.parentElement!;
    expect(within(row).getByText('-')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Audit X3 / consistency D1: a failed load used to read "No attempts yet".
// ---------------------------------------------------------------------------

describe('ExamResults - failed loads', () => {
  it('says the attempts failed to load instead of "No attempts yet"', async () => {
    listAttempts.mockRejectedValueOnce(new Error('Network error - could not reach server'));
    const user = userEvent.setup();
    renderResults();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not load the attempts for this exam.');
    expect(alert).toHaveTextContent('Could not reach the server.');
    expect(screen.queryByText(/no attempts yet/i)).not.toBeInTheDocument();

    await user.click(within(alert).getByRole('button', { name: 'Try again' }));

    expect(await screen.findByText('Bhavya Nair')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows the empty state only for a real empty list', async () => {
    listAttempts.mockResolvedValueOnce([]);
    renderResults();

    expect(await screen.findByText('No attempts yet')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('reports a failed exam lookup without hiding the attempts', async () => {
    getExam.mockRejectedValueOnce(new Error('Exam service unavailable'));
    renderResults();

    expect(await screen.findByText('Bhavya Nair')).toBeInTheDocument();
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "Could not load this exam's details.",
    );
  });

  it('says similarity could not be checked rather than "-" for every attempt', async () => {
    getExam.mockResolvedValue({ ...EXAM, kind: 'coding' });
    listSimilaritySignals.mockRejectedValueOnce(new Error('Signals unavailable'));
    renderResults();

    expect(await screen.findByText('Bhavya Nair')).toBeInTheDocument();
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not load the similarity signals.',
    );
  });
});
