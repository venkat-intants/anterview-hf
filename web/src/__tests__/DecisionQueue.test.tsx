// Tests for the decision queue — the human end of the workflow engine (D-05, E2).
//
// The rule the whole engine is built on is that no automation ends a
// candidacy. This page is where that rule is kept. It is a master-detail
// workspace: a compact list of everyone waiting, and one panel for the person
// selected that goes Evidence -> Decision -> Reason & confirm. What matters:
//
//   • held and finished candidates appear in ONE list. Held candidates behind
//     their own tab are held candidates nobody opens;
//   • the panel belongs to the selected person, and what is offered depends on
//     THEIR state: a hold to release only for the held, a review verdict only
//     for those waiting on a review;
//   • no outcome is pre-selected, recommended, or styled as the primary;
//   • a hire or a reject goes to the enrolment it was chosen for, never on the
//     first click, and never without a reason code and a why;
//   • after a recorded action the panel moves on and says so;
//   • there is no Undo, because the API cannot revert a decision;
//   • the evidence is one click away, for THIS application;
//   • a closed opening with people still waiting says so.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
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
  waiting_days: 3,
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
  code_evidence: {
    signal_count: 2,
    unreviewed_signal_count: 2,
    finding_count: 1,
    no_concern_count: 0,
    follow_up_count: 1,
    confirmed_count: 0,
  },
};

const REVIEW: DecisionQueueRow = {
  ...FINISHED,
  enrolment_id: 'en-review',
  applicant_id: 'ap-review',
  full_name: 'Chetan Iyer',
  email: 'chetan@example.com',
  scorecards: null,
  code_evidence: null,
  awaiting_review: true,
  review_round_title: 'Hiring manager review',
  review_criteria: [
    { competency_id: 'c1', name: 'Communication', weight: 0.5 },
    { competency_id: 'c2', name: 'Ownership', weight: 0.5 },
  ],
};

const REASONS = [
  { code: 'strong-fit', label: 'Strong fit', applies_to: 'hired' as const, requires_explanation: false },
  { code: 'not-enough-exp', label: 'Not enough experience', applies_to: 'rejected' as const, requires_explanation: false },
  { code: 'other', label: 'Other', applies_to: 'both' as const, requires_explanation: true },
];

const getDecisionQueue = vi.fn();
const releaseHold = vi.fn();
const recordFinalDecision = vi.fn();
const recordRoundReview = vi.fn();
vi.mock('../api/workflows', () => ({
  getDecisionQueue: (...a: unknown[]) => getDecisionQueue(...a) as unknown,
  releaseHold: (...a: unknown[]) => releaseHold(...a) as unknown,
  recordFinalDecision: (...a: unknown[]) => recordFinalDecision(...a) as unknown,
  recordRoundReview: (...a: unknown[]) => recordRoundReview(...a) as unknown,
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

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ user: { user_id: 'u-1', full_name: 'Priya Raghavan' } }),
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
const toastWarning = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
    info: vi.fn(),
    warning: (...a: unknown[]) => toastWarning(...a) as unknown,
  },
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useParams: () => ({ requisitionId: 'req-1' }) };
});

import DecisionQueue from '../pages/hr/DecisionQueue';

function renderQueue() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DecisionQueue />
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}

/** The name is on the list row and on the panel, so wait for either. */
const ready = (name: string) => screen.findAllByText(name);

/** The one panel on screen — it always belongs to the selected person. */
const panel = () => screen.getByTestId('decision-card');
const row = (name: string) =>
  screen.getAllByTestId('queue-row').find((r) => r.textContent?.includes(name)) as HTMLElement;

async function select(name: string) {
  await userEvent.click(row(name));
}
async function toDecision() {
  await userEvent.click(await screen.findByTestId('to-decision'));
}
async function choose(label: string | RegExp) {
  await userEvent.click(within(panel()).getByRole('button', { name: label }));
}

beforeEach(() => {
  vi.clearAllMocks();
  getRequisition.mockResolvedValue({ id: 'req-1', title: 'Backend Engineer', status: 'open',
    unresolved: 2 });
  getDecisionQueue.mockResolvedValue([HELD, FINISHED]);
  recordFinalDecision.mockResolvedValue({ enrolment_id: 'en-done', status: 'hired' });
  releaseHold.mockResolvedValue({ action: 'released', enrolment_id: 'en-held' });
  recordRoundReview.mockResolvedValue({ action: 'advanced', enrolment_id: 'en-review' });
  listDecisionReasons.mockResolvedValue(REASONS);
});

afterEach(() => {
  // @ts-expect-error — back to jsdom's "no matchMedia" (the desktop layout)
  delete window.matchMedia;
});

describe('DecisionQueue - the list', () => {
  it('shows held and finished candidates together, with the first selected', async () => {
    renderQueue();
    await ready('Asha Rao');

    expect(screen.getAllByTestId('queue-row')).toHaveLength(2);
    expect(screen.getByText('1 held below a round threshold')).toBeTruthy();
    expect(screen.getByTestId('queue-position').textContent).toBe('1 of 2');
    expect(row('Asha Rao').getAttribute('aria-current')).toBe('true');
    expect(row('Bhavya Nair').getAttribute('aria-current')).toBeNull();
  });

  it('shows each person\'s key scores as small numerals, and says scores never decide', async () => {
    renderQueue();
    await ready('Asha Rao');
    expect(within(row('Asha Rao')).getByText('Resume 71')).toBeTruthy();
    expect(within(row('Asha Rao')).getByText('Avg 54%')).toBeTruthy();
    expect(within(row('Bhavya Nair')).getByText('Avg 79%')).toBeTruthy();
    expect(within(row('Asha Rao')).getByText(/waiting 3 days/)).toBeTruthy();
    expect(screen.getByText('Scores rank and explain. They never decide.')).toBeTruthy();
  });

  it('labels the held pill in words, not the raw status value (audit HR-L5)', async () => {
    renderQueue();
    await ready('Asha Rao');
    expect(within(panel()).getByText('Held')).toBeTruthy();
    expect(within(panel()).queryByText('held')).toBeNull();
  });

  it('selecting another person swaps the panel, marks them current and updates the position', async () => {
    renderQueue();
    await ready('Asha Rao');
    expect(panel().getAttribute('aria-label')).toBe('Decision for Asha Rao');

    await select('Bhavya Nair');

    expect(panel().getAttribute('aria-label')).toBe('Decision for Bhavya Nair');
    expect(row('Bhavya Nair').getAttribute('aria-current')).toBe('true');
    expect(row('Asha Rao').getAttribute('aria-current')).toBeNull();
    expect(screen.getByTestId('queue-position').textContent).toBe('2 of 2');
  });

  it('moves with the arrow keys, Home and End, taking focus with it', async () => {
    getDecisionQueue.mockResolvedValue([HELD, FINISHED, REVIEW]);
    const user = userEvent.setup();
    renderQueue();
    await ready('Asha Rao');

    row('Asha Rao').focus();
    await user.keyboard('{ArrowDown}');
    expect(row('Bhavya Nair').getAttribute('aria-current')).toBe('true');
    expect(document.activeElement).toBe(row('Bhavya Nair'));
    expect(panel().getAttribute('aria-label')).toBe('Decision for Bhavya Nair');

    await user.keyboard('{End}');
    expect(row('Chetan Iyer').getAttribute('aria-current')).toBe('true');
    await user.keyboard('{ArrowDown}');
    expect(row('Chetan Iyer').getAttribute('aria-current')).toBe('true');
    await user.keyboard('{ArrowUp}');
    expect(row('Bhavya Nair').getAttribute('aria-current')).toBe('true');
    await user.keyboard('{Home}');
    expect(row('Asha Rao').getAttribute('aria-current')).toBe('true');
  });

  it('is one tab stop: only the current row is in the tab order', async () => {
    renderQueue();
    await ready('Asha Rao');
    expect(row('Asha Rao').tabIndex).toBe(0);
    expect(row('Bhavya Nair').tabIndex).toBe(-1);
  });
});

describe('DecisionQueue - step 1, evidence', () => {
  it('says the hold was not a rejection, because the badge reads like one', async () => {
    renderQueue();
    await ready('Asha Rao');
    expect(within(panel()).getByText(/Nothing was decided automatically/)).toBeTruthy();
  });

  it('shows where they are and their scores as a summary', async () => {
    renderQueue();
    await ready('Asha Rao');

    expect(within(panel()).getByText(/Held on Fundamentals · workflow v2/)).toBeTruthy();
    expect(within(panel()).getByText(/average across rounds 54%/)).toBeTruthy();
    expect(within(panel()).getByLabelText(/Completed rounds for Asha Rao/).textContent).toMatch(
      /Fundamentals\s*54%\s*· held/,
    );
    await select('Bhavya Nair');
    expect(within(panel()).getByText(/Finished every round/)).toBeTruthy();
  });

  it('shows human-interview scorecard progress when the candidate has any', async () => {
    renderQueue();
    await ready('Bhavya Nair');
    await select('Bhavya Nair');
    expect(within(panel()).getByText(/2\/3 scorecards in/)).toBeTruthy();
    expect(within(panel()).getByText(/1 late/)).toBeTruthy();
    await select('Asha Rao');
    expect(within(panel()).queryByText(/scorecards in/)).toBeNull();
  });

  it('shows code-evidence counts, keeping an unreviewed signal apart from a finding', async () => {
    renderQueue();
    await ready('Bhavya Nair');
    await select('Bhavya Nair');
    expect(
      within(panel()).getByText(
        '2 similarity signals awaiting review · 1 finding flagged for follow-up',
      ),
    ).toBeTruthy();
    await select('Asha Rao');
    expect(within(panel()).queryByText(/similarity signal/)).toBeNull();
  });

  it('lists what a review round assesses, for the person waiting on that review', async () => {
    getDecisionQueue.mockResolvedValue([REVIEW]);
    renderQueue();
    await ready('Chetan Iyer');
    expect(within(panel()).getByText('What Hiring manager review assesses')).toBeTruthy();
    expect(within(panel()).getByText(/Communication/)).toBeTruthy();
  });

  it('opens the full evidence for this application, not the person in general', async () => {
    const user = userEvent.setup();
    renderQueue();
    await ready('Asha Rao');
    await user.click(within(panel()).getByText(/Scores, evidence & history/));
    expect(screen.getByTestId('drawer').textContent).toBe('ap-held:en-held');
  });

  it('carries no outcome control on this step', async () => {
    renderQueue();
    await ready('Asha Rao');
    for (const name of ['Hire', 'Reject', 'Let them continue']) {
      expect(within(panel()).queryByRole('button', { name: new RegExp(name) })).toBeNull();
    }
  });
});

describe('DecisionQueue - SLA (PH4-O1, informational only)', () => {
  const OVERDUE: DecisionQueueRow = {
    ...HELD,
    enrolment_id: 'en-overdue',
    applicant_id: 'ap-overdue',
    full_name: 'Dev Overdue',
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
    await ready('Dev Overdue');

    expect(within(panel()).getByText(/Overdue since/)).toBeTruthy();
    expect(within(panel()).getByText(/Priya HR/)).toBeTruthy();
    expect(within(panel()).getByText('1 open exception')).toBeTruthy();
    expect(within(row('Dev Overdue')).getByText('Overdue')).toBeTruthy();
    // Nothing SLA-related on a row with no SLA.
    expect(within(row('Bhavya Nair')).queryByText(/Overdue|Due soon/)).toBeNull();
  });

  it('filters to overdue-or-due-soon without hiding anyone from the underlying queue', async () => {
    const user = userEvent.setup();
    getDecisionQueue.mockResolvedValue([OVERDUE, FINISHED]);
    renderQueue();
    await ready('Dev Overdue');
    expect(row('Bhavya Nair')).toBeTruthy();

    await user.click(screen.getByLabelText(/Overdue or due soon only/));
    expect(row('Dev Overdue')).toBeTruthy();
    expect(screen.getAllByTestId('queue-row')).toHaveLength(1);

    // Toggling back restores the full queue — the filter never re-fetches or
    // drops data, it only narrows what is rendered.
    await user.click(screen.getByLabelText(/Overdue or due soon only/));
    expect(screen.getAllByTestId('queue-row')).toHaveLength(2);
    expect(getDecisionQueue).toHaveBeenCalledTimes(1);
  });
});

describe('DecisionQueue - step 2, the legal actions for this state', () => {
  const names = () =>
    within(panel())
      .getAllByRole('button')
      .map((b) => b.textContent ?? '');

  it('a held candidate: let them continue, hire, reject - and no review verdict', async () => {
    renderQueue();
    await ready('Asha Rao');
    await toDecision();

    const text = names().join('|');
    expect(text).toMatch(/Let them continue/);
    expect(text).toMatch(/Hire/);
    expect(text).toMatch(/Reject/);
    expect(text).not.toMatch(/Passes this round|Hold for a decision/);
  });

  it('a candidate waiting on a review: pass or hold, plus hire and reject - and no release', async () => {
    getDecisionQueue.mockResolvedValue([REVIEW]);
    renderQueue();
    await ready('Chetan Iyer');
    await toDecision();

    const text = names().join('|');
    expect(text).toMatch(/Passes this round/);
    expect(text).toMatch(/Hold for a decision/);
    expect(text).toMatch(/Hire/);
    expect(text).toMatch(/Reject/);
    expect(text).not.toMatch(/Let them continue/);
  });

  it('a candidate who finished every round: only hire and reject', async () => {
    renderQueue();
    await ready('Bhavya Nair');
    await select('Bhavya Nair');
    await toDecision();

    const text = names().join('|');
    expect(text).toMatch(/Hire/);
    expect(text).toMatch(/Reject/);
    expect(text).not.toMatch(/Let them continue|Passes this round|Hold for a decision/);
  });

  it('pre-selects nothing, recommends nothing, and styles no outcome as the primary', async () => {
    renderQueue();
    await ready('Asha Rao');
    await toDecision();

    const options = within(panel())
      .getAllByRole('button')
      .filter((b) => /Let them continue|Hire|Reject/.test(b.textContent ?? ''));
    expect(options.length).toBe(3);
    for (const o of options) {
      expect(o.getAttribute('aria-pressed')).toBeNull();
      expect(o.getAttribute('aria-checked')).toBeNull();
      expect(o.getAttribute('aria-current')).toBeNull();
      // the primary fill: none of them has it, and they are all the same
      expect(o.className).not.toMatch(/bg-primary|bg-destructive/);
      expect(o.className).toBe(options[0].className);
      // and focus did not land on an outcome
      expect(document.activeElement).not.toBe(o);
    }
    expect(within(panel()).queryByText(/recommended|suggested|best option/i)).toBeNull();
    expect(within(panel()).getByText(/Nothing is selected for you/)).toBeTruthy();
  });

  it('puts focus on the step, not on an outcome', async () => {
    renderQueue();
    await ready('Asha Rao');
    await toDecision();
    const focused = document.activeElement as HTMLElement;
    expect(focused.tagName).not.toBe('BUTTON');
  });

  it('Back returns to the evidence', async () => {
    renderQueue();
    await ready('Asha Rao');
    await toDecision();
    await userEvent.click(within(panel()).getByRole('button', { name: 'Back to evidence' }));
    expect(screen.getByTestId('step-evidence')).toBeTruthy();
  });
});

describe('DecisionQueue - step 3, reason and confirm', () => {
  async function reachConfirm(name: string, action: string | RegExp) {
    renderQueue();
    await ready(name);
    if (name !== 'Asha Rao') await select(name);
    await toDecision();
    await choose(action);
    return within(panel());
  }

  it('does not hire on the first click: choosing only moves to the confirm step', async () => {
    const p = await reachConfirm('Bhavya Nair', /^Hire/);
    expect(recordFinalDecision).not.toHaveBeenCalled();
    expect(p.getByTestId('confirm-summary').textContent).toMatch(/ends their candidacy/);
  });

  it('will not record a hire or reject without a reason code chosen', async () => {
    const p = await reachConfirm('Bhavya Nair', /^Reject/);
    const confirm = p.getByText('Confirm').closest('button') as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(p.getByText('Choose a reason above first.')).toBeTruthy();
    await userEvent.click(confirm);
    expect(recordFinalDecision).not.toHaveBeenCalled();
  });

  it('will not record a hire or reject without free-text why, even with a reason chosen', async () => {
    const p = await reachConfirm('Bhavya Nair', /^Hire/);
    await userEvent.selectOptions(p.getByLabelText('Reason'), 'strong-fit');
    expect((p.getByText('Confirm').closest('button') as HTMLButtonElement).disabled).toBe(true);
    expect(p.getByText(/Write at least 3 characters above first\./)).toBeTruthy();
  });

  it('sends the decision, its reason code and its free-text reason to the enrolment chosen', async () => {
    const p = await reachConfirm('Bhavya Nair', /^Hire/);
    await userEvent.selectOptions(p.getByLabelText('Reason'), 'strong-fit');
    await userEvent.type(p.getByLabelText(/Why/), 'Strong across all three rounds');
    await userEvent.click(p.getByText('Confirm'));

    await waitFor(() => expect(recordFinalDecision).toHaveBeenCalledTimes(1));
    expect(recordFinalDecision).toHaveBeenCalledWith('en-done', {
      decision: 'hired',
      reason: 'Strong across all three rounds',
      reason_code: 'strong-fit',
    });
  });

  it('refreshes the attention panel and the rest of the console after a decision (HR-L12)', async () => {
    const spy = vi.spyOn(QueryClient.prototype, 'invalidateQueries');
    try {
      const p = await reachConfirm('Bhavya Nair', /^Hire/);
      await userEvent.selectOptions(p.getByLabelText('Reason'), 'strong-fit');
      await userEvent.type(p.getByLabelText(/Why/), 'Strong across all three rounds');
      await userEvent.click(p.getByText('Confirm'));

      await waitFor(() => expect(recordFinalDecision).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(spy).toHaveBeenCalledWith({ queryKey: ['hr-attention'] }));
      expect(spy).toHaveBeenCalledWith({ queryKey: ['hr'] });
    } finally {
      spy.mockRestore();
    }
  });

  it('only offers reasons that apply to the decision in progress', async () => {
    const p = await reachConfirm('Bhavya Nair', /^Hire/);
    const labels = Array.from(p.getByLabelText('Reason').querySelectorAll('option')).map(
      (o) => o.textContent,
    );
    expect(labels).toContain('Strong fit');
    expect(labels).toContain('Other');
    expect(labels).not.toContain('Not enough experience');
  });

  it('needs at least 10 characters of why when the chosen reason requires an explanation', async () => {
    const p = await reachConfirm('Bhavya Nair', /^Hire/);
    await userEvent.selectOptions(p.getByLabelText('Reason'), 'other');
    await userEvent.type(p.getByLabelText(/Why/), 'short');
    expect((p.getByText('Confirm').closest('button') as HTMLButtonElement).disabled).toBe(true);
    expect(p.getByText(/Write at least 10 characters above first\./)).toBeTruthy();
  });

  it('says what will happen and who is recorded as deciding', async () => {
    const p = await reachConfirm('Bhavya Nair', /^Reject/);
    const summary = p.getByTestId('confirm-summary').textContent ?? '';
    expect(summary).toMatch(/Bhavya Nair is rejected\. This ends their candidacy/);
    expect(summary).toMatch(/Recorded as decided by you \(Priya Raghavan\), not by the system/);
  });

  it('carries no Undo, because the API cannot revert a decision', async () => {
    const p = await reachConfirm('Bhavya Nair', /^Hire/);
    expect(p.queryByRole('button', { name: /undo/i })).toBeNull();
    expect(p.getByTestId('confirm-summary').textContent).toMatch(/no undo/i);
  });

  it('rejects with the destructive style and hires with the default; Back goes to the choice', async () => {
    const p = await reachConfirm('Bhavya Nair', /^Reject/);
    expect((p.getByTestId('confirm-decision')).className).toMatch(/destructive/);
    await userEvent.click(p.getByRole('button', { name: 'Back' }));
    expect(screen.getByTestId('step-decision')).toBeTruthy();
  });

  it('releases the hold for the right person, with no reason required', async () => {
    const p = await reachConfirm('Asha Rao', /Let them continue/);
    expect(p.queryByLabelText('Reason')).toBeNull();
    expect((p.getByText('Confirm').closest('button') as HTMLButtonElement).disabled).toBe(false);
    await userEvent.click(p.getByText('Confirm'));

    await waitFor(() => expect(releaseHold).toHaveBeenCalledTimes(1));
    expect(releaseHold).toHaveBeenCalledWith('en-held', { reason: '' });
  });

  it('records a pass and a hold on a review round, each as a verdict with the note', async () => {
    getDecisionQueue.mockResolvedValue([REVIEW]);
    renderQueue();
    await ready('Chetan Iyer');
    await toDecision();
    await choose(/Passes this round/);
    await userEvent.type(screen.getByLabelText(/Why/), 'Clear answers');
    await userEvent.click(screen.getByText('Confirm'));
    await waitFor(() => expect(recordRoundReview).toHaveBeenCalledWith('en-review', { passed: true, note: 'Clear answers' }));
    expect(String(toastSuccess.mock.calls[0][0])).toMatch(/advances/);
  });

  it('a hold verdict says it is held for you, not rejected', async () => {
    getDecisionQueue.mockResolvedValue([REVIEW]);
    renderQueue();
    await ready('Chetan Iyer');
    await toDecision();
    await choose(/Hold for a decision/);
    expect(screen.getByTestId('confirm-summary').textContent).toMatch(/does not reject/);
    await userEvent.click(screen.getByText('Confirm'));
    await waitFor(() => expect(recordRoundReview).toHaveBeenCalledWith('en-review', { passed: false, note: '' }));
    expect(String(toastSuccess.mock.calls[0][0])).toMatch(/held for you/);
  });
});

describe('DecisionQueue - after a decision', () => {
  const THREE = [HELD, FINISHED, REVIEW];

  async function hireBhavya() {
    getDecisionQueue.mockResolvedValue(THREE);
    renderQueue();
    await ready('Asha Rao');
    await select('Bhavya Nair');
    await toDecision();
    await choose(/^Hire/);
    const p = within(panel());
    await userEvent.selectOptions(p.getByLabelText('Reason'), 'strong-fit');
    await userEvent.type(p.getByLabelText(/Why/), 'Strong across all three rounds');
    await userEvent.click(p.getByText('Confirm'));
  }

  it('moves on to the next person, at step 1, and says so in a polite live region', async () => {
    await hireBhavya();

    await waitFor(() =>
      expect(panel().getAttribute('aria-label')).toBe('Decision for Chetan Iyer'),
    );
    expect(screen.getByTestId('step-evidence')).toBeTruthy();
    expect(row('Chetan Iyer').getAttribute('aria-current')).toBe('true');

    const live = screen.getByTestId('decision-announcer');
    expect(live.getAttribute('aria-live')).toBe('polite');
    expect(live.getAttribute('role')).toBe('status');
    expect(live.textContent).toBe('Bhavya Nair hired. Now showing Chetan Iyer.');
    expect(toastSuccess).toHaveBeenCalledWith('Bhavya Nair hired');
  });

  it('puts focus on the new person\'s panel, not on an outcome', async () => {
    await hireBhavya();
    await waitFor(() =>
      expect(panel().getAttribute('aria-label')).toBe('Decision for Chetan Iyer'),
    );
    await waitFor(() => expect(panel().contains(document.activeElement)).toBe(true));
    expect((document.activeElement as HTMLElement).tagName).not.toBe('BUTTON');
  });

  it('falls back to the previous person when the last one is decided', async () => {
    getDecisionQueue.mockResolvedValue([HELD, FINISHED]);
    renderQueue();
    await ready('Asha Rao');
    await select('Bhavya Nair');
    await toDecision();
    await choose(/^Reject/);
    const p = within(panel());
    await userEvent.selectOptions(p.getByLabelText('Reason'), 'not-enough-exp');
    await userEvent.type(p.getByLabelText(/Why/), 'Not enough depth');
    await userEvent.click(p.getByText('Confirm'));
    await waitFor(() => expect(panel().getAttribute('aria-label')).toBe('Decision for Asha Rao'));
    expect(screen.getByTestId('decision-announcer').textContent).toBe(
      'Bhavya Nair rejected. Now showing Asha Rao.',
    );
  });

  it('says so when nobody is left, and the queue refetches', async () => {
    getDecisionQueue.mockResolvedValue([FINISHED]);
    renderQueue();
    await ready('Bhavya Nair');
    await toDecision();
    await choose(/^Hire/);
    const p = within(panel());
    await userEvent.selectOptions(p.getByLabelText('Reason'), 'strong-fit');
    await userEvent.type(p.getByLabelText(/Why/), 'Strong across all three rounds');
    getDecisionQueue.mockResolvedValue([]);
    await userEvent.click(p.getByText('Confirm'));

    await waitFor(() =>
      expect(screen.getByTestId('decision-announcer').textContent).toBe(
        'Bhavya Nair hired. Nobody else is waiting.',
      ),
    );
    expect(await screen.findByText('Nobody is waiting')).toBeTruthy();
    expect(getDecisionQueue.mock.calls.length).toBeGreaterThan(1);
  });

  it('does not advance when the save fails, and keeps what was typed', async () => {
    recordFinalDecision.mockRejectedValue(new Error('server said no'));
    getDecisionQueue.mockResolvedValue(THREE);
    renderQueue();
    await ready('Asha Rao');
    await select('Bhavya Nair');
    await toDecision();
    await choose(/^Hire/);
    const p = within(panel());
    await userEvent.selectOptions(p.getByLabelText('Reason'), 'strong-fit');
    await userEvent.type(p.getByLabelText(/Why/), 'Strong across all three rounds');
    await userEvent.click(p.getByText('Confirm'));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('server said no'));
    expect(panel().getAttribute('aria-label')).toBe('Decision for Bhavya Nair');
    expect(within(panel()).getByLabelText<HTMLInputElement>(/Why/).value).toBe(
      'Strong across all three rounds',
    );
    expect(screen.getByTestId('decision-announcer').textContent).toBe('');
  });
});

describe('DecisionQueue - typed input is never carried to, or lost for, the wrong outcome', () => {
  it('clears a typed why when the chosen action changes (hire -> back -> reject)', async () => {
    renderQueue();
    await ready('Bhavya Nair');
    await select('Bhavya Nair');
    await toDecision();
    await choose(/^Hire/);
    await userEvent.type(within(panel()).getByLabelText(/Why/), 'Great culture fit');
    await userEvent.click(within(panel()).getByRole('button', { name: 'Back' }));
    await choose(/^Reject/);
    expect(within(panel()).getByLabelText<HTMLInputElement>(/Why/).value).toBe('');
  });

  it('keeps the typed why when the same action is chosen again', async () => {
    renderQueue();
    await ready('Bhavya Nair');
    await select('Bhavya Nair');
    await toDecision();
    await choose(/^Hire/);
    await userEvent.type(within(panel()).getByLabelText(/Why/), 'Great culture fit');
    await userEvent.click(within(panel()).getByRole('button', { name: 'Back' }));
    await choose(/^Hire/);
    expect(within(panel()).getByLabelText<HTMLInputElement>(/Why/).value).toBe('Great culture fit');
  });

  async function typeWhyOnAsha() {
    await ready('Asha Rao');
    await toDecision();
    await choose(/Let them continue/);
    await userEvent.type(within(panel()).getByLabelText(/Why/), 'Talked it over');
  }

  it('asks before leaving a candidate whose step 3 has typed text, and can keep editing', async () => {
    renderQueue();
    await typeWhyOnAsha();

    await select('Bhavya Nair');
    const ask = screen.getByTestId('switch-confirm');
    expect(ask.getAttribute('role')).toBe('alert');
    expect(ask.textContent).toMatch(/Bhavya Nair/);
    expect(panel().getAttribute('aria-label')).toBe('Decision for Asha Rao');

    await userEvent.click(within(ask).getByRole('button', { name: 'Keep editing' }));
    expect(screen.queryByTestId('switch-confirm')).toBeNull();
    expect(within(panel()).getByLabelText<HTMLInputElement>(/Why/).value).toBe('Talked it over');
  });

  it('discards and switches when the reviewer agrees', async () => {
    renderQueue();
    await typeWhyOnAsha();
    await select('Bhavya Nair');
    await userEvent.click(screen.getByRole('button', { name: 'Discard and switch' }));
    expect(panel().getAttribute('aria-label')).toBe('Decision for Bhavya Nair');
    expect(screen.queryByTestId('switch-confirm')).toBeNull();
  });

  it('asks for arrow-key moves too, and leaves focus where it was', async () => {
    const user = userEvent.setup();
    renderQueue();
    await typeWhyOnAsha();
    row('Asha Rao').focus();
    await user.keyboard('{ArrowDown}');
    expect(screen.getByTestId('switch-confirm')).toBeTruthy();
    expect(row('Asha Rao').getAttribute('aria-current')).toBe('true');
  });

  it('does not ask when nothing was typed', async () => {
    renderQueue();
    await ready('Asha Rao');
    await toDecision();
    await choose(/Let them continue/);
    await select('Bhavya Nair');
    expect(screen.queryByTestId('switch-confirm')).toBeNull();
    expect(panel().getAttribute('aria-label')).toBe('Decision for Bhavya Nair');
  });

  it('returns to step 2 with a notice when a refetch makes the chosen action illegal', async () => {
    const { client } = renderQueue();
    await ready('Asha Rao');
    await toDecision();
    await choose(/Let them continue/);
    expect(screen.getByTestId('step-confirm')).toBeTruthy();

    // Someone else released the hold: they are no longer held.
    getDecisionQueue.mockResolvedValue([{ ...HELD, held: false, held_reason: null }, FINISHED]);
    await client.invalidateQueries({ queryKey: ['hr', 'decision-queue', 'req-1'] });

    expect(await screen.findByTestId('step-decision')).toBeTruthy();
    expect(screen.getByTestId('action-changed-notice').textContent).toMatch(/no longer available/);
    expect(screen.queryByRole('button', { name: /Let them continue/ })).toBeNull();
    expect(releaseHold).not.toHaveBeenCalled();
  });
});

// PH4-D4 — a job_simulation/portfolio round's own submission state, so "not
// started" reads apart from "submitted, awaiting review".
describe('DecisionQueue - job-simulation/portfolio submission state (PH4-D4)', () => {
  const AWAITING_SUBMITTED: DecisionQueueRow = {
    ...FINISHED,
    enrolment_id: 'en-task-1',
    applicant_id: 'ap-task-1',
    full_name: 'Divya Task',
    held: false,
    awaiting_review: true,
    review_round_title: 'Take-home simulation',
    task_submission: {
      status: 'submitted',
      due_at: '2026-09-10T00:00:00.000Z',
      submitted_at: '2026-09-09T00:00:00.000Z',
    },
  };

  const AWAITING_IN_PROGRESS: DecisionQueueRow = {
    ...AWAITING_SUBMITTED,
    enrolment_id: 'en-task-2',
    applicant_id: 'ap-task-2',
    full_name: 'Farhan Task',
    task_submission: {
      status: 'in_progress',
      due_at: '2026-09-10T00:00:00.000Z',
      submitted_at: null,
    },
  };

  it('shows "Submission received" for a submitted job-simulation/portfolio round', async () => {
    getDecisionQueue.mockResolvedValue([AWAITING_SUBMITTED]);
    renderQueue();
    await ready('Divya Task');
    expect(within(panel()).getByText('Submission received')).toBeTruthy();
  });

  it('shows the submission\'s own lifecycle when it has not been submitted yet', async () => {
    getDecisionQueue.mockResolvedValue([AWAITING_IN_PROGRESS]);
    renderQueue();
    await ready('Farhan Task');
    expect(within(panel()).getByText('Submission in progress')).toBeTruthy();
    expect(within(panel()).queryByText('Submission received')).toBeNull();
  });

  it('shows no submission tag for a round with no task_submission', async () => {
    getDecisionQueue.mockResolvedValue([FINISHED]);
    renderQueue();
    await ready('Bhavya Nair');
    expect(within(panel()).queryByText(/^Submission/)).toBeNull();
  });
});

describe('DecisionQueue - resolved or not', () => {
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
      /Resolved - every candidate/,
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

  it('keeps the page\'s explanation that nothing is decided automatically', async () => {
    renderQueue();
    expect(
      await screen.findByText(/nothing here was decided automatically, and nothing here moves until you move it/),
    ).toBeTruthy();
  });
});

describe('DecisionQueue - loading and failing, quietly', () => {
  it('shows a status while loading, not a wall of skeletons', async () => {
    getDecisionQueue.mockReturnValue(new Promise(() => undefined));
    renderQueue();
    expect(await screen.findByText('Loading queue…')).toBeTruthy();
    expect(screen.queryAllByTestId('queue-row')).toHaveLength(0);
  });

  it('reports a failure as an alert, not as an empty queue', async () => {
    getDecisionQueue.mockRejectedValue(new Error('queue is down'));
    renderQueue();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not load the decision queue.');
    expect(alert).toHaveTextContent('queue is down');
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
    expect(screen.queryByText('Nobody is waiting')).toBeNull();
  });
});

describe('DecisionQueue - releasing a hold reports where they went', () => {
  // Releasing a hold used to clear a flag. It now takes the round's pass branch
  // through the runner, which mints the next round's exam link or interview
  // invite and emails the candidate — so "continues" on its own no longer tells
  // HR what their click did, and in one case actively misleads them.
  async function release() {
    renderQueue();
    await ready('Asha Rao');
    await toDecision();
    await choose(/Let them continue/);
    await userEvent.click(screen.getByText('Confirm'));
  }

  it('names the round the candidate moved to', async () => {
    releaseHold.mockResolvedValue({
      action: 'advanced', enrolment_id: 'en-held', from_round: 'Trade test',
      to_round: 'Panel interview',
    });
    await release();
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    expect(String(toastSuccess.mock.calls[0][0])).toMatch(/moved to Panel interview/);
  });

  it('says they are awaiting the final decision when that was the last round', async () => {
    releaseHold.mockResolvedValue({
      action: 'completed', enrolment_id: 'en-held', from_round: 'Trade test', to_round: null,
    });
    await release();
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    expect(String(toastSuccess.mock.calls[0][0])).toMatch(/final decision/i);
  });

  it('warns rather than congratulating when nobody actually moved', async () => {
    // The round was deleted under the candidate. The hold is lifted either way —
    // stranding them in held would be worse — but reporting this as a successful
    // advance would stop HR looking for someone who still needs moving.
    releaseHold.mockResolvedValue({
      action: 'advanced', enrolment_id: 'en-held', to_round: null,
      reason: 'hold released - the round no longer exists, so the candidate did not move',
    });
    await release();
    await waitFor(() => expect(toastWarning).toHaveBeenCalled());
    expect(String(toastWarning.mock.calls[0][0])).toMatch(/did not move/);
    expect(toastSuccess).not.toHaveBeenCalled();
  });
});

describe('DecisionQueue - below lg the panel is a sheet', () => {
  function narrow() {
    window.matchMedia = vi.fn().mockImplementation((q: string) => ({
      matches: false,
      media: q,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })) as unknown as typeof window.matchMedia;
  }

  it('shows only the list until someone is chosen, then a full-height dialog', async () => {
    narrow();
    renderQueue();
    await ready('Asha Rao');
    expect(screen.queryByTestId('decision-card')).toBeNull();
    expect(screen.queryByRole('dialog')).toBeNull();

    await select('Bhavya Nair');
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByTestId('decision-card').getAttribute('aria-label')).toBe(
      'Decision for Bhavya Nair',
    );
    expect(dialog.className).toMatch(/h-\[100dvh\]/);
  });

  it('closes the sheet and moves on once an action is recorded', async () => {
    narrow();
    getDecisionQueue.mockResolvedValue([HELD, FINISHED]);
    renderQueue();
    await ready('Asha Rao');
    await select('Asha Rao');
    await toDecision();
    await choose(/Let them continue/);
    await userEvent.click(screen.getByText('Confirm'));

    await waitFor(() => expect(releaseHold).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.getByTestId('decision-announcer').textContent).toBe(
      'Asha Rao continues. Now showing Bhavya Nair.',
    );
  });
});
