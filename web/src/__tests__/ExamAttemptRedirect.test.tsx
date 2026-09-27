// ExamAttemptRedirect — PH5-E1. An exam-attempt citation carries the ATTEMPT id
// and nothing else (`CITATION_ROUTES.exam_attempt` = /hr/exams/attempts/{id}),
// while every attempt API in data_gateway is scoped by exam. App.tsx mounted no
// route at that shape at all, so the chip opened the 404 page.
//
// What is pinned here: the resolver finds the owning exam from the attempt lists
// the console can already read and forwards to the real detail screen, and when
// it cannot, it SAYS so rather than leaving a spinner or an empty card.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AttemptResult, ExamSummary } from '../api/exams';

const listExams = vi.fn();
const listAttempts = vi.fn();
vi.mock('../api/exams', () => ({
  listExams: (...a: unknown[]) => listExams(...a) as unknown,
  listAttempts: (...a: unknown[]) => listAttempts(...a) as unknown,
}));

import ExamAttemptRedirect from '../pages/hr/ExamAttemptRedirect';

const ATTEMPT_ID = 'at-7';

function exam(id: string, title: string): ExamSummary {
  return {
    id,
    title,
    description: null,
    target_job_title: null,
    pass_threshold: 60,
    time_limit_seconds: null,
    allow_retake: false,
    auto_advance_on_pass: false,
    status: 'published',
    kind: 'mcq',
    created_at: '2026-09-01T00:00:00.000Z',
    question_count: 5,
    attempt_count: 1,
  };
}

function attempt(id: string): AttemptResult {
  return {
    attempt_id: id,
    applicant_id: 'ap-1',
    applicant_name: 'Bhavya Nair',
    score_raw: 4,
    score_max: 5,
    score_percent: 80,
    passed: true,
    status: 'submitted',
    submitted_at: '2026-09-10T00:00:00.000Z',
    attempt_no: 1,
  };
}

/** Mounted as App.tsx mounts it, with the detail route as a landing marker so
 *  the redirect is observed as a real navigation rather than a mocked call. */
function renderResolver() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/hr/exams/attempts/${ATTEMPT_ID}`]}>
        <Routes>
          <Route path="/hr/exams/attempts/:attemptId" element={<ExamAttemptRedirect />} />
          <Route
            path="/hr/exams/:examId/attempts/:attemptId"
            element={<div>attempt detail screen</div>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ExamAttemptRedirect', () => {
  it('forwards to the attempt detail screen of the exam that owns it', async () => {
    listExams.mockResolvedValue([exam('ex-1', 'Aptitude'), exam('ex-2', 'Coding')]);
    listAttempts.mockImplementation((examId: string) =>
      Promise.resolve(examId === 'ex-2' ? [attempt(ATTEMPT_ID)] : [attempt('at-other')]),
    );

    renderResolver();

    expect(await screen.findByText('attempt detail screen')).toBeInTheDocument();
    // Every exam is asked, because a bare attempt id names no exam — the cost
    // this resolver exists to carry until the backend can answer directly.
    await waitFor(() => expect(listAttempts).toHaveBeenCalledWith('ex-2'));
  });

  it('shows a searching state while the lists are still arriving', async () => {
    listExams.mockResolvedValue([exam('ex-1', 'Aptitude')]);
    // Never settles — the honest "still looking" case.
    listAttempts.mockReturnValue(new Promise(() => {}));

    renderResolver();

    expect(await screen.findByRole('status')).toHaveTextContent(/finding that exam attempt/i);
    expect(screen.queryByText('attempt detail screen')).not.toBeInTheDocument();
  });

  it('says the attempt is not there rather than redirecting nowhere', async () => {
    listExams.mockResolvedValue([exam('ex-1', 'Aptitude')]);
    listAttempts.mockResolvedValue([attempt('at-other')]);

    renderResolver();

    expect(await screen.findByText(/could not open that attempt/i)).toBeInTheDocument();
    expect(screen.getByText(/may have been deleted/i)).toBeInTheDocument();
  });

  it('distinguishes a failed read from a missing attempt', async () => {
    // "We could not look" and "it is not there" are different facts, and only
    // one of them should send an HR manager hunting for a deleted record.
    listExams.mockRejectedValue(new Error('network'));

    renderResolver();

    expect(await screen.findByText(/exams could not be loaded/i)).toBeInTheDocument();
    expect(listAttempts).not.toHaveBeenCalled();
  });
});
