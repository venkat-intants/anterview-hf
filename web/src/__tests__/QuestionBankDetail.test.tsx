// QuestionBankDetail — PH4-D1. A question is read first and edited on purpose;
// the editor is only actually editable while it is a draft; version history and
// the withdraw rule are unchanged.
//
// STRUCTURAL CHANGE (2026-10-13): the page is now a five-stage flow, so each
// state lives on the stage that owns it - drafts on ?stage=check, in-review on
// ?stage=send, approved and retired on ?stage=approved - and a draft is opened
// with Edit instead of being an always-open form. The assertions are the same
// ones as before; only the route to reach each state moved. The flow itself is
// covered by QuestionBankFlow.test.tsx.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { BankOut, BankQuestion, BankQuestionDetail } from '../api/questionBanks';

const listQuestionBanks = vi.fn();
const listBankQuestions = vi.fn();
const getBankQuestion = vi.fn();
const createBankQuestion = vi.fn();
const updateBankQuestion = vi.fn();
const deleteBankQuestion = vi.fn();
const submitBankQuestion = vi.fn();
const withdrawBankQuestion = vi.fn();
const retireBankQuestion = vi.fn();
const newBankQuestionVersion = vi.fn();
const listBankCompetencies = vi.fn();
vi.mock('../api/questionBanks', async (importActual) => ({
  // Everything the page's tree imports that is not under test stays real
  // (isReferenceUnverified and the constants); only the network calls are
  // replaced.
  ...(await importActual<typeof import('../api/questionBanks')>()),
  listQuestionBanks: (...a: unknown[]) => listQuestionBanks(...a) as unknown,
  listBankQuestions: (...a: unknown[]) => listBankQuestions(...a) as unknown,
  getBankQuestion: (...a: unknown[]) => getBankQuestion(...a) as unknown,
  createBankQuestion: (...a: unknown[]) => createBankQuestion(...a) as unknown,
  updateBankQuestion: (...a: unknown[]) => updateBankQuestion(...a) as unknown,
  deleteBankQuestion: (...a: unknown[]) => deleteBankQuestion(...a) as unknown,
  submitBankQuestion: (...a: unknown[]) => submitBankQuestion(...a) as unknown,
  withdrawBankQuestion: (...a: unknown[]) => withdrawBankQuestion(...a) as unknown,
  retireBankQuestion: (...a: unknown[]) => retireBankQuestion(...a) as unknown,
  newBankQuestionVersion: (...a: unknown[]) => newBankQuestionVersion(...a) as unknown,
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

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useParams: () => ({ bankId: 'bank-1' }) };
});

// The page reads the signed-in user because only the person who submitted a
// question may withdraw it — the server 403s anyone else, and the control is
// gated so that refusal is never a surprise. 'u-1' is the fixtures' author.
const mockUseAuth = vi.fn(() => ({ user: { user_id: 'u-1' } }));
vi.mock('../context/AuthContext', () => ({ useAuth: () => mockUseAuth() as unknown }));

import QuestionBankDetail from '../pages/hr/QuestionBankDetail';

function renderPage(stage: 'check' | 'send' | 'approved' = 'check') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/hr/question-banks/bank-1?stage=${stage}`]}>
        <QuestionBankDetail />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const BANK: BankOut = {
  id: 'bank-1',
  name: 'Backend Core',
  description: null,
  counts: { draft: 1, approved: 1, retired: 1 },
  used_in_exams: 1,
  created_at: '2026-09-01T00:00:00.000Z',
  updated_at: '2026-09-01T00:00:00.000Z',
};

function question(over: Partial<BankQuestion> = {}): BankQuestion {
  return {
    id: 'q-draft',
    bank_id: 'bank-1',
    root_id: 'q-draft',
    version: 1,
    kind: 'mcq',
    prompt: 'What is a closure?',
    points: 1,
    options: ['a', 'b', 'c', 'd'],
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
    status: 'draft',
    origin: 'authored',
    content_hash: 'hash',
    created_by_user_id: 'u-1',
    submitted_by_user_id: null,
    submitted_at: null,
    reviewed_by_user_id: null,
    reviewed_at: null,
    review_note: null,
    retired_by_user_id: null,
    retired_at: null,
    created_at: '2026-09-01T00:00:00.000Z',
    updated_at: '2026-09-01T00:00:00.000Z',
    ...over,
  };
}

const DRAFT_Q = question();
const APPROVED_V2 = question({
  id: 'q-approved-v2',
  root_id: 'q-approved-v2',
  version: 2,
  status: 'approved',
  prompt: 'What is a monad? (v2)',
});
const RETIRED_V1 = question({
  id: 'q-retired-v1',
  root_id: 'q-approved-v2',
  version: 1,
  status: 'retired',
  prompt: 'What is a monad? (v1)',
});

beforeEach(() => {
  vi.clearAllMocks();
  listQuestionBanks.mockResolvedValue([BANK]);
  listBankCompetencies.mockResolvedValue([]);
  listBankQuestions.mockResolvedValue([DRAFT_Q, APPROVED_V2, RETIRED_V1]);
});

describe('QuestionBankDetail - the editor is draft-only', () => {
  it('shows a draft read-only first, and is fully editable once Edit is chosen', async () => {
    const user = userEvent.setup();
    const detail: BankQuestionDetail = { question: DRAFT_Q, versions: [DRAFT_Q], used_in_exams: [] };
    getBankQuestion.mockResolvedValue(detail);
    renderPage('check');

    // The first draft is open already (wide screens), read as a reader sees it.
    expect(await screen.findByTestId('mcq-read-view')).toHaveTextContent('What is a closure?');
    expect(screen.queryByDisplayValue('What is a closure?')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Submit for review' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Edit' }));
    expect(await screen.findByDisplayValue('What is a closure?')).toBeEnabled();
  });

  it('renders an approved question read-only, and offers New version as the way to change it', async () => {
    const detail: BankQuestionDetail = {
      question: APPROVED_V2,
      versions: [RETIRED_V1, APPROVED_V2],
      used_in_exams: [{ exam_id: 'exam-1', exam_title: 'Backend Engineer - Round 1' }],
    };
    getBankQuestion.mockResolvedValue(detail);
    renderPage('approved');

    expect(
      await screen.findByText(/only a draft can be edited/),
    ).toBeInTheDocument();
    // Read-only view: no editable textarea for the prompt.
    expect(screen.queryByDisplayValue('What is a monad? (v2)')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retire' })).toBeInTheDocument();
    // The read-only banner tells the user to start a new version, so the
    // control it points at has to exist. It did not: the review found the
    // whole revise-an-approved-question path unreachable, with this test's
    // old name claiming to cover exactly that.
    expect(screen.getByRole('button', { name: 'New version' })).toBeInTheDocument();
  });

  it('New version opens the content for editing and saves as a new version', async () => {
    const user = userEvent.setup();
    getBankQuestion.mockResolvedValue({
      question: APPROVED_V2,
      versions: [RETIRED_V1, APPROVED_V2],
      used_in_exams: [],
    });
    newBankQuestionVersion.mockResolvedValue({ id: 'q-approved-v3', version: 3 });
    renderPage('approved');

    await user.click(await screen.findByRole('button', { name: 'New version' }));

    // Pre-filled with the current content, and now editable.
    const prompt = await screen.findByDisplayValue('What is a monad? (v2)');
    expect(prompt).toBeEnabled();

    await user.click(screen.getByRole('button', { name: 'Save as new version' }));
    await waitFor(() => expect(newBankQuestionVersion).toHaveBeenCalled());
    expect(newBankQuestionVersion.mock.calls[0][0]).toBe('q-approved-v2');
    expect(updateBankQuestion).not.toHaveBeenCalled();
  });

  it('offers Withdraw only to the person who submitted the question', async () => {
    // Submitted by someone else: the server would 403, so the control is not
    // offered at all.
    const theirs = question({
      id: 'q-in-review',
      status: 'in_review',
      prompt: 'Whose question is this?',
      submitted_by_user_id: 'u-someone-else',
    });
    getBankQuestion.mockResolvedValue({ question: theirs, versions: [theirs], used_in_exams: [] });
    listBankQuestions.mockResolvedValue([theirs]);
    const view = renderPage('send');

    // Wait for the detail panel itself: an in_review question renders
    // read-only, so its banner is the signal the action bar has rendered.
    await screen.findByText(/only a draft can be edited/);
    expect(screen.queryByRole('button', { name: 'Withdraw' })).not.toBeInTheDocument();

    // Submitted by the signed-in user ('u-1'): offered.
    view.unmount();
    const mine = question({ ...theirs, submitted_by_user_id: 'u-1' });
    getBankQuestion.mockResolvedValue({ question: mine, versions: [mine], used_in_exams: [] });
    listBankQuestions.mockResolvedValue([mine]);
    renderPage('send');

    expect(await screen.findByRole('button', { name: 'Withdraw' })).toBeInTheDocument();
  });
});

describe('QuestionBankDetail - version history and usage', () => {
  it('lists every version and switches the selection when one is clicked', async () => {
    const user = userEvent.setup();
    const detail: BankQuestionDetail = {
      question: APPROVED_V2,
      versions: [RETIRED_V1, APPROVED_V2],
      used_in_exams: [{ exam_id: 'exam-1', exam_title: 'Backend Engineer - Round 1' }],
    };
    getBankQuestion.mockResolvedValue({ ...detail, question: APPROVED_V2 });
    renderPage('approved');

    await screen.findByText('Version history');

    const history = screen.getByText('Version history').closest('div') as HTMLElement;
    expect(within(history).getByText('v1')).toBeInTheDocument();
    expect(within(history).getByText('v2')).toBeInTheDocument();

    // Used-in-exams is shown with a link to the exam.
    expect(
      screen.getByRole('link', { name: 'Backend Engineer - Round 1' }),
    ).toHaveAttribute('href', '/hr/exams/exam-1');

    getBankQuestion.mockResolvedValue({ ...detail, question: RETIRED_V1 });
    await user.click(within(history).getByText('v1'));
    await waitFor(() => expect(getBankQuestion).toHaveBeenLastCalledWith('q-retired-v1'));
  });
});
