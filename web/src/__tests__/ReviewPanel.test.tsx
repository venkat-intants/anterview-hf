// ReviewPanel — PH4-O6. The review lifecycle's actions, one per state, and
// what a 422 on submit renders as (an object detail carrying either a
// ValidationReport or a SimulationResult, never a bare string).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ApiError } from '../api/client';
import ReviewPanel from '../components/workflow/ReviewPanel';
import type { ReviewState } from '../api/workflowReview';

const getReview = vi.fn();
const submitForReview = vi.fn();
const withdrawReview = vi.fn();
const reopenForEdits = vi.fn();
vi.mock('../api/workflowReview', () => ({
  getReview: (...a: unknown[]) => getReview(...a) as unknown,
  submitForReview: (...a: unknown[]) => submitForReview(...a) as unknown,
  withdrawReview: (...a: unknown[]) => withdrawReview(...a) as unknown,
  reopenForEdits: (...a: unknown[]) => reopenForEdits(...a) as unknown,
  reviewErrorDetail: (err: unknown) => {
    const detail = (err as { detail?: unknown } | null)?.detail;
    if (detail && typeof detail === 'object' && 'message' in detail) return detail;
    return null;
  },
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

function renderPanel(onChanged = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { onChanged, ...render(
    <QueryClientProvider client={client}>
      <ReviewPanel workflowId="wf-1" onChanged={onChanged} />
    </QueryClientProvider>,
  ) };
}

function state(over: Partial<ReviewState> = {}): ReviewState {
  return {
    review_status: 'draft', submitted_at: null, submitted_by_name: null,
    reviewed_at: null, reviewed_by_name: null, note: null, history: [],
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ReviewPanel — draft', () => {
  it('offers to submit for review, with an optional note', async () => {
    getReview.mockResolvedValue(state());
    submitForReview.mockResolvedValue({
      review_status: 'in_review',
      simulation: { errors: 0 },
      approvers: 2,
      review: state({ review_status: 'in_review' }),
    });
    const user = userEvent.setup();
    const { onChanged } = renderPanel();
    await screen.findByText('Draft');

    await user.type(screen.getByLabelText(/note for the reviewer/i), 'Please look soon');
    await user.click(screen.getByRole('button', { name: 'Submit for review' }));

    await waitFor(() => expect(submitForReview).toHaveBeenCalledWith('wf-1', 'Please look soon'));
    expect(onChanged).toHaveBeenCalledWith(expect.objectContaining({ review_status: 'in_review' }));
    expect(toastSuccess).toHaveBeenCalledWith('Submitted for review — 2 approvers notified');
  });

  it('renders a 422 validation-report detail rather than a bare failure', async () => {
    getReview.mockResolvedValue(state());
    submitForReview.mockRejectedValue(
      new ApiError('Fix these before submitting for review.', 422, {
        message: 'Fix these before submitting for review.',
        validation: { publishable: false, errors: ['Fundamentals needs questions.'], warnings: [], coverage: [] },
      }),
    );
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('Draft');

    await user.click(screen.getByRole('button', { name: 'Submit for review' }));

    expect(await screen.findByText('Fundamentals needs questions.')).toBeTruthy();
    expect(toastError).toHaveBeenCalledWith('Fix these before submitting for review.');
  });

  it('renders a 422 simulation-report detail', async () => {
    getReview.mockResolvedValue(state());
    submitForReview.mockRejectedValue(
      new ApiError('The dry run found errors. Fix them first.', 422, {
        message: 'The dry run found errors. Fix them first.',
        simulation: {
          rounds: [{ findings: [{ severity: 'error', message: 'A branch points nowhere.' }] }],
          workflow_findings: [],
        },
      }),
    );
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('Draft');

    await user.click(screen.getByRole('button', { name: 'Submit for review' }));

    expect(await screen.findByText('A branch points nowhere.')).toBeTruthy();
  });
});

describe('ReviewPanel — changes requested', () => {
  it('shows the reviewer note and still offers to resubmit', async () => {
    getReview.mockResolvedValue(
      state({ review_status: 'changes_requested', note: 'Add a human review round.', reviewed_by_name: 'Priya SA' }),
    );
    renderPanel();
    expect(await screen.findByText('Changes requested')).toBeTruthy();
    expect(screen.getByText('Add a human review round.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Submit for review' })).toBeTruthy();
  });
});

describe('ReviewPanel — in review', () => {
  it('is locked, and offers to withdraw', async () => {
    getReview.mockResolvedValue(
      state({ review_status: 'in_review', submitted_by_name: 'Rekha HR' }),
    );
    withdrawReview.mockResolvedValue(state({ review_status: 'draft' }));
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('In review');

    expect(screen.getByText(/submitted by Rekha HR/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Submit for review' })).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Withdraw from review' }));
    await waitFor(() => expect(withdrawReview).toHaveBeenCalledWith('wf-1'));
  });
});

describe('ReviewPanel — approved', () => {
  it('offers to reopen, warning it will need approving again', async () => {
    getReview.mockResolvedValue(state({ review_status: 'approved', reviewed_by_name: 'Priya SA' }));
    reopenForEdits.mockResolvedValue(state({ review_status: 'draft' }));
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('Approved');

    expect(screen.getByText(/will need approving again/)).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Reopen for edits' }));
    await waitFor(() => expect(reopenForEdits).toHaveBeenCalledWith('wf-1', ''));
  });
});

describe('ReviewPanel — history', () => {
  it('is collapsible and shows who did what, when', async () => {
    getReview.mockResolvedValue(
      state({
        history: [
          { action: 'submitted', note: 'Ready', actor_name: 'Rekha HR', at: '2026-09-01T00:00:00.000Z', simulation_id: null },
        ],
      }),
    );
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('Draft');

    expect(screen.queryByText(/Submitted for review — Rekha HR/)).toBeNull();
    await user.click(screen.getByRole('button', { name: /show history/i }));
    expect(screen.getByText(/Submitted for review — Rekha HR/)).toBeTruthy();
  });
});
