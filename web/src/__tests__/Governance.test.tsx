// Budget, approval and scheduled publishing — PH3-B2 / PH3-B4a.
//
// Two things are worth asserting in the UI rather than only on the server.
//
// First, that an unapproved opening cannot be scheduled to go live. The server
// refuses it, but a form that lets somebody fill it in and press a button
// before telling them is a worse experience than one that says so first.
//
// Second, that the schedule never shows a time without the sentence describing
// how precise it is. The publisher is an interval loop; "09:00" alone is a
// promise the architecture does not make.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { PendingApproval, PublishSchedule, Requisition } from '../api/requisitions';

const getPublishSchedule = vi.fn();
const setPublishSchedule = vi.fn();
const cancelPublishSchedule = vi.fn();
const submitRequisitionForApproval = vi.fn();
const updateRequisition = vi.fn();
const listPendingApprovals = vi.fn();
const approveRequisition = vi.fn();
const rejectRequisition = vi.fn();

vi.mock('../api/requisitions', async () => {
  const actual = await vi.importActual<typeof import('../api/requisitions')>('../api/requisitions');
  return {
    ...actual,
    getPublishSchedule: (...a: unknown[]) => getPublishSchedule(...a) as unknown,
    setPublishSchedule: (...a: unknown[]) => setPublishSchedule(...a) as unknown,
    cancelPublishSchedule: (...a: unknown[]) => cancelPublishSchedule(...a) as unknown,
    submitRequisitionForApproval: (...a: unknown[]) =>
      submitRequisitionForApproval(...a) as unknown,
    updateRequisition: (...a: unknown[]) => updateRequisition(...a) as unknown,
    listPendingApprovals: (...a: unknown[]) => listPendingApprovals(...a) as unknown,
    approveRequisition: (...a: unknown[]) => approveRequisition(...a) as unknown,
    rejectRequisition: (...a: unknown[]) => rejectRequisition(...a) as unknown,
  };
});

vi.mock('../lib/toast', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

import GovernancePanel from '../components/workflow/GovernancePanel';
import ApprovalQueue from '../pages/superadmin/ApprovalQueue';

// A COPY of the sentence the server builds in
// `scheduled_publishing.py::tolerance_sentence`, supplied here as a mock so
// these tests can assert it is rendered. It therefore CANNOT detect the two
// drifting apart — and they did: the server's wording changed in 8ac95fd and
// this constant kept the pre-8ac95fd text until 2026-09-26. The number in it
// is derived from a deployment-tuned poll interval, so treat any exact-match
// assertion on this string as testing the rendering, never the promise.
const TOLERANCE =
  'Scheduled openings go live within about 1 minute(s) of the chosen time while the ' +
  'service is running; if the service is asleep, shortly after it next wakes.';

function requisition(over: Partial<Requisition> = {}): Requisition {
  return {
    id: 'req-1',
    title: 'Backend Engineer',
    level: 'mid',
    status: 'open',
    jd_text: null,
    target_hires: 3,
    closes_at: null,
    owner_user_id: null,
    from_backfill: false,
    public_apply_enabled: false,
    created_at: '2026-09-01T10:00:00Z',
    total_enrolments: 0,
    hired: 0,
    awaiting_decision: 0,
    funnel: [],
    department: null,
    location: null,
    employment_type: null,
    experience_min_years: null,
    experience_max_years: null,
    salary_min: null,
    salary_max: null,
    salary_currency: null,
    salary_visible: false,
    responsibilities: [],
    required_skills: [],
    nice_to_have_skills: [],
    approval_status: 'draft',
    ...over,
  };
}

function schedule(over: Partial<PublishSchedule> = {}): PublishSchedule {
  return {
    requisition_id: 'req-1',
    publish_at: null,
    published_at: null,
    public_apply_enabled: false,
    tolerance: TOLERANCE,
    ...over,
  };
}

function renderPanel(req = requisition()) {
  const client = new QueryClient({
    // See ResumeApplication.test.tsx: a focus-triggered refetch mid-interaction
    // can replace the data a form was seeded from.
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <GovernancePanel requisition={req} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getPublishSchedule.mockResolvedValue(schedule());
  setPublishSchedule.mockResolvedValue(schedule({ publish_at: '2099-01-01T09:00:00Z' }));
  cancelPublishSchedule.mockResolvedValue(schedule());
  submitRequisitionForApproval.mockResolvedValue(
    requisition({ approval_status: 'pending_approval' }),
  );
  updateRequisition.mockResolvedValue(requisition());
  listPendingApprovals.mockResolvedValue([]);
});

// ===========================================================================
// Approval state is legible
// ===========================================================================
describe('approval state', () => {
  it.each([
    ['draft', 'Not submitted'],
    ['pending_approval', 'Awaiting approval'],
    ['approved', 'Approved'],
    ['rejected', 'Sent back'],
  ] as const)('reads %s as "%s"', async (status, label) => {
    renderPanel(requisition({ approval_status: status }));
    expect(await screen.findByText(label)).toBeInTheDocument();
  });

  it('offers submission from draft', async () => {
    renderPanel();
    expect(await screen.findByRole('button', { name: /Submit for approval/ })).toBeInTheDocument();
  });

  it('offers resubmission after changes were requested', async () => {
    renderPanel(requisition({ approval_status: 'rejected' }));
    expect(await screen.findByRole('button', { name: /Submit for approval/ })).toBeInTheDocument();
  });

  it('keeps one primary action: Schedule is primary only once approved (audit A5)', async () => {
    renderPanel();
    const submit = await screen.findByRole('button', { name: /Submit for approval/ });
    expect(submit.className).toMatch(/(^|\s)bg-primary(\s|$)/);
    // The schedule half loads its own query, so its button arrives after it.
    expect((await screen.findByRole('button', { name: /^Schedule$/ })).className).not.toMatch(
      /(^|\s)bg-primary(\s|$)/,
    );
  });

  it('makes Schedule the primary action on an approved opening', async () => {
    renderPanel(requisition({ approval_status: 'approved' }));
    await screen.findByText('Approved');
    expect((await screen.findByRole('button', { name: /^Schedule$/ })).className).toMatch(
      /(^|\s)bg-primary(\s|$)/,
    );
  });

  it('offers no approve button to HR at all', async () => {
    // Not hidden conditionally — absent. Approving requires the super_admin
    // role and a user holds exactly one, so this console could never do it.
    renderPanel(requisition({ approval_status: 'pending_approval' }));
    await screen.findByText('Awaiting approval');
    expect(screen.queryByRole('button', { name: /^Approve/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Request changes/ })).not.toBeInTheDocument();
  });

  it('shows the approver’s note when there is one', async () => {
    renderPanel(
      requisition({
        approval_status: 'rejected',
        approval_note: 'Confirm the budget with finance first.',
        approval_decided_by_name: 'Arjun',
      }),
    );
    expect(await screen.findByText(/Confirm the budget with finance/)).toBeInTheDocument();
  });
});

// ===========================================================================
// The publish gate, explained before it bites
// ===========================================================================
describe('an unapproved opening', () => {
  it('says why it cannot go public yet', async () => {
    renderPanel();
    expect(await screen.findByText(/cannot accept public applications/)).toBeInTheDocument();
  });

  it('cannot be scheduled', async () => {
    renderPanel();
    await screen.findByText(/Publish automatically/);
    // The form renders once the schedule query settles (a skeleton until then).
    expect(await screen.findByLabelText(/Go live at/)).toBeDisabled();
    expect(screen.getByRole('button', { name: /Schedule/ })).toBeDisabled();
  });

  it('stops saying so once approved', async () => {
    renderPanel(requisition({ approval_status: 'approved' }));
    await screen.findByText('Approved');
    expect(screen.queryByText(/cannot accept public applications/)).not.toBeInTheDocument();
  });
});

// ===========================================================================
// The schedule tells the truth about its own precision
// ===========================================================================
describe('scheduled publishing', () => {
  it('never shows a time without the tolerance', async () => {
    getPublishSchedule.mockResolvedValue(schedule({ publish_at: '2099-01-01T09:00:00Z' }));
    renderPanel(requisition({ approval_status: 'approved' }));
    expect(await screen.findByText(/Scheduled for/)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(TOLERANCE.slice(0, 40)))).toBeInTheDocument();
  });

  it('shows the tolerance on the empty form too', async () => {
    renderPanel(requisition({ approval_status: 'approved' }));
    expect(await screen.findByText(new RegExp(TOLERANCE.slice(0, 40)))).toBeInTheDocument();
  });

  it('sends an absolute instant, not a local wall-clock string', async () => {
    // "Publish at 09:00" landing five and a half hours out is exactly the bug
    // a scheduling feature must not have.
    renderPanel(requisition({ approval_status: 'approved' }));
    const input = await screen.findByLabelText(/Go live at/);
    await userEvent.type(input, '2099-01-01T09:00');
    await userEvent.click(screen.getByRole('button', { name: /Schedule/ }));
    await waitFor(() => expect(setPublishSchedule).toHaveBeenCalled());
    const [, sent] = setPublishSchedule.mock.calls[0] as [string, string];
    expect(sent).toMatch(/Z$/);
    expect(new Date(sent).toISOString()).toBe(sent);
  });

  it('offers cancellation once something is scheduled', async () => {
    getPublishSchedule.mockResolvedValue(schedule({ publish_at: '2099-01-01T09:00:00Z' }));
    renderPanel(requisition({ approval_status: 'approved' }));
    await userEvent.click(
      await screen.findByRole('button', { name: /Cancel scheduled publication/ }),
    );
    // It asks first: a cancelled schedule is not restored by itself.
    expect(cancelPublishSchedule).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel publication' }));
    await waitFor(() => expect(cancelPublishSchedule).toHaveBeenCalledWith('req-1'));
  });

  it('will not schedule an opening that is already live', async () => {
    getPublishSchedule.mockResolvedValue(schedule({ public_apply_enabled: true }));
    renderPanel(requisition({ approval_status: 'approved', public_apply_enabled: true }));
    expect(await screen.findByText(/already accepting applications/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Schedule/ })).toBeDisabled();
  });

  it('says the schedule could not load, instead of offering to schedule over it', async () => {
    getPublishSchedule.mockRejectedValue(new Error('offline'));
    renderPanel(requisition({ approval_status: 'approved' }));
    expect(await screen.findByText('Could not load the publish schedule.')).toBeInTheDocument();
    expect(screen.queryByLabelText(/Go live at/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});

// ===========================================================================
// Budget
// ===========================================================================
describe('budget', () => {
  it('sends all four parts together, because an amount alone means nothing', async () => {
    renderPanel();
    await userEvent.type(await screen.findByLabelText(/Budget amount/), '5000000');
    await userEvent.click(screen.getByRole('button', { name: /Save budget/ }));
    await waitFor(() =>
      expect(updateRequisition).toHaveBeenCalledWith(
        'req-1',
        expect.objectContaining({
          budget_amount: 5000000,
          budget_currency: 'INR',
          budget_basis: 'total',
          budget_period: 'annual',
        }),
      ),
    );
  });

  it('clears all four when the amount is cleared', async () => {
    renderPanel(
      requisition({
        budget_amount: 5000000,
        budget_currency: 'INR',
        budget_basis: 'total',
        budget_period: 'annual',
      }),
    );
    await userEvent.clear(await screen.findByLabelText(/Budget amount/));
    await userEvent.click(screen.getByRole('button', { name: /Save budget/ }));
    await waitFor(() =>
      expect(updateRequisition).toHaveBeenCalledWith(
        'req-1',
        expect.objectContaining({
          budget_amount: null,
          budget_currency: null,
          budget_basis: null,
          budget_period: null,
        }),
      ),
    );
  });

  it('says plainly that budget is not headcount', async () => {
    renderPanel();
    expect(await screen.findByText(/Money, not headcount/)).toBeInTheDocument();
  });
});

// ===========================================================================
// The super admin's queue
// ===========================================================================
function pending(over: Partial<PendingApproval> = {}): PendingApproval {
  return {
    id: 'req-9',
    title: 'Data Engineer',
    level: 'senior',
    department: 'Data',
    location: 'Hyderabad',
    target_hires: 2,
    budget_amount: 5_000_000,
    budget_currency: 'INR',
    budget_basis: 'total',
    budget_period: 'annual',
    budget_notes: null,
    submitted_at: '2026-09-10T09:00:00Z',
    submitted_by_name: 'Meera',
    note: null,
    ...over,
  };
}

function renderQueue() {
  const client = new QueryClient({
    // See ResumeApplication.test.tsx: a focus-triggered refetch mid-interaction
    // can replace the data a form was seeded from.
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ApprovalQueue />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('the approval queue', () => {
  it('says nothing is waiting rather than showing an empty list', async () => {
    renderQueue();
    expect(await screen.findByText(/Nothing is waiting on you/)).toBeInTheDocument();
  });

  it('leads with the budget, because that is the question', async () => {
    listPendingApprovals.mockResolvedValue([pending()]);
    renderQueue();
    expect(await screen.findByText(/INR 50,00,000/)).toBeInTheDocument();
  });

  it('flags an opening submitted with no budget at all', async () => {
    listPendingApprovals.mockResolvedValue([pending({ budget_amount: null })]);
    renderQueue();
    expect(await screen.findByText(/No budget recorded/)).toBeInTheDocument();
  });

  it('shows how long somebody has been waiting', async () => {
    listPendingApprovals.mockResolvedValue([pending()]);
    renderQueue();
    expect(await screen.findByText(/Waiting \d+ days/)).toBeInTheDocument();
  });

  // AD-F12: the same decision pattern as the workflow, offer and question
  // queues - approve in two steps, send back with a reason.
  it('approves in two steps, like every other review queue', async () => {
    listPendingApprovals.mockResolvedValue([pending()]);
    approveRequisition.mockResolvedValue(requisition());
    renderQueue();
    await userEvent.click(await screen.findByRole('button', { name: /^Approve$/ }));
    expect(approveRequisition).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Confirm approval' }));
    await waitFor(() => expect(approveRequisition).toHaveBeenCalledWith('req-9', undefined));
  });

  it('needs a reason of at least 10 characters to send something back', async () => {
    // Sending an opening back costs somebody a week. It deserves a beat in
    // which to write down why.
    listPendingApprovals.mockResolvedValue([pending()]);
    rejectRequisition.mockResolvedValue(requisition());
    renderQueue();
    await userEvent.click(await screen.findByRole('button', { name: /Request changes/ }));
    await userEvent.type(screen.getByLabelText(/what needs to change/i), 'too short');
    expect(screen.getByRole('button', { name: 'Send back' })).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/what needs to change/i), ' - add a budget');
    await userEvent.click(screen.getByRole('button', { name: 'Send back' }));
    await waitFor(() =>
      expect(rejectRequisition).toHaveBeenCalledWith('req-9', 'too short - add a budget'),
    );
  });

  it('sends the note with the decision', async () => {
    listPendingApprovals.mockResolvedValue([pending()]);
    approveRequisition.mockResolvedValue(requisition());
    renderQueue();
    await userEvent.click(await screen.findByRole('button', { name: /^Approve$/ }));
    await userEvent.type(screen.getByLabelText(/note \(optional\)/i), 'Go ahead');
    await userEvent.click(screen.getByRole('button', { name: 'Confirm approval' }));
    await waitFor(() => expect(approveRequisition).toHaveBeenCalledWith('req-9', 'Go ahead'));
  });

  it('does not carry a half-typed rejection note into an approval', async () => {
    listPendingApprovals.mockResolvedValue([pending()]);
    approveRequisition.mockResolvedValue(requisition());
    renderQueue();
    await userEvent.click(await screen.findByRole('button', { name: /Request changes/ }));
    await userEvent.type(screen.getByLabelText(/what needs to change/i), 'No, because');
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await userEvent.click(screen.getByRole('button', { name: /^Approve$/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Confirm approval' }));
    await waitFor(() => expect(approveRequisition).toHaveBeenCalledWith('req-9', undefined));
  });

  it('keeps the row locked after a decision until the queue refetches (AD-F22)', async () => {
    listPendingApprovals.mockResolvedValue([pending()]);
    approveRequisition.mockResolvedValue(requisition());
    renderQueue();
    await userEvent.click(await screen.findByRole('button', { name: /^Approve$/ }));
    // The refetch is held, as if the network were slow.
    listPendingApprovals.mockImplementation(() => new Promise(() => undefined));
    await userEvent.click(screen.getByRole('button', { name: 'Confirm approval' }));

    expect(await screen.findByText(/Approved\. Updating the queue/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Approve$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Confirm approval' })).not.toBeInTheDocument();
    expect(approveRequisition).toHaveBeenCalledTimes(1);
  });

  it('hands the controls back if the refetch still lists the row', async () => {
    // Without this the row stayed on "Updating the queue" for good, with no
    // decision controls and no retry.
    listPendingApprovals.mockResolvedValue([pending()]);
    approveRequisition.mockResolvedValue(requisition());
    renderQueue();
    await userEvent.click(await screen.findByRole('button', { name: /^Approve$/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Confirm approval' }));

    expect(await screen.findByRole('button', { name: /^Approve$/ })).toBeInTheDocument();
    expect(screen.queryByText(/Updating the queue/)).not.toBeInTheDocument();
    expect(listPendingApprovals).toHaveBeenCalledTimes(2);
  });

  it('offers a retry when the refetch after a decision fails', async () => {
    listPendingApprovals.mockResolvedValueOnce([pending()]);
    approveRequisition.mockResolvedValue(requisition());
    renderQueue();
    await userEvent.click(await screen.findByRole('button', { name: /^Approve$/ }));
    listPendingApprovals.mockRejectedValueOnce(new Error('offline'));
    await userEvent.click(screen.getByRole('button', { name: 'Confirm approval' }));

    expect(await screen.findByText('Could not load the approval queue.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});

describe('submitting refreshes every view of the opening (HR-L12)', () => {
  it('invalidates the whole HR console, not only this opening', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const spy = vi.spyOn(client, 'invalidateQueries');
    render(
      <QueryClientProvider client={client}>
        <GovernancePanel requisition={requisition()} />
      </QueryClientProvider>,
    );
    await userEvent.click(await screen.findByRole('button', { name: /Submit for approval/ }));
    await waitFor(() => expect(submitRequisitionForApproval).toHaveBeenCalled());
    // ['hr'] is the prefix of ['hr', 'requisitions', ...], which the HR
    // home's "Openings in setup" badge reads.
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ queryKey: ['hr'] }));
  });
});
