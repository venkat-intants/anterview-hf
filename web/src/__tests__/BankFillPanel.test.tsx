// BankFillPanel — the three ways to fill a question bank.
//
// Two properties this screen must not lose, both of which are easy to lose by
// accident:
//
//   1. AI output is a PREVIEW. Generating must store nothing; a bug that saved
//      on generate would put unreviewed model output into a library that feeds
//      real assessments, and would look like a success.
//   2. The bulk approve must never read as a success when it approved nothing.
//      In a one-person company that is the NORMAL answer — the author cannot
//      approve their own questions — and HR has to understand that they need a
//      second reviewer, not that the button is broken.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const api = {
  generateBankQuestions: vi.fn(),
  createBankQuestionsBulk: vi.fn(),
  importBankQuestions: vi.fn(),
  downloadBankQuestionTemplate: vi.fn(),
  submitAllBankQuestions: vi.fn(),
  approveAllBankQuestions: vi.fn(),
};
vi.mock('../api/questionBanks', async () => {
  const actual = await vi.importActual<typeof import('../api/questionBanks')>(
    '../api/questionBanks',
  );
  return {
    ...actual,
    generateBankQuestions: (...a: unknown[]) => api.generateBankQuestions(...a) as unknown,
    createBankQuestionsBulk: (...a: unknown[]) => api.createBankQuestionsBulk(...a) as unknown,
    importBankQuestions: (...a: unknown[]) => api.importBankQuestions(...a) as unknown,
    downloadBankQuestionTemplate: (...a: unknown[]) =>
      api.downloadBankQuestionTemplate(...a) as unknown,
    submitAllBankQuestions: (...a: unknown[]) => api.submitAllBankQuestions(...a) as unknown,
    approveAllBankQuestions: (...a: unknown[]) => api.approveAllBankQuestions(...a) as unknown,
  };
});

const toastSuccess = vi.fn();
const toastWarning = vi.fn();
const toastError = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
    warning: (...a: unknown[]) => toastWarning(...a) as unknown,
    error: (...a: unknown[]) => toastError(...a) as unknown,
    info: vi.fn(),
  },
}));

import BankFillPanel from '../components/bank/BankFillPanel';

const GENERATED = [
  { kind: 'mcq' as const, prompt: 'What does a multimeter measure?',
    options: ['Pressure', 'Voltage'], correct_index: 1, points: 1 },
  { kind: 'mcq' as const, prompt: 'Which tool sets torque?',
    options: ['Hammer', 'Torque wrench'], correct_index: 1, points: 1 },
];

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BankFillPanel bankId="bank-1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  api.generateBankQuestions.mockResolvedValue({ questions: GENERATED });
  api.createBankQuestionsBulk.mockResolvedValue(GENERATED);
  api.importBankQuestions.mockResolvedValue({ added: 0, errors: [], questions: [] });
  api.downloadBankQuestionTemplate.mockResolvedValue(undefined);
  api.submitAllBankQuestions.mockResolvedValue({ acted: 0, skipped: [] });
  api.approveAllBankQuestions.mockResolvedValue({ acted: 0, skipped: [] });
});

describe('BankFillPanel — everything arrives as a draft', () => {
  it('says so before HR adds anything', () => {
    renderPanel();
    const panel = screen.getByTestId('bank-fill-panel');
    expect(panel).toHaveTextContent(/arrives as a draft/i);
    expect(panel).toHaveTextContent(/not a way past it/i);
  });
});

describe('BankFillPanel — AI is a preview, not a save', () => {
  it('generating stores nothing', async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.type(screen.getByLabelText('Topic'), 'Electrical safety');
    await user.click(screen.getByRole('button', { name: /generate a preview/i }));

    await screen.findByTestId('bank-ai-preview');
    expect(api.generateBankQuestions).toHaveBeenCalledTimes(1);
    // The property. A generate that saved would look identical on screen.
    expect(api.createBankQuestionsBulk).not.toHaveBeenCalled();
  });

  it('shows each suggestion with its correct answer marked', async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.type(screen.getByLabelText('Topic'), 'Electrical safety');
    await user.click(screen.getByRole('button', { name: /generate a preview/i }));

    const preview = await screen.findByTestId('bank-ai-preview');
    expect(within(preview).getByText(/What does a multimeter measure\?/)).toBeInTheDocument();
    expect(within(preview).getByText(/B\. Voltage ✓/)).toBeInTheDocument();
  });

  it('saves only when HR asks, and only what was previewed', async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.type(screen.getByLabelText('Topic'), 'Electrical safety');
    await user.click(screen.getByRole('button', { name: /generate a preview/i }));
    await screen.findByTestId('bank-ai-preview');

    await user.click(screen.getByRole('button', { name: /add all 2 as drafts/i }));

    await waitFor(() => expect(api.createBankQuestionsBulk).toHaveBeenCalledTimes(1));
    expect(api.createBankQuestionsBulk).toHaveBeenCalledWith('bank-1', GENERATED);
  });

  it('discarding a preview stores nothing', async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.type(screen.getByLabelText('Topic'), 'Electrical safety');
    await user.click(screen.getByRole('button', { name: /generate a preview/i }));
    await screen.findByTestId('bank-ai-preview');

    await user.click(screen.getByRole('button', { name: /discard/i }));

    expect(screen.queryByTestId('bank-ai-preview')).not.toBeInTheDocument();
    expect(api.createBankQuestionsBulk).not.toHaveBeenCalled();
  });

  it('will not generate without a topic', () => {
    renderPanel();
    const btn = screen.getByRole('button', { name: /generate a preview/i });
    expect(btn).toBeDisabled();
  });
});

describe('BankFillPanel — importing a spreadsheet', () => {
  it('sends the chosen file with the difficulty and language defaults', async () => {
    const user = userEvent.setup();
    api.importBankQuestions.mockResolvedValue({ added: 3, errors: [], questions: [] });
    renderPanel();

    await user.selectOptions(screen.getByLabelText('Difficulty'), 'hard');
    await user.selectOptions(screen.getByLabelText('Language'), 'te');
    const file = new File(['Question,Option A\n'], 'bank.csv', { type: 'text/csv' });
    await user.upload(screen.getByLabelText(/choose a spreadsheet to import/i), file);

    await waitFor(() => expect(api.importBankQuestions).toHaveBeenCalledTimes(1));
    expect(api.importBankQuestions).toHaveBeenCalledWith('bank-1', file, {
      difficulty: 'hard',
      language: 'te',
    });
  });

  it('names the row and the reason for every row it could not use', async () => {
    // The whole point of per-row errors: "import failed" sends someone back to
    // a forty-row file with nowhere to look.
    const user = userEvent.setup();
    api.importBankQuestions.mockResolvedValue({
      added: 37,
      errors: [
        { row: 3, message: "correct answer 'Z' matches no option" },
        { row: 14, message: 'need at least 2 options' },
      ],
      questions: [],
    });
    renderPanel();

    const file = new File(['x'], 'bank.xlsx');
    await user.upload(screen.getByLabelText(/choose a spreadsheet to import/i), file);

    const errors = await screen.findByTestId('bank-import-errors');
    expect(errors).toHaveTextContent("Row 3: correct answer 'Z' matches no option");
    expect(errors).toHaveTextContent('Row 14: need at least 2 options');
    // Partial success, reported as such.
    expect(String(toastSuccess.mock.calls[0][0])).toMatch(/37 questions added/);
  });

  it('warns rather than congratulating when nothing could be imported', async () => {
    const user = userEvent.setup();
    api.importBankQuestions.mockResolvedValue({
      added: 0,
      errors: [{ row: 2, message: 'missing question text' }],
      questions: [],
    });
    renderPanel();

    const file = new File(['x'], 'bank.xlsx');
    await user.upload(screen.getByLabelText(/choose a spreadsheet to import/i), file);

    await waitFor(() => expect(toastWarning).toHaveBeenCalled());
    expect(String(toastWarning.mock.calls[0][0])).toMatch(/no rows could be imported/i);
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it('offers the template, because the layout is not guessable', async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole('button', { name: /download the template/i }));
    await waitFor(() => expect(api.downloadBankQuestionTemplate).toHaveBeenCalledTimes(1));
  });

  it('explains that the Correct column takes a letter, a number or the text', () => {
    renderPanel();
    const panel = screen.getByTestId('bank-fill-panel');
    expect(panel).toHaveTextContent(/letter \(A–D\)/i);
    expect(panel).toHaveTextContent(/number \(1–4\)/i);
    expect(panel).toHaveTextContent(/option.s own text/i);
  });
});

describe('BankFillPanel — the two-person rule survives the bulk button', () => {
  it('states the rule before HR presses anything', () => {
    renderPanel();
    const panel = screen.getByTestId('bank-fill-panel');
    expect(panel).toHaveTextContent(/someone other than its author must approve it/i);
    expect(panel).toHaveTextContent(/they do not skip it/i);
  });

  it('reports approving nothing as the rule, not as a success', async () => {
    // The normal answer in a one-person company. Reported as a success, HR
    // would believe 40 questions were usable when none of them are.
    const user = userEvent.setup();
    api.approveAllBankQuestions.mockResolvedValue({
      acted: 0,
      skipped: [
        { question_id: 'q1', reason: 'You wrote or submitted this question — another reviewer must approve it.' },
        { question_id: 'q2', reason: 'You wrote or submitted this question — another reviewer must approve it.' },
      ],
    });
    renderPanel();

    await user.click(screen.getByRole('button', { name: /approve all i am allowed to/i }));

    const result = await screen.findByTestId('bank-bulk-result');
    expect(result).toHaveTextContent('0 approved');
    expect(result).toHaveTextContent(/another reviewer must approve it/i);
    expect(result).toHaveTextContent(/ask another HR manager/i);
    expect(toastSuccess).not.toHaveBeenCalled();
    expect(toastWarning).toHaveBeenCalled();
  });

  it('groups the skipped reasons with a count rather than listing ids', async () => {
    const user = userEvent.setup();
    api.approveAllBankQuestions.mockResolvedValue({
      acted: 5,
      skipped: [
        { question_id: 'a', reason: 'You wrote or submitted this question — another reviewer must approve it.' },
        { question_id: 'b', reason: 'You wrote or submitted this question — another reviewer must approve it.' },
        { question_id: 'c', reason: "You changed this question's content — another reviewer must approve it." },
      ],
    });
    renderPanel();

    await user.click(screen.getByRole('button', { name: /approve all i am allowed to/i }));

    const result = await screen.findByTestId('bank-bulk-result');
    expect(result).toHaveTextContent('5 approved');
    expect(result).toHaveTextContent('3 left for someone else');
    expect(result).toHaveTextContent(/another reviewer must approve it\.\s*\(2\)/);
    expect(result).toHaveTextContent(/changed this question's content.*\(1\)/);
    // A partial success IS a success — 5 really were approved.
    expect(toastSuccess).toHaveBeenCalled();
  });

  it('does not show the second-reviewer nudge when the submit button approved nothing', async () => {
    // Submitting one's own drafts is allowed, so "0 submitted" is not the
    // two-person rule talking and must not be explained as if it were.
    const user = userEvent.setup();
    api.submitAllBankQuestions.mockResolvedValue({ acted: 0, skipped: [] });
    renderPanel();

    await user.click(screen.getByRole('button', { name: /send all drafts for review/i }));

    const result = await screen.findByTestId('bank-bulk-result');
    expect(result).toHaveTextContent('0 submitted');
    expect(result).not.toHaveTextContent(/ask another HR manager/i);
  });

  it('submitting is not approving', async () => {
    const user = userEvent.setup();
    api.submitAllBankQuestions.mockResolvedValue({ acted: 4, skipped: [] });
    renderPanel();

    await user.click(screen.getByRole('button', { name: /send all drafts for review/i }));

    await waitFor(() => expect(api.submitAllBankQuestions).toHaveBeenCalledTimes(1));
    expect(api.approveAllBankQuestions).not.toHaveBeenCalled();
  });
});
