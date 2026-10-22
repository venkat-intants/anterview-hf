// Tests for the HR hiring pipeline (FE-2).
//
// The pipeline is the one screen where a human records a hire/reject. Two
// properties matter enough to pin:
//   • the decision goes to the applicant whose button was pressed, with the
//     rationale typed on THAT card — a card-index bug here rejects the wrong
//     person, and the write is audit-logged server-side;
//   • hire/reject are offered only at stages where they make sense, so a
//     candidate cannot be hired before anyone has looked at them.

import { analyticsDefaults } from './analyticsFixture';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { PipelineResponse, PipelineRow, HrAnalytics } from '../api/pipeline';

const ROW_BASE = {
  target_job_title: 'Backend Engineer',
  target_level: 'mid',
  ats_recommendation: 'strong',
  total_exam_attempts: 1,
  scorecard_id: null,
  updated_at: '2026-08-01T10:00:00.000Z',
} as const;

const SHORTLISTED: PipelineRow = {
  ...ROW_BASE,
  applicant_id: 'ap-1',
  enrolment_id: 'en-1',
  full_name: 'Bhavya Nair',
  status: 'shortlisted',
  ats_overall: 84,
  best_exam_percent: 78,
  exam_passed: true,
  interview_status: 'invited',
  interview_score: null,
  // Shortlisted and sitting a round: not a decision yet (HR-H1).
  can_hire: false,
};

const NEW: PipelineRow = {
  ...ROW_BASE,
  applicant_id: 'ap-2',
  full_name: 'Chetan Iyer',
  status: 'new',
  ats_overall: 41,
  ats_recommendation: 'weak',
  best_exam_percent: null,
  exam_passed: null,
  interview_status: null,
  interview_score: null,
  can_hire: false,
};

const HIRED: PipelineRow = {
  ...ROW_BASE,
  applicant_id: 'ap-3',
  full_name: 'Deepa Menon',
  status: 'hired',
  ats_overall: 91,
  best_exam_percent: 88,
  exam_passed: true,
  interview_status: 'completed',
  interview_score: 8.4,
  can_hire: false,
};

// Held just under a round's pass mark: waiting for a person, so hireable.
const HELD: PipelineRow = {
  ...ROW_BASE,
  applicant_id: 'ap-4',
  enrolment_id: 'en-4',
  requisition_id: 'req-1',
  full_name: 'Esha Rao',
  status: 'held',
  ats_overall: 66,
  best_exam_percent: 58,
  exam_passed: false,
  interview_status: null,
  interview_score: null,
  can_hire: true,
};

const RESPONSE: PipelineResponse = {
  items: [SHORTLISTED, NEW, HIRED, HELD],
  count: 4,
  limit: 50,
  offset: 0,
};

const ANALYTICS: HrAnalytics = {
  funnel: {
    total_applicants: 3,
    shortlisted: 1,
    exam_taken: 2,
    exam_passed: 2,
    interview_invited: 1,
    interview_completed: 1,
    hired: 1,
    rejected: 0,
  },
  averages: { avg_ats: 72, avg_exam_percent: 83, avg_interview_composite: 8.4 },
  ...analyticsDefaults(),
};

const getPipeline = vi.fn();
const getHrAnalytics = vi.fn();
const setApplicantDecision = vi.fn();
vi.mock('../api/pipeline', () => ({
  getPipeline: (...a: unknown[]) => getPipeline(...a) as unknown,
  getHrAnalytics: (...a: unknown[]) => getHrAnalytics(...a) as unknown,
  setApplicantDecision: (...a: unknown[]) => setApplicantDecision(...a) as unknown,
}));

const REASONS = [
  { code: 'strong-fit', label: 'Strong fit', applies_to: 'hired' as const, requires_explanation: false },
  { code: 'weak-signals', label: 'Weak signals', applies_to: 'rejected' as const, requires_explanation: false },
];
const listDecisionReasons = vi.fn();
vi.mock('../api/scorecards', () => ({
  listDecisionReasons: (...a: unknown[]) => listDecisionReasons(...a) as unknown,
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

import HRPipeline from '../pages/hr/HRPipeline';

function renderPipeline(path = '/hr/pipeline') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <HRPipeline />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/**
 * The COLLAPSED row for a named candidate — where the scores and the quick
 * hire/reject buttons live. Scoping every assertion to one candidate's own row
 * is the point: an unscoped query would pass even if the page rendered the
 * right control on the wrong card.
 */
function cardFor(name: string): HTMLElement {
  return screen.getByText(name).parentElement!.parentElement!.parentElement!;
}

/** The whole card, including the expanded detail panel below the row. */
function cardRootFor(name: string): HTMLElement {
  return cardFor(name).parentElement!;
}

beforeEach(() => {
  vi.clearAllMocks();
  getPipeline.mockResolvedValue(RESPONSE);
  getHrAnalytics.mockResolvedValue(ANALYTICS);
  setApplicantDecision.mockResolvedValue({ id: 'ap-1', status: 'hired' });
  listDecisionReasons.mockResolvedValue(REASONS);
});

describe('HRPipeline - listing', () => {
  it('shows every candidate with their per-stage scores', async () => {
    renderPipeline();

    await screen.findByText('Bhavya Nair');
    const card = cardFor('Bhavya Nair');
    expect(within(card).getByText('84')).toBeInTheDocument(); // ATS
    expect(within(card).getByText('78')).toBeInTheDocument(); // exam %
    expect(within(card).getByText('Backend Engineer · mid')).toBeInTheDocument();
  });

  it('renders a dash for a stage the candidate has not reached', async () => {
    renderPipeline();

    await screen.findByText('Chetan Iyer');
    // No exam and no interview yet — must read as "not reached", not as zero.
    expect(within(cardFor('Chetan Iyer')).getAllByText('-').length).toBeGreaterThan(0);
    expect(within(cardFor('Chetan Iyer')).queryByLabelText('Exam passed')).not.toBeInTheDocument();
  });

  it('counts the candidates in the section heading', async () => {
    renderPipeline();
    expect(await screen.findByRole('heading', { name: /candidates \(4\)/i })).toBeInTheDocument();
  });

  it('requests the chosen stage from the server and resets paging', async () => {
    const user = userEvent.setup();
    renderPipeline();

    await screen.findByText('Bhavya Nair');
    await user.click(screen.getByRole('tab', { name: /exam passed/i }));

    await waitFor(() =>
      expect(getPipeline).toHaveBeenLastCalledWith({
        stage: 'exam_passed',
        limit: 50,
        offset: 0,
      }),
    );
  });

  it('names the stage in the empty state so the operator knows why it is blank', async () => {
    getPipeline.mockResolvedValue({ items: [], count: 0, limit: 50, offset: 0 });
    const user = userEvent.setup();
    renderPipeline();

    expect(await screen.findByText(/no candidates yet/i)).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: /interviewed/i }));
    expect(await screen.findByText(/no candidates at the "interviewed" stage/i)).toBeInTheDocument();
  });
});

describe('HRPipeline - hire / reject', () => {
  async function expand(user: ReturnType<typeof userEvent.setup>, name: string) {
    await screen.findByText(name);
    await user.click(within(cardFor(name)).getByRole('button', { name: /expand details/i }));
    return cardRootFor(name);
  }

  // A hire/reject write now always needs a reason code (O4), which has no
  // affordance on the collapsed row — Expand details is the one way in.
  it('has no one-click decision on the collapsed row any more', async () => {
    renderPipeline();
    await screen.findByText('Esha Rao');
    expect(within(cardFor('Esha Rao')).queryByRole('button', { name: /^hire$/i })).not.toBeInTheDocument();
    expect(within(cardFor('Esha Rao')).queryByRole('button', { name: /^reject$/i })).not.toBeInTheDocument();
  });

  it('will not confirm without a reason code chosen', async () => {
    const user = userEvent.setup();
    renderPipeline();
    const card = await expand(user, 'Esha Rao');
    await user.click(within(card).getByRole('button', { name: /^hire$/i }));

    expect(within(card).getByRole('button', { name: 'Confirm hire' })).toBeDisabled();
    expect(within(card).getByText('Choose a reason.')).toBeInTheDocument();
    expect(setApplicantDecision).not.toHaveBeenCalled();
  });

  it('needs at least three characters of why, as every other decision path does (HR-H1)', async () => {
    const user = userEvent.setup();
    renderPipeline();
    const card = await expand(user, 'Esha Rao');
    await user.click(within(card).getByRole('button', { name: /^hire$/i }));
    await user.selectOptions(within(card).getByLabelText('Reason'), 'strong-fit');
    await user.type(within(card).getByLabelText(/why\?/i), 'ok');

    expect(within(card).getByRole('button', { name: 'Confirm hire' })).toBeDisabled();
    expect(within(card).getByText('Write at least 3 characters explaining why.')).toBeInTheDocument();
  });

  it('sends the decision for the candidate whose card it is, with the reason and the why', async () => {
    const user = userEvent.setup();
    renderPipeline();
    const card = await expand(user, 'Esha Rao');
    await user.click(within(card).getByRole('button', { name: /^hire$/i }));
    await user.selectOptions(within(card).getByLabelText('Reason'), 'strong-fit');
    await user.type(within(card).getByLabelText(/why\?/i), 'Strong system-design answers');
    await user.click(within(card).getByRole('button', { name: 'Confirm hire' }));

    await waitFor(() =>
      // With the application the card is about (B5).
      expect(setApplicantDecision).toHaveBeenCalledWith(
        'ap-4',
        'hired',
        'strong-fit',
        'Strong system-design answers',
        'en-4',
      ),
    );
    expect(toastSuccess).toHaveBeenCalledWith('Esha Rao hired');
  });

  it('offers only the reasons that apply to the chosen outcome (HR-M1)', async () => {
    const user = userEvent.setup();
    renderPipeline();
    const card = await expand(user, 'Esha Rao');
    await user.click(within(card).getByRole('button', { name: /^reject$/i }));
    const select = within(card).getByLabelText('Reason');
    expect(within(select).queryByRole('option', { name: 'Strong fit' })).not.toBeInTheDocument();
    expect(within(select).getByRole('option', { name: 'Weak signals' })).toBeInTheDocument();
  });

  it('does not offer Hire to someone still inside the workflow, and says why (HR-H1)', async () => {
    const user = userEvent.setup();
    renderPipeline();
    const card = await expand(user, 'Bhavya Nair');
    expect(within(card).queryByRole('button', { name: /^hire$/i })).not.toBeInTheDocument();
    expect(within(card).getByText(/hire becomes available once they finish the workflow/i)).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: /^reject$/i })).toBeInTheDocument();
  });

  it('does not offer Hire before a candidate has been shortlisted', async () => {
    const user = userEvent.setup();
    renderPipeline();
    const card = await expand(user, 'Chetan Iyer');
    expect(within(card).queryByRole('button', { name: /^hire$/i })).not.toBeInTheDocument();
    // Rejecting an unscreened applicant is still allowed.
    expect(within(card).getByRole('button', { name: /^reject$/i })).toBeInTheDocument();
  });

  it('offers neither decision once one has been recorded', async () => {
    const user = userEvent.setup();
    renderPipeline();
    const card = await expand(user, 'Deepa Menon');
    expect(within(card).queryByRole('button', { name: /^hire$/i })).not.toBeInTheDocument();
    expect(within(card).queryByRole('button', { name: /^reject$/i })).not.toBeInTheDocument();
  });

  it('marks a held candidate and links to the decision queue (HR-M11)', async () => {
    const user = userEvent.setup();
    renderPipeline();
    await screen.findByText('Esha Rao');
    expect(within(cardFor('Esha Rao')).getByText('Held')).toBeInTheDocument();
    const card = await expand(user, 'Esha Rao');
    expect(within(card).getByRole('link', { name: 'Decide in the decision queue' })).toHaveAttribute(
      'href',
      '/hr/requisitions/req-1/decisions',
    );
  });

  it('surfaces a rejected decision write instead of implying it succeeded', async () => {
    setApplicantDecision.mockRejectedValue(new Error('Applicant already decided'));
    const user = userEvent.setup();
    renderPipeline();
    const card = await expand(user, 'Esha Rao');
    await user.click(within(card).getByRole('button', { name: /^hire$/i }));
    await user.selectOptions(within(card).getByLabelText('Reason'), 'strong-fit');
    await user.type(within(card).getByLabelText(/why\?/i), 'Strong panel');
    await user.click(within(card).getByRole('button', { name: 'Confirm hire' }));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('Applicant already decided'));
    expect(toastSuccess).not.toHaveBeenCalled();
  });
});

describe('HRPipeline - a failed load', () => {
  it('says the pipeline could not load instead of showing an empty one (HR-M5)', async () => {
    getPipeline.mockRejectedValue(new Error('boom'));
    renderPipeline();
    expect(await screen.findByText('Could not load the pipeline.')).toBeInTheDocument();
    expect(screen.queryByText(/no candidates yet/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});

describe('HRPipeline - list and board are the same data', () => {
  it('offers both views', async () => {
    renderPipeline();
    await screen.findByText('Bhavya Nair');
    expect(screen.getByRole('group', { name: 'Pipeline view' })).toBeInTheDocument();
  });

  it('switches to the board without refetching', async () => {
    // The board is an arrangement, not a different dataset. A switch that
    // refetched would paginate differently and quietly show a different set.
    const user = userEvent.setup();
    renderPipeline();
    await screen.findByText('Bhavya Nair');
    const before = getPipeline.mock.calls.length;

    await user.click(screen.getByRole('button', { name: 'board' }));
    expect(screen.getByRole('region', { name: 'Candidate pipeline board' })).toBeInTheDocument();
    expect(getPipeline.mock.calls.length).toBe(before);
  });

  it('remembers the choice', async () => {
    const user = userEvent.setup();
    renderPipeline();
    await screen.findByText('Bhavya Nair');
    await user.click(screen.getByRole('button', { name: 'board' }));
    expect(localStorage.getItem('intants:pipeline-view')).toBe('board');
  });
});

describe('HRPipeline - deep links (HR-L9)', () => {
  it('opens on the stage the link names', async () => {
    renderPipeline('/hr/pipeline?stage=exam_passed');
    await waitFor(() =>
      expect(getPipeline).toHaveBeenCalledWith({ stage: 'exam_passed', limit: 50, offset: 0 }),
    );
  });
});
