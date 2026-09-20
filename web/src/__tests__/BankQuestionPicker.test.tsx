// BankQuestionPicker — PH4-D1. "Add from bank" per exam section: a row
// already in this exam is disabled with its reason, and the add response's
// own skip reasons are reported back for whatever could not be added.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { BankQuestionSearchRow } from '../api/questionBanks';

const searchBankQuestions = vi.fn();
const addBankQuestionsToSection = vi.fn();
const listBankCompetencies = vi.fn();
vi.mock('../api/questionBanks', () => ({
  searchBankQuestions: (...a: unknown[]) => searchBankQuestions(...a) as unknown,
  addBankQuestionsToSection: (...a: unknown[]) => addBankQuestionsToSection(...a) as unknown,
  listBankCompetencies: (...a: unknown[]) => listBankCompetencies(...a) as unknown,
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

import { BankQuestionPicker } from '../components/bank/BankQuestionPicker';

function row(over: Partial<BankQuestionSearchRow> = {}): BankQuestionSearchRow {
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
    status: 'approved',
    origin: 'authored',
    content_hash: 'hash-1',
    created_by_user_id: 'u-1',
    submitted_by_user_id: 'u-2',
    submitted_at: '2026-09-01T00:00:00.000Z',
    reviewed_by_user_id: 'u-3',
    reviewed_at: '2026-09-02T00:00:00.000Z',
    review_note: null,
    retired_by_user_id: null,
    retired_at: null,
    created_at: '2026-09-01T00:00:00.000Z',
    updated_at: '2026-09-02T00:00:00.000Z',
    bank_name: 'Backend Core',
    already_in_exam: false,
    ...over,
  };
}

function renderPicker() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BankQuestionPicker
        examId={'11111111-1111-1111-1111-111111111111'}
        sectionId={'22222222-2222-2222-2222-222222222222'}
        sectionKind="mcq"
        onClose={vi.fn()}
        onAdded={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listBankCompetencies.mockResolvedValue([]);
});

describe('BankQuestionPicker — rows already in the exam', () => {
  it('disables the checkbox and shows the reason, and does not count it as available', async () => {
    const user = userEvent.setup();
    searchBankQuestions.mockResolvedValue([
      row({ id: 'q-1', prompt: 'Already copied question', already_in_exam: true }),
      row({ id: 'q-2', prompt: 'A fresh question', already_in_exam: false }),
    ]);
    renderPicker();

    await screen.findByText('Already copied question');
    const alreadyIn = screen.getByLabelText('Select Already copied question');
    const fresh = screen.getByLabelText('Select A fresh question');
    expect(alreadyIn).toBeDisabled();
    expect(fresh).not.toBeDisabled();
    expect(screen.getByText('Already in this exam')).toBeInTheDocument();
    expect(screen.getByText('0/100 selected · 1 available')).toBeInTheDocument();

    await user.click(fresh);
    expect(screen.getByText('1/100 selected · 1 available')).toBeInTheDocument();
  });
});

describe('BankQuestionPicker — selection cap', () => {
  it('refuses to select a 101st question and shows why', async () => {
    const user = userEvent.setup();
    const rows = Array.from({ length: 101 }, (_, i) =>
      row({ id: `q-${i}`, prompt: `Question ${i}` }),
    );
    searchBankQuestions.mockResolvedValue(rows);
    renderPicker();

    await screen.findByText('Question 0');
    for (let i = 0; i < 100; i++) {
      await user.click(screen.getByLabelText(`Select Question ${i}`));
    }
    expect(screen.getByText('100/100 selected · 101 available')).toBeInTheDocument();

    const capped = screen.getByLabelText('Select Question 100');
    expect(capped).toBeDisabled();
    expect(screen.getByText('Selection limit reached (100)')).toBeInTheDocument();
  });
});

describe('BankQuestionPicker — adding, with skip reasons', () => {
  it('reports the server’s own skip reason for anything not added', async () => {
    const user = userEvent.setup();
    searchBankQuestions.mockResolvedValue([
      row({ id: 'q-1', prompt: 'Pick me' }),
      row({ id: 'q-2', prompt: 'Duplicate content' }),
    ]);
    addBankQuestionsToSection.mockResolvedValue({
      added: 1,
      skipped: [{ id: 'q-2', reason: 'an identical question is already in this exam' }],
    });
    renderPicker();

    await screen.findByText('Pick me');
    await user.click(screen.getByLabelText('Select Pick me'));
    await user.click(screen.getByLabelText('Select Duplicate content'));
    await user.click(screen.getByRole('button', { name: /Add 2 questions/ }));

    await waitFor(() =>
      expect(addBankQuestionsToSection).toHaveBeenCalledWith(
        '11111111-1111-1111-1111-111111111111',
        '22222222-2222-2222-2222-222222222222',
        expect.arrayContaining(['q-1', 'q-2']),
      ),
    );
    expect(await screen.findByText('1 added, 1 skipped.')).toBeInTheDocument();
    expect(
      screen.getByText('Skipped — an identical question is already in this exam'),
    ).toBeInTheDocument();
  });
});

describe('BankQuestionPicker — empty and error states', () => {
  it('shows an empty state when nothing approved matches the filters', async () => {
    searchBankQuestions.mockResolvedValue([]);
    renderPicker();
    expect(
      await screen.findByText('No approved MCQ questions match these filters.'),
    ).toBeInTheDocument();
  });

  it('shows an error state when the search fails', async () => {
    searchBankQuestions.mockRejectedValue(new Error('boom'));
    renderPicker();
    expect(
      await screen.findByText("Could not load the bank's approved questions."),
    ).toBeInTheDocument();
  });
});
