// Tests for the decision queue — the human end of the workflow engine (D-05, E2).
//
// The rule the whole engine is built on is that no automation ends a
// candidacy. This page is where that rule is kept, so what matters is:
//
//   • held and finished candidates appear in ONE list. Held candidates behind
//     their own tab are held candidates nobody opens, which would make the rule
//     true on paper and false in practice;
//   • a hire or a reject goes to the enrolment whose card it was pressed on,
//     never on the first click, and never without a reason — it ends someone's
//     candidacy and is recorded against the person who did it;
//   • releasing a hold is offered only where there is a hold to release;
//   • the evidence is one click away, for THIS application;
//   • a closed opening with people still waiting says so.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { DecisionQueueRow } from '../api/workflows';

const HELD: DecisionQueueRow = {
  enrolment_id: 'en-held',
  applicant_id: 'ap-held',
  full_name: 'Asha Rao',
  email: 'asha@example.com',
  status: 'held',
  held: true,
  held_reason: 'Scored 54% against a 60% threshold on Fundamentals',
  ats_overall: 71,
  rounds_taken: 1,
  best_percent: 54,
  current_round_title: 'Fundamentals',
  workflow_version: 2,
  composite_percent: 54,
  round_results: [
    { round_id: 'r1', title: 'Fundamentals', position: 0, kind: 'mcq', percent: 54,
      passed: false, graded_by: 'auto' },
  ],
  scorecards: null,
};

const FINISHED: DecisionQueueRow = {
  enrolment_id: 'en-done',
  applicant_id: 'ap-done',
  full_name: 'Bhavya Nair',
  email: 'bhavya@example.com',
  status: 'interviewed',
  held: false,
  held_reason: null,
  ats_overall: 88,
  rounds_taken: 3,
  best_percent: 82,
  workflow_version: 2,
  composite_percent: 79.3,
  scorecards: { assigned: 3, submitted: 2, late: 1 },
};

const REASONS = [
  { code: 'strong-fit', label: 'Strong fit', applies_to: 'hired' as const, requires_explanation: false },
  { code: 'not-enough-exp', label: 'Not enough experience', applies_to: 'rejected' as const, requires_explanation: false },
  { code: 'other', label: 'Other', applies_to: 'both' as const, requires_explanation: true },
];

const getDecisionQueue = vi.fn();
const releaseHold = vi.fn();
const recordFinalDecision = vi.fn();
vi.mock('../api/workflows', () => ({
  getDecisionQueue: (...a: unknown[]) => getDecisionQueue(...a) as unknown,
  releaseHold: (...a: unknown[]) => releaseHold(...a) as unknown,
  recordFinalDecision: (...a: unknown[]) => recordFinalDecision(...a) as unknown,
  recordRoundReview: vi.fn(),
}));

const listDecisionReasons = vi.fn();
vi.mock('../api/scorecards', () => ({
  listDecisionReasons: (...a: unknown[]) => listDecisionReasons(...a) as unknown,
}));

const getRequisition = vi.fn();
vi.mock('../api/requisitions', () => ({
  getRequisition: (...a: unknown[]) => getRequisition(...a) as unknown,
}));

// The drawer has its own tests; here it only has to open for the right person.
vi.mock('../components/CandidateDrawer', () => ({
  default: ({ applicantId, enrolmentId }: { applicantId: string | null; enrolmentId?: string | null }) =>
    applicantId ? <div data-testid="drawer">{`${applicantId}:${enrolmentId}`}</div> : null,
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
  return { ...actual, useParams: () => ({ requisitionId: 'req-1' }) };
});

import DecisionQueue from '../pages/hr/DecisionQueue';

function renderQueue() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <DecisionQueue />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** One person's card. Scoped, so a control can never be found on another's. */
function cardFor(name: string): HTMLElement {
  return screen.getByText(name).closest('div.rounded-\\[24px\\]') as HTMLElement;
}

beforeEach(() => {
  vi.clearAllMocks();
  getRequisition.mockResolvedValue({ id: 'req-1', title: 'Backend Engineer', status: 'open',
    unresolved: 2 });
  getDecisionQueue.mockResolvedValue([HELD, FINISHED]);
  recordFinalDecision.mockResolvedValue({ enrolment_id: 'en-done', status: 'hired' });
  releaseHold.mockResolvedValue({ action: 'released', enrolment_id: 'en-held' });
  listDecisionReasons.mockResolvedValue(REASONS);
});

describe('DecisionQueue — one list', () => {
  it('shows held and finished candidates together', async () => {
    renderQueue();
    await screen.findByText('Asha Rao');

    expect(screen.getByText('Bhavya Nair')).toBeTruthy();
    expect(screen.getByText('1 held below a round threshold')).toBeTruthy();
  });

  it('says the hold was not a rejection, because the badge reads like one', async () => {
    renderQueue();
    await screen.findByText('Asha Rao');

    expect(
      within(cardFor('Asha Rao')).getByText(/Nothing was decided automatically/),
    ).toBeTruthy();
  });

  it('offers a release only where there is a hold', async () => {
    renderQueue();
    await screen.findByText('Asha Rao');

    expect(within(cardFor('Asha Rao')).getByText('Let them continue')).toBeTruthy();
    expect(within(cardFor('Bhavya Nair')).queryByText('Let them continue')).toBeNull();
  });

  it('shows where each candidate is, and their scores as a summary', async () => {
    renderQueue();
    await screen.findByText('Asha Rao');

    const held = cardFor('Asha Rao');
    expect(within(held).getByText(/Held on Fundamentals · workflow v2/)).toBeTruthy();
    expect(within(held).getByText(/average across rounds 54%/)).toBeTruthy();
    expect(within(held).getByLabelText(/Completed rounds for Asha Rao/).textContent).toMatch(
      /Fundamentals\s*54%\s*· held/,
    );
    expect(within(cardFor('Bhavya Nair')).getByText(/Finished every round/)).toBeTruthy();
  });

  it('shows human-interview scorecard progress when the candidate has any', async () => {
    renderQueue();
    await screen.findByText('Bhavya Nair');

    const finished = cardFor('Bhavya Nair');
    expect(within(finished).getByText(/2\/3 scorecards in/)).toBeTruthy();
    expect(within(finished).getByText(/1 late/)).toBeTruthy();
    // Asha has no human_review round on her workflow — nothing to show.
    expect(within(cardFor('Asha Rao')).queryByText(/scorecards in/)).toBeNull();
  });
});

describe('DecisionQueue — SLA (PH4-O1, informational only)', () => {
  const OVERDUE: DecisionQueueRow = {
    ...HELD,
    enrolment_id: 'en-overdue',
    applicant_id: 'ap-overdue',
    full_name: 'Chetan Iyer',
    sla: {
      state: 'overdue',
      sla_hours: 24,
      entered_at: '2026-09-01T00:00:00.000Z',
      due_at: '2026-09-02T00:00:00.000Z',
      hours_remaining: -10,
    },
    stage_owner_name: 'Priya HR',
    open_exceptions: 1,
  };

  it('shows an overdue badge with the due time and owner, and an open-exceptions badge', async () => {
    getDecisionQueue.mockResolvedValue([OVERDUE, FINISHED]);
    renderQueue();
    await screen.findByText('Chetan Iyer');

    const card = cardFor('Chetan Iyer');
    expect(within(card).getByText(/Overdue since/)).toBeTruthy();
    expect(within(card).getByText(/Priya HR/)).toBeTruthy();
    expect(within(card).getByText('1 open exception')).toBeTruthy();
    // Nothing SLA-related on a row with no SLA.
    expect(within(cardFor('Bhavya Nair')).queryByText(/Overdue|Due soon|On track/)).toBeNull();
  });

  it('filters to overdue-or-due-soon without hiding anyone from the underlying queue', async () => {
    const user = userEvent.setup();
    getDecisionQueue.mockResolvedValue([OVERDUE, FINISHED]);
    renderQueue();
    await screen.findByText('Chetan Iyer');
    expect(screen.getByText('Bhavya Nair')).toBeTruthy();

    await user.click(screen.getByLabelText(/Overdue or due soon only/));

    expect(screen.getByText('Chetan Iyer')).toBeTruthy();
    expect(screen.queryByText('Bhavya Nair')).toBeNull();
    // Toggling back restores the full queue — the filter never re-fetches or
    // drops data, it only narrows what is rendered.
    await user.click(screen.getByLabelText(/Overdue or due soon only/));
    expect(screen.getByText('Bhavya Nair')).toBeTruthy();
    expect(getDecisionQueue).toHaveBeenCalledTimes(1);
  });
});

describe('DecisionQueue — the decision itself', () => {
  it('does not hire on the first click', async () => {
    const user = userEvent.setup();
    renderQueue();
    await screen.findByText('Bhavya Nair');

    await user.click(within(cardFor('Bhavya Nair')).getByText('Hire'));

    expect(recordFinalDecision).not.toHaveBeenCalled();
    expect(within(cardFor('Bhavya Nair')).getByText(/ends their candidacy/)).toBeTruthy();
  });

  it('will not record a hire or reject without a reason code chosen', async () => {
    const user = userEvent.setup();
    renderQueue();
    await screen.findByText('Bhavya Nair');

    const card = cardFor('Bhavya Nair');
    await user.click(within(card).getByText('Reject'));
    const confirm = within(card).getByText('Confirm').closest('button') as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(within(card).getByText('Choose a reason above first.')).toBeTruthy();
    await user.click(confirm);
    expect(recordFinalDecision).not.toHaveBeenCalled();
  });

  it('will not record a hire or reject without free-text why, even with a reason chosen', async () => {
    const user = userEvent.setup();
    renderQueue();
    await screen.findByText('Bhavya Nair');

    const card = cardFor('Bhavya Nair');
    await user.click(within(card).getByText('Hire'));
    await user.selectOptions(within(card).getByLabelText('Reason'), 'strong-fit');
    const confirm = within(card).getByText('Confirm').closest('button') as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(within(card).getByText(/Write at least 3 characters above first\./)).toBeTruthy();
  });

  it('sends the decision, its reason code and its free-text reason to the enrolment whose card it was pressed on', async () => {
    const user = userEvent.setup();
    renderQueue();
    await screen.findByText('Bhavya Nair');

    const card = cardFor('Bhavya Nair');
    await user.type(within(card).getByLabelText(/Why/), 'Strong across all three rounds');
    await user.click(within(card).getByText('Hire'));
    await user.selectOptions(within(card).getByLabelText('Reason'), 'strong-fit');
    await user.click(within(card).getByText('Confirm'));

    await waitFor(() => expect(recordFinalDecision).toHaveBeenCalledTimes(1));
    expect(recordFinalDecision).toHaveBeenCalledWith('en-done', {
      decision: 'hired',
      reason: 'Strong across all three rounds',
      reason_code: 'strong-fit',
    });
  });

  it('only offers reasons that apply to the decision in progress', async () => {
    const user = userEvent.setup();
    renderQueue();
    await screen.findByText('Bhavya Nair');

    const card = cardFor('Bhavya Nair');
    await user.click(within(card).getByText('Hire'));
    const options = within(card)
      .getByLabelText('Reason')
      .querySelectorAll('option');
    const labels = Array.from(options).map((o) => o.textContent);
    // "Not enough experience" applies to rejected only — must not be offered
    // for a hire.
    expect(labels).toContain('Strong fit');
    expect(labels).toContain('Other');
    expect(labels).not.toContain('Not enough experience');
  });

  it('needs at least 10 characters of why when the chosen reason requires an explanation', async () => {
    const user = userEvent.setup();
    renderQueue();
    await screen.findByText('Bhavya Nair');

    const card = cardFor('Bhavya Nair');
    await user.click(within(card).getByText('Hire'));
    await user.selectOptions(within(card).getByLabelText('Reason'), 'other');
    await user.type(within(card).getByLabelText(/Why/), 'short');
    const confirm = within(card).getByText('Confirm').closest('button') as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(within(card).getByText(/Write at least 10 characters above first\./)).toBeTruthy();
  });

  it('releases the hold for the right person', async () => {
    const user = userEvent.setup();
    renderQueue();
    await screen.findByText('Asha Rao');

    await user.click(within(cardFor('Asha Rao')).getByText('Let them continue'));

    await waitFor(() => expect(releaseHold).toHaveBeenCalledTimes(1));
    expect(releaseHold).toHaveBeenCalledWith('en-held', { reason: '' });
  });

  it('opens the evidence for this application, not the person in general', async () => {
    const user = userEvent.setup();
    renderQueue();
    await screen.findByText('Asha Rao');

    await user.click(within(cardFor('Asha Rao')).getByText(/Scores, evidence & history/));
    expect(screen.getByTestId('drawer').textContent).toBe('ap-held:en-held');
  });
});

describe('DecisionQueue — resolved or not', () => {
  it('says a closed opening still has people waiting, and that closing rejected nobody', async () => {
    getRequisition.mockResolvedValue({ id: 'req-1', title: 'Backend Engineer', status: 'closed',
      unresolved: 2 });
    renderQueue();

    const note = await screen.findByTestId('resolution-note');
    expect(note.textContent).toMatch(/closed, and 2 candidates below still need a final decision/);
    expect(note.textContent).toMatch(/did not reject anyone/);
  });

  it('says when an opening is fully resolved', async () => {
    getRequisition.mockResolvedValue({ id: 'req-1', title: 'Backend Engineer', status: 'closed',
      unresolved: 0 });
    getDecisionQueue.mockResolvedValue([]);
    renderQueue();

    expect((await screen.findByTestId('resolution-note')).textContent).toMatch(
      /Resolved — every candidate/,
    );
  });

  it('explains an empty queue while candidates are still in progress', async () => {
    getRequisition.mockResolvedValue({ id: 'req-1', title: 'Backend Engineer', status: 'open',
      unresolved: 4 });
    getDecisionQueue.mockResolvedValue([]);
    renderQueue();

    expect(await screen.findByText('Nobody is waiting')).toBeTruthy();
    expect((await screen.findByTestId('resolution-note')).textContent).toMatch(
      /4 candidates are still in progress/,
    );
  });
});
