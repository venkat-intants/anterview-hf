// ExamAttemptDetail — PH4-D3 "Code evidence" tab + PH4-D2 time-adjustment
// badge. The evidence panel itself (source viewer, quality card, signal vs.
// finding separation, coverage wording) is covered in
// CodeEvidencePanel.test.tsx; this file pins the things that live on
// ExamAttemptDetail specifically: the tab only exists where there is a
// coding question, switching to it hides the MCQ/coding overview, and the
// adjustment badge reads exactly what the attempt itself was given.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AttemptResult, ExamDetail, AttemptBreakdown } from '../api/exams';

const getExam = vi.fn();
const listAttempts = vi.fn();
const getAttemptBreakdown = vi.fn();
vi.mock('../api/exams', () => ({
  getExam: (...a: unknown[]) => getExam(...a) as unknown,
  listAttempts: (...a: unknown[]) => listAttempts(...a) as unknown,
  getAttemptBreakdown: (...a: unknown[]) => getAttemptBreakdown(...a) as unknown,
  CODING_LANGUAGES: ['python', 'javascript'],
}));

// The panel itself is covered in CodeEvidencePanel.test.tsx — stub it here so
// this file only pins what ExamAttemptDetail itself is responsible for: which
// tab is showing, and what props the panel was handed.
vi.mock('../components/hr/CodeEvidencePanel', () => ({
  default: (props: { examId: string; attemptId: string }) => (
    <div data-testid="mock-code-evidence-panel">
      evidence for {props.examId}/{props.attemptId}
    </div>
  ),
}));

import ExamAttemptDetail from '../pages/hr/ExamAttemptDetail';

const EXAM: ExamDetail = {
  id: 'e-1',
  title: 'Backend Screening',
  description: null,
  target_job_title: null,
  pass_threshold: 60,
  time_limit_seconds: null,
  allow_retake: false,
  auto_advance_on_pass: false,
  status: 'published',
  kind: 'coding',
  created_at: '2026-07-01T10:00:00.000Z',
  attempt_count: 1,
  questions: [],
};

const ATTEMPT: AttemptResult = {
  attempt_id: 'a-1',
  applicant_id: 'ap-1',
  applicant_name: 'Rahul Verma',
  score_raw: 8,
  score_max: 10,
  score_percent: 80,
  passed: true,
  status: 'submitted',
  submitted_at: '2026-08-01T10:00:00.000Z',
  attempt_no: 1,
};

const CODING_BREAKDOWN: AttemptBreakdown = {
  attempt_id: 'a-1',
  score_percent: 80,
  passed: true,
  per_question: {},
  coding: { 'q-1': { points: 10, raw: 8, language: 'python', submitted: true } },
  adjustment: null,
};

function renderAttempt(examId = 'e-1', attemptId = 'a-1') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/hr/exams/${examId}/attempts/${attemptId}`]}>
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
  listAttempts.mockResolvedValue([ATTEMPT]);
  getAttemptBreakdown.mockResolvedValue(CODING_BREAKDOWN);
});

describe('ExamAttemptDetail — the Code evidence tab', () => {
  it('shows the tab when the attempt has a coding question, and switches into it', async () => {
    const user = userEvent.setup();
    renderAttempt();

    const tab = await screen.findByRole('tab', { name: /code evidence/i });
    // Overview is showing by default.
    expect(await screen.findByText('MCQ correct')).toBeInTheDocument();
    expect(screen.queryByTestId('mock-code-evidence-panel')).not.toBeInTheDocument();

    await user.click(tab);

    expect(await screen.findByTestId('mock-code-evidence-panel')).toHaveTextContent('evidence for e-1/a-1');
    // The overview content is not shown at the same time as the evidence tab.
    expect(screen.queryByText('MCQ correct')).not.toBeInTheDocument();
  });

  it('has no Code evidence tab for a pure MCQ attempt', async () => {
    getAttemptBreakdown.mockResolvedValue({
      attempt_id: 'a-1',
      score_percent: 80,
      passed: true,
      per_question: { 'q-1': true },
      coding: {},
      adjustment: null,
    });
    renderAttempt();

    await screen.findByText('MCQ correct');
    expect(screen.queryByRole('tab', { name: /code evidence/i })).not.toBeInTheDocument();
  });
});

describe('ExamAttemptDetail — PH4-D2 time-adjustment badge', () => {
  it('shows the exact percentage this attempt was given', async () => {
    getAttemptBreakdown.mockResolvedValue({
      ...CODING_BREAKDOWN,
      adjustment: { extra_time_percent: 50, extra_time_seconds: 1800, auto_submit_relaxed: false },
    });
    renderAttempt();

    expect(await screen.findByText('Time adjustment applied: +50%')).toBeInTheDocument();
  });

  it('falls back to the raw seconds when no percentage was recorded on the row', async () => {
    getAttemptBreakdown.mockResolvedValue({
      ...CODING_BREAKDOWN,
      adjustment: { extra_time_percent: null, extra_time_seconds: 900, auto_submit_relaxed: true },
    });
    renderAttempt();

    expect(
      await screen.findByText('Time adjustment applied: +900s · auto-submit relaxed'),
    ).toBeInTheDocument();
  });

  it('shows nothing when the attempt carries no adjustment', async () => {
    renderAttempt();
    await screen.findByText('MCQ correct');
    expect(screen.queryByText(/Time adjustment applied/)).not.toBeInTheDocument();
  });

  it('carries no note or basis — only the two facts the server returns', async () => {
    getAttemptBreakdown.mockResolvedValue({
      ...CODING_BREAKDOWN,
      adjustment: { extra_time_percent: 25, extra_time_seconds: 900, auto_submit_relaxed: true },
    });
    renderAttempt();

    expect(
      await screen.findByText('Time adjustment applied: +25% · auto-submit relaxed'),
    ).toBeInTheDocument();
  });
});
