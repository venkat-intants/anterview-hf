// OfferApprovals — PH4-A3, decision D4-2. The super admin's queue of offers
// waiting for approval, and one in full with compensation shown. A decision
// is offered only while the offer is actually pending, and "send back"
// enforces the same 10-character minimum as the server.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { OfferDetail, OfferOut } from '../api/offers';

const listOfferApprovals = vi.fn();
const getAdminOffer = vi.fn();
const approveOffer = vi.fn();
const rejectOffer = vi.fn();
vi.mock('../api/offers', () => ({
  listOfferApprovals: (...a: unknown[]) => listOfferApprovals(...a) as unknown,
  getAdminOffer: (...a: unknown[]) => getAdminOffer(...a) as unknown,
  approveOffer: (...a: unknown[]) => approveOffer(...a) as unknown,
  rejectOffer: (...a: unknown[]) => rejectOffer(...a) as unknown,
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
  },
}));

let paramOfferId: string | undefined;
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useParams: () => ({ offerId: paramOfferId }) };
});

import OfferApprovals from '../pages/superadmin/OfferApprovals';

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <OfferApprovals />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const QUEUE_ROW: OfferOut = {
  id: 'offer-1',
  enrolment_id: 'enr-1',
  requisition_id: 'req-1',
  template_id: null,
  status: 'pending_approval',
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
  submitted_at: '2026-09-10T00:00:00.000Z',
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
  updated_at: '2026-09-10T00:00:00.000Z',
};

const DETAIL: OfferDetail = { ...QUEUE_ROW, history: [] };

beforeEach(() => {
  vi.clearAllMocks();
  paramOfferId = undefined;
  listOfferApprovals.mockResolvedValue([QUEUE_ROW]);
  getAdminOffer.mockResolvedValue(DETAIL);
});

describe('OfferApprovals — the queue', () => {
  it('lists a waiting offer, with compensation, linking to its detail', async () => {
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');
    expect(screen.getByText(/INR/)).toBeInTheDocument();
    expect(screen.getByText(/1,200,000|12,00,000/)).toBeInTheDocument();
    const link = screen.getByRole('link', { name: 'Kiran Rao · Backend Engineer' });
    expect(link).toHaveAttribute('href', '/superadmin/offer-approvals/offer-1');
  });

  it('says so when nothing is waiting', async () => {
    listOfferApprovals.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText('Nothing is waiting on you.')).toBeInTheDocument();
  });
});

describe('OfferApprovals — one offer in full', () => {
  beforeEach(() => {
    paramOfferId = 'offer-1';
  });

  it('shows employment terms and compensation', async () => {
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');
    expect(screen.getByText('Bengaluru')).toBeInTheDocument();
    expect(screen.getByText('3 months')).toBeInTheDocument();
    expect(screen.getByText('30 days')).toBeInTheDocument();
  });

  it('approves with an optional note', async () => {
    const user = userEvent.setup();
    approveOffer.mockResolvedValue({ ...DETAIL, status: 'approved' });
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');

    await user.click(screen.getByRole('button', { name: 'Approve' }));
    await user.type(screen.getByLabelText(/note \(optional\)/i), 'Looks good');
    await user.click(screen.getByRole('button', { name: 'Confirm approval' }));

    await waitFor(() => expect(approveOffer).toHaveBeenCalledWith('offer-1', 'Looks good'));
    expect(toastSuccess).toHaveBeenCalled();
  });

  it('will not send back with fewer than 10 characters', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');

    await user.click(screen.getByRole('button', { name: 'Send back' }));
    await user.type(screen.getByLabelText(/what needs to change/i), 'too short');

    expect(screen.getByRole('button', { name: 'Send back' })).toBeDisabled();
    expect(screen.getByText(/Write at least 10 characters/)).toBeInTheDocument();
    expect(rejectOffer).not.toHaveBeenCalled();
  });

  it('sends back once the note is long enough', async () => {
    const user = userEvent.setup();
    rejectOffer.mockResolvedValue({ ...DETAIL, status: 'rejected' });
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');

    await user.click(screen.getByRole('button', { name: 'Send back' }));
    await user.type(screen.getByLabelText(/what needs to change/i), 'Raise the base salary');
    await user.click(screen.getByRole('button', { name: 'Send back' }));

    await waitFor(() =>
      expect(rejectOffer).toHaveBeenCalledWith('offer-1', 'Raise the base salary'),
    );
  });

  it('shows separation-of-duties refusal exactly as the server worded it', async () => {
    const user = userEvent.setup();
    approveOffer.mockRejectedValue(
      new Error('An offer is approved by someone other than the person who wrote or submitted it.'),
    );
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');

    await user.click(screen.getByRole('button', { name: 'Approve' }));
    await user.click(screen.getByRole('button', { name: 'Confirm approval' }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        'An offer is approved by someone other than the person who wrote or submitted it.',
      ),
    );
  });

  it('offers no decision once an offer is no longer pending', async () => {
    getAdminOffer.mockResolvedValue({ ...DETAIL, status: 'approved' });
    renderPage();
    await screen.findByText('Kiran Rao · Backend Engineer');

    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Send back' })).not.toBeInTheDocument();
    expect(screen.getByText(/nothing for you to decide/)).toBeInTheDocument();
  });
});
