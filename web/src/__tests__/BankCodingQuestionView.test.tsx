// BankCodingQuestionView — the read-only preview of a coding question.
//
// A reviewer decides from this screen, so two facts must never be missing or
// ambiguous: which tests the candidate sees, and whether anyone ran the
// reference solution.

import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  BankCodingQuestionView,
  type CodingQuestionPreview,
} from '../components/hr/bank/BankCodingQuestionView';
import { REFERENCE_UNVERIFIED_TAG, UNVERIFIED_EXPLANATION, type BankQuestion } from '../api/questionBanks';

function question(over: Partial<CodingQuestionPreview> = {}): CodingQuestionPreview {
  return {
    prompt: 'Read n and print n squared.\nConstraints: 1 <= n <= 1000.',
    allowed_languages: ['python', 'cpp'],
    starter_code: null,
    reference_solution: 'print(int(input()) ** 2)',
    test_cases: [
      { stdin: '3', expected_output: '9', is_sample: true, weight: 1 },
      { stdin: '1000', expected_output: '1000000', is_sample: false, weight: 2 },
      { stdin: '1', expected_output: '1', is_sample: false, weight: 1 },
    ],
    time_limit_ms: 5000,
    points: 100,
    tags: [REFERENCE_UNVERIFIED_TAG],
    reference_solution_language: 'python',
    ...over,
  };
}

describe('BankCodingQuestionView', () => {
  it('shows the statement, the languages and the limits', () => {
    render(<BankCodingQuestionView question={question()} />);
    expect(screen.getByText(/Read n and print n squared\./)).toBeInTheDocument();
    const facts = screen.getByRole('list', { name: 'Question facts' });
    expect(within(facts).getByText('Python')).toBeInTheDocument();
    expect(within(facts).getByText('C++')).toBeInTheDocument();
    expect(within(facts).getByText('5 s time limit')).toBeInTheDocument();
    expect(within(facts).getByText('100 points')).toBeInTheDocument();
  });

  it('shows only the sample cases as examples', () => {
    render(<BankCodingQuestionView question={question()} />);
    expect(screen.getByText('Example 1 input')).toBeInTheDocument();
    expect(screen.queryByText('Example 2 input')).not.toBeInTheDocument();
  });

  it('lists every test and marks the hidden ones in words', () => {
    render(<BankCodingQuestionView question={question()} />);
    expect(screen.getByText('Test cases (1 sample, 2 hidden)')).toBeInTheDocument();
    expect(screen.getAllByTestId('test-sample')).toHaveLength(1);
    const hidden = screen.getAllByTestId('test-hidden');
    expect(hidden).toHaveLength(2);
    for (const row of hidden) expect(within(row).getByText('Hidden')).toBeInTheDocument();
    expect(within(hidden[0]).getByText('Weight 2')).toBeInTheDocument();
    expect(within(hidden[0]).getByText('1000000')).toBeInTheDocument();
  });

  it('says an AI-written solution was never run', () => {
    render(<BankCodingQuestionView question={question()} />);
    expect(screen.getByTestId('unverified-badge')).toHaveTextContent('Unverified');
    expect(screen.getByText(UNVERIFIED_EXPLANATION, { exact: false })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Reference solution (Python)' })).toBeInTheDocument();
    expect(screen.getByText('print(int(input()) ** 2)')).toBeInTheDocument();
  });

  it('makes no unverified claim about a hand-written question', () => {
    render(<BankCodingQuestionView question={question({ tags: ['arrays'] })} />);
    expect(screen.queryByTestId('unverified-badge')).not.toBeInTheDocument();
    expect(screen.queryByText(/have not been run/)).not.toBeInTheDocument();
  });

  it('the server tag is the single source: no reference solution, tag present, still unverified', () => {
    render(<BankCodingQuestionView question={question({ reference_solution: null })} />);
    expect(screen.getByTestId('unverified-badge')).toHaveTextContent('Unverified');
    expect(screen.getByText(UNVERIFIED_EXPLANATION, { exact: false })).toBeInTheDocument();
    expect(screen.getByText(/No reference solution\./)).toBeInTheDocument();
  });

  it('a reference solution without the tag is not called unverified', () => {
    render(<BankCodingQuestionView question={question({ tags: [] })} />);
    expect(screen.queryByTestId('unverified-badge')).not.toBeInTheDocument();
  });

  it('shows starter code on its own tab, and says when there is none', async () => {
    const user = userEvent.setup();
    const { unmount } = render(<BankCodingQuestionView question={question()} />);
    await user.click(screen.getByRole('tab', { name: 'Starter code' }));
    expect(screen.getByText(/No starter code\./)).toBeInTheDocument();
    unmount();

    render(<BankCodingQuestionView question={question({ starter_code: 'n = int(input())' })} />);
    await user.click(screen.getByRole('tab', { name: 'Starter code' }));
    expect(screen.getByText('n = int(input())')).toBeInTheDocument();
    // Two languages, one starter string: the reviewer is told it is shared.
    expect(screen.getByText(/whichever language the candidate picks/)).toBeInTheDocument();
  });

  it('renders a saved bank question as it comes from the API, nulls included', () => {
    const saved: BankQuestion = {
      id: 'q1', bank_id: 'b1', root_id: 'q1', version: 1, kind: 'coding',
      prompt: 'Print hello.', points: 50, options: null, correct_index: null,
      starter_code: null, reference_solution: null, allowed_languages: null, test_cases: null,
      time_limit_ms: null, difficulty: 'easy', language: 'en', competencies: [], tags: [],
      status: 'draft', origin: 'ai_draft', content_hash: 'x', created_by_user_id: null,
      submitted_by_user_id: null, submitted_at: null, reviewed_by_user_id: null,
      reviewed_at: null, review_note: null, retired_by_user_id: null, retired_at: null,
      created_at: '2026-10-13T00:00:00Z', updated_at: '2026-10-13T00:00:00Z',
    };
    render(<BankCodingQuestionView question={saved} />);
    expect(screen.getByText('Print hello.')).toBeInTheDocument();
    expect(screen.getByText('No test cases.')).toBeInTheDocument();
    expect(screen.getByText('Test cases (0 sample, 0 hidden)')).toBeInTheDocument();
  });

  it('clips a very long test input and says how much is not shown', () => {
    const long = 'x'.repeat(1500);
    render(
      <BankCodingQuestionView
        question={question({
          test_cases: [
            { stdin: long, expected_output: '1', is_sample: false, weight: 1 },
            { stdin: '1', expected_output: '1', is_sample: true, weight: 1 },
          ],
        })}
      />,
    );
    expect(screen.getByText(/300 more characters not shown/)).toBeInTheDocument();
  });
});
