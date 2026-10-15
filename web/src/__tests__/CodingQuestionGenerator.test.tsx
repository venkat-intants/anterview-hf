// CodingQuestionGenerator — drafting coding questions for a bank with AI.
//
// The properties this panel must not lose:
//
//   1. Generating SAVES NOTHING. Only "Add n to bank as drafts" writes, and it
//      writes exactly the drafts HR kept.
//   2. The preview says nobody ran the code, and marks which tests are hidden.
//   3. A failure is shown where the person is looking, with a way to retry —
//      and Cancel really abandons the request rather than hiding it.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ApiError } from '../api/client';

const api = {
  generateBankCodingQuestions: vi.fn(),
  createBankQuestionsBulk: vi.fn(),
};
vi.mock('../api/questionBanks', async () => {
  const actual = await vi.importActual<typeof import('../api/questionBanks')>(
    '../api/questionBanks',
  );
  return {
    ...actual,
    generateBankCodingQuestions: (...a: unknown[]) =>
      api.generateBankCodingQuestions(...a) as unknown,
    createBankQuestionsBulk: (...a: unknown[]) => api.createBankQuestionsBulk(...a) as unknown,
  };
});

import { CodingQuestionGenerator } from '../components/hr/bank/CodingQuestionGenerator';
import {
  REFERENCE_UNVERIFIED_TAG,
  type BankCodingGenerateResult,
  type GeneratedBankCodingQuestion,
} from '../api/questionBanks';

function draft(over: Partial<GeneratedBankCodingQuestion> = {}): GeneratedBankCodingQuestion {
  return {
    kind: 'coding',
    prompt: 'Read two integers a and b and print their sum.',
    points: 100,
    difficulty: 'medium',
    language: 'en',
    allowed_languages: ['python'],
    starter_code: null,
    reference_solution: 'a, b = map(int, input().split())\nprint(a + b)',
    reference_solution_language: 'python',
    reference_solution_verified: false,
    test_cases: [
      { stdin: '2 3', expected_output: '5', is_sample: true, weight: 1 },
      { stdin: '-4 10', expected_output: '6', is_sample: false, weight: 2 },
    ],
    time_limit_ms: 5000,
    tags: [REFERENCE_UNVERIFIED_TAG],
    warnings: [],
    ...over,
  };
}

const TWO: BankCodingGenerateResult = {
  questions: [
    draft(),
    draft({ prompt: 'Read n integers and print the largest.', warnings: ['1 duplicate test case removed'] }),
  ],
  requested: 2,
  dropped: [],
  not_returned: 0,
};

const onGenerated = vi.fn();

let client: QueryClient;

function Panel(props: { disabled?: boolean }): JSX.Element {
  return (
    <QueryClientProvider client={client}>
      <CodingQuestionGenerator bankId="bank-1" onGenerated={onGenerated} {...props} />
    </QueryClientProvider>
  );
}

function renderPanel(props: { disabled?: boolean } = {}) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<Panel {...props} />);
}

async function fillTopic(user: ReturnType<typeof userEvent.setup>, text = 'Arrays'): Promise<void> {
  await user.type(screen.getByLabelText('Topic or skill'), text);
}

async function generateTwo(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await fillTopic(user);
  await user.click(screen.getByRole('button', { name: 'Generate drafts' }));
  await screen.findByTestId('generator-step-review');
}

beforeEach(() => {
  vi.clearAllMocks();
  api.generateBankCodingQuestions.mockResolvedValue(TWO);
  api.createBankQuestionsBulk.mockImplementation((_bank: string, qs: unknown[]) =>
    Promise.resolve(qs.map((q, i) => ({ ...(q as object), id: `saved-${i}`, status: 'draft' }))),
  );
});

describe('CodingQuestionGenerator — describe', () => {
  it('opens on the describe step with one primary action', () => {
    renderPanel();
    expect(screen.getByTestId('generator-step-describe')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Generate drafts' })).toBeEnabled();
    expect(screen.queryByTestId('generator-step-review')).not.toBeInTheDocument();
  });

  it('refuses an empty topic inline and calls nothing', async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole('button', { name: 'Generate drafts' }));
    expect(screen.getByText('Say what the questions should be about.')).toBeInTheDocument();
    expect(screen.getByLabelText('Topic or skill')).toHaveAttribute('aria-invalid', 'true');
    expect(api.generateBankCodingQuestions).not.toHaveBeenCalled();
    expect(screen.getByTestId('generator-step-describe')).toBeInTheDocument();
  });

  it('refuses a request with no programming language', async () => {
    const user = userEvent.setup();
    renderPanel();
    await fillTopic(user);
    await user.click(screen.getByRole('button', { name: 'Python' })); // on by default -> off
    await user.click(screen.getByRole('button', { name: 'Generate drafts' }));
    expect(screen.getByText('Pick at least one programming language.')).toBeInTheDocument();
    expect(api.generateBankCodingQuestions).not.toHaveBeenCalled();
  });

  it('sends exactly what was chosen', async () => {
    const user = userEvent.setup();
    renderPanel();
    await fillTopic(user, '  Binary search  ');
    await user.type(screen.getByLabelText('Role (optional)'), 'Backend Engineer');
    await user.click(screen.getByRole('button', { name: 'hard' }));
    await user.click(screen.getByRole('button', { name: 'C++' }));
    await user.click(screen.getByRole('button', { name: 'Telugu' }));
    await user.click(screen.getByRole('button', { name: '3 questions' }));
    await user.click(screen.getByRole('button', { name: 'Generate drafts' }));

    await screen.findByTestId('generator-step-review');
    expect(api.generateBankCodingQuestions).toHaveBeenCalledTimes(1);
    const [bankId, params, opts] = api.generateBankCodingQuestions.mock.calls[0] as [
      string,
      unknown,
      { signal: AbortSignal },
    ];
    expect(bankId).toBe('bank-1');
    expect(params).toEqual({
      topic: 'Binary search',
      num_questions: 3,
      difficulty: 'hard',
      language: 'te',
      allowed_languages: ['python', 'cpp'],
      job_title: 'Backend Engineer',
    });
    expect(opts.signal).toBeInstanceOf(AbortSignal);
  });

  it('offers no more than the server allows per request', () => {
    renderPanel();
    expect(screen.getByRole('button', { name: '5 questions' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '6 questions' })).not.toBeInTheDocument();
  });

  it('cannot generate while disabled', () => {
    renderPanel({ disabled: true });
    expect(screen.getByRole('button', { name: 'Generate drafts' })).toBeDisabled();
  });
});

describe('CodingQuestionGenerator — generating', () => {
  it('shows honest progress, and Cancel abandons the request', async () => {
    const user = userEvent.setup();
    let resolve: (v: BankCodingGenerateResult) => void = () => undefined;
    api.generateBankCodingQuestions.mockReturnValue(
      new Promise<BankCodingGenerateResult>((r) => {
        resolve = r;
      }),
    );
    renderPanel();
    await fillTopic(user);
    await user.click(screen.getByRole('button', { name: 'Generate drafts' }));

    const pane = await screen.findByTestId('generator-step-generating');
    expect(within(pane).getByText(/usually takes up to a minute/)).toBeInTheDocument();
    expect(within(pane).getByText('Nothing is saved while this runs.')).toBeInTheDocument();
    // An elapsed clock, not an invented percentage.
    expect(within(pane).getByTestId('generator-elapsed')).toHaveTextContent('0 s elapsed');
    expect(within(pane).queryByRole('progressbar')).not.toBeInTheDocument();

    const [, , opts] = api.generateBankCodingQuestions.mock.calls[0] as [
      string,
      unknown,
      { signal: AbortSignal },
    ];
    await user.click(within(pane).getByRole('button', { name: 'Cancel' }));
    expect(opts.signal.aborted).toBe(true);
    expect(screen.getByTestId('generator-step-describe')).toBeInTheDocument();
    // What was typed is still there.
    expect(screen.getByLabelText('Topic or skill')).toHaveValue('Arrays');

    // A response that arrives after the cancel must not resurrect the review.
    resolve(TWO);
    await Promise.resolve();
    await Promise.resolve();
    expect(screen.queryByTestId('generator-step-review')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows a failure inline and retries with the same request', async () => {
    const user = userEvent.setup();
    api.generateBankCodingQuestions
      .mockRejectedValueOnce(new Error('AI generation failed: upstream timed out'))
      .mockResolvedValueOnce(TWO);
    renderPanel();
    await fillTopic(user);
    await user.click(screen.getByRole('button', { name: 'Generate drafts' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('AI generation failed: upstream timed out');
    expect(screen.getByTestId('generator-step-describe')).toBeInTheDocument();

    await user.click(within(alert).getByRole('button', { name: 'Try again' }));
    await screen.findByTestId('generator-step-review');
    expect(api.generateBankCodingQuestions).toHaveBeenCalledTimes(2);
    expect(api.generateBankCodingQuestions.mock.calls[1]?.[1]).toEqual(
      api.generateBankCodingQuestions.mock.calls[0]?.[1],
    );
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});

describe('CodingQuestionGenerator — what the server says when generation fails', () => {
  const PAUSED =
    'Usage limits are temporarily unavailable, so this action is paused. Please try again in a minute.';
  const DAILY =
    "Your company has reached today's limit for AI question generation. Try again tomorrow.";

  it('shows the 503 sentence inline, not a status code, and offers Try again', async () => {
    const user = userEvent.setup();
    api.generateBankCodingQuestions.mockRejectedValue(new ApiError(PAUSED, 503));
    renderPanel();
    await fillTopic(user);
    await user.click(screen.getByRole('button', { name: 'Generate drafts' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(PAUSED);
    expect(alert).not.toHaveTextContent('503');
    expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('shows the daily-limit sentence WITHOUT a retry button', async () => {
    const user = userEvent.setup();
    api.generateBankCodingQuestions.mockRejectedValue(new ApiError(DAILY, 429));
    renderPanel();
    await fillTopic(user);
    await user.click(screen.getByRole('button', { name: 'Generate drafts' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(DAILY);
    expect(within(alert).queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
  });

  it('offers Try again for the per-minute 429', async () => {
    const user = userEvent.setup();
    api.generateBankCodingQuestions.mockRejectedValue(
      new ApiError('Too many requests. Try again in a minute.', 429),
    );
    renderPanel();
    await fillTopic(user);
    await user.click(screen.getByRole('button', { name: 'Generate drafts' }));
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});

describe('CodingQuestionGenerator — a save that outlives the panel', () => {
  it('still refreshes the lists, but does not call into the unmounted parent', async () => {
    const user = userEvent.setup();
    let finish: (rows: unknown[]) => void = () => undefined;
    api.createBankQuestionsBulk.mockReturnValue(
      new Promise<unknown[]>((r) => {
        finish = r;
      }),
    );
    const { unmount } = renderPanel();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    await generateTwo(user);
    await user.click(screen.getByRole('button', { name: 'Add 2 to bank as drafts' }));
    await waitFor(() => expect(api.createBankQuestionsBulk).toHaveBeenCalledTimes(1));

    unmount();
    finish([{ id: 's1' }, { id: 's2' }]);
    await waitFor(() => expect(invalidate).toHaveBeenCalled());
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['hr', 'question-banks'] });
    expect(onGenerated).not.toHaveBeenCalled();
  });
});

describe('CodingQuestionGenerator — review', () => {
  it('previews every draft and saves nothing by generating', async () => {
    const user = userEvent.setup();
    renderPanel();
    await generateTwo(user);

    const drafts = screen.getAllByTestId('generator-draft');
    expect(drafts).toHaveLength(2);
    expect(within(drafts[0]).getByText('Read two integers a and b and print their sum.')).toBeInTheDocument();
    // Hidden tests are marked as hidden, in words.
    expect(within(drafts[0]).getAllByTestId('test-hidden')).toHaveLength(1);
    expect(within(drafts[0]).getByText('Hidden')).toBeInTheDocument();
    expect(within(drafts[0]).getByText('Sample')).toBeInTheDocument();
    // Nobody ran it, and it says so — once for the batch and once per draft.
    expect(screen.getByText('Nobody has run this code.')).toBeInTheDocument();
    expect(within(drafts[0]).getByTestId('unverified-badge')).toHaveTextContent('Unverified');
    // A repair the server made is not hidden from the person deciding.
    expect(within(drafts[1]).getByText(/1 duplicate test case removed/)).toBeInTheDocument();

    expect(screen.getByTestId('generator-summary')).toHaveTextContent('2 of 2 drafted.');
    expect(api.createBankQuestionsBulk).not.toHaveBeenCalled();
    expect(onGenerated).not.toHaveBeenCalled();
  });

  it('says what was left out when only some came back', async () => {
    const user = userEvent.setup();
    api.generateBankCodingQuestions.mockResolvedValue({
      questions: [draft()],
      requested: 4,
      dropped: [{ index: 1, reason: 'it has no hidden test case' }],
      not_returned: 2,
    });
    renderPanel();
    await fillTopic(user);
    await user.click(screen.getByRole('button', { name: 'Generate drafts' }));

    const summary = await screen.findByTestId('generator-summary');
    expect(summary).toHaveTextContent('1 of 4 drafted.');
    expect(summary).toHaveTextContent('Left out: it has no hidden test case.');
    expect(summary).toHaveTextContent('2 questions not returned by the AI.');
  });

  it('adds only the drafts that were kept, without the preview-only fields', async () => {
    const user = userEvent.setup();
    renderPanel();
    await generateTwo(user);

    await user.click(screen.getByRole('button', { name: 'Discard question 2' }));
    expect(screen.getByText('This draft will not be added.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Add 1 to bank as draft' }));

    await waitFor(() => expect(api.createBankQuestionsBulk).toHaveBeenCalledTimes(1));
    const [bankId, saved] = api.createBankQuestionsBulk.mock.calls[0] as [
      string,
      Record<string, unknown>[],
    ];
    expect(bankId).toBe('bank-1');
    expect(saved).toHaveLength(1);
    expect(saved[0]).toEqual({
      kind: 'coding',
      prompt: 'Read two integers a and b and print their sum.',
      points: 100,
      difficulty: 'medium',
      language: 'en',
      tags: [REFERENCE_UNVERIFIED_TAG],
      allowed_languages: ['python'],
      starter_code: null,
      reference_solution: 'a, b = map(int, input().split())\nprint(a + b)',
      test_cases: TWO.questions[0].test_cases,
      time_limit_ms: 5000,
    });
    // The client never sends a status or an origin: the server decides both.
    expect(saved[0]).not.toHaveProperty('status');
    expect(saved[0]).not.toHaveProperty('origin');

    await waitFor(() => expect(onGenerated).toHaveBeenCalledTimes(1));
    expect(onGenerated.mock.calls[0]?.[0]).toHaveLength(1);
    // Back at the start, saying what happened and what is still required.
    const note = await screen.findByTestId('generator-saved');
    expect(note).toHaveTextContent('1 draft added to this bank.');
    expect(note).toHaveTextContent('someone other than you must approve it');
  });

  it('lets a discarded draft be kept again', async () => {
    const user = userEvent.setup();
    renderPanel();
    await generateTwo(user);
    await user.click(screen.getByRole('button', { name: 'Discard question 1' }));
    expect(screen.getByRole('button', { name: 'Add 1 to bank as draft' })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: 'Keep question 1' }));
    expect(screen.getByRole('button', { name: 'Add 2 to bank as drafts' })).toBeEnabled();
  });

  it('cannot add when every draft is discarded', async () => {
    const user = userEvent.setup();
    renderPanel();
    await generateTwo(user);
    await user.click(screen.getByRole('button', { name: 'Discard question 1' }));
    await user.click(screen.getByRole('button', { name: 'Discard question 2' }));
    expect(screen.getByRole('button', { name: 'Add 0 to bank as drafts' })).toBeDisabled();
    expect(screen.getByText(/Every draft is discarded/)).toBeInTheDocument();
  });

  it('keeps the drafts on screen when saving fails, and retries', async () => {
    const user = userEvent.setup();
    api.createBankQuestionsBulk.mockRejectedValueOnce(new Error('Question bank not found.'));
    renderPanel();
    await generateTwo(user);
    await user.click(screen.getByRole('button', { name: 'Add 2 to bank as drafts' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Question bank not found.');
    expect(screen.getAllByTestId('generator-draft')).toHaveLength(2);
    expect(onGenerated).not.toHaveBeenCalled();

    await user.click(within(alert).getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(onGenerated).toHaveBeenCalledTimes(1));
    expect(api.createBankQuestionsBulk).toHaveBeenCalledTimes(2);
  });

  it('start over discards the preview without saving it', async () => {
    const user = userEvent.setup();
    renderPanel();
    await generateTwo(user);
    await user.click(screen.getByRole('button', { name: 'Start over' }));
    expect(screen.getByTestId('generator-step-describe')).toBeInTheDocument();
    expect(api.createBankQuestionsBulk).not.toHaveBeenCalled();
    expect(screen.queryByTestId('generator-saved')).not.toBeInTheDocument();
  });
});
