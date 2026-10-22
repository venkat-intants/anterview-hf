// ExamAttemptRedirect — PH5-E1. An exam-attempt citation carries the ATTEMPT id
// and nothing else (`CITATION_ROUTES.exam_attempt` = /hr/exams/attempts/{id}),
// while every attempt API in data_gateway is scoped by exam. App.tsx mounted no
// route at that shape at all, so the chip opened the 404 page.
//
// What is pinned here: the resolver finds the owning exam in ONE request (audit
// E25: it used to ask every exam for its attempts) and forwards to the real
// detail screen; when it cannot, it says whether the attempt is missing or the
// lookup failed, rather than leaving a spinner or calling a failure "not found".

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ApiError } from '../api/client';

const locateAttempt = vi.fn();
vi.mock('../api/exams', () => ({
  locateAttempt: (...a: unknown[]) => locateAttempt(...a) as unknown,
}));

import ExamAttemptRedirect from '../pages/hr/ExamAttemptRedirect';

const ATTEMPT_ID = 'at-7';

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
  it('forwards to the attempt detail screen of the exam that owns it, in one request', async () => {
    locateAttempt.mockResolvedValue({ attempt_id: ATTEMPT_ID, exam_id: 'ex-2' });

    renderResolver();

    expect(await screen.findByText('attempt detail screen')).toBeInTheDocument();
    expect(locateAttempt).toHaveBeenCalledTimes(1);
    expect(locateAttempt).toHaveBeenCalledWith(ATTEMPT_ID);
  });

  it('shows a searching state while the lookup is in flight', async () => {
    locateAttempt.mockReturnValue(new Promise(() => {}));

    renderResolver();

    expect(await screen.findByRole('status')).toHaveTextContent(/finding that exam attempt/i);
    expect(screen.queryByText('attempt detail screen')).not.toBeInTheDocument();
  });

  it('says the attempt is not there when the server says 404', async () => {
    locateAttempt.mockRejectedValue(new ApiError('Attempt not found.', 404));

    renderResolver();

    expect(await screen.findByText(/could not open that attempt/i)).toBeInTheDocument();
    expect(screen.getByText(/may have been deleted/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
  });

  it('distinguishes a failed lookup from a missing attempt, and can retry', async () => {
    // "We could not look" and "it is not there" are different facts, and only
    // one of them should send an HR manager hunting for a deleted record.
    locateAttempt
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce({ attempt_id: ATTEMPT_ID, exam_id: 'ex-1' });
    const user = userEvent.setup();

    renderResolver();

    expect(await screen.findByText(/could not be looked up just now/i)).toBeInTheDocument();
    expect(screen.queryByText(/may have been deleted/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('attempt detail screen')).toBeInTheDocument();
  });
});
