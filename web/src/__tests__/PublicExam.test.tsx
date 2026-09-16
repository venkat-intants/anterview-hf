// Candidate exam start page — the intro card.
//
// Two things a candidate saw that were wrong:
//   • "Round 1: Round 1" — an auto-named round printed through the round label
//     repeats itself. A default name adds nothing, so it is suppressed; a real
//     round name still shows.
//   • a "Language: EN · हि · తె" fact, hard-coded, although an exam's questions
//     are in one language. The fact is gone; questions and duration remain.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { TakeExam } from '../api/publicExam';

const getPublicExam = vi.fn();
vi.mock('../api/publicExam', () => ({
  getPublicExam: (...a: unknown[]) => getPublicExam(...a) as unknown,
  startExam: vi.fn(),
  submitRound: vi.fn(),
}));

import PublicExam from '../pages/PublicExam';

const EXAM: TakeExam = {
  exam_id: 'e1',
  title: 'Backend fundamentals',
  description: null,
  round_id: 'er-1',
  round_title: 'Round 1',
  round_number: 1,
  kind: 'mcq',
  time_limit_seconds: 1800,
  total_questions: 20,
  allow_retake: false,
  already_submitted: false,
  server_now: '2026-09-16T00:00:00.000Z',
  deadline: null,
  scheduled_at: null,
  max_integrity_violations: 3,
  sections: [],
  questions: [],
  coding_questions: [],
};

function renderExam() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PublicExam />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.location.hash = '#exam_tok_123456';
});

afterEach(() => {
  window.location.hash = '';
});

describe('PublicExam — intro card', () => {
  it('does not print an auto-named round as "Round 1: Round 1"', async () => {
    getPublicExam.mockResolvedValue(EXAM);
    renderExam();

    await screen.findByRole('heading', { name: 'Backend fundamentals' });
    expect(screen.queryByText(/Round 1: Round 1/)).toBeNull();
    expect(screen.queryByText(/^Round 1:/)).toBeNull();
  });

  it('still names a round that has a real title', async () => {
    getPublicExam.mockResolvedValue({ ...EXAM, round_title: 'System design', round_number: 2 });
    renderExam();

    await screen.findByRole('heading', { name: 'Backend fundamentals' });
    expect(screen.getByText('Round 2: System design')).toBeTruthy();
  });

  it('keeps a round label whose default-looking name does not match its number', async () => {
    getPublicExam.mockResolvedValue({ ...EXAM, round_title: 'Round 1', round_number: 2 });
    renderExam();

    await screen.findByRole('heading', { name: 'Backend fundamentals' });
    expect(screen.getByText('Round 2: Round 1')).toBeTruthy();
  });

  it('shows questions and duration, and no hard-coded language fact', async () => {
    getPublicExam.mockResolvedValue(EXAM);
    renderExam();

    await screen.findByRole('heading', { name: 'Backend fundamentals' });
    expect(screen.getByText('20 questions')).toBeTruthy();
    expect(screen.getByText('30 min')).toBeTruthy();
    expect(screen.queryByText('Language')).toBeNull();
    expect(screen.queryByText('EN · हि · తె')).toBeNull();
  });
});
