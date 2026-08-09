// Tests for the HR hiring pipeline (FE-2).
//
// The pipeline is the one screen where a human records a hire/reject. Two
// properties matter enough to pin:
//   • the decision goes to the applicant whose button was pressed, with the
//     rationale typed on THAT card — a card-index bug here rejects the wrong
//     person, and the write is audit-logged server-side;
//   • hire/reject are offered only at stages where they make sense, so a
//     candidate cannot be hired before anyone has looked at them.

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
  full_name: 'Bhavya Nair',
  status: 'shortlisted',
  ats_overall: 84,
  best_exam_percent: 78,
  exam_passed: true,
  interview_status: 'invited',
  interview_score: null,
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
};

const RESPONSE: PipelineResponse = {
  items: [SHORTLISTED, NEW, HIRED],
  count: 3,
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
};

const getPipeline = vi.fn();
const getHrAnalytics = vi.fn();
const setApplicantDecision = vi.fn();
vi.mock('../api/pipeline', () => ({
  getPipeline: (...a: unknown[]) => getPipeline(...a) as unknown,
  getHrAnalytics: (...a: unknown[]) => getHrAnalytics(...a) as unknown,
  setApplicantDecision: (...a: unknown[]) => setApplicantDecision(...a) as unknown,
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

function renderPipeline() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
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
});

describe('HRPipeline — listing', () => {
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
    expect(within(cardFor('Chetan Iyer')).getAllByText('—').length).toBeGreaterThan(0);
    expect(within(cardFor('Chetan Iyer')).queryByLabelText('Exam passed')).not.toBeInTheDocument();
  });

  it('counts the candidates in the section heading', async () => {
    renderPipeline();
    expect(await screen.findByRole('heading', { name: /candidates \(3\)/i })).toBeInTheDocument();
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

describe('HRPipeline — hire / reject', () => {
  it('sends the decision for the candidate whose button was pressed', async () => {
    const user = userEvent.setup();
    renderPipeline();

    await screen.findByText('Bhavya Nair');
    await user.click(within(cardFor('Bhavya Nair')).getByRole('button', { name: /^hire$/i }));

    await waitFor(() =>
      expect(setApplicantDecision).toHaveBeenCalledWith('ap-1', 'hired', ''),
    );
    expect(toastSuccess).toHaveBeenCalledWith('Applicant hired');
  });

  it('carries the rationale typed on that card into the audit-logged write', async () => {
    const user = userEvent.setup();
    renderPipeline();

    await screen.findByText('Bhavya Nair');
    await user.click(
      within(cardFor('Bhavya Nair')).getByRole('button', { name: /expand details/i }),
    );
    const card = cardRootFor('Bhavya Nair');
    await user.type(
      within(card).getByLabelText(/decision rationale/i),
      'Strong system-design answers',
    );
    await user.click(within(card).getAllByRole('button', { name: /^reject$/i })[0]);

    await waitFor(() =>
      expect(setApplicantDecision).toHaveBeenCalledWith(
        'ap-1',
        'rejected',
        'Strong system-design answers',
      ),
    );
  });

  it('does not offer Hire before a candidate has been shortlisted', async () => {
    renderPipeline();

    await screen.findByText('Chetan Iyer');
    const card = cardFor('Chetan Iyer');
    expect(within(card).queryByRole('button', { name: /^hire$/i })).not.toBeInTheDocument();
    // Rejecting an unscreened applicant is still allowed.
    expect(within(card).getByRole('button', { name: /^reject$/i })).toBeInTheDocument();
  });

  it('offers neither decision once one has been recorded', async () => {
    renderPipeline();

    await screen.findByText('Deepa Menon');
    const card = cardFor('Deepa Menon');
    expect(within(card).queryByRole('button', { name: /^hire$/i })).not.toBeInTheDocument();
    expect(within(card).queryByRole('button', { name: /^reject$/i })).not.toBeInTheDocument();
  });

  it('surfaces a rejected decision write instead of implying it succeeded', async () => {
    setApplicantDecision.mockRejectedValue(new Error('Applicant already decided'));
    const user = userEvent.setup();
    renderPipeline();

    await screen.findByText('Bhavya Nair');
    await user.click(within(cardFor('Bhavya Nair')).getByRole('button', { name: /^hire$/i }));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('Applicant already decided'));
    expect(toastSuccess).not.toHaveBeenCalled();
  });
});
