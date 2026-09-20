// QuestionReviews (HR) and QuestionReviews (super admin) — PH4-D1. Both are
// thin wrappers around the shared QuestionReviewQueue; what matters here is
// that each wires up its OWN endpoints (`/hr` vs `/admin`) — the review logic
// itself is covered by QuestionReviewQueue.test.tsx.

import type { ReactElement } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const listBankReviewQueue = vi.fn();
const approveBankQuestion = vi.fn();
const requestBankQuestionChanges = vi.fn();
const listAdminQuestionReviews = vi.fn();
const approveBankQuestionAdmin = vi.fn();
const requestBankQuestionChangesAdmin = vi.fn();
vi.mock('../api/questionBanks', () => ({
  listBankReviewQueue: (...a: unknown[]) => listBankReviewQueue(...a) as unknown,
  approveBankQuestion: (...a: unknown[]) => approveBankQuestion(...a) as unknown,
  requestBankQuestionChanges: (...a: unknown[]) => requestBankQuestionChanges(...a) as unknown,
  listAdminQuestionReviews: (...a: unknown[]) => listAdminQuestionReviews(...a) as unknown,
  approveBankQuestionAdmin: (...a: unknown[]) => approveBankQuestionAdmin(...a) as unknown,
  requestBankQuestionChangesAdmin: (...a: unknown[]) =>
    requestBankQuestionChangesAdmin(...a) as unknown,
}));

import HrQuestionReviews from '../pages/hr/QuestionReviews';
import SuperAdminQuestionReviews from '../pages/superadmin/QuestionReviews';

function renderWith(node: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  listBankReviewQueue.mockResolvedValue([]);
  listAdminQuestionReviews.mockResolvedValue([]);
});

describe('HR QuestionReviews', () => {
  it('fetches through the HR review-queue endpoint', async () => {
    renderWith(<HrQuestionReviews />);
    expect(await screen.findByText('Nothing is waiting for review.')).toBeInTheDocument();
    expect(listBankReviewQueue).toHaveBeenCalledTimes(1);
    expect(listAdminQuestionReviews).not.toHaveBeenCalled();
  });
});

describe('Super admin QuestionReviews', () => {
  it('fetches through the admin review-queue endpoint, mirroring HR', async () => {
    renderWith(<SuperAdminQuestionReviews />);
    expect(await screen.findByText('Nothing is waiting for review.')).toBeInTheDocument();
    expect(listAdminQuestionReviews).toHaveBeenCalledTimes(1);
    expect(listBankReviewQueue).not.toHaveBeenCalled();
  });
});
