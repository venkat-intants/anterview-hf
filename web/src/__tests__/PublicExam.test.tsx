// Candidate exam start page — the intro card.
//
// Two things a candidate saw that were wrong:
//   • "Round 1: Round 1" — an auto-named round printed through the round label
//     repeats itself. A default name adds nothing, so it is suppressed; a real
//     round name still shows.
//   • a "Language: EN · हि · తె" fact, hard-coded, although an exam's questions
//     are in one language. The fact is gone; questions and duration remain.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { TakeExam } from '../api/publicExam';
import i18n from '../lib/i18n';

const getPublicExam = vi.fn();
const startExam = vi.fn();
const sendIntegrityEvent = vi.fn();
vi.mock('../api/publicExam', () => ({
  getPublicExam: (...a: unknown[]) => getPublicExam(...a) as unknown,
  startExam: (...a: unknown[]) => startExam(...a) as unknown,
  submitRound: vi.fn(),
  sendIntegrityEvent: (...a: unknown[]) => sendIntegrityEvent(...a) as unknown,
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

// PH4-D2 — the candidate is told the FACT that their time includes an
// adjustment, never the percentage, never a note, never who recorded it or
// why. The round/section timers themselves already arrive pre-scaled from
// the server (exam_take.py), so there is nothing to recompute here — only
// this banner to show, in EN, HI and TE.
describe('PublicExam — the accommodation banner (PH4-D2)', () => {
  afterEach(async () => {
    await i18n.changeLanguage('en');
  });

  it('says nothing when no adjustment is effective for this attempt', async () => {
    getPublicExam.mockResolvedValue({ ...EXAM, adjustments: null });
    renderExam();
    await screen.findByRole('heading', { name: 'Backend fundamentals' });
    expect(screen.queryByText(/adjustment/i)).toBeNull();
  });

  it('says nothing when adjustments carries no extra time and no deadline extension', async () => {
    getPublicExam.mockResolvedValue({
      ...EXAM,
      adjustments: { extra_time_percent: null, deadline_extended: false },
    });
    renderExam();
    await screen.findByRole('heading', { name: 'Backend fundamentals' });
    expect(screen.queryByText(/adjustment/i)).toBeNull();
  });

  it('shows the banner in English when extra time is effective', async () => {
    getPublicExam.mockResolvedValue({
      ...EXAM,
      adjustments: { extra_time_percent: 50, deadline_extended: false },
    });
    renderExam();
    await screen.findByRole('heading', { name: 'Backend fundamentals' });
    expect(screen.getByText('Your time for this round includes an adjustment.')).toBeInTheDocument();
    // The fact only — never the percentage this test set up with.
    expect(screen.queryByText(/50%/)).toBeNull();
  });

  it('shows the banner when only the deadline is extended, not the round timer', async () => {
    getPublicExam.mockResolvedValue({
      ...EXAM,
      adjustments: { extra_time_percent: null, deadline_extended: true },
    });
    renderExam();
    await screen.findByRole('heading', { name: 'Backend fundamentals' });
    expect(screen.getByText('Your time for this round includes an adjustment.')).toBeInTheDocument();
  });

  it('shows the banner in Hindi', async () => {
    await i18n.changeLanguage('hi');
    getPublicExam.mockResolvedValue({
      ...EXAM,
      adjustments: { extra_time_percent: 50, deadline_extended: false },
    });
    renderExam();
    await screen.findByRole('heading', { name: 'Backend fundamentals' });
    expect(screen.getByText('इस राउंड के लिए आपके समय में एक समायोजन शामिल है।')).toBeInTheDocument();
  });

  it('shows the banner in Telugu', async () => {
    await i18n.changeLanguage('te');
    getPublicExam.mockResolvedValue({
      ...EXAM,
      adjustments: { extra_time_percent: 50, deadline_extended: false },
    });
    renderExam();
    await screen.findByRole('heading', { name: 'Backend fundamentals' });
    expect(screen.getByText('ఈ రౌండ్ కోసం మీ సమయంలో ఒక సర్దుబాటు చేర్చబడింది.')).toBeInTheDocument();
  });
});

// PH4-D2: `max_violations: null` (this attempt's auto-submit is relaxed)
// arrives from POST /exam/start and must reach useExamProctor BEFORE it is
// enabled — `handleStart` sets it in the same batch as `setPhase('taking')`.
// Only reading the code verified this until now; this drives it through an
// actual violation and checks the copy it produces: the no-countdown
// "Violation N recorded." sentence, never "N remaining before auto-submit.".
describe("PublicExam — the proctor's max_violations wiring (security review)", () => {
  beforeEach(() => {
    // The violation banner also requires `isFullscreen` — jsdom implements no
    // real Fullscreen API, so this forces the one signal it can give:
    // `document.fullscreenElement` already set before the component mounts.
    Object.defineProperty(document, 'fullscreenElement', {
      value: document.body,
      configurable: true,
    });
  });

  afterEach(() => {
    Object.defineProperty(document, 'fullscreenElement', { value: null, configurable: true });
  });

  it("wires null max_violations into the proctor before it is enabled, driving the no-countdown copy", async () => {
    const user = userEvent.setup();
    getPublicExam.mockResolvedValue(EXAM);
    startExam.mockResolvedValue({
      attempt_id: 'att-1',
      started_at: '2026-09-16T00:00:00.000Z',
      deadline: null,
      max_violations: null,
    });
    sendIntegrityEvent.mockResolvedValue({
      accepted: true,
      violation_count: 1,
      max_violations: null,
      integrity_score: 90,
    });

    renderExam();
    await screen.findByRole('heading', { name: 'Backend fundamentals' });
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: 'Start exam' }));

    // Now in the "taking" phase — the consent checkbox is gone.
    await waitFor(() => expect(screen.queryByRole('checkbox')).not.toBeInTheDocument());

    document.dispatchEvent(new Event('copy'));

    expect(await screen.findByText('Violation 1 recorded.')).toBeInTheDocument();
    // The relaxed attempt never shows a countdown to auto-submit.
    expect(screen.queryByText(/remaining/i)).not.toBeInTheDocument();
  });
});
