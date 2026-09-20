// WorkflowReviews — the super admin's approval queue and one version in full
// (PH4-O6, decision D4-2).
//
// What matters here: the queue is oldest-first and links into the detail; the
// detail renders branches as words a reviewer can act on, the validation and
// dry-run reports, and stage owners; Approve and Request changes are offered
// only while a version is actually waiting (`in_review`); and the note-length
// rule on "request changes" is enforced client-side the same as the server.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { PendingWorkflowReview, WorkflowReviewDetail } from '../api/workflowReview';

const listPendingWorkflowReviews = vi.fn();
const getWorkflowReviewDetail = vi.fn();
const approveWorkflowReview = vi.fn();
const requestWorkflowChanges = vi.fn();
vi.mock('../api/workflowReview', () => ({
  listPendingWorkflowReviews: (...a: unknown[]) => listPendingWorkflowReviews(...a) as unknown,
  getWorkflowReviewDetail: (...a: unknown[]) => getWorkflowReviewDetail(...a) as unknown,
  approveWorkflowReview: (...a: unknown[]) => approveWorkflowReview(...a) as unknown,
  requestWorkflowChanges: (...a: unknown[]) => requestWorkflowChanges(...a) as unknown,
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

let paramWorkflowId: string | undefined;
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useParams: () => ({ workflowId: paramWorkflowId }) };
});

import WorkflowReviews from '../pages/superadmin/WorkflowReviews';

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <WorkflowReviews />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const QUEUE_ROW: PendingWorkflowReview = {
  workflow_id: 'wf-1',
  version: 2,
  requisition_id: 'req-1',
  opening_title: 'Backend Engineer',
  submitted_at: '2026-09-10T00:00:00.000Z',
  submitted_by_name: 'Rekha HR',
  note: 'Ready for a look',
  rounds: 2,
};

const DETAIL: WorkflowReviewDetail = {
  workflow_id: 'wf-1',
  requisition_id: 'req-1',
  opening_title: 'Backend Engineer',
  version: 2,
  status: 'draft',
  settings: {},
  rounds: [
    {
      round_id: 'r-1', position: 0, title: 'Fundamentals', kind: 'mcq', pass_threshold: 60,
      deadline_days: 7, on_pass: 'Conversation', on_fail: null, fast_track_min_percent: 85,
      on_fast_track: 'Conversation', criteria: [{ name: 'Python proficiency', weight: 0.4 }],
    },
    {
      round_id: 'r-2', position: 1, title: 'Conversation', kind: 'ai_interview',
      pass_threshold: 60, deadline_days: 7, on_pass: null, on_fail: null,
      fast_track_min_percent: null, on_fast_track: null, criteria: [],
    },
  ],
  validation: { publishable: true, errors: [], warnings: ['Communication is unmeasured'], coverage: [] },
  simulation: {
    simulation_id: 'sim-1', workflow_id: 'wf-1', version: 2, fingerprint: 'fp',
    created_at: '2026-09-10T00:00:00.000Z', stale: false, run_by_name: 'Rekha HR',
    status: 'passed', errors: 0, warnings: 0, rounds: [], workflow_findings: [], scenarios: [],
  },
  stages: [
    { round_id: 'r-1', stage: 'Fundamentals', kind: 'mcq', owner_user_id: null, owner_name: null, sla_hours: null },
    { round_id: null, stage: 'Final decision', kind: 'decision', owner_user_id: 'u-1', owner_name: 'Priya HR', sla_hours: 48 },
  ],
  review: {
    review_status: 'in_review',
    submitted_at: '2026-09-10T00:00:00.000Z',
    submitted_by_name: 'Rekha HR',
    reviewed_at: null,
    reviewed_by_name: null,
    note: 'Ready for a look',
    history: [
      { action: 'submitted', note: 'Ready for a look', actor_name: 'Rekha HR', at: '2026-09-10T00:00:00.000Z', simulation_id: 'sim-1' },
    ],
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  paramWorkflowId = undefined;
  listPendingWorkflowReviews.mockResolvedValue([QUEUE_ROW]);
  getWorkflowReviewDetail.mockResolvedValue(DETAIL);
});

describe('WorkflowReviews — the queue', () => {
  it('lists a waiting version, oldest-looking first, linking to its detail', async () => {
    renderPage();
    await screen.findByText('Backend Engineer · v2');

    expect(screen.getByText('Ready for a look')).toBeTruthy();
    expect(screen.getByText(/by Rekha HR/)).toBeTruthy();
    const link = screen.getByRole('link', { name: 'Backend Engineer · v2' });
    expect(link).toHaveAttribute('href', '/superadmin/workflow-reviews/wf-1');
  });

  it('says so when nothing is waiting', async () => {
    listPendingWorkflowReviews.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText('Nothing is waiting on you.')).toBeTruthy();
  });
});

describe('WorkflowReviews — one version in full', () => {
  beforeEach(() => {
    paramWorkflowId = 'wf-1';
  });

  it('renders rounds with their branches in words, and the criteria they assess', async () => {
    renderPage();
    await screen.findByText('Fundamentals');

    expect(screen.getByText(/Assesses: Python proficiency/)).toBeTruthy();
    expect(screen.getAllByText(/If they pass/).length).toBe(2);
    // Fundamentals' fail branch is null → hold for a person, in words.
    expect(screen.getAllByText(/Hold for a person/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Fast-track at 85%/)).toBeTruthy();
  });

  it('shows the validation report', async () => {
    renderPage();
    await screen.findByText('Fundamentals');
    expect(screen.getByText('Communication is unmeasured')).toBeTruthy();
  });

  it('shows the dry run result', async () => {
    renderPage();
    await screen.findByText('Fundamentals');
    expect(screen.getByText('Passed')).toBeTruthy();
    // Anchored: the submission summary above also mentions "Rekha HR".
    expect(screen.getByText(/^by Rekha HR$/)).toBeTruthy();
  });

  it('says a candidate who waits for a person is not a failure (auto-advance off)', async () => {
    getWorkflowReviewDetail.mockResolvedValue({
      ...DETAIL,
      simulation: {
        ...DETAIL.simulation!,
        scenarios: [
          {
            id: 'SIM-001', candidate: 'Simulated candidate 1', description: 'passes every round',
            end: 'waiting', steps: [],
          },
        ],
      },
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Fundamentals');
    await user.click(screen.getByRole('button', { name: /show 1 scenario/i }));
    expect(screen.getByText(/waits for a person to move them on/)).toBeTruthy();
    expect(screen.queryByText(/could not finish/)).toBeNull();
  });

  it('shows stage owners and SLAs, including the final decision', async () => {
    renderPage();
    await screen.findByText('Fundamentals');
    // "Final decision" also appears as the Conversation round's pass branch.
    expect(screen.getAllByText('Final decision').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/Priya HR/)).toBeTruthy();
    expect(screen.getByText(/48h SLA/)).toBeTruthy();
  });

  it('shows the submission history', async () => {
    renderPage();
    await screen.findByText('Fundamentals');
    expect(screen.getByText(/Submitted for review — Rekha HR/)).toBeTruthy();
  });

  it('approves with an optional note', async () => {
    const user = userEvent.setup();
    approveWorkflowReview.mockResolvedValue({ review_status: 'approved' });
    renderPage();
    await screen.findByText('Fundamentals');

    await user.click(screen.getByRole('button', { name: 'Approve' }));
    await user.type(screen.getByLabelText(/note \(optional\)/i), 'Looks solid');
    await user.click(screen.getByRole('button', { name: 'Confirm approval' }));

    await waitFor(() => expect(approveWorkflowReview).toHaveBeenCalledWith('wf-1', 'Looks solid'));
    expect(toastSuccess).toHaveBeenCalled();
  });

  it('will not send "request changes" with fewer than 10 characters', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Fundamentals');

    await user.click(screen.getByRole('button', { name: 'Request changes' }));
    await user.type(screen.getByLabelText(/what needs to change/i), 'too short');

    expect(screen.getByRole('button', { name: 'Send back' })).toBeDisabled();
    expect(screen.getByText(/Write at least 10 characters/)).toBeTruthy();
    expect(requestWorkflowChanges).not.toHaveBeenCalled();
  });

  it('sends "request changes" once the note is long enough', async () => {
    const user = userEvent.setup();
    requestWorkflowChanges.mockResolvedValue({ review_status: 'changes_requested' });
    renderPage();
    await screen.findByText('Fundamentals');

    await user.click(screen.getByRole('button', { name: 'Request changes' }));
    await user.type(screen.getByLabelText(/what needs to change/i), 'Please add a review round');
    await user.click(screen.getByRole('button', { name: 'Send back' }));

    await waitFor(() =>
      expect(requestWorkflowChanges).toHaveBeenCalledWith('wf-1', 'Please add a review round'),
    );
  });

  it('shows a server error rather than failing silently', async () => {
    const user = userEvent.setup();
    approveWorkflowReview.mockRejectedValue(
      new Error('A workflow cannot be approved by the person who wrote it.'),
    );
    renderPage();
    await screen.findByText('Fundamentals');

    await user.click(screen.getByRole('button', { name: 'Approve' }));
    await user.click(screen.getByRole('button', { name: 'Confirm approval' }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        'A workflow cannot be approved by the person who wrote it.',
      ),
    );
  });

  it('offers no decision once a version is no longer waiting', async () => {
    getWorkflowReviewDetail.mockResolvedValue({
      ...DETAIL,
      review: { ...DETAIL.review, review_status: 'approved' },
    });
    renderPage();
    await screen.findByText('Fundamentals');

    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Request changes' })).toBeNull();
    expect(screen.getByText(/nothing for you to.*decide/)).toBeTruthy();
  });
});
