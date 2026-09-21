// TaskSubmissionSection — PH4-D4. The candidate drawer's view of a job
// simulation / portfolio submission: lifecycle, re-issue and withdraw.
//
// The one thing this file exists to pin: THERE IS NO REJECT ACTION HERE. A
// submission is evidence, not a decision (CLAUDE.md constraint 9 / D-05) —
// the round's pass/hold verdict is recorded on the decision queue, through
// the existing round-review action, never from this section.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { TaskSubmissionSummary } from '../api/jobTasks';

const listEnrolmentTasks = vi.fn();
const reissueTaskSubmission = vi.fn();
const withdrawTaskSubmission = vi.fn();
vi.mock('../api/jobTasks', () => ({
  listEnrolmentTasks: (...a: unknown[]) => listEnrolmentTasks(...a) as unknown,
  reissueTaskSubmission: (...a: unknown[]) => reissueTaskSubmission(...a) as unknown,
  withdrawTaskSubmission: (...a: unknown[]) => withdrawTaskSubmission(...a) as unknown,
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
  },
}));

import TaskSubmissionSection from '../components/hr/TaskSubmissionSection';

function renderSection(enrolmentId = 'en-1') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TaskSubmissionSection enrolmentId={enrolmentId} />
    </QueryClientProvider>,
  );
}

const SUBMISSION: TaskSubmissionSummary = {
  id: 'sub-1',
  enrolment_id: 'en-1',
  round_id: 'r-1',
  round_title: 'Take-home simulation',
  kind: 'job_simulation',
  status: 'submitted',
  candidate_name: 'Nadia Newbie',
  due_at: '2026-09-20T00:00:00.000Z',
  started_at: '2026-09-18T00:00:00.000Z',
  submitted_at: '2026-09-19T00:00:00.000Z',
  attempt_no: 1,
  created_at: '2026-09-15T00:00:00.000Z',
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('TaskSubmissionSection', () => {
  it('renders nothing for an application with no task round', async () => {
    listEnrolmentTasks.mockResolvedValue([]);
    const { container } = renderSection();
    await waitFor(() => expect(listEnrolmentTasks).toHaveBeenCalledWith('en-1'));
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('shows the round, its lifecycle state and the key dates', async () => {
    listEnrolmentTasks.mockResolvedValue([SUBMISSION]);
    renderSection();

    expect(await screen.findByText('Take-home simulation')).toBeInTheDocument();
    expect(screen.getByText('submitted')).toBeInTheDocument();
  });

  it('never offers a reject action of any kind', async () => {
    listEnrolmentTasks.mockResolvedValue([SUBMISSION]);
    renderSection();
    await screen.findByText('Take-home simulation');

    for (const label of [/reject/i, /fail/i, /decline/i, /hire/i, /advance/i]) {
      expect(screen.queryByRole('button', { name: label })).not.toBeInTheDocument();
    }
  });

  it('withdraws an open submission with an optional reason', async () => {
    const user = userEvent.setup();
    listEnrolmentTasks.mockResolvedValue([
      { ...SUBMISSION, status: 'in_progress', submitted_at: null },
    ]);
    withdrawTaskSubmission.mockResolvedValue({ ...SUBMISSION, status: 'withdrawn' });
    renderSection();

    await screen.findByText('Take-home simulation');
    await user.type(
      screen.getByLabelText(/withdraw reason for take-home simulation/i),
      'Role was closed',
    );
    await user.click(screen.getByRole('button', { name: /^withdraw$/i }));

    await waitFor(() =>
      expect(withdrawTaskSubmission).toHaveBeenCalledWith('sub-1', 'Role was closed'),
    );
    expect(toastSuccess).toHaveBeenCalled();
  });

  it('offers no withdraw control once a submission is no longer open', async () => {
    listEnrolmentTasks.mockResolvedValue([SUBMISSION]); // status: submitted
    renderSection();
    await screen.findByText('Take-home simulation');
    expect(screen.queryByRole('button', { name: /^withdraw$/i })).not.toBeInTheDocument();
  });

  it('re-issues a fresh link for an expired submission', async () => {
    const user = userEvent.setup();
    listEnrolmentTasks.mockResolvedValue([
      { ...SUBMISSION, status: 'expired', submitted_at: null },
    ]);
    reissueTaskSubmission.mockResolvedValue({ ...SUBMISSION, id: 'sub-2', attempt_no: 2 });
    renderSection();

    await screen.findByText('Take-home simulation');
    await user.click(screen.getByRole('button', { name: /re-issue a link/i }));

    await waitFor(() => expect(reissueTaskSubmission).toHaveBeenCalledWith('sub-1'));
    expect(toastSuccess).toHaveBeenCalled();
  });

  it('says so when the submissions could not be loaded', async () => {
    listEnrolmentTasks.mockRejectedValue(new Error('boom'));
    renderSection();
    expect(
      await screen.findByText(/could not load this application.s task submissions/i),
    ).toBeInTheDocument();
  });
});
