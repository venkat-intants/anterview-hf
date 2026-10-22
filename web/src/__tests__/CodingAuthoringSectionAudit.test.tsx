// Coding section composer, audit wave 2 (exams area).
//
//   E3  deleting a coding question needs a confirm
//   E7  a part-failed "Add N problems" keeps only the unsaved drafts
//   C1  failures read as sentences, never the raw error text

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { CodingQuestion, GeneratedCodingQuestion } from '../api/exams';

const api = vi.hoisted(() => ({
  listSectionCodingQuestions: vi.fn(),
  addSectionCodingQuestion: vi.fn(),
  deleteSectionCodingQuestion: vi.fn(),
  generateCodingQuestions: vi.fn(),
}));
vi.mock('../api/exams', () => ({
  CODING_LANGUAGES: ['python', 'javascript'],
  listSectionCodingQuestions: (...a: unknown[]) => api.listSectionCodingQuestions(...a) as unknown,
  addSectionCodingQuestion: (...a: unknown[]) => api.addSectionCodingQuestion(...a) as unknown,
  deleteSectionCodingQuestion: (...a: unknown[]) => api.deleteSectionCodingQuestion(...a) as unknown,
  generateCodingQuestions: (...a: unknown[]) => api.generateCodingQuestions(...a) as unknown,
}));
vi.mock('../api/questionBanks', () => ({
  saveCodingQuestionToBank: vi.fn(),
  listQuestionBanks: vi.fn().mockResolvedValue([]),
  getBankQuestion: vi.fn(),
  isReferenceUnverified: () => false,
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

import CodingAuthoringSection from '../pages/hr/CodingAuthoringSection';

const QUESTION: CodingQuestion = {
  id: 'cq-1',
  prompt: 'Reverse a string',
  allowed_languages: ['python'],
  starter_code: null,
  reference_solution: null,
  test_cases: [{ stdin: 'ab', expected_output: 'ba', is_sample: true, weight: 1 }],
  time_limit_ms: 2000,
  points: 100,
  position: 0,
};

function draft(prompt: string): GeneratedCodingQuestion {
  return {
    prompt,
    allowed_languages: ['python'],
    starter_code: null,
    reference_solution: null,
    test_cases: [{ stdin: '1', expected_output: '1', is_sample: true, weight: 1 }],
    time_limit_ms: 2000,
    points: 100,
  };
}

function renderSection() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <CodingAuthoringSection examId="e-1" sectionId="s-1" locked={false} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  api.listSectionCodingQuestions.mockResolvedValue([]);
});

describe('coding section composer', () => {
  it('asks before deleting a coding question (E3)', async () => {
    api.listSectionCodingQuestions.mockResolvedValue([QUESTION]);
    api.deleteSectionCodingQuestion.mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('button', { name: 'Delete coding question 1' }));
    expect(api.deleteSectionCodingQuestion).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Delete question' }));

    await waitFor(() =>
      expect(api.deleteSectionCodingQuestion).toHaveBeenCalledWith('e-1', 's-1', 'cq-1'),
    );
  });

  it('keeps only the unsaved drafts when an add fails part-way (E7, C1)', async () => {
    api.generateCodingQuestions.mockResolvedValue({
      questions: [draft('Draft one'), draft('Draft two')],
    });
    api.addSectionCodingQuestion
      .mockResolvedValueOnce({ ...QUESTION, id: 'n-1' })
      .mockRejectedValueOnce(new TypeError('Failed to fetch'));
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('tab', { name: /^ai$/i }));
    await user.type(screen.getByLabelText('Topic'), 'strings');
    await user.click(screen.getByRole('button', { name: 'Generate' }));
    await user.click(await screen.findByRole('button', { name: 'Add 2 problems' }));

    expect(await screen.findByRole('button', { name: 'Add 1 problem' })).toBeInTheDocument();
    expect(screen.queryByText('Draft one')).not.toBeInTheDocument();
    expect(screen.getByText('Draft two')).toBeInTheDocument();
    const message = String(toastError.mock.calls[0]?.[0]);
    expect(message).toMatch(/1 problem was added/);
    expect(message).not.toMatch(/Failed to fetch/);
  });
});
