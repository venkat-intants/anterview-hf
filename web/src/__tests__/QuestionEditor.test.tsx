// The question editor.
//
// Almost all of this is about one rule being VISIBLE rather than enforced: once
// somebody has answered a question, its wording is frozen. The server refuses
// the edit with a 409, which is correct — but a form that only tells you at
// save time is a form that wasted your work, so the field is disabled and the
// reason is on screen before anyone types.
//
// The other half is not letting a recruiter author a question that cannot be
// answered: a choice with fewer than two options renders as an empty dropdown,
// and if it is required, an application nobody can submit.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ApplicationQuestion } from '../api/questions';

const listQuestions = vi.fn();
const addQuestion = vi.fn();
const updateQuestion = vi.fn();
const retireQuestion = vi.fn();

vi.mock('../api/questions', async () => {
  const actual = await vi.importActual<typeof import('../api/questions')>(
    '../api/questions',
  );
  return {
    ...actual,
    listQuestions: (...a: unknown[]) => listQuestions(...a) as unknown,
    addQuestion: (...a: unknown[]) => addQuestion(...a) as unknown,
    updateQuestion: (...a: unknown[]) => updateQuestion(...a) as unknown,
    retireQuestion: (...a: unknown[]) => retireQuestion(...a) as unknown,
  };
});

const toastError = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: vi.fn(),
  },
}));

import QuestionEditor from '../components/workflow/QuestionEditor';

function question(over: Partial<ApplicationQuestion> = {}): ApplicationQuestion {
  return {
    id: 'q-1',
    position: 0,
    prompt: 'Why are you interested in this role?',
    kind: 'long_text',
    help_text: null,
    required: false,
    options: [],
    answer_count: 0,
    ...over,
  };
}

function renderEditor() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <QuestionEditor requisitionId="req-1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listQuestions.mockResolvedValue([question()]);
  addQuestion.mockResolvedValue([question(), question({ id: 'q-2' })]);
  updateQuestion.mockResolvedValue([question()]);
  retireQuestion.mockResolvedValue(undefined);
});

describe('QuestionEditor — the freeze is visible before you type', () => {
  it('lets an unanswered question be reworded', async () => {
    renderEditor();
    const field = await screen.findByLabelText('Question 1');
    expect(field).not.toBeDisabled();
  });

  it('disables the prompt once anyone has answered', async () => {
    listQuestions.mockResolvedValue([question({ answer_count: 3 })]);
    renderEditor();
    expect(await screen.findByLabelText('Question 1')).toBeDisabled();
  });

  it('explains why, with the number of people it would affect', async () => {
    listQuestions.mockResolvedValue([question({ answer_count: 3 })]);
    renderEditor();
    expect(await screen.findByText(/3 applicants have answered/)).toBeInTheDocument();
    expect(screen.getByText(/Retire it and add a new one/)).toBeInTheDocument();
  });

  it('gets the grammar right for a single answer', async () => {
    listQuestions.mockResolvedValue([question({ answer_count: 1 })]);
    renderEditor();
    expect(await screen.findByText(/1 applicant has answered/)).toBeInTheDocument();
  });

  it('still lets requiredness change on an answered question', async () => {
    // Order and requiredness do not change what an existing answer means.
    const user = userEvent.setup();
    listQuestions.mockResolvedValue([question({ answer_count: 3 })]);
    renderEditor();
    await screen.findByLabelText('Question 1');

    await user.click(screen.getByRole('checkbox', { name: /Required/ }));
    await waitFor(() =>
      expect(updateQuestion).toHaveBeenCalledWith('q-1', { required: true }),
    );
  });
});

describe('QuestionEditor — authoring', () => {
  it('will not add a question with no wording', async () => {
    const user = userEvent.setup();
    renderEditor();
    await screen.findByLabelText('Question 1');
    await user.click(screen.getByRole('button', { name: /Add a question/ }));

    expect(
      screen.getByRole<HTMLButtonElement>('button', { name: 'Add question' }).disabled,
    ).toBe(true);
  });

  it('adds a plain question', async () => {
    const user = userEvent.setup();
    renderEditor();
    await screen.findByLabelText('Question 1');
    await user.click(screen.getByRole('button', { name: /Add a question/ }));
    await user.type(
      screen.getByLabelText('What do you want to ask?'),
      'Notice period in weeks?',
    );
    await user.click(screen.getByRole('button', { name: 'Add question' }));

    await waitFor(() =>
      expect(addQuestion).toHaveBeenCalledWith(
        'req-1',
        expect.objectContaining({ prompt: 'Notice period in weeks?', kind: 'short_text' }),
      ),
    );
  });

  it('asks for options when the kind needs them, and refuses fewer than two', async () => {
    const user = userEvent.setup();
    renderEditor();
    await screen.findByLabelText('Question 1');
    await user.click(screen.getByRole('button', { name: /Add a question/ }));
    await user.type(screen.getByLabelText('What do you want to ask?'), 'Notice period?');
    await user.selectOptions(screen.getByLabelText('Answer type'), 'single_choice');

    const options = screen.getByLabelText('Options, one per line');
    await user.type(options, 'Immediate');
    expect(screen.getByText(/at least two options/)).toBeInTheDocument();
    expect(
      screen.getByRole<HTMLButtonElement>('button', { name: 'Add question' }).disabled,
    ).toBe(true);

    await user.type(options, '\n30 days');
    await waitFor(() =>
      expect(
        screen.getByRole<HTMLButtonElement>('button', { name: 'Add question' }).disabled,
      ).toBe(false),
    );
  });

  it('sends the options as a list', async () => {
    const user = userEvent.setup();
    renderEditor();
    await screen.findByLabelText('Question 1');
    await user.click(screen.getByRole('button', { name: /Add a question/ }));
    await user.type(screen.getByLabelText('What do you want to ask?'), 'Notice period?');
    await user.selectOptions(screen.getByLabelText('Answer type'), 'single_choice');
    await user.type(
      screen.getByLabelText('Options, one per line'),
      'Immediate\n30 days\n60 days',
    );
    await user.click(screen.getByRole('button', { name: 'Add question' }));

    await waitFor(() =>
      expect(addQuestion).toHaveBeenCalledWith(
        'req-1',
        expect.objectContaining({ options: ['Immediate', '30 days', '60 days'] }),
      ),
    );
  });

  it('warns that the answer type is permanent, while it is still a free choice', async () => {
    const user = userEvent.setup();
    renderEditor();
    await screen.findByLabelText('Question 1');
    await user.click(screen.getByRole('button', { name: /Add a question/ }));

    expect(screen.getByText(/cannot be changed later/)).toBeInTheDocument();
  });

  it('surfaces a server refusal instead of failing silently', async () => {
    addQuestion.mockRejectedValue(new Error('An opening can ask at most 20 questions.'));
    const user = userEvent.setup();
    renderEditor();
    await screen.findByLabelText('Question 1');
    await user.click(screen.getByRole('button', { name: /Add a question/ }));
    await user.type(screen.getByLabelText('What do you want to ask?'), 'One more?');
    await user.click(screen.getByRole('button', { name: 'Add question' }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('An opening can ask at most 20 questions.'),
    );
  });
});

describe('QuestionEditor — the empty state', () => {
  it('says an empty list is a valid choice, not a missing step', async () => {
    listQuestions.mockResolvedValue([]);
    renderEditor();
    expect(
      await screen.findByText(/the form simply does not have a questions step/),
    ).toBeInTheDocument();
  });

  it('counts how many are required', async () => {
    listQuestions.mockResolvedValue([
      question({ id: 'a', required: true }),
      question({ id: 'b', required: false }),
    ]);
    renderEditor();
    expect(await screen.findByText('1 of 2 required.')).toBeInTheDocument();
  });
});
