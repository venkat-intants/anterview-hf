// QuestionBanks — PH4-D1. The banks list: create, rename, archive, and the
// per-status counts plus "used in N exams".

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
vi.mock('../api/questionBanks', () => ({
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

describe('QuestionBanks — the list', () => {
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
    expect(
      await screen.findByText('No banks yet — create your first one above.'),
    ).toBeInTheDocument();
  });
});

describe('QuestionBanks — create', () => {
  it('creates a bank from the form', async () => {
    const user = userEvent.setup();
    createQuestionBank.mockResolvedValue({ ...BANK, id: 'bank-2', name: 'Frontend Core' });
    renderPage();
    await screen.findByRole('link', { name: 'Backend Core' });

    await user.type(screen.getByLabelText('Name'), 'Frontend Core');
    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() =>
      expect(createQuestionBank).toHaveBeenCalledWith({
        name: 'Frontend Core',
        description: null,
      }),
    );
  });
});

describe('QuestionBanks — rename and archive', () => {
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
    await user.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(archiveQuestionBank).toHaveBeenCalledWith('bank-1'));
  });
});
