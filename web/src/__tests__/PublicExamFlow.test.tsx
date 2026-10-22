// The exam attempt itself: what survives a reload, what asks before it acts,
// and the small labels around it (audit CA-M2, CA-M3, CA-M25, CA-L3, CA-L10,
// CA-L21).

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { PublicSection, TakeExam } from '../api/publicExam';

const getPublicExam = vi.fn();
const startExam = vi.fn();
const submitRound = vi.fn();
const sendIntegrityEvent = vi.fn();
vi.mock('../api/publicExam', () => ({
  getPublicExam: (...a: unknown[]) => getPublicExam(...a) as unknown,
  startExam: (...a: unknown[]) => startExam(...a) as unknown,
  grantCameraConsent: vi.fn(),
  submitRound: (...a: unknown[]) => submitRound(...a) as unknown,
  sendIntegrityEvent: (...a: unknown[]) => sendIntegrityEvent(...a) as unknown,
}));
vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: false, isInitializing: false, user: null }),
}));

import PublicExam from '../pages/PublicExam';

function mcq(id: string, position: number, qids: string[], limit: number | null = null): PublicSection {
  return {
    id,
    title: `Section ${id}`,
    kind: 'mcq',
    position,
    time_limit_seconds: limit,
    questions: qids.map((q, i) => ({
      id: q,
      position: i,
      prompt: `Question ${q}?`,
      options: ['Yes', 'No'],
      points: 1,
    })),
    coding_questions: [],
  };
}

const BASE: TakeExam = {
  exam_id: 'e1',
  title: 'Backend fundamentals',
  description: null,
  round_id: 'er-1',
  round_title: 'Round 1',
  round_number: 1,
  kind: 'mcq',
  time_limit_seconds: null,
  total_questions: 3,
  allow_retake: false,
  already_submitted: false,
  server_now: '2026-09-16T00:00:00.000Z',
  deadline: null,
  scheduled_at: null,
  max_integrity_violations: 3,
  sections: [mcq('a', 0, ['q1', 'q2'], 600), mcq('b', 1, ['q3'])],
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

async function start(user: ReturnType<typeof userEvent.setup>, label = 'Start exam') {
  await screen.findByRole('heading', { name: 'Backend fundamentals' });
  await user.click(screen.getByRole('checkbox'));
  await user.click(screen.getByRole('button', { name: label }));
  await waitFor(() => expect(screen.queryByRole('checkbox')).not.toBeInTheDocument());
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  window.location.hash = '#exam_tok_123456';
  Object.defineProperty(document, 'fullscreenElement', { value: document.body, configurable: true });
  getPublicExam.mockResolvedValue(BASE);
  startExam.mockResolvedValue({
    attempt_id: 'att-1',
    started_at: '2026-09-16T00:00:00.000Z',
    deadline: null,
    max_violations: 3,
    camera_in_use: false,
  });
  submitRound.mockResolvedValue({
    attempt_id: 'att-1',
    score_raw: 1,
    score_max: 3,
    score_percent: 33,
    passed: false,
    status: 'submitted',
    submitted_at: '2026-09-16T00:10:00.000Z',
  });
});

afterEach(() => {
  window.location.hash = '';
  Object.defineProperty(document, 'fullscreenElement', { value: null, configurable: true });
});

describe('reload during an attempt (CA-M2)', () => {
  it('puts back answers, the section and its clock, and labels the button Resume', async () => {
    const user = userEvent.setup();
    const first = renderExam();
    await start(user);
    await user.click(within(screen.getByRole('radiogroup', { name: 'Question q1?' })).getByRole('radio', { name: 'Yes' }));
    first.unmount();

    // Two minutes pass in the saved record, as if the reload came later.
    const key = 'intants:exam-progress:e1:er-1';
    const saved = JSON.parse(sessionStorage.getItem(key) ?? 'null') as {
      sectionStartedAt: Record<string, number>;
    };
    expect(saved).not.toBeNull();
    saved.sectionStartedAt.a = Date.now() - 120_000;
    sessionStorage.setItem(key, JSON.stringify(saved));

    // The tab reloads: the hash is gone, the token is in sessionStorage.
    window.location.hash = '';
    renderExam();
    await screen.findByRole('heading', { name: 'Backend fundamentals' });
    expect(screen.getByRole('button', { name: 'Resume exam' })).toBeInTheDocument();
    await start(user, 'Resume exam');

    const group = screen.getByRole('radiogroup', { name: 'Question q1?' });
    expect(within(group).getByRole('radio', { name: 'Yes' })).toBeChecked();
    // The section clock carries on from when the section was entered; it is
    // not handed back a fresh 10:00.
    const clock = screen.getByLabelText(/section time remaining/i);
    expect(clock.textContent).toMatch(/^0?8:0\d$|^07:5\d$/);
  });

  it('does not hand one attempt the answers of another', async () => {
    const user = userEvent.setup();
    const first = renderExam();
    await start(user);
    await user.click(within(screen.getByRole('radiogroup', { name: 'Question q1?' })).getByRole('radio', { name: 'Yes' }));
    first.unmount();

    startExam.mockResolvedValue({
      attempt_id: 'att-2',
      started_at: '2026-09-16T01:00:00.000Z',
      deadline: null,
      max_violations: 3,
      camera_in_use: false,
    });
    renderExam();
    await start(user, 'Resume exam');
    expect(
      within(screen.getByRole('radiogroup', { name: 'Question q1?' })).getByRole('radio', { name: 'Yes' }),
    ).not.toBeChecked();
  });
});

describe('leaving a section or submitting asks first (CA-M3)', () => {
  it('Next section warns about unanswered questions and can be backed out of', async () => {
    const user = userEvent.setup();
    renderExam();
    await start(user);
    await user.click(screen.getByRole('button', { name: 'Next section' }));

    const dialog = screen.getByTestId('exam-confirm');
    expect(dialog).toHaveTextContent('You cannot come back to this one.');
    expect(dialog).toHaveTextContent('2 questions are unanswered.');
    expect(within(dialog).getByRole('button', { name: 'Keep working' })).toHaveFocus();

    await user.click(within(dialog).getByRole('button', { name: 'Keep working' }));
    expect(screen.getByRole('radiogroup', { name: 'Question q1?' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Next section' }));
    await user.click(within(screen.getByTestId('exam-confirm')).getByRole('button', { name: 'Go to next section' }));
    expect(await screen.findByRole('radiogroup', { name: 'Question q3?' })).toBeInTheDocument();
  });

  it('Submit counts every unanswered question and only submits once confirmed', async () => {
    const user = userEvent.setup();
    getPublicExam.mockResolvedValue({ ...BASE, sections: [mcq('a', 0, ['q1', 'q2'])] });
    renderExam();
    await start(user);
    await user.click(within(screen.getByRole('radiogroup', { name: 'Question q1?' })).getByRole('radio', { name: 'No' }));
    await user.click(screen.getByRole('button', { name: 'Submit exam' }));

    expect(submitRound).not.toHaveBeenCalled();
    const dialog = screen.getByTestId('exam-confirm');
    expect(dialog).toHaveTextContent('1 question is unanswered.');
    await user.click(within(dialog).getByRole('button', { name: 'Submit exam' }));
    await waitFor(() => expect(submitRound).toHaveBeenCalledOnce());
    // Progress is dropped once the attempt is in.
    await screen.findByTestId('exam-submitted');
    expect(Object.keys(sessionStorage).some((k) => k.startsWith('intants:exam-progress:'))).toBe(false);
  });
});

describe('labels and edge cases', () => {
  it('offers the language switcher before the timed attempt (CA-M25)', async () => {
    renderExam();
    await screen.findByRole('heading', { name: 'Backend fundamentals' });
    expect(screen.getByRole('group', { name: 'UI language' })).toBeInTheDocument();
  });

  it('names section kinds in words, not "Mcq" (CA-L10)', async () => {
    getPublicExam.mockResolvedValue({
      ...BASE,
      sections: [mcq('a', 0, ['q1']), { ...mcq('b', 1, []), kind: 'coding' }],
    });
    renderExam();
    await screen.findByRole('heading', { name: 'Backend fundamentals' });
    expect(screen.getByText('Multiple choice')).toBeInTheDocument();
    expect(screen.getByText('Coding')).toBeInTheDocument();
    expect(screen.queryByText(/^mcq$/i)).not.toBeInTheDocument();
  });

  it('can be submitted when the response has no sections, only questions (CA-L21)', async () => {
    const user = userEvent.setup();
    getPublicExam.mockResolvedValue({ ...BASE, sections: [], questions: mcq('x', 0, ['q9']).questions });
    renderExam();
    await start(user);
    expect(screen.getByRole('radiogroup', { name: 'Question q9?' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Submit exam' })).toBeInTheDocument();
  });

  it('shows a bare count, not "1/", when auto-submit is relaxed (CA-L3)', async () => {
    const user = userEvent.setup();
    startExam.mockResolvedValue({
      attempt_id: 'att-1',
      started_at: '2026-09-16T00:00:00.000Z',
      deadline: null,
      max_violations: null,
      camera_in_use: false,
    });
    sendIntegrityEvent.mockResolvedValue({
      accepted: true,
      violation_count: 1,
      max_violations: null,
      integrity_score: 90,
    });
    renderExam();
    await start(user);
    document.dispatchEvent(new Event('copy'));
    await screen.findByText('Violation 1 recorded.');
    expect(screen.queryByText('1/')).not.toBeInTheDocument();
  });
});
