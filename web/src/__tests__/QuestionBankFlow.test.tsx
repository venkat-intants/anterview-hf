// QuestionBankFlow - the bank page as a five-stage guided flow.
//
// What is pinned here: which stage a bank opens on (from its real counts), that
// ?stage= wins and that a refetch never swaps the open panel, that the four
// add-methods each open their panel and come back, that a coding generator's
// onGenerated refreshes the lists and the tally, the drafts master-detail and its
// badges, the send step and the API it calls, the first-time path from /new, and
// the unsaved-changes guard.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation, useNavigationType } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { REFERENCE_UNVERIFIED_TAG, type BankOut, type BankQuestion } from '../api/questionBanks';

const listQuestionBanks = vi.fn();
const listBankQuestions = vi.fn();
const getBankQuestion = vi.fn();
const createQuestionBank = vi.fn();
const createBankQuestion = vi.fn();
const updateBankQuestion = vi.fn();
const submitAllBankQuestions = vi.fn();
const approveAllBankQuestions = vi.fn();
const listBankCompetencies = vi.fn();
vi.mock('../api/questionBanks', async (importActual) => ({
  ...(await importActual<typeof import('../api/questionBanks')>()),
  listQuestionBanks: (...a: unknown[]) => listQuestionBanks(...a) as unknown,
  listBankQuestions: (...a: unknown[]) => listBankQuestions(...a) as unknown,
  getBankQuestion: (...a: unknown[]) => getBankQuestion(...a) as unknown,
  createQuestionBank: (...a: unknown[]) => createQuestionBank(...a) as unknown,
  createBankQuestion: (...a: unknown[]) => createBankQuestion(...a) as unknown,
  updateBankQuestion: (...a: unknown[]) => updateBankQuestion(...a) as unknown,
  submitAllBankQuestions: (...a: unknown[]) => submitAllBankQuestions(...a) as unknown,
  approveAllBankQuestions: (...a: unknown[]) => approveAllBankQuestions(...a) as unknown,
  listBankCompetencies: (...a: unknown[]) => listBankCompetencies(...a) as unknown,
}));

vi.mock('../lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ user: { user_id: 'u-1' } }),
}));

// The coding generator is its own component with its own tests; here it is a
// stub that can report "I saved some drafts", which is the contract the page
// relies on.
vi.mock('../components/hr/bank/CodingQuestionGenerator', () => ({
  CodingQuestionGenerator: ({ onGenerated }: { onGenerated: (q: unknown[]) => void }) => (
    <section data-testid="coding-question-generator">
      <h2>Draft coding questions with AI</h2>
      <button type="button" onClick={() => onGenerated([{ id: 'new-1' }, { id: 'new-2' }])}>
        stub: save two drafts
      </button>
    </section>
  ),
}));

import QuestionBankDetail from '../pages/hr/QuestionBankDetail';

function Where(): JSX.Element {
  const l = useLocation();
  const nav = useNavigationType();
  return (
    <p data-testid="where" data-nav={nav}>
      {`${l.pathname}${l.search}`}
    </p>
  );
}

function renderAt(
  url: string,
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <Where />
        <Routes>
          <Route path="/hr/question-banks/:bankId" element={<QuestionBankDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, client };
}

const bank = (counts: BankOut['counts'], over: Partial<BankOut> = {}): BankOut => ({
  id: 'bank-1',
  name: 'Backend Core',
  description: 'SQL and REST',
  counts,
  used_in_exams: 2,
  created_at: '2026-09-01T00:00:00.000Z',
  updated_at: '2026-09-01T00:00:00.000Z',
  ...over,
});

function q(over: Partial<BankQuestion> = {}): BankQuestion {
  return {
    id: 'q-1',
    bank_id: 'bank-1',
    root_id: 'q-1',
    version: 1,
    kind: 'mcq',
    prompt: 'What is a closure?',
    points: 1,
    options: ['A function plus its scope', 'A loop', 'A class', 'A module'],
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
    content_hash: 'h',
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

const CODING_AI = q({
  id: 'q-code',
  kind: 'coding',
  prompt: 'Write a function that reverses a string.\nRead from stdin.',
  origin: 'ai_draft',
  reference_solution: 'print(input()[::-1])',
  allowed_languages: ['python'],
  starter_code: null,
  test_cases: [
    { stdin: 'abc', expected_output: 'cba', is_sample: true, weight: 1 },
    { stdin: 'xyz', expected_output: 'zyx', is_sample: false, weight: 1 },
  ],
  time_limit_ms: 5000,
  tags: [REFERENCE_UNVERIFIED_TAG],
});
const MCQ_AI = q({ id: 'q-mcq', origin: 'ai_draft', prompt: 'Which verb is idempotent?' });

function mockBank(counts: BankOut['counts'], list: BankQuestion[] = []): void {
  listQuestionBanks.mockResolvedValue([bank(counts)]);
  listBankQuestions.mockResolvedValue(list);
  getBankQuestion.mockImplementation((id: string) => {
    const found = list.find((x) => x.id === id) ?? list[0];
    return Promise.resolve({ question: found, versions: [found], used_in_exams: [] });
  });
  listBankCompetencies.mockResolvedValue([]);
}

// By the stage's own key: the stage titles also appear inside other stages' reasons.
const rowsOf = (list: HTMLElement): HTMLElement[] =>
  Array.from(list.children).filter((el): el is HTMLElement => el.tagName === 'LI');

const stageButton = (key: string): HTMLElement => {
  const el = screen
    .getByRole('navigation', { name: 'Bank flow' })
    .querySelector<HTMLElement>(`button[id$="-stage-${key}"]`);
  if (!el) throw new Error(`no stage button for ${key}`);
  return el;
};

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe('QuestionBankFlow - which stage a bank opens on', () => {
  it.each([
    ['an empty bank', {}, [], 'add'],
    ['a bank with drafts', { draft: 2 }, [MCQ_AI, CODING_AI], 'check'],
    ['a bank waiting on review', { in_review: 2 }, [q({ status: 'in_review' })], 'send'],
    ['a fully approved bank', { approved: 3 }, [q({ status: 'approved' })], 'approved'],
  ] as const)('%s', async (_n, counts, list, current) => {
    mockBank(counts, [...list]);
    renderAt('/hr/question-banks/bank-1');
    await screen.findByRole('navigation', { name: 'Bank flow' });
    await waitFor(() => expect(stageButton(current)).toHaveAttribute('aria-current', 'step'));
  });

  it('?stage= wins over the bank state', async () => {
    mockBank({ draft: 2 }, [MCQ_AI, CODING_AI]);
    renderAt('/hr/question-banks/bank-1?stage=send');
    await screen.findByRole('navigation', { name: 'Bank flow' });
    expect(stageButton('send')).toHaveAttribute('aria-current', 'step');
    expect(stageButton('check')).not.toHaveAttribute('aria-current');
  });

  it('ignores a ?stage= that is not a stage', async () => {
    mockBank({ draft: 2 }, [MCQ_AI]);
    renderAt('/hr/question-banks/bank-1?stage=nonsense');
    await waitFor(() => expect(stageButton('check')).toHaveAttribute('aria-current', 'step'));
  });

  it('keeps the open stage when a refetch changes the counts underneath it', async () => {
    mockBank({ draft: 2 }, [MCQ_AI, CODING_AI]);
    const { client } = renderAt('/hr/question-banks/bank-1');
    await waitFor(() => expect(stageButton('check')).toHaveAttribute('aria-current', 'step'));

    // Someone else approves everything while this page is open.
    listQuestionBanks.mockResolvedValue([bank({ approved: 2 })]);
    listBankQuestions.mockResolvedValue([
      { ...MCQ_AI, status: 'approved' },
      { ...CODING_AI, status: 'approved' },
    ]);
    await client.invalidateQueries({ queryKey: ['hr', 'question-banks'] });

    await waitFor(() =>
      expect(stageButton('check')).toHaveTextContent(/No drafts left unsubmitted/),
    );
    // The panel the person is working in did not move.
    expect(stageButton('check')).toHaveAttribute('aria-current', 'step');
    expect(stageButton('approved')).not.toHaveAttribute('aria-current');
  });

  it('carries the chosen stage in the URL', async () => {
    const user = userEvent.setup();
    mockBank({ draft: 1 }, [MCQ_AI]);
    renderAt('/hr/question-banks/bank-1');
    await waitFor(() => expect(stageButton('check')).toHaveAttribute('aria-current', 'step'));

    await user.click(stageButton('add'));
    expect(screen.getByTestId('where')).toHaveTextContent('?stage=add');
    expect(stageButton('add')).toHaveAttribute('aria-current', 'step');
  });

  it('REPLACES the history entry on a stage change, so Back leaves the page rather than stepping stages', async () => {
    const user = userEvent.setup();
    mockBank({ draft: 1 }, [MCQ_AI]);
    renderAt('/hr/question-banks/bank-1');
    await waitFor(() => expect(stageButton('check')).toHaveAttribute('aria-current', 'step'));

    await user.click(stageButton('add'));
    expect(screen.getByTestId('where')).toHaveAttribute('data-nav', 'REPLACE');
  });

  it('opens a deep link to a stage', async () => {
    mockBank({ draft: 1 }, [MCQ_AI]);
    renderAt('/hr/question-banks/bank-1?stage=approved');
    await waitFor(() => expect(stageButton('approved')).toHaveAttribute('aria-current', 'step'));
  });
});

describe('QuestionBankFlow - the next step strip', () => {
  it('names the real next step and offers a way there only when you are elsewhere', async () => {
    const user = userEvent.setup();
    mockBank({ draft: 2 }, [MCQ_AI, CODING_AI]);
    renderAt('/hr/question-banks/bank-1?stage=add');
    const strip = await screen.findByTestId('bank-next-step');
    // The strip mounts before its counts arrive (a separate query); on a slow
    // CI runner a synchronous read saw it empty. Wait for the content itself.
    await waitFor(() => expect(strip).toHaveTextContent('2 drafts not yet submitted'));
    expect(strip).toHaveTextContent('2 written by AI');
    expect(strip).toHaveTextContent('tests nobody has run');

    await user.click(within(strip).getByRole('button', { name: 'Check 2 drafts' }));
    await waitFor(() => expect(stageButton('check')).toHaveAttribute('aria-current', 'step'));
    // On the next stage itself the rail and the panel already say it: no second strip.
    expect(screen.queryByTestId('bank-next-step')).not.toBeInTheDocument();
  });
});

describe('QuestionBankFlow - adding questions', () => {
  it('opens each method in place, one at a time, and returns to the chooser', async () => {
    const user = userEvent.setup();
    mockBank({}, []);
    renderAt('/hr/question-banks/bank-1?stage=add');
    await screen.findByRole('heading', { name: 'How do you want to add questions?' });
    await waitFor(() => expect(screen.getByTestId('bank-tally')).toHaveTextContent('no questions yet'));

    const methods = [
      ['method-write', () => screen.findByRole('form', { name: 'Bank question' })],
      ['method-mcq-ai', () => screen.findByRole('button', { name: 'Generate a preview' })],
      ['method-coding-ai', () => screen.findByTestId('coding-question-generator')],
      ['method-import', () => screen.findByRole('button', { name: 'Choose a file' })],
    ] as const;

    for (const [id, expectPanel] of methods) {
      await user.click(screen.getByTestId(id));
      await expectPanel();
      // One at a time: the chooser is gone while a method is open.
      expect(screen.queryByTestId('method-write')).not.toBeInTheDocument();
      await user.click(screen.getByRole('button', { name: 'All methods' }));
      await screen.findByRole('heading', { name: 'How do you want to add questions?' });
    }
  });

  it('moves focus to the new panel heading when a method opens', async () => {
    const user = userEvent.setup();
    mockBank({}, []);
    renderAt('/hr/question-banks/bank-1?stage=add');
    await user.click(await screen.findByTestId('method-mcq-ai'));
    const heading = await screen.findByRole('heading', { name: 'Generate MCQs with AI', level: 3 });
    await waitFor(() => expect(heading).toHaveFocus());
    // Back at the chooser, focus follows to ITS heading.
    await user.click(screen.getByRole('button', { name: 'All methods' }));
    const chooser = await screen.findByRole('heading', {
      name: 'How do you want to add questions?',
    });
    await waitFor(() => expect(chooser).toHaveFocus());
  });

  it('offers only the four methods the API really supports', async () => {
    mockBank({}, []);
    renderAt('/hr/question-banks/bank-1?stage=add');
    const list = await screen.findByRole('list', { name: 'Ways to add questions' });
    expect(
      within(list)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual([
      expect.stringContaining('Write one yourself'),
      expect.stringContaining('Generate MCQs with AI'),
      expect.stringContaining('Generate coding questions with AI'),
      expect.stringContaining('Import from a file'),
    ]);
  });

  it('after the coding generator saves, the lists are refetched and the tally follows', async () => {
    const user = userEvent.setup();
    mockBank({}, []);
    renderAt('/hr/question-banks/bank-1?stage=add');
    await user.click(await screen.findByTestId('method-coding-ai'));
    await screen.findByTestId('coding-question-generator');
    const before = listBankQuestions.mock.calls.length;

    // The server now holds two coding drafts.
    listQuestionBanks.mockResolvedValue([bank({ draft: 2 })]);
    listBankQuestions.mockResolvedValue([
      q({ id: 'c1', kind: 'coding', origin: 'ai_draft', test_cases: [] }),
      q({ id: 'c2', kind: 'coding', origin: 'ai_draft', test_cases: [] }),
    ]);
    await user.click(screen.getByRole('button', { name: 'stub: save two drafts' }));

    await waitFor(() => expect(listBankQuestions.mock.calls.length).toBeGreaterThan(before));
    await waitFor(() => expect(screen.getByTestId('bank-tally')).toHaveTextContent('2 coding'));
    expect(screen.getByTestId('bank-tally')).toHaveTextContent('2 not yet submitted');
    // And the person is told what to do with them. AI never approves.
    expect(screen.getByTestId('bank-added')).toHaveTextContent('2 coding drafts added');
    expect(
      within(screen.getByTestId('bank-added')).getByRole('button', { name: 'Check your drafts' }),
    ).toBeInTheDocument();
  });

  it('writing a question saves a draft through createBankQuestion', async () => {
    const user = userEvent.setup();
    mockBank({}, []);
    createBankQuestion.mockResolvedValue(q());
    renderAt('/hr/question-banks/bank-1?stage=add');
    await user.click(await screen.findByTestId('method-write'));

    await user.type(await screen.findByLabelText('Prompt'), 'What is HTTP?');
    for (const n of [1, 2, 3, 4])
      await user.type(screen.getByLabelText(`Option ${n}`), `Choice ${n}`);
    await user.click(screen.getByRole('button', { name: 'Create question' }));

    await waitFor(() => expect(createBankQuestion).toHaveBeenCalled());
    expect(createBankQuestion.mock.calls[0][0]).toBe('bank-1');
    expect(createBankQuestion.mock.calls[0][1]).toMatchObject({
      kind: 'mcq',
      prompt: 'What is HTTP?',
    });
  });
});

describe('QuestionBankFlow - check your drafts', () => {
  beforeEach(() => mockBank({ draft: 2 }, [CODING_AI, MCQ_AI]));

  it('lists drafts with their kind, origin and the unverified-solution badge', async () => {
    renderAt('/hr/question-banks/bank-1?stage=check');
    const list = await screen.findByRole('list', { name: 'Draft questions' });
    const rows = rowsOf(list);
    expect(rows).toHaveLength(2);

    const coding = rows[0];
    expect(coding).toHaveTextContent('Write a function that reverses a string.');
    expect(coding).not.toHaveTextContent('Read from stdin'); // first line only
    expect(within(coding).getByTestId('ai-draft-badge')).toHaveTextContent('AI draft');
    expect(within(coding).getByTestId('row-unverified-badge')).toHaveTextContent(
      'Unverified',
    );

    // An MCQ has no solution to be unverified.
    expect(within(rows[1]).getByTestId('ai-draft-badge')).toBeInTheDocument();
    expect(within(rows[1]).queryByTestId('row-unverified-badge')).not.toBeInTheDocument();
  });

  it('opens the whole coding question - tests and code - and says nobody has run it', async () => {
    const user = userEvent.setup();
    renderAt('/hr/question-banks/bank-1?stage=check');
    await screen.findByTestId('bank-coding-question-view');
    expect(screen.getByTestId('ai-draft-note')).toHaveTextContent('Written by AI');
    // Said once, by the view's own note under the solution, not three times.
    expect(screen.getAllByText(/have not been run/)).toHaveLength(1);
    expect(screen.getByTestId('unverified-badge')).toBeInTheDocument();
    expect(screen.getAllByTestId(/^test-(sample|hidden)$/)).toHaveLength(2);

    // Choosing the other draft swaps the pane to the MCQ.
    await user.click(screen.getByRole('button', { name: /Which verb is idempotent/ }));
    expect(await screen.findByTestId('mcq-read-view')).toHaveTextContent(
      'Which verb is idempotent?',
    );
    expect(screen.getByText('Correct answer')).toBeInTheDocument();
  });

  it('filters the drafts by kind', async () => {
    const user = userEvent.setup();
    renderAt('/hr/question-banks/bank-1?stage=check');
    await screen.findByRole('list', { name: 'Draft questions' });
    await user.selectOptions(screen.getByLabelText('Filter by kind'), 'coding');
    const rows = rowsOf(screen.getByRole('list', { name: 'Draft questions' }));
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent('reverses a string');
  });

  it('caps a long list at eight rows with Show all', async () => {
    const many = Array.from({ length: 11 }, (_, i) =>
      q({ id: `m${i}`, prompt: `Question number ${i}` }),
    );
    mockBank({ draft: 11 }, many);
    const user = userEvent.setup();
    renderAt('/hr/question-banks/bank-1?stage=check');
    const list = await screen.findByRole('list', { name: 'Draft questions' });
    expect(rowsOf(list)).toHaveLength(8);
    await user.click(screen.getByRole('button', { name: 'Show all 11 questions' }));
    expect(rowsOf(screen.getByRole('list', { name: 'Draft questions' }))).toHaveLength(11);
  });

  it('states what nothing-left looks like as a fact', async () => {
    mockBank({ in_review: 1 }, [q({ status: 'in_review' })]);
    renderAt('/hr/question-banks/bank-1?stage=check');
    expect(
      await screen.findByText('No drafts left unsubmitted', { selector: 'p' }),
    ).toBeInTheDocument();
  });
});

describe('QuestionBankFlow - send for review', () => {
  it('counts the batch by kind, says who can approve, and submits the whole bank', async () => {
    const user = userEvent.setup();
    mockBank({ draft: 3 }, [CODING_AI, MCQ_AI, q({ id: 'q-3', prompt: 'Third' })]);
    submitAllBankQuestions.mockResolvedValue({ acted: 3, skipped: [] });
    renderAt('/hr/question-banks/bank-1?stage=send');

    const summary = await screen.findByTestId('send-summary');
    expect(summary).toHaveTextContent('2 MCQ · 1 coding');
    expect(summary).toHaveTextContent('2 written by AI');
    expect(summary).toHaveTextContent('1 coding question has tests nobody has run');
    expect(screen.getByText(/You cannot approve your own/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open question reviews' })).toHaveAttribute(
      'href',
      '/hr/question-banks/reviews',
    );

    await user.click(screen.getByRole('button', { name: 'Send 3 drafts for review' }));
    await waitFor(() => expect(submitAllBankQuestions).toHaveBeenCalledWith('bank-1'));
    expect(await screen.findByTestId('bank-bulk-result')).toHaveTextContent('3 submitted.');
  });

  it('reports what the review rule refused, per reason', async () => {
    const user = userEvent.setup();
    mockBank({ draft: 2 }, [MCQ_AI, q({ id: 'q-3' })]);
    submitAllBankQuestions.mockResolvedValue({
      acted: 1,
      skipped: [{ question_id: 'q-3', reason: 'Has no correct answer' }],
    });
    renderAt('/hr/question-banks/bank-1?stage=send');
    await user.click(await screen.findByRole('button', { name: 'Send 2 drafts for review' }));
    const result = await screen.findByTestId('bank-bulk-result');
    expect(result).toHaveTextContent('1 submitted. 1 not sent.');
    expect(result).toHaveTextContent('Has no correct answer (1)');
  });

  it('once everything is submitted the rail says waiting, with the count in review', async () => {
    mockBank(
      { in_review: 3 },
      [1, 2, 3].map((n) => q({ id: `r${n}`, status: 'in_review', prompt: `In review ${n}` })),
    );
    renderAt('/hr/question-banks/bank-1?stage=send');
    await screen.findByText('Nothing to send');
    expect(stageButton('send')).toHaveTextContent('Waiting');
    expect(stageButton('send')).toHaveTextContent('3 in review');
    expect(screen.getByText(/3 questions waiting for someone else to approve/)).toBeInTheDocument();
  });
});

describe('QuestionBankFlow - approved and in use', () => {
  it('lists what was sent back with the reviewer note, and opens it on the drafts stage', async () => {
    const user = userEvent.setup();
    const back = q({
      id: 'q-back',
      prompt: 'Explain REST.',
      review_note: 'Option C is also correct.',
    });
    mockBank({ draft: 1, approved: 2 }, [
      back,
      q({ id: 'a1', status: 'approved' }),
      q({ id: 'a2', status: 'approved', kind: 'coding' }),
    ]);
    renderAt('/hr/question-banks/bank-1?stage=approved');

    const sent = await screen.findByRole('list', { name: 'Questions sent back' });
    expect(sent).toHaveTextContent('Explain REST.');
    expect(sent).toHaveTextContent('Option C is also correct.');
    expect(screen.getByRole('link', { name: /Go to exams/ })).toHaveAttribute('href', '/hr/exams');
    expect(screen.getByText(/Add from bank/)).toBeInTheDocument();

    await user.click(within(sent).getByRole('button', { name: 'Open question' }));
    await waitFor(() => expect(stageButton('check')).toHaveAttribute('aria-current', 'step'));
    expect((await screen.findAllByTestId('sent-back-badge')).length).toBeGreaterThan(0);
    expect(await screen.findByTestId('review-note')).toHaveTextContent('Option C is also correct.');
  });

  it('says plainly when nothing is approved yet', async () => {
    mockBank({ in_review: 1 }, [q({ status: 'in_review' })]);
    renderAt('/hr/question-banks/bank-1?stage=approved');
    expect(await screen.findByText('Nothing is approved yet')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Go to exams/ })).not.toBeInTheDocument();
  });
});

describe('QuestionBankFlow - first-time creation from /new', () => {
  it('is the same rail with only stage 1 open, and lands on Add questions when created', async () => {
    const user = userEvent.setup();
    createQuestionBank.mockResolvedValue(bank({}, { id: 'bank-9', name: 'Fresh bank' }));
    listQuestionBanks.mockResolvedValue([]);
    listBankQuestions.mockResolvedValue([]);
    listBankCompetencies.mockResolvedValue([]);
    renderAt('/hr/question-banks/new');

    await screen.findByRole('form', { name: 'New bank' });
    expect(stageButton('setup')).toHaveAttribute('aria-current', 'step');
    expect(stageButton('add')).toHaveTextContent('Not yet');
    expect(listQuestionBanks).not.toHaveBeenCalled();

    // A blocked stage cannot be opened before the bank exists.
    await user.click(stageButton('check'));
    expect(screen.getByTestId('where')).toHaveTextContent('/hr/question-banks/new');

    // The name is required, with the reason beside the field.
    await user.click(screen.getByRole('button', { name: 'Create bank and add questions' }));
    expect(await screen.findByText('Give the bank a name.')).toBeInTheDocument();
    expect(createQuestionBank).not.toHaveBeenCalled();

    await user.type(screen.getByLabelText('Name'), 'Fresh bank');
    await user.type(screen.getByLabelText('What does it assess?'), 'Git and CI');
    await user.click(screen.getByRole('button', { name: 'Create bank and add questions' }));

    await waitFor(() =>
      expect(createQuestionBank).toHaveBeenCalledWith({
        name: 'Fresh bank',
        description: 'Git and CI',
      }),
    );
    // Same flow: it lands on stage 2 of the new bank.
    await waitFor(() =>
      expect(screen.getByTestId('where')).toHaveTextContent('/hr/question-banks/bank-9?stage=add'),
    );
  });
});

describe('QuestionBankFlow - unsaved changes', () => {
  it('asks before leaving a stage with an edited draft, and stays if declined', async () => {
    const user = userEvent.setup();
    mockBank({ draft: 1 }, [MCQ_AI]);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderAt('/hr/question-banks/bank-1?stage=check');

    await user.click(await screen.findByRole('button', { name: 'Edit' }));
    const prompt = await screen.findByDisplayValue('Which verb is idempotent?');
    await user.type(prompt, ' (edited)');

    await user.click(stageButton('add'));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(stageButton('check')).toHaveAttribute('aria-current', 'step');
    expect(screen.getByDisplayValue('Which verb is idempotent? (edited)')).toBeInTheDocument();

    // Agreeing to lose the edit lets the move happen.
    confirm.mockReturnValue(true);
    await user.click(stageButton('add'));
    await waitFor(() => expect(stageButton('add')).toHaveAttribute('aria-current', 'step'));
  });

  it('does not ask when nothing was edited', async () => {
    const user = userEvent.setup();
    mockBank({ draft: 1 }, [MCQ_AI]);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderAt('/hr/question-banks/bank-1?stage=check');
    await user.click(await screen.findByRole('button', { name: 'Edit' }));
    await screen.findByDisplayValue('Which verb is idempotent?');
    await user.click(stageButton('add'));
    expect(confirm).not.toHaveBeenCalled();
  });
});
