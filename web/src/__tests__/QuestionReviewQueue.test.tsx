// QuestionReviewQueue — PH4-D1. Shared body of the HR and super-admin
// bank-question review queues.
//
// What matters here: a question you wrote or submitted is disabled with the
// reason shown, rather than offered and then refused; "request changes"
// enforces the 5-character note client-side the same as the server; and a
// 403 that DOES reach the server (own_submission was stale, or the account
// is a different reviewer than expected) is shown exactly as the server
// wrote it — never swallowed or reworded.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { REFERENCE_UNVERIFIED_TAG, type ReviewQueueRow } from '../api/questionBanks';

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

import { QuestionReviewQueue } from '../components/bank/QuestionReviewQueue';

function row(over: Partial<ReviewQueueRow> = {}): ReviewQueueRow {
  return {
    id: 'q-1',
    bank_id: 'bank-1',
    root_id: 'q-1',
    version: 1,
    kind: 'mcq',
    prompt: 'What is a closure?',
    points: 1,
    options: ['a', 'b'],
    correct_index: 0,
    starter_code: null,
    reference_solution: null,
    allowed_languages: null,
    test_cases: null,
    time_limit_ms: null,
    difficulty: 'medium',
    language: 'en',
    competencies: [],
    tags: [],
    status: 'in_review',
    origin: 'authored',
    content_hash: 'hash',
    created_by_user_id: 'u-1',
    submitted_by_user_id: 'u-1',
    submitted_at: '2026-09-10T00:00:00.000Z',
    reviewed_by_user_id: null,
    reviewed_at: null,
    review_note: null,
    retired_by_user_id: null,
    retired_at: null,
    created_at: '2026-09-01T00:00:00.000Z',
    updated_at: '2026-09-01T00:00:00.000Z',
    bank_name: 'Backend Core',
    own_submission: false,
    reason: null,
    ...over,
  };
}

function renderQueue(props: {
  fetchQueue: () => Promise<ReviewQueueRow[]>;
  approve: (id: string, note?: string | null) => Promise<ReviewQueueRow>;
  requestChanges: (id: string, note: string) => Promise<ReviewQueueRow>;
}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <QuestionReviewQueue
        queryKey={['test', 'bank-review-queue']}
        fetchQueue={props.fetchQueue}
        approve={props.approve}
        requestChanges={props.requestChanges}
        emptyHint="Nothing yet."
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('QuestionReviewQueue — a question you wrote or submitted', () => {
  it('is disabled, with the reason shown, instead of offering Approve', async () => {
    const fetchQueue = vi
      .fn()
      .mockResolvedValue([
        row({ own_submission: true, reason: 'You wrote or submitted this question.' }),
      ]);
    renderQueue({ fetchQueue, approve: vi.fn(), requestChanges: vi.fn() });

    expect(await screen.findByText('You wrote or submitted this question.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Request changes' })).not.toBeInTheDocument();
  });
});

describe('QuestionReviewQueue — approving someone else’s question', () => {
  it('offers Approve and Request changes', async () => {
    const fetchQueue = vi.fn().mockResolvedValue([row()]);
    renderQueue({ fetchQueue, approve: vi.fn(), requestChanges: vi.fn() });

    expect(await screen.findByRole('button', { name: 'Approve' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Request changes' })).toBeInTheDocument();
  });

  it('shows the server’s own 403 sentence verbatim when approval is refused anyway', async () => {
    const user = userEvent.setup();
    const fetchQueue = vi.fn().mockResolvedValue([row()]);
    const approve = vi
      .fn()
      .mockRejectedValue(
        new Error('You wrote or submitted this question — another reviewer must approve it.'),
      );
    renderQueue({ fetchQueue, approve, requestChanges: vi.fn() });

    await user.click(await screen.findByRole('button', { name: 'Approve' }));
    await user.click(screen.getByRole('button', { name: 'Confirm approval' }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        'You wrote or submitted this question — another reviewer must approve it.',
      ),
    );
  });

  it('requires at least 5 characters before Send back is enabled', async () => {
    const user = userEvent.setup();
    const fetchQueue = vi.fn().mockResolvedValue([row()]);
    const requestChanges = vi.fn().mockResolvedValue(row({ status: 'draft' }));
    renderQueue({ fetchQueue, approve: vi.fn(), requestChanges });

    await user.click(await screen.findByRole('button', { name: 'Request changes' }));
    const noteInput = screen.getByLabelText('What needs to change (required)');
    const sendBack = screen.getByRole('button', { name: 'Send back' });
    expect(sendBack).toBeDisabled();

    await user.type(noteInput, 'fix');
    expect(sendBack).toBeDisabled();

    await user.type(noteInput, ' the options');
    expect(sendBack).toBeEnabled();

    await user.click(sendBack);
    await waitFor(() => expect(requestChanges).toHaveBeenCalledWith('q-1', 'fix the options'));
  });
});

describe('QuestionReviewQueue — empty and error states', () => {
  it('shows the empty hint when nothing is waiting', async () => {
    const fetchQueue = vi.fn().mockResolvedValue([]);
    renderQueue({ fetchQueue, approve: vi.fn(), requestChanges: vi.fn() });
    expect(await screen.findByText('Nothing is waiting for review.')).toBeInTheDocument();
    expect(screen.getByText('Nothing yet.')).toBeInTheDocument();
  });

  it('shows an error state when the queue fails to load', async () => {
    const fetchQueue = vi.fn().mockRejectedValue(new Error('boom'));
    renderQueue({ fetchQueue, approve: vi.fn(), requestChanges: vi.fn() });
    expect(
      await screen.findByText('Could not load the question review queue.'),
    ).toBeInTheDocument();
  });
});

const CODING_ROW = (over: Partial<ReviewQueueRow> = {}): ReviewQueueRow =>
  row({
    id: 'q-code',
    kind: 'coding',
    prompt: 'Reverse a string read from stdin.',
    options: null,
    correct_index: null,
    origin: 'ai_draft',
    reference_solution: 'print(input()[::-1])',
    allowed_languages: ['python'],
    test_cases: [
      { stdin: 'abc', expected_output: 'cba', is_sample: true, weight: 1 },
      { stdin: 'hello', expected_output: 'olleh', is_sample: false, weight: 2 },
    ],
    time_limit_ms: 5000,
    tags: [REFERENCE_UNVERIFIED_TAG],
    ...over,
  });

describe('QuestionReviewQueue - what a reviewer sees before deciding', () => {
  it('shows the kind, the AI-draft origin and the unverified-solution badge on every item', async () => {
    const fetchQueue = vi
      .fn()
      .mockResolvedValue([
        CODING_ROW(),
        row({ id: 'q-mcq', origin: 'authored' }),
        row({ id: 'q-mcq-ai', origin: 'ai_draft', prompt: 'AI-written MCQ' }),
      ]);
    renderQueue({ fetchQueue, approve: vi.fn(), requestChanges: vi.fn() });

    const items = await screen.findAllByTestId('review-row');
    expect(items).toHaveLength(3);

    const [coding, human, ai] = items;
    expect(within(coding).getByText('Coding')).toBeInTheDocument();
    expect(within(coding).getByTestId('ai-draft-badge')).toHaveTextContent('AI draft');
    expect(within(coding).getByTestId('row-unverified-badge')).toHaveTextContent(
      'Unverified',
    );

    expect(within(human).getByText('MCQ')).toBeInTheDocument();
    expect(within(human).queryByTestId('ai-draft-badge')).not.toBeInTheDocument();
    expect(within(human).queryByTestId('row-unverified-badge')).not.toBeInTheDocument();

    expect(within(ai).getByTestId('ai-draft-badge')).toBeInTheDocument();
    // An MCQ has no solution that could be unverified, AI or not.
    expect(within(ai).queryByTestId('row-unverified-badge')).not.toBeInTheDocument();
  });

  it('renders a coding question with its tests and code, not just its title', async () => {
    const fetchQueue = vi.fn().mockResolvedValue([CODING_ROW()]);
    renderQueue({ fetchQueue, approve: vi.fn(), requestChanges: vi.fn() });

    const view = await screen.findByTestId('bank-coding-question-view');
    expect(within(view).getByText('Problem statement')).toBeInTheDocument();
    expect(within(view).getAllByTestId('test-sample')).toHaveLength(1);
    expect(within(view).getAllByTestId('test-hidden')).toHaveLength(1);
    expect(view).toHaveTextContent('print(input()[::-1])');
    expect(within(view).getByTestId('unverified-badge')).toBeInTheDocument();
  });

  it('shows an MCQ’s options with the right one named in words, not colour', async () => {
    const fetchQueue = vi
      .fn()
      .mockResolvedValue([row({ options: ['red', 'green'], correct_index: 1 })]);
    renderQueue({ fetchQueue, approve: vi.fn(), requestChanges: vi.fn() });

    const content = await screen.findByTestId('review-content');
    expect(content).toHaveTextContent('red');
    expect(content).toHaveTextContent('green');
    expect(within(content).getAllByText('Correct answer')).toHaveLength(1);
  });

  it('keeps the decision unchanged: a coding item still needs the 5-character note to send back', async () => {
    const user = userEvent.setup();
    const requestChanges = vi.fn().mockResolvedValue(CODING_ROW({ status: 'draft' }));
    const fetchQueue = vi.fn().mockResolvedValue([CODING_ROW()]);
    renderQueue({ fetchQueue, approve: vi.fn(), requestChanges });

    await user.click(await screen.findByRole('button', { name: 'Request changes' }));
    expect(screen.getByRole('button', { name: 'Send back' })).toBeDisabled();
    await user.type(screen.getByLabelText('What needs to change (required)'), 'Test 2 is wrong');
    await user.click(screen.getByRole('button', { name: 'Send back' }));
    await waitFor(() => expect(requestChanges).toHaveBeenCalledWith('q-code', 'Test 2 is wrong'));
  });

  it('caps a long queue at five with Show all', async () => {
    const user = userEvent.setup();
    const many = Array.from({ length: 7 }, (_, i) =>
      row({ id: `q-${i}`, prompt: `Question ${i}` }),
    );
    renderQueue({
      fetchQueue: vi.fn().mockResolvedValue(many),
      approve: vi.fn(),
      requestChanges: vi.fn(),
    });
    expect(await screen.findAllByTestId('review-row')).toHaveLength(5);
    await user.click(screen.getByRole('button', { name: 'Show all 7 questions' }));
    expect(screen.getAllByTestId('review-row')).toHaveLength(7);
  });
});
