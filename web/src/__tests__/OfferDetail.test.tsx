// OfferDetail — PH4-A3/A4. One offer in full: the fields are editable only in
// draft/rejected, every action is offered only where the state machine
// actually allows it, the approval note / decline / withdraw reasons are
// shown, a document review needs a reason for anything but verify, and the
// server's own refusal (naming missing documents) is what preboarding
// completion surfaces on failure.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { OfferDetail as OfferDetailShape, OfferDocuments } from '../api/offers';

const getOffer = vi.fn();
const updateOffer = vi.fn();
const submitOffer = vi.fn();
const recallOffer = vi.fn();
const reopenOffer = vi.fn();
const sendOffer = vi.fn();
const resendOffer = vi.fn();
const withdrawOffer = vi.fn();
const getOfferDocuments = vi.fn();
const reviewDocument = vi.fn();
const downloadDocument = vi.fn();
const completePreboarding = vi.fn();
const hrmsExport = vi.fn();
vi.mock('../api/offers', () => ({
  getOffer: (...a: unknown[]) => getOffer(...a) as unknown,
  updateOffer: (...a: unknown[]) => updateOffer(...a) as unknown,
  submitOffer: (...a: unknown[]) => submitOffer(...a) as unknown,
  recallOffer: (...a: unknown[]) => recallOffer(...a) as unknown,
  reopenOffer: (...a: unknown[]) => reopenOffer(...a) as unknown,
  sendOffer: (...a: unknown[]) => sendOffer(...a) as unknown,
  resendOffer: (...a: unknown[]) => resendOffer(...a) as unknown,
  withdrawOffer: (...a: unknown[]) => withdrawOffer(...a) as unknown,
  getOfferDocuments: (...a: unknown[]) => getOfferDocuments(...a) as unknown,
  reviewDocument: (...a: unknown[]) => reviewDocument(...a) as unknown,
  downloadDocument: (...a: unknown[]) => downloadDocument(...a) as unknown,
  completePreboarding: (...a: unknown[]) => completePreboarding(...a) as unknown,
  hrmsExport: (...a: unknown[]) => hrmsExport(...a) as unknown,
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
  },
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useParams: () => ({ offerId: 'offer-1' }) };
});

import OfferDetail from '../pages/hr/OfferDetail';

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <OfferDetail />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function offer(over: Partial<OfferDetailShape> = {}): OfferDetailShape {
  return {
    id: 'offer-1',
    enrolment_id: 'enr-1',
    requisition_id: 'req-1',
    template_id: null,
    status: 'draft',
    job_title: 'Backend Engineer',
    employment_type: 'full_time',
    start_date: '2026-10-01',
    location: 'Bengaluru',
    base_salary: '1200000.00',
    currency: 'INR',
    pay_period: 'annual',
    bonus: null,
    equity: null,
    benefits: null,
    terms: null,
    probation_months: 3,
    notice_period_days: 30,
    valid_days: 7,
    created_by_user_id: 'u-1',
    submitted_at: null,
    decided_at: null,
    approval_note: null,
    sent_at: null,
    expires_at: null,
    first_viewed_at: null,
    responded_at: null,
    decline_reason: null,
    withdrawn_at: null,
    withdraw_reason: null,
    preboarding_completed_at: null,
    candidate_name: 'Kiran Rao',
    created_at: '2026-09-01T00:00:00.000Z',
    updated_at: '2026-09-01T00:00:00.000Z',
    history: [],
    ...over,
  };
}

const EMPTY_DOCS: OfferDocuments = {
  offer_status: 'accepted',
  preboarding_completed_at: null,
  items: [],
  outstanding: [],
  complete_ready: true,
  history: [],
  candidate_name: 'Kiran Rao',
  job_title: 'Backend Engineer',
};

beforeEach(() => {
  vi.clearAllMocks();
  getOfferDocuments.mockResolvedValue(EMPTY_DOCS);
});

describe('OfferDetail - fields', () => {
  it('renders the candidate, job title and status', async () => {
    getOffer.mockResolvedValue(offer());
    renderPage();
    expect(await screen.findByText('Kiran Rao · Backend Engineer')).toBeInTheDocument();
    expect(screen.getByText('draft')).toBeInTheDocument();
  });

  it('leaves fields editable in draft', async () => {
    getOffer.mockResolvedValue(offer({ status: 'draft' }));
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');
    expect(screen.getByDisplayValue('Backend Engineer')).toBeEnabled();
  });

  it('disables fields once sent', async () => {
    getOffer.mockResolvedValue(offer({ status: 'sent', sent_at: '2026-09-05T00:00:00.000Z' }));
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');
    expect(screen.getByDisplayValue('Backend Engineer')).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument();
  });

  it('saves only the changed fields', async () => {
    const user = userEvent.setup();
    getOffer.mockResolvedValue(offer());
    updateOffer.mockResolvedValue(offer({ location: 'Hyderabad' }));
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');

    const location = screen.getByDisplayValue('Bengaluru');
    await user.clear(location);
    await user.type(location, 'Hyderabad');
    await user.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() =>
      expect(updateOffer).toHaveBeenCalledWith('offer-1', { location: 'Hyderabad' }),
    );
  });
});

describe('OfferDetail - actions only where legal', () => {
  it('a draft offers submit and withdraw, nothing else', async () => {
    getOffer.mockResolvedValue(offer({ status: 'draft' }));
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');

    expect(screen.getByRole('button', { name: 'Submit for approval' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Withdraw offer' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Recall' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reopen for editing' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Send to candidate' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Re-send' })).not.toBeInTheDocument();
  });

  it('pending approval offers only recall and withdraw', async () => {
    getOffer.mockResolvedValue(
      offer({ status: 'pending_approval', submitted_at: '2026-09-02T00:00:00.000Z' }),
    );
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');

    expect(screen.getByRole('button', { name: 'Recall' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Withdraw offer' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Submit for approval' })).not.toBeInTheDocument();
  });

  it('an approved offer offers reopen, send and withdraw', async () => {
    getOffer.mockResolvedValue(
      offer({ status: 'approved', decided_at: '2026-09-03T00:00:00.000Z' }),
    );
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');

    expect(screen.getByRole('button', { name: 'Reopen for editing' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send to candidate' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Withdraw offer' })).toBeInTheDocument();
  });

  it('a sent offer offers re-send and withdraw, and explains what re-sending does', async () => {
    getOffer.mockResolvedValue(offer({ status: 'sent', sent_at: '2026-09-05T00:00:00.000Z' }));
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');

    expect(screen.getByRole('button', { name: 'Re-send' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Withdraw offer' })).toBeInTheDocument();
    expect(screen.getByText(/retires the old link and lifts a lock/)).toBeInTheDocument();
  });

  it('an accepted offer (preboarding open) offers re-send but never withdraw', async () => {
    getOffer.mockResolvedValue(
      offer({ status: 'accepted', responded_at: '2026-09-06T00:00:00.000Z' }),
    );
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');

    expect(screen.getByRole('button', { name: 'Re-send' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Withdraw offer' })).not.toBeInTheDocument();
  });

  it('does not claim there is nothing left once preboarding is complete: the HRMS export remains', async () => {
    getOffer.mockResolvedValue(
      offer({
        status: 'accepted',
        responded_at: '2026-09-06T00:00:00.000Z',
        preboarding_completed_at: '2026-09-10T00:00:00.000Z',
      }),
    );
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');

    expect(
      screen.getByText('Preboarding is complete. The HRMS export is on the Documents tab.'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/nothing left to do here/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Re-send' })).not.toBeInTheDocument();
  });

  it('shows the rejection note, and the decline/withdraw reasons in their own states', async () => {
    getOffer.mockResolvedValue(
      offer({ status: 'rejected', approval_note: 'Please raise the base salary.' }),
    );
    renderPage();
    // The banner is gone; the note sits on the track's returned step.
    const track = await screen.findByTestId('offer-track');
    expect(within(track).getByText('Please raise the base salary.')).toBeInTheDocument();
    expect(within(track).getByText('Sent back')).toBeInTheDocument();
  });
});

describe('OfferDetail - withdrawing', () => {
  it('withdraws with an optional reason', async () => {
    const user = userEvent.setup();
    getOffer.mockResolvedValue(offer({ status: 'draft' }));
    withdrawOffer.mockResolvedValue(offer({ status: 'withdrawn' }));
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');

    await user.type(screen.getByLabelText('Withdraw reason'), 'Position closed');
    await user.click(screen.getByRole('button', { name: 'Withdraw offer' }));
    await user.click(screen.getByRole('button', { name: 'Withdraw' }));

    await waitFor(() => expect(withdrawOffer).toHaveBeenCalledWith('offer-1', 'Position closed'));
  });
});

describe('OfferDetail - documents review', () => {
  it('requires a reason to reject, but not to verify', async () => {
    const user = userEvent.setup();
    getOffer.mockResolvedValue(
      offer({ status: 'accepted', responded_at: '2026-09-06T00:00:00.000Z' }),
    );
    getOfferDocuments.mockResolvedValue({
      ...EMPTY_DOCS,
      items: [
        {
          requirement_id: 'req-a',
          name: 'PAN card',
          doc_type: 'tax',
          description: null,
          mandatory: true,
          requires_expiry: false,
          state: 'submitted',
          document: {
            id: 'doc-1',
            version: 1,
            file_name: 'pan.pdf',
            content_type: 'application/pdf',
            size_bytes: 1000,
            expires_on: null,
            uploaded_at: '2026-09-06T00:00:00.000Z',
            reviewed_at: null,
            review_note: null,
            reviewed_by: null,
          },
        },
      ],
    });
    renderPage();
    await screen.findByText('PAN card');

    await user.click(screen.getByRole('button', { name: 'Reject' }));
    const confirmReject = screen.getByRole('button', { name: /Confirm - Reject/ });
    expect(confirmReject).toBeDisabled();
    await user.type(screen.getByLabelText(/tell the candidate/i), 'Blurry scan');
    expect(confirmReject).toBeEnabled();

    reviewDocument.mockResolvedValue({ document_id: 'doc-1', state: 'rejected' });
    await user.click(confirmReject);
    await waitFor(() =>
      expect(reviewDocument).toHaveBeenCalledWith('doc-1', 'reject', 'Blurry scan'),
    );
  });
});

describe('OfferDetail - preboarding', () => {
  it('surfaces the server’s refusal naming the missing documents', async () => {
    const user = userEvent.setup();
    getOffer.mockResolvedValue(
      offer({ status: 'accepted', responded_at: '2026-09-06T00:00:00.000Z' }),
    );
    completePreboarding.mockRejectedValue(
      new Error('These mandatory documents are not verified yet: PAN card, Aadhaar card.'),
    );
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');

    await user.click(screen.getByRole('button', { name: 'Mark preboarding complete' }));
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        'These mandatory documents are not verified yet: PAN card, Aadhaar card.',
      ),
    );
  });

  it('offers the HRMS export only once preboarding is complete', async () => {
    const user = userEvent.setup();
    getOffer.mockResolvedValue(
      offer({
        status: 'accepted',
        responded_at: '2026-09-06T00:00:00.000Z',
        preboarding_completed_at: '2026-09-10T00:00:00.000Z',
      }),
    );
    hrmsExport.mockResolvedValue({
      export_id: 'exp-1',
      algorithm: 'HMAC-SHA256',
      key_id: 'key-1',
      signature: 'sig-abc',
      payload: { schema: 'anthire.preboarding.v1' },
    });
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');

    expect(screen.queryByRole('button', { name: 'Prepare HRMS export' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Prepare HRMS export' }));

    await waitFor(() => expect(hrmsExport).toHaveBeenCalledWith('offer-1'));
    expect(await screen.findByText(/key-1/)).toBeInTheDocument();
    expect(screen.getByText(/sig-abc/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Download JSON/ })).toBeInTheDocument();
  });

  it('does not offer the export before preboarding is complete', async () => {
    getOffer.mockResolvedValue(
      offer({ status: 'accepted', responded_at: '2026-09-06T00:00:00.000Z' }),
    );
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');
    expect(screen.queryByRole('button', { name: 'Prepare HRMS export' })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// The approval track: one primary per state, and only the legal ones
// ---------------------------------------------------------------------------

const AT = '2026-09-03T00:00:00.000Z';

const PRIMARY_BY_STATE: [string, Partial<OfferDetailShape>, string | null][] = [
  ['draft', { status: 'draft' }, 'Submit for approval'],
  // The move is the approver's, so HR has no primary — the track says who acts.
  ['pending approval', { status: 'pending_approval', submitted_at: AT }, null],
  [
    'sent back',
    { status: 'rejected', submitted_at: AT, decided_at: AT, approval_note: 'Raise the base.' },
    'Submit for approval',
  ],
  ['approved', { status: 'approved', submitted_at: AT, decided_at: AT }, 'Send to candidate'],
  // The move is the candidate's.
  ['sent', { status: 'sent', sent_at: AT }, null],
  [
    'accepted',
    { status: 'accepted', sent_at: AT, responded_at: AT },
    'Mark preboarding complete',
  ],
  [
    'accepted, preboarding complete',
    { status: 'accepted', sent_at: AT, responded_at: AT, preboarding_completed_at: AT },
    null,
  ],
  ['declined', { status: 'declined', sent_at: AT, responded_at: AT, decline_reason: 'Took another job' }, null],
  ['expired', { status: 'expired', sent_at: AT, expires_at: AT }, null],
  ['withdrawn', { status: 'withdrawn', withdrawn_at: AT, withdraw_reason: 'Position closed' }, null],
];

describe('OfferDetail - one primary action, driven by status', () => {
  it.each(PRIMARY_BY_STATE)('%s', async (_label, over, primary) => {
    getOffer.mockResolvedValue(offer(over));
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');

    const found = screen.queryAllByTestId('offer-primary');
    if (primary === null) {
      expect(found).toHaveLength(0);
    } else {
      expect(found).toHaveLength(1);
      expect(found[0]).toHaveAccessibleName(primary);
    }
    // The three banners, the Approval card and the Actions card are gone.
    expect(screen.queryByTestId('approval-state')).not.toBeInTheDocument();
    expect(screen.getByTestId('offer-track')).toBeInTheDocument();
  });

  it('names who must act on the pending step', async () => {
    getOffer.mockResolvedValue(offer({ status: 'pending_approval', submitted_at: AT }));
    renderPage();
    const track = await screen.findByTestId('offer-track');
    const current = within(track)
      .getAllByRole('listitem')
      .filter((li) => li.getAttribute('aria-current') === 'step');
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveTextContent('Approved');
    expect(current[0]).toHaveTextContent('Company admin decides');
  });

  it('shows the decline and withdraw reasons inline on the track', async () => {
    getOffer.mockResolvedValue(
      offer({ status: 'declined', sent_at: AT, responded_at: AT, decline_reason: 'Took another job' }),
    );
    const { unmount } = renderPage();
    const track = await screen.findByTestId('offer-track');
    expect(within(track).getByText('Decline reason: Took another job')).toBeInTheDocument();
    unmount();

    getOffer.mockResolvedValue(offer({ status: 'withdrawn', withdraw_reason: 'Position closed' }));
    renderPage();
    const track2 = await screen.findByTestId('offer-track');
    expect(within(track2).getByText('Withdraw reason: Position closed')).toBeInTheDocument();
  });

  it('keeps the quiet actions beside the primary', async () => {
    getOffer.mockResolvedValue(offer({ status: 'approved', submitted_at: AT, decided_at: AT }));
    renderPage();
    const track = await screen.findByTestId('offer-track');
    expect(within(track).getByRole('button', { name: 'Reopen for editing' })).toBeInTheDocument();
    expect(within(track).getByRole('button', { name: 'Withdraw offer' })).toBeInTheDocument();
    expect(within(track).getByRole('button', { name: 'Send to candidate' })).toBeInTheDocument();
  });
});

describe('OfferDetail - submit while the form is dirty', () => {
  it('is disabled with the reason beside it, and enabled again once saved', async () => {
    const user = userEvent.setup();
    getOffer.mockResolvedValue(offer());
    updateOffer.mockResolvedValue(offer({ location: 'Hyderabad' }));
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');

    const submit = screen.getByRole('button', { name: 'Submit for approval' });
    expect(submit).toBeEnabled();

    const location = screen.getByDisplayValue('Bengaluru');
    await user.clear(location);
    await user.type(location, 'Hyderabad');

    expect(submit).toBeDisabled();
    const reason = screen.getByText(/Save your changes first/);
    expect(submit).toHaveAttribute('aria-describedby', reason.id);

    getOffer.mockResolvedValue(
      offer({ location: 'Hyderabad', updated_at: '2026-09-02T00:00:00.000Z' }),
    );
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Submit for approval' })).toBeEnabled());
    expect(screen.queryByText(/Save your changes first/)).not.toBeInTheDocument();
  });
});

describe('OfferDetail - documents appear only once accepted', () => {
  it.each([
    ['draft', { status: 'draft' }],
    ['sent', { status: 'sent', sent_at: AT }],
  ] as [string, Partial<OfferDetailShape>][])(
    'a %s offer has no documents tab and does not fetch documents',
    async (_label, over) => {
      getOffer.mockResolvedValue(offer(over));
      renderPage();
      await screen.findByText('Kiran Rao · Backend Engineer');

      expect(screen.queryByRole('tab')).not.toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: 'Documents' })).not.toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: 'Preboarding' })).not.toBeInTheDocument();
      expect(getOfferDocuments).not.toHaveBeenCalled();
    },
  );

  it('an accepted offer opens on its Documents & preboarding tab, with Terms one click away', async () => {
    const user = userEvent.setup();
    getOffer.mockResolvedValue(offer({ status: 'accepted', sent_at: AT, responded_at: AT }));
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');

    const docs = screen.getByRole('tab', { name: 'Documents & preboarding' });
    expect(docs).toHaveAttribute('aria-selected', 'true');
    expect(await screen.findByRole('heading', { name: 'Preboarding' })).toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: 'Terms' }));
    expect(screen.getByDisplayValue('Backend Engineer')).toBeDisabled();
  });
});

describe('OfferDetail - History is a record, so it is closed by default', () => {
  const entry = (n: number, action = 'created') => ({
    action,
    actor_type: 'user',
    actor_name: 'Priya Raghavan',
    at: `2026-09-${String(n).padStart(2, '0')}T10:00:00.000Z`,
    details: {},
  });

  beforeEach(() => localStorage.clear());

  it('shows the count and the newest entry while closed, and the entries once opened', async () => {
    getOffer.mockResolvedValue(
      offer({ history: [entry(1, 'created'), entry(2, 'submitted'), entry(3, 'approved')] }),
    );
    renderPage();
    const history = await screen.findByTestId('offer-history');
    const toggle = within(history).getByRole('button', { name: /History/ });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(within(history).getByTestId('collapsible-summary').textContent).toMatch(
      /^3 entries · latest: .* by Priya Raghavan/,
    );
    expect(within(history).queryAllByRole('listitem')).toHaveLength(0);

    await userEvent.click(toggle);
    expect(within(history).getAllByRole('listitem')).toHaveLength(3);
  });

  it('shows five entries, then "Show all"', async () => {
    getOffer.mockResolvedValue(offer({ history: Array.from({ length: 9 }, (_, i) => entry(i + 1)) }));
    renderPage();
    const history = await screen.findByTestId('offer-history');
    await userEvent.click(within(history).getByRole('button', { name: /History/ }));
    expect(within(history).getAllByRole('listitem')).toHaveLength(5);
    await userEvent.click(within(history).getByRole('button', { name: 'Show all 9 entries' }));
    expect(within(history).getAllByRole('listitem')).toHaveLength(9);
  });

  it('says so when there is nothing yet', async () => {
    getOffer.mockResolvedValue(offer({ history: [] }));
    renderPage();
    const history = await screen.findByTestId('offer-history');
    await userEvent.click(within(history).getByRole('button', { name: /History/ }));
    expect(within(history).getByText('No activity recorded yet.')).toBeTruthy();
  });
});
