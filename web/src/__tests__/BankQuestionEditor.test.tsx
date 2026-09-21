// BankQuestionEditor — PH4-D1. Content is editable only while a question is
// a draft (or being created); anything else renders read-only. Covers MCQ
// validation and the shape handed to onSave.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { BankQuestion } from '../api/questionBanks';

const listBankCompetencies = vi.fn();
vi.mock('../api/questionBanks', () => ({
  listBankCompetencies: (...a: unknown[]) => listBankCompetencies(...a) as unknown,
}));

import { BankQuestionEditor } from '../components/bank/BankQuestionEditor';

function renderEditor(props: Parameters<typeof BankQuestionEditor>[0]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BankQuestionEditor {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listBankCompetencies.mockResolvedValue([]);
});

describe('BankQuestionEditor — creating', () => {
  it('defaults to MCQ, and refuses to save with a blank option', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    renderEditor({ onSave });

    await user.type(screen.getByLabelText('Prompt'), 'What is a closure?');
    await user.type(screen.getByLabelText('Option 1'), 'A function with state');
    // Options 2-4 are left blank.
    await user.click(screen.getByRole('button', { name: 'Create question' }));

    expect(await screen.findByText('Every option needs text.')).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it('saves a well-formed MCQ question', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    renderEditor({ onSave });

    await user.type(screen.getByLabelText('Prompt'), 'What is a closure?');
    const opts = ['A', 'B', 'C', 'D'];
    for (let i = 0; i < opts.length; i++) {
      await user.type(screen.getByLabelText(`Option ${i + 1}`), opts[i]);
    }
    await user.click(screen.getByLabelText('Mark option 2 correct'));
    await user.click(screen.getByRole('button', { name: 'Create question' }));

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: 'mcq',
        prompt: 'What is a closure?',
        options: ['A', 'B', 'C', 'D'],
        correct_index: 1,
      }),
    );
  });

  it('switches to the coding form when Coding is chosen', async () => {
    const user = userEvent.setup();
    renderEditor({ onSave: vi.fn() });
    await user.click(screen.getByRole('button', { name: 'Coding' }));
    expect(screen.getByText('Allowed languages')).toBeInTheDocument();
    expect(screen.queryByText('Options')).not.toBeInTheDocument();
  });
});

describe('BankQuestionEditor — anything but draft is read-only', () => {
  function approvedQuestion(): BankQuestion {
    return {
      id: 'q-1', bank_id: 'bank-1', root_id: 'q-1', version: 1, kind: 'mcq',
      prompt: 'What is a monad?', points: 1, options: ['a', 'b'], correct_index: 0,
      starter_code: null, reference_solution: null, allowed_languages: null, test_cases: null,
      time_limit_ms: null, difficulty: 'medium', language: 'en', competencies: [], tags: [],
      status: 'approved', origin: 'authored', content_hash: 'h',
      created_by_user_id: 'u-1', submitted_by_user_id: 'u-1', submitted_at: '2026-09-01T00:00:00.000Z',
      reviewed_by_user_id: 'u-2', reviewed_at: '2026-09-02T00:00:00.000Z', review_note: null,
      retired_by_user_id: null, retired_at: null,
      created_at: '2026-09-01T00:00:00.000Z', updated_at: '2026-09-02T00:00:00.000Z',
    };
  }

  it('shows the prompt and options as text, with no form controls', () => {
    renderEditor({ question: approvedQuestion(), onSave: vi.fn() });
    expect(screen.getByText('What is a monad?')).toBeInTheDocument();
    expect(screen.queryByLabelText('Prompt')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument();
    expect(screen.getByText(/only a draft can be edited/)).toBeInTheDocument();
  });

  it('is editable and pre-filled once the caller opens the new-version flow', () => {
    renderEditor({ question: approvedQuestion(), forceEditable: true, onSave: vi.fn() });
    expect(screen.getByLabelText('Prompt')).toHaveValue('What is a monad?');
    expect(screen.getByRole('button', { name: 'Save as new version' })).toBeInTheDocument();
  });
});

describe('BankQuestionEditor — the form belongs to the question it is showing', () => {
  function draft(over: Partial<BankQuestion>): BankQuestion {
    return {
      id: 'q-a', bank_id: 'bank-1', root_id: 'q-a', version: 1, kind: 'mcq',
      prompt: 'Question A', points: 1, options: ['a', 'b', 'c', 'd'], correct_index: 0,
      starter_code: null, reference_solution: null, allowed_languages: null, test_cases: null,
      time_limit_ms: null, difficulty: 'medium', language: 'en', competencies: [], tags: [],
      status: 'draft', origin: 'authored', content_hash: 'h',
      created_by_user_id: 'u-1', submitted_by_user_id: null, submitted_at: null,
      reviewed_by_user_id: null, reviewed_at: null, review_note: null,
      retired_by_user_id: null, retired_at: null,
      created_at: '2026-09-01T00:00:00.000Z', updated_at: '2026-09-01T00:00:00.000Z',
      ...over,
    };
  }

  // The regression this pins: form state was initialised once per mount, and
  // the page does not remount the editor when a question already in the query
  // cache is re-selected. Showing A while still holding B's state was reachable
  // by clicking A, then B, then A again — and "Save changes" then PATCHed B's
  // content onto A. The server cannot catch that: the request is well-formed,
  // correctly scoped and correctly authorised; only the content is wrong.
  it('follows the question it is given rather than keeping the previous one', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = render(
      <QueryClientProvider client={client}>
        <BankQuestionEditor question={draft({})} onSave={vi.fn()} />
      </QueryClientProvider>,
    );
    expect(screen.getByLabelText('Prompt')).toHaveValue('Question A');

    rerender(
      <QueryClientProvider client={client}>
        <BankQuestionEditor
          question={draft({ id: 'q-b', root_id: 'q-b', prompt: 'Question B' })}
          onSave={vi.fn()}
        />
      </QueryClientProvider>,
    );
    expect(screen.getByLabelText('Prompt')).toHaveValue('Question B');
  });

  it('does not wipe an in-progress edit when the same question is refetched', async () => {
    const user = userEvent.setup();
    const q = draft({});
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = render(
      <QueryClientProvider client={client}>
        <BankQuestionEditor question={q} onSave={vi.fn()} />
      </QueryClientProvider>,
    );
    await user.clear(screen.getByLabelText('Prompt'));
    await user.type(screen.getByLabelText('Prompt'), 'Half-typed edit');

    // A background refetch hands back the same id as a new object.
    rerender(
      <QueryClientProvider client={client}>
        <BankQuestionEditor question={{ ...q }} onSave={vi.fn()} />
      </QueryClientProvider>,
    );
    expect(screen.getByLabelText('Prompt')).toHaveValue('Half-typed edit');
  });
});
