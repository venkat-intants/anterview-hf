// Tests for the decision queue — the human end of the workflow engine (D-05).
//
// The rule the whole engine is built on is that no automation ends a
// candidacy. This page is where that rule is kept, so what matters is:
//
//   • held and finished candidates appear in ONE list. Held candidates behind
//     their own tab are held candidates nobody opens, which would make the rule
//     true on paper and false in practice;
//   • a hire or a reject goes to the enrolment whose card it was pressed on,
//     and never on the first click — it ends someone's candidacy and is
//     audit-logged against the person who did it;
//   • releasing a hold is offered only where there is a hold to release.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { DecisionQueueRow } from '../api/workflows';

const HELD: DecisionQueueRow = {
  enrolment_id: 'en-held',
  full_name: 'Asha Rao',
  email: 'asha@example.com',
  status: 'held',
  held: true,
  held_reason: 'Scored 54% against a 60% threshold on Fundamentals',
  ats_overall: 71,
  rounds_taken: 1,
  best_percent: 54,
};

const FINISHED: DecisionQueueRow = {
  enrolment_id: 'en-done',
  full_name: 'Bhavya Nair',
  email: 'bhavya@example.com',
  status: 'interviewed',
  held: false,
  held_reason: null,
  ats_overall: 88,
  rounds_taken: 3,
  best_percent: 82,
};

const getDecisionQueue = vi.fn();
const releaseHold = vi.fn();
vi.mock('../api/workflows', () => ({
  getDecisionQueue: (...a: unknown[]) => getDecisionQueue(...a) as unknown,
  releaseHold: (...a: unknown[]) => releaseHold(...a) as unknown,
}));

const getRequisition = vi.fn();
const setEnrolmentStatus = vi.fn();
vi.mock('../api/requisitions', () => ({
  getRequisition: (...a: unknown[]) => getRequisition(...a) as unknown,
  setEnrolmentStatus: (...a: unknown[]) => setEnrolmentStatus(...a) as unknown,
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
  getRequisition.mockResolvedValue({ id: 'req-1', title: 'Backend Engineer' });
  getDecisionQueue.mockResolvedValue([HELD, FINISHED]);
  setEnrolmentStatus.mockResolvedValue({ id: 'en-done', status: 'hired' });
  releaseHold.mockResolvedValue({ action: 'released', enrolment_id: 'en-held' });
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
});

describe('DecisionQueue — the decision itself', () => {
  it('does not hire on the first click', async () => {
    const user = userEvent.setup();
    renderQueue();
    await screen.findByText('Bhavya Nair');

    await user.click(within(cardFor('Bhavya Nair')).getByText('Hire'));

    expect(setEnrolmentStatus).not.toHaveBeenCalled();
    expect(within(cardFor('Bhavya Nair')).getByText(/ends their candidacy/)).toBeTruthy();
  });

  it('sends the decision to the enrolment whose card it was pressed on', async () => {
    const user = userEvent.setup();
    renderQueue();
    await screen.findByText('Bhavya Nair');

    const card = cardFor('Bhavya Nair');
    await user.type(within(card).getByLabelText(/Why/), 'Strong across all three rounds');
    await user.click(within(card).getByText('Hire'));
    await user.click(within(card).getByText('Confirm'));

    await waitFor(() => expect(setEnrolmentStatus).toHaveBeenCalledTimes(1));
    expect(setEnrolmentStatus).toHaveBeenCalledWith(
      'en-done',
      'hired',
      'Strong across all three rounds',
    );
  });

  it('releases the hold for the right person', async () => {
    const user = userEvent.setup();
    renderQueue();
    await screen.findByText('Asha Rao');

    await user.click(within(cardFor('Asha Rao')).getByText('Let them continue'));

    await waitFor(() => expect(releaseHold).toHaveBeenCalledTimes(1));
    expect(releaseHold).toHaveBeenCalledWith('en-held', { reason: '' });
  });
});

describe('DecisionQueue — empty', () => {
  it('says nobody is waiting rather than rendering an empty list', async () => {
    getDecisionQueue.mockResolvedValue([]);
    renderQueue();

    expect(await screen.findByText('Nobody is waiting')).toBeTruthy();
  });
});
