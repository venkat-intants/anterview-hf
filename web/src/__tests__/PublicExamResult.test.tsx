// The exam finish screen is NOT a verdict. The software never tells a candidate
// they passed or failed: it says the assessment is submitted and is with the
// hiring team, shows the score as a plain number, and says who acts next. This
// pins that for every kind of result payload, in every language.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ExamResult, TakeExam } from '../api/publicExam';
import i18n from '../lib/i18n';

const getPublicExam = vi.fn();
const startExam = vi.fn();
const submitRound = vi.fn();
vi.mock('../api/publicExam', () => ({
  getPublicExam: (...a: unknown[]) => getPublicExam(...a) as unknown,
  startExam: (...a: unknown[]) => startExam(...a) as unknown,
  grantCameraConsent: vi.fn(),
  submitRound: (...a: unknown[]) => submitRound(...a) as unknown,
  sendIntegrityEvent: vi.fn(),
}));

const auth = vi.hoisted(() => ({
  value: {
    isAuthenticated: false,
    isInitializing: false,
    user: null as { roles: string[] } | null,
  },
}));
vi.mock('../context/AuthContext', () => ({ useAuth: () => auth.value }));

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
  total_questions: 0,
  allow_retake: false,
  already_submitted: false,
  server_now: '2026-09-16T00:00:00.000Z',
  deadline: null,
  scheduled_at: null,
  max_integrity_violations: 3,
  // One empty section: the page only offers "Submit exam" once it has a last
  // section to be on.
  sections: [
    {
      id: 's1',
      title: 'Section 1',
      kind: 'mcq',
      position: 0,
      time_limit_seconds: null,
      questions: [],
      coding_questions: [],
    },
  ],
  questions: [],
  coding_questions: [],
};

const RESULT: ExamResult = {
  attempt_id: 'att-1',
  score_raw: 4,
  score_max: 5,
  score_percent: 80,
  passed: true,
  status: 'submitted',
  submitted_at: '2026-09-16T00:10:00.000Z',
};

// Any word that reads as an outcome, in any of the three languages.
const OUTCOME_TOKENS = [
  /\bpass(ed)?\b/i,
  /\bfail(ed)?\b/i,
  /not this time/i,
  /congratulations/i,
  /unsuccessful/i,
  /उत्तीर्ण/,
  /अनुत्तीर्ण/,
  /इस बार नहीं/,
  /ఉత్తీర్ణ/,
  /ఈసారి కాదు/,
];

async function sitAndSubmit(result: ExamResult): Promise<void> {
  const user = userEvent.setup();
  getPublicExam.mockResolvedValue(EXAM);
  startExam.mockResolvedValue({
    attempt_id: 'att-1',
    started_at: '2026-09-16T00:00:00.000Z',
    deadline: null,
    max_violations: 3,
    camera_in_use: false,
  });
  submitRound.mockResolvedValue(result);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PublicExam />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByRole('heading', { name: 'Backend fundamentals' });
  await user.click(screen.getByRole('checkbox'));
  await user.click(screen.getByRole('button', { name: 'Start exam' }));
  await user.click(await screen.findByRole('button', { name: 'Submit exam' }));
  await screen.findByTestId('exam-submitted');
}

beforeEach(() => {
  vi.clearAllMocks();
  window.location.hash = '#exam_tok_123456';
  auth.value = { isAuthenticated: false, isInitializing: false, user: null };
  Object.defineProperty(document, 'fullscreenElement', {
    value: document.body,
    configurable: true,
  });
});

afterEach(async () => {
  window.location.hash = '';
  Object.defineProperty(document, 'fullscreenElement', { value: null, configurable: true });
  await i18n.changeLanguage('en');
});

describe('PublicExam — the finish screen is not a verdict', () => {
  it.each([
    ['a passing result', RESULT],
    ['a failing result', { ...RESULT, passed: false, score_raw: 1, score_percent: 20 }],
    [
      'a result with no score',
      { ...RESULT, passed: false, score_raw: 0, score_max: 0, score_percent: 0 },
    ],
    ['a result submitted at the time limit', { ...RESULT, status: 'expired' }],
  ])('renders no outcome for %s', async (_name, result) => {
    await sitAndSubmit(result);

    const screenEl = screen.getByTestId('exam-submitted');
    expect(screen.getByRole('heading', { name: 'Assessment submitted' })).toBeInTheDocument();
    expect(screenEl).toHaveTextContent('with the hiring team');
    for (const token of OUTCOME_TOKENS) {
      expect(document.body.textContent ?? '', String(token)).not.toMatch(token);
    }
    // No tick or cross mark: the only SVG is the neutral "sent" icon, and
    // nothing on the screen is coloured by the score.
    expect(document.querySelector('[class*="ui-danger"]')).toBeNull();
  });

  it('shows the score as a plain number when there is one', async () => {
    await sitAndSubmit(RESULT);
    expect(screen.getByText(/Score: 80% \(4\/5 points\)/)).toBeInTheDocument();
  });

  it('says who acts next, and offers a sign-in link only when there is no session', async () => {
    await sitAndSubmit(RESULT);
    const next = screen.getByRole('heading', { name: 'What happens next' }).closest('section');
    expect(next).not.toBeNull();
    expect(
      within(next as HTMLElement).getByText(/A person makes that decision/),
    ).toBeInTheDocument();
    const link = within(next as HTMLElement).getByRole('link', {
      name: 'Track this in My applications',
    });
    expect(link).toHaveAttribute('href', '/login');
  });

  it('offers no account link at all to a guest session', async () => {
    auth.value = {
      isAuthenticated: true,
      isInitializing: false,
      user: { roles: ['guest_candidate'] },
    };
    await sitAndSubmit(RESULT);
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'What happens next' })).toBeInTheDocument(),
    );
    expect(screen.queryByRole('link', { name: /my applications/i })).toBeNull();
    expect(document.querySelector('a[href="/login"], a[href="/applications"]')).toBeNull();
  });

  it.each(['hi', 'te'] as const)('is a non-verdict in %s too', async (lng) => {
    await sitAndSubmit(RESULT);
    await i18n.changeLanguage(lng);
    await waitFor(() =>
      expect(
        screen.queryByRole('heading', { name: 'Assessment submitted' }),
      ).not.toBeInTheDocument(),
    );
    for (const token of OUTCOME_TOKENS) {
      expect(document.body.textContent ?? '', String(token)).not.toMatch(token);
    }
  });
});
