// The "Unverified" marker is derived by the SERVER (the tag `reference-unverified`
// on every AI coding draft, with or without a reference solution). The UI reads
// the tag and nothing else, and on an EXAM coding question - which has no tags -
// looks the tag up on the bank question it was copied from.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import {
  REFERENCE_UNVERIFIED_TAG,
  isReferenceUnverified,
  type BankQuestion,
  type BankQuestionDetail,
} from '../api/questionBanks';
import type { CodingQuestion } from '../api/exams';
import { bankFacts } from '../lib/bankFlow';

const getBankQuestion = vi.fn();
vi.mock('../api/questionBanks', async (importActual) => ({
  ...(await importActual<typeof import('../api/questionBanks')>()),
  getBankQuestion: (...a: unknown[]) => getBankQuestion(...a) as unknown,
}));

const listSectionCodingQuestions = vi.fn();
vi.mock('../api/exams', async (importActual) => ({
  ...(await importActual<typeof import('../api/exams')>()),
  listSectionCodingQuestions: (...a: unknown[]) => listSectionCodingQuestions(...a) as unknown,
}));

vi.mock('../lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));
vi.mock('../components/CodeEditor', () => ({ default: () => <div data-testid="code-editor" /> }));
// The picker is only mounted on demand; keep it out of the way.
vi.mock('../components/bank/BankQuestionPicker', () => ({ BankQuestionPicker: () => null }));

import CodingAuthoringSection from '../pages/hr/CodingAuthoringSection';

/** An IntersectionObserver the test can fire, per observed element. */
const observers: { cb: IntersectionObserverCallback; el: Element }[] = [];
class FakeIO {
  private readonly cb: IntersectionObserverCallback;
  constructor(cb: IntersectionObserverCallback) {
    this.cb = cb;
  }
  observe(el: Element): void {
    observers.push({ cb: this.cb, el });
  }
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
}
const original = globalThis.IntersectionObserver;
function reveal(el: Element): void {
  const o = observers.find((x) => x.el === el);
  if (!o) throw new Error('not observed');
  act(() => {
    o.cb([{ isIntersecting: true, target: el } as IntersectionObserverEntry], {} as IntersectionObserver);
  });
}

function bankQ(over: Partial<BankQuestion> = {}): BankQuestion {
  return {
    id: 'bq-1', bank_id: 'b', root_id: 'bq-1', version: 1, kind: 'coding', prompt: 'p', points: 100,
    options: null, correct_index: null, starter_code: null, reference_solution: null,
    allowed_languages: ['python'], test_cases: [], time_limit_ms: 5000, difficulty: 'medium',
    language: 'en', competencies: [], tags: [], status: 'draft', origin: 'ai_draft', content_hash: 'h',
    created_by_user_id: 'u', submitted_by_user_id: null, submitted_at: null, reviewed_by_user_id: null,
    reviewed_at: null, review_note: null, retired_by_user_id: null, retired_at: null,
    created_at: '2026-09-01T00:00:00.000Z', updated_at: '2026-09-01T00:00:00.000Z', ...over,
  };
}
const detail = (q: BankQuestion): BankQuestionDetail => ({ question: q, versions: [q], used_in_exams: [] });

function examQ(id: string, src: string | null): CodingQuestion {
  return {
    id, prompt: `Problem ${id}`, allowed_languages: ['python'], starter_code: null,
    reference_solution: null, test_cases: [{ stdin: '1', expected_output: '1', is_sample: true, weight: 1 }],
    time_limit_ms: 5000, points: 100, position: 0, source_bank_question_id: src,
  };
}

let client: QueryClient;
function renderSection() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <CodingAuthoringSection examId="ex-1" sectionId="sec-1" locked={false} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  observers.length = 0;
  globalThis.IntersectionObserver = FakeIO as unknown as typeof IntersectionObserver;
});
afterEach(() => {
  globalThis.IntersectionObserver = original;
});

describe('isReferenceUnverified reads the tag and nothing else', () => {
  it('is true for the tag even with no reference solution', () => {
    expect(isReferenceUnverified({ tags: [REFERENCE_UNVERIFIED_TAG] })).toBe(true);
    expect(isReferenceUnverified(bankQ({ tags: [REFERENCE_UNVERIFIED_TAG], reference_solution: null }))).toBe(true);
  });

  it('is false without the tag, even for an AI draft that has a reference solution', () => {
    expect(isReferenceUnverified(bankQ({ tags: [], reference_solution: 'print(1)' }))).toBe(false);
    expect(isReferenceUnverified({ tags: null })).toBe(false);
    expect(isReferenceUnverified({})).toBe(false);
  });

  it('counts a tagged coding draft with no reference solution in the bank facts', () => {
    const list = [bankQ({ tags: [REFERENCE_UNVERIFIED_TAG], reference_solution: null })];
    const f = bankFacts(
      {
        id: 'b', name: 'n', description: null, counts: { draft: 1 }, used_in_exams: 0,
        created_at: '', updated_at: '',
      },
      list,
    );
    expect(f.unverifiedDrafts).toBe(1);
  });
});

describe('exam coding questions look their bank question up', () => {
  it('shows the Unverified badge when the source bank question carries the tag', async () => {
    listSectionCodingQuestions.mockResolvedValue([examQ('e1', 'bq-1')]);
    getBankQuestion.mockResolvedValue(detail(bankQ({ tags: [REFERENCE_UNVERIFIED_TAG] })));
    renderSection();
    const item = (await screen.findByText(/Problem e1/)).closest('li') as HTMLElement;
    reveal(item.querySelector('span.inline-flex') as Element);
    expect(await screen.findByTestId('exam-unverified-badge')).toHaveTextContent('Unverified');
    expect(getBankQuestion).toHaveBeenCalledWith('bq-1');
  });

  it('fetches nothing until the row is on screen', async () => {
    listSectionCodingQuestions.mockResolvedValue([examQ('e1', 'bq-1')]);
    getBankQuestion.mockResolvedValue(detail(bankQ({ tags: [REFERENCE_UNVERIFIED_TAG] })));
    renderSection();
    await screen.findByText(/Problem e1/);
    await new Promise((r) => setTimeout(r, 30));
    expect(getBankQuestion).not.toHaveBeenCalled();
  });

  it('asks nothing for a question written in the exam (no source id)', async () => {
    listSectionCodingQuestions.mockResolvedValue([examQ('e1', null)]);
    renderSection();
    await screen.findByText(/Problem e1/);
    expect(observers).toHaveLength(0);
    expect(getBankQuestion).not.toHaveBeenCalled();
  });

  it('shows no badge when the tag is absent (an approved version cleared it)', async () => {
    listSectionCodingQuestions.mockResolvedValue([examQ('e1', 'bq-1')]);
    getBankQuestion.mockResolvedValue(detail(bankQ({ tags: [], status: 'approved' })));
    renderSection();
    const item = (await screen.findByText(/Problem e1/)).closest('li') as HTMLElement;
    reveal(item.querySelector('span.inline-flex') as Element);
    await waitFor(() => expect(getBankQuestion).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('exam-unverified-badge')).toBeNull();
  });

  it('shows nothing, and does not block the editor, when the lookup fails', async () => {
    listSectionCodingQuestions.mockResolvedValue([examQ('e1', 'bq-1')]);
    getBankQuestion.mockRejectedValue(new Error('403'));
    renderSection();
    const item = (await screen.findByText(/Problem e1/)).closest('li') as HTMLElement;
    reveal(item.querySelector('span.inline-flex') as Element);
    await waitFor(() => expect(getBankQuestion).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('exam-unverified-badge')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('button', { name: /Add coding question/ })).toBeInTheDocument();
  });

  it('looks each bank question up once and never polls', async () => {
    listSectionCodingQuestions.mockResolvedValue([examQ('e1', 'bq-1'), examQ('e2', 'bq-1')]);
    getBankQuestion.mockResolvedValue(detail(bankQ({ tags: [REFERENCE_UNVERIFIED_TAG] })));
    renderSection();
    await screen.findByText(/Problem e2/);
    for (const li of screen.getAllByRole('listitem').filter((x) => x.textContent?.includes('Problem'))) {
      reveal(li.querySelector('span.inline-flex') as Element);
    }
    await waitFor(() => expect(screen.getAllByTestId('exam-unverified-badge')).toHaveLength(2));
    expect(getBankQuestion).toHaveBeenCalledTimes(1);
    const q = client.getQueryCache().find({ queryKey: ['hr', 'bank-questions', 'bq-1'] });
    expect(q?.observers.every((o) => !o.options.refetchInterval)).toBe(true);
    expect(q?.observers[0]?.options.staleTime).toBeGreaterThanOrEqual(10 * 60 * 1000);
  });
});
