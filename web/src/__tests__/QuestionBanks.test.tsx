// QuestionBanks — PH4-D1. The banks list: rename, archive, the per-status counts
// plus "used in N exams", the one next action per bank, search and the status
// filter, and "New bank" as the page's single primary (it opens the creation
// flow, which lives on the bank page - see QuestionBankFlow.test.tsx).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { BankOut } from '../api/questionBanks';

const listQuestionBanks = vi.fn();
const createQuestionBank = vi.fn();
const updateQuestionBank = vi.fn();
const archiveQuestionBank = vi.fn();
vi.mock('../api/questionBanks', async (importActual) => ({
  ...(await importActual<typeof import('../api/questionBanks')>()),
  listQuestionBanks: (...a: unknown[]) => listQuestionBanks(...a) as unknown,
  createQuestionBank: (...a: unknown[]) => createQuestionBank(...a) as unknown,
  updateQuestionBank: (...a: unknown[]) => updateQuestionBank(...a) as unknown,
  archiveQuestionBank: (...a: unknown[]) => archiveQuestionBank(...a) as unknown,
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

import QuestionBanks from '../pages/hr/QuestionBanks';

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <QuestionBanks />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const BANK: BankOut = {
  id: 'bank-1',
  name: 'Backend Core',
  description: 'Core CS fundamentals',
  counts: { draft: 2, approved: 5 },
  used_in_exams: 3,
  created_at: '2026-09-01T00:00:00.000Z',
  updated_at: '2026-09-01T00:00:00.000Z',
};

beforeEach(() => {
  vi.clearAllMocks();
  listQuestionBanks.mockResolvedValue([BANK]);
});

describe('QuestionBanks - the list', () => {
  it('shows each bank with its status counts and usage', async () => {
    renderPage();
    expect(await screen.findByRole('link', { name: 'Backend Core' })).toBeInTheDocument();
    expect(screen.getByText('2 draft')).toBeInTheDocument();
    expect(screen.getByText('5 approved')).toBeInTheDocument();
    expect(screen.getByText('Used in 3 exams')).toBeInTheDocument();
  });

  it('shows an empty state when there are no banks', async () => {
    listQuestionBanks.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText('No banks yet')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Create your first bank' })).toHaveAttribute(
      'href',
      '/hr/question-banks/new',
    );
  });
});

describe('QuestionBanks - next action per bank', () => {
  const at = (id: string, name: string, counts: BankOut['counts']): BankOut => ({
    ...BANK,
    id,
    name,
    counts,
  });

  it('links each bank to the stage it needs, in words that say what to do', async () => {
    listQuestionBanks.mockResolvedValue([
      at('b-empty', 'Empty bank', {}),
      at('b-drafts', 'Drafty bank', { draft: 3, approved: 1 }),
      at('b-wait', 'Waiting bank', { in_review: 2 }),
      at('b-ready', 'Ready bank', { approved: 5 }),
    ]);
    renderPage();
    await screen.findByRole('link', { name: 'Empty bank' });

    const hrefOf = (re: RegExp) => screen.getByRole('link', { name: re }).getAttribute('href');
    expect(hrefOf(/Add the first questions/)).toBe('/hr/question-banks/b-empty?stage=add');
    expect(hrefOf(/3 drafts to submit/)).toBe('/hr/question-banks/b-drafts?stage=check');
    expect(hrefOf(/Waiting on review/)).toBe('/hr/question-banks/b-wait?stage=send');
    expect(hrefOf(/Ready to use in exams/)).toBe('/hr/question-banks/b-ready?stage=approved');
  });

  it('shows status counts only from the real fields, and a plain note for an empty bank', async () => {
    listQuestionBanks.mockResolvedValue([
      at('b-1', 'Mixed', { draft: 1, in_review: 2, approved: 3, retired: 4 }),
      at('b-2', 'Nothing', {}),
    ]);
    renderPage();
    await screen.findByRole('link', { name: 'Mixed' });
    for (const t of ['1 draft', '2 in review', '3 approved', '4 retired']) {
      expect(screen.getByText(t)).toBeInTheDocument();
    }
    expect(screen.getByText('No questions')).toBeInTheDocument();
  });
});

describe('QuestionBanks - search and filter', () => {
  beforeEach(() => {
    listQuestionBanks.mockResolvedValue([
      { ...BANK, id: 'b-1', name: 'SQL basics', description: null, counts: { draft: 2 } },
      {
        ...BANK,
        id: 'b-2',
        name: 'Networking',
        description: 'TCP and DNS',
        counts: { approved: 4 },
      },
      { ...BANK, id: 'b-3', name: 'Archive me', description: null, counts: {} },
    ]);
  });

  it('narrows by search over the name and the description', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('link', { name: 'SQL basics' });

    await user.type(screen.getByLabelText('Search banks'), 'dns');
    expect(screen.queryByRole('link', { name: 'SQL basics' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Networking' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('1 of 3 banks');
  });

  it('narrows by what the bank needs', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('link', { name: 'SQL basics' });

    await user.selectOptions(screen.getByLabelText('Filter banks by status'), 'drafts');
    expect(screen.getByRole('link', { name: 'SQL basics' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Networking' })).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('Filter banks by status'), 'empty');
    expect(screen.getByRole('link', { name: 'Archive me' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'SQL basics' })).not.toBeInTheDocument();
  });

  it('says so when nothing matches, and offers no kind filter the list cannot honour', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('link', { name: 'SQL basics' });
    await user.type(screen.getByLabelText('Search banks'), 'zzzz');
    expect(screen.getByText('No banks match')).toBeInTheDocument();
    expect(screen.queryByLabelText(/kind/i)).not.toBeInTheDocument();
  });
});

describe('QuestionBanks - MCQ / coding split (optional kind_counts)', () => {
  it('shows "n MCQ · n coding" and a kind filter when a bank carries kind_counts', async () => {
    listQuestionBanks.mockResolvedValue([
      { ...BANK, id: 'b-1', name: 'Mixed bank', kind_counts: { mcq: 12, coding: 3 } },
      { ...BANK, id: 'b-2', name: 'Only MCQ', kind_counts: { mcq: 4, coding: 0 } },
      { ...BANK, id: 'b-3', name: 'Old row' },
    ]);
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('link', { name: 'Mixed bank' });

    expect(screen.getByText('12 MCQ · 3 coding')).toBeInTheDocument();
    expect(screen.getByText('4 MCQ · 0 coding')).toBeInTheDocument();
    // The row that did not report a split shows nothing for it, not zeros.
    expect(screen.getAllByTestId('bank-kind-counts')).toHaveLength(2);

    const filter = screen.getByLabelText('Filter banks by question type');
    await user.selectOptions(filter, 'coding');
    expect(screen.getByRole('link', { name: 'Mixed bank' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Only MCQ' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Old row' })).not.toBeInTheDocument();

    await user.selectOptions(filter, 'mcq');
    expect(screen.getByRole('link', { name: 'Only MCQ' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Old row' })).not.toBeInTheDocument();
  });

  it('shows no split and no kind filter when no bank carries kind_counts', async () => {
    renderPage();
    await screen.findByRole('link', { name: 'Backend Core' });
    expect(screen.queryByTestId('bank-kind-counts')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Filter banks by question type')).not.toBeInTheDocument();
    expect(screen.queryByText(/MCQ ·/)).not.toBeInTheDocument();
  });
});

describe('QuestionBanks - New bank is the one primary', () => {
  it('is a link into the creation flow, and the permanent create form is gone', async () => {
    renderPage();
    await screen.findByRole('link', { name: 'Backend Core' });

    expect(screen.getByRole('link', { name: 'New bank' })).toHaveAttribute(
      'href',
      '/hr/question-banks/new',
    );
    expect(screen.queryByLabelText('Name')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Create' })).not.toBeInTheDocument();
    expect(createQuestionBank).not.toHaveBeenCalled();
  });
});

describe('QuestionBanks - rename and archive', () => {
  it('renames a bank', async () => {
    const user = userEvent.setup();
    updateQuestionBank.mockResolvedValue({ ...BANK, name: 'Backend Fundamentals' });
    renderPage();
    await screen.findByRole('link', { name: 'Backend Core' });

    await user.click(screen.getByRole('button', { name: 'Rename' }));
    const input = screen.getByLabelText('New name for Backend Core');
    await user.clear(input);
    await user.type(input, 'Backend Fundamentals');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(updateQuestionBank).toHaveBeenCalledWith('bank-1', { name: 'Backend Fundamentals' }),
    );
  });

  it('archives a bank after confirming', async () => {
    const user = userEvent.setup();
    archiveQuestionBank.mockResolvedValue({ id: 'bank-1', archived: true });
    renderPage();
    await screen.findByRole('link', { name: 'Backend Core' });

    await user.click(screen.getByRole('button', { name: 'Archive' }));
    // Archiving is confirmed with "Archive", not "Delete" (audit X5).
    expect(screen.queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Archive' }));

    await waitFor(() => expect(archiveQuestionBank).toHaveBeenCalledWith('bank-1'));
  });
});
