// InterviewerScorecard (/interviewer/scorecards/:id) — D4-1.
//
// The doc's flow is Kit → Conduct → Notes → Scorecard → Submit, and the rule
// underneath all of it is D-05: a scorecard can never decide an outcome, only
// record one interviewer's assessment. What matters here:
//   • the score control is keyboard-usable radio semantics, and "not
//     assessed" is mutually exclusive with a score;
//   • submit needs a confirmation, and a submitted scorecard is read-only;
//   • a correction opens a NEW scorecard and navigates there;
//   • server errors show up on the page, not just in a toast.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { InterviewKit, PrivateNotes, ScorecardDetail } from '../api/interviewer';
import { ApiError } from '../api/client';

const api = {
  getScorecard: vi.fn(),
  getScorecardKit: vi.fn(),
  getPrivateNotes: vi.fn(),
  saveScorecard: vi.fn(),
  savePrivateNotes: vi.fn(),
  submitScorecard: vi.fn(),
  requestCorrection: vi.fn(),
};
vi.mock('../api/interviewer', () => ({
  getScorecard: (...a: unknown[]) => api.getScorecard(...a) as unknown,
  getScorecardKit: (...a: unknown[]) => api.getScorecardKit(...a) as unknown,
  getPrivateNotes: (...a: unknown[]) => api.getPrivateNotes(...a) as unknown,
  saveScorecard: (...a: unknown[]) => api.saveScorecard(...a) as unknown,
  savePrivateNotes: (...a: unknown[]) => api.savePrivateNotes(...a) as unknown,
  submitScorecard: (...a: unknown[]) => api.submitScorecard(...a) as unknown,
  requestCorrection: (...a: unknown[]) => api.requestCorrection(...a) as unknown,
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

import InterviewerScorecard from '../pages/interviewer/InterviewerScorecard';

function detail(over: Partial<ScorecardDetail> = {}): ScorecardDetail {
  return {
    scorecard_id: '5c0f1a2b-3d4e-4f60-8a71-9b8c7d6e5f41',
    state: 'in_progress',
    status: 'in_progress',
    candidate_name: 'Deepa Menon',
    job_title: 'Backend Engineer',
    round_title: 'Panel interview',
    due_at: '2026-09-30T10:00:00.000Z',
    submitted_at: null,
    summary: null,
    correction_reason: null,
    is_correction: false,
    superseded: false,
    can_edit: true,
    can_correct: false,
    criteria: [
      {
        competency_id: 'c-sysdesign',
        competency_name: 'System design',
        weight: 0.6,
        anchors: { low: 'Cannot decompose a problem', mid: 'Decomposes with prompting', high: 'Decomposes unprompted' },
        score: null,
        not_assessed: false,
        evidence: null,
      },
      {
        competency_id: 'c-comm',
        competency_name: 'Communication',
        weight: 0.4,
        anchors: null,
        score: null,
        not_assessed: false,
        evidence: null,
      },
    ],
    ...over,
  };
}

function kit(over: Partial<InterviewKit> = {}): InterviewKit {
  return {
    round_title: 'Panel interview',
    instructions: 'Keep it to 45 minutes.',
    interviewer_notes_from_hr: 'Focus on system design depth.',
    has_custom_kit: true,
    updated_at: '2026-09-01T00:00:00.000Z',
    criteria: [
      {
        competency_id: 'c-sysdesign',
        competency_name: 'System design',
        weight: 0.6,
        anchors: { low: 'Cannot decompose a problem', mid: 'Decomposes with prompting', high: 'Decomposes unprompted' },
        frozen_probes: ['Design a URL shortener'],
        what_to_evaluate: ['Depth of tradeoff discussion'],
        look_for: ['Mentions caching'],
        probes: ['What would you change at 10x scale?'],
      },
      {
        competency_id: 'c-comm',
        competency_name: 'Communication',
        weight: 0.4,
        anchors: null,
        frozen_probes: [],
        what_to_evaluate: [],
        look_for: [],
        probes: [],
      },
    ],
    ...over,
  };
}

function notes(over: Partial<PrivateNotes> = {}): PrivateNotes {
  return { notes: '', updated_at: null, ...over };
}

function renderPage(id = '5c0f1a2b-3d4e-4f60-8a71-9b8c7d6e5f41', path = `/interviewer/scorecards/${id}`) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/interviewer/scorecards/:scorecardId" element={<InterviewerScorecard />} />
          <Route path="/interviewer" element={<div>my interviews page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  api.getScorecard.mockResolvedValue(detail());
  api.getScorecardKit.mockResolvedValue(kit());
  api.getPrivateNotes.mockResolvedValue(notes());
  api.saveScorecard.mockResolvedValue({ scorecard_id: '5c0f1a2b-3d4e-4f60-8a71-9b8c7d6e5f41', status: 'in_progress' });
  api.submitScorecard.mockResolvedValue({
    scorecard_id: '5c0f1a2b-3d4e-4f60-8a71-9b8c7d6e5f41',
    status: 'submitted',
    submitted_at: '2026-09-10T00:00:00.000Z',
    late: false,
  });
  api.savePrivateNotes.mockResolvedValue({ updated_at: '2026-09-10T00:00:00.000Z' });
  api.requestCorrection.mockResolvedValue({ scorecard_id: '5c0f1a2b-3d4e-4f60-8a71-9b8c7d6e5f42', corrects: '5c0f1a2b-3d4e-4f60-8a71-9b8c7d6e5f41' });
});

describe('InterviewerScorecard — loading and error', () => {
  it('shows a loading state before the scorecard arrives', () => {
    api.getScorecard.mockImplementation(() => new Promise(() => undefined));
    renderPage();
    expect(screen.getByText(/Loading scorecard/)).toBeInTheDocument();
  });

  it('surfaces a load failure', async () => {
    api.getScorecard.mockRejectedValue(new Error('Not your scorecard'));
    renderPage();
    expect(await screen.findByText('Not your scorecard')).toBeInTheDocument();
  });

  it('says an assignment is no longer available on a 404, not a generic error', async () => {
    // A withdrawn assignment now 404s on every interviewer-facing endpoint.
    api.getScorecard.mockRejectedValue(new ApiError('Not found', 404));
    renderPage();
    expect(await screen.findByText('This assignment is no longer available.')).toBeInTheDocument();
  });
});

describe('InterviewerScorecard — the interview kit tab', () => {
  it('opens on the kit tab, showing instructions, HR notes and per-criterion guidance', async () => {
    renderPage();

    expect(await screen.findByText('Keep it to 45 minutes.')).toBeInTheDocument();
    expect(screen.getByText('Focus on system design depth.')).toBeInTheDocument();
    expect(screen.getByText('Depth of tradeoff discussion')).toBeInTheDocument();
    expect(screen.getByText('Mentions caching')).toBeInTheDocument();
    // Frozen + kit probes both shown, labelled as guidance.
    expect(screen.getByText('Design a URL shortener')).toBeInTheDocument();
    expect(screen.getByText('What would you change at 10x scale?')).toBeInTheDocument();
    expect(screen.getByText(/guidance, not mandatory questions/)).toBeInTheDocument();
  });

  it('shows the anchors as weak / adequate / strong', async () => {
    renderPage();
    await screen.findByText('Keep it to 45 minutes.');
    expect(screen.getByText('Weak')).toBeInTheDocument();
    expect(screen.getByText('Adequate')).toBeInTheDocument();
    expect(screen.getByText('Strong')).toBeInTheDocument();
  });

  it('works with no custom kit — the no-kit fallback', async () => {
    api.getScorecardKit.mockResolvedValue(kit({ has_custom_kit: false, instructions: null, interviewer_notes_from_hr: null }));
    renderPage();

    expect(await screen.findByText(/has not customised this round/i)).toBeInTheDocument();
    // The criteria still render even with no custom kit.
    expect(screen.getByText('System design')).toBeInTheDocument();
  });

  it('labels the notes textarea as private and saves them', async () => {
    const user = userEvent.setup();
    renderPage();

    const textarea = await screen.findByLabelText(/Private/i);
    expect(screen.getByText(/not shared with HR, not part of your scorecard/i)).toBeInTheDocument();
    await user.type(textarea, 'Candidate hesitated on caching.');
    await user.click(screen.getByRole('button', { name: /save notes/i }));

    await waitFor(() =>
      expect(api.savePrivateNotes).toHaveBeenCalledWith('5c0f1a2b-3d4e-4f60-8a71-9b8c7d6e5f41', 'Candidate hesitated on caching.'),
    );
    expect(toastSuccess).toHaveBeenCalledWith('Private notes saved');
    // The "Saved …" line follows the save rather than staying at "Not saved yet".
    expect(await screen.findByText(/^Saved /)).toBeInTheDocument();
    expect(screen.queryByText('Not saved yet')).not.toBeInTheDocument();
  });
});

describe('InterviewerScorecard — the scorecard tab', () => {
  async function openScorecardTab() {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Deepa Menon');
    await user.click(screen.getByRole('tab', { name: 'Scorecard' }));
    return user;
  }

  it('scores a criterion by clicking', async () => {
    const user = await openScorecardTab();

    const group = screen.getByRole('radiogroup', { name: 'Score — System design' });
    const four = within(group).getByRole('radio', { name: 'Score 4' });
    expect(four).toHaveAttribute('aria-checked', 'false');
    await user.click(four);
    expect(four).toHaveAttribute('aria-checked', 'true');
  });

  it('follows the radio-group keyboard pattern: one tab stop, arrows move and select', async () => {
    const user = await openScorecardTab();

    const group = screen.getByRole('radiogroup', { name: 'Score — System design' });
    const radios = within(group).getAllByRole('radio');
    // One Tab stop per group — score 1 while nothing is chosen.
    expect(radios.map((r) => r.getAttribute('tabindex'))).toEqual(['0', '-1', '-1', '-1', '-1']);

    radios[0].focus();
    await user.keyboard('{ArrowRight}{ArrowRight}');
    expect(within(group).getByRole('radio', { name: 'Score 3' })).toHaveAttribute('aria-checked', 'true');
    expect(within(group).getByRole('radio', { name: 'Score 3' })).toHaveFocus();
    expect(within(group).getByRole('radio', { name: 'Score 3' })).toHaveAttribute('tabindex', '0');

    await user.keyboard('{End}');
    expect(within(group).getByRole('radio', { name: 'Score 5' })).toHaveAttribute('aria-checked', 'true');
    await user.keyboard('{ArrowRight}');
    expect(within(group).getByRole('radio', { name: 'Score 1' })).toHaveAttribute('aria-checked', 'true');
    await user.keyboard('{ArrowLeft}');
    expect(within(group).getByRole('radio', { name: 'Score 5' })).toHaveFocus();
  });

  it('makes "not assessed" mutually exclusive with a score', async () => {
    const user = await openScorecardTab();

    const group = screen.getByRole('radiogroup', { name: 'Score — System design' });
    await user.click(within(group).getByRole('radio', { name: 'Score 3' }));
    expect(within(group).getByRole('radio', { name: 'Score 3' })).toHaveAttribute('aria-checked', 'true');

    await user.click(screen.getByRole('switch', { name: 'Mark System design as not assessed' }));

    // The score is cleared once marked not assessed.
    expect(within(group).getByRole('radio', { name: 'Score 3' })).toHaveAttribute('aria-checked', 'false');
    expect(within(group).getByRole('radio', { name: 'Score 3' })).toBeDisabled();
  });

  it('saves a draft with the scores, evidence and summary entered', async () => {
    const user = await openScorecardTab();

    const group = screen.getByRole('radiogroup', { name: 'Score — System design' });
    await user.click(within(group).getByRole('radio', { name: 'Score 5' }));
    await user.type(screen.getAllByLabelText('Evidence')[0], 'Walked through sharding unprompted.');
    await user.type(screen.getByLabelText('Overall summary'), 'Strong candidate.');
    await user.click(screen.getByRole('button', { name: /save draft/i }));

    await waitFor(() => expect(api.saveScorecard).toHaveBeenCalledTimes(1));
    const [id, body] = api.saveScorecard.mock.calls[0] as [string, { scores: unknown[]; summary: string | null }];
    expect(id).toBe('5c0f1a2b-3d4e-4f60-8a71-9b8c7d6e5f41');
    expect(body.summary).toBe('Strong candidate.');
    expect(body.scores).toContainEqual({
      competency_id: 'c-sysdesign',
      score: 5,
      not_assessed: false,
      evidence: 'Walked through sharding unprompted.',
    });
  });

  it('asks for confirmation before submitting, and explains submission is final', async () => {
    const user = await openScorecardTab();

    await user.click(screen.getByRole('button', { name: /submit scorecard/i }));
    expect(api.submitScorecard).not.toHaveBeenCalled();
    // `.` rather than a literal apostrophe: the component uses a typographic
    // "can’t" (curly quote), matching this codebase's convention elsewhere.
    expect(screen.getByText(/can.t be edited — only corrected/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /confirm submit/i }));
    await waitFor(() => expect(api.submitScorecard).toHaveBeenCalledTimes(1));
    expect(toastSuccess).toHaveBeenCalledWith('Scorecard submitted');
  });

  it('shows the server error inline, not only as a toast', async () => {
    api.saveScorecard.mockRejectedValue(new Error('Score or mark as not assessed: System design.'));
    const user = await openScorecardTab();

    await user.click(screen.getByRole('button', { name: /save draft/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Score or mark as not assessed: System design.',
    );
  });
});

describe('InterviewerScorecard — read-only, superseded and correction', () => {
  it('disables every control when can_edit is false and there is nothing to correct', async () => {
    api.getScorecard.mockResolvedValue(detail({ can_edit: false, can_correct: false, status: 'submitted', state: 'submitted' }));
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Deepa Menon');
    await user.click(screen.getByRole('tab', { name: 'Scorecard' }));

    expect(screen.queryByRole('button', { name: /save draft/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /submit scorecard/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /request correction/i })).not.toBeInTheDocument();
  });

  it('shows a superseded banner and stays read-only', async () => {
    api.getScorecard.mockResolvedValue(
      detail({ can_edit: false, can_correct: false, superseded: true, status: 'submitted', state: 'submitted' }),
    );
    renderPage();

    expect(await screen.findByText(/This scorecard was corrected/i)).toBeInTheDocument();
  });

  it('requests a correction with a 10-1000 character reason and navigates to the new draft', async () => {
    api.getScorecard.mockResolvedValue(
      detail({ can_edit: false, can_correct: true, status: 'submitted', state: 'submitted' }),
    );
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Deepa Menon');
    await user.click(screen.getByRole('tab', { name: 'Scorecard' }));
    await user.click(screen.getByRole('button', { name: /request correction/i }));

    const startButton = screen.getByRole('button', { name: /start correction/i });
    expect(startButton).toBeDisabled();

    await user.type(screen.getByLabelText(/why does this need correcting/i), 'Too short');
    expect(startButton).toBeDisabled();

    await user.type(screen.getByLabelText(/why does this need correcting/i), ' — realised the wrong evidence was recorded.');
    expect(startButton).not.toBeDisabled();

    await user.click(startButton);
    await waitFor(() => expect(api.requestCorrection).toHaveBeenCalledTimes(1));
    expect(api.requestCorrection.mock.calls[0]?.[0]).toBe('5c0f1a2b-3d4e-4f60-8a71-9b8c7d6e5f41');

    // Navigated to the NEW draft's scorecard id, not back to the original.
    await waitFor(() =>
      expect(api.getScorecard).toHaveBeenCalledWith('5c0f1a2b-3d4e-4f60-8a71-9b8c7d6e5f42'),
    );
  });

  it('starts the correction draft afresh: its own scores, no leftover state', async () => {
    const ORIGINAL = '5c0f1a2b-3d4e-4f60-8a71-9b8c7d6e5f41';
    const CORRECTION = '5c0f1a2b-3d4e-4f60-8a71-9b8c7d6e5f42';
    const scored = (score: number) => [
      { ...detail().criteria[0], score },
      { ...detail().criteria[1], score: 3 },
    ];
    api.getScorecard.mockImplementation((id: string) =>
      Promise.resolve(
        id === ORIGINAL
          ? detail({ can_edit: false, can_correct: true, status: 'submitted', state: 'submitted',
                     summary: 'Original summary', criteria: scored(2) })
          : detail({ scorecard_id: CORRECTION, is_correction: true,
                     correction_reason: 'Scored design too low',
                     summary: 'Corrected summary', criteria: scored(4) }),
      ),
    );
    const user = userEvent.setup();
    renderPage(ORIGINAL);

    await screen.findByText('Deepa Menon');
    await user.click(screen.getByRole('tab', { name: 'Scorecard' }));
    expect(screen.getByLabelText('Overall summary')).toHaveValue('Original summary');
    await user.click(screen.getByRole('button', { name: /request correction/i }));
    await user.type(screen.getByLabelText(/why does this need correcting/i), 'Scored design too low');
    await user.click(screen.getByRole('button', { name: /start correction/i }));

    // The new draft's own content — not the first scorecard's hydrated state.
    expect(await screen.findByText(/Correcting the earlier scorecard/)).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: 'Scorecard' }));
    expect(screen.getByLabelText('Overall summary')).toHaveValue('Corrected summary');
    const group = screen.getByRole('radiogroup', { name: 'Score — System design' });
    expect(within(group).getByRole('radio', { name: 'Score 4' })).toHaveAttribute('aria-checked', 'true');
    // And none of the first page's transient state (the correction form).
    expect(screen.queryByLabelText(/why does this need correcting/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});

describe('InterviewerScorecard — the route parameter', () => {
  it('refuses an id that is not a scorecard id, and requests nothing', async () => {
    renderPage('x', '/interviewer/scorecards/..%2F..%2Fhr%2Frounds%2Fabc%2Fkit');
    expect(await screen.findByText('Scorecard not found.')).toBeInTheDocument();
    expect(api.getScorecard).not.toHaveBeenCalled();
    expect(api.getScorecardKit).not.toHaveBeenCalled();
    expect(api.getPrivateNotes).not.toHaveBeenCalled();
  });
});
