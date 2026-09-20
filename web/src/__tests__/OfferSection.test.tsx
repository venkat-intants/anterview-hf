// OfferSection — PH4-A3. The candidate drawer's own view of an application's
// offer(s): the "Create offer" control is offered only once the application
// is recorded as a hire (D-05: the decision itself is made elsewhere).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { OfferOut, OfferTemplate } from '../api/offers';

const listEnrolmentOffers = vi.fn();
const createEnrolmentOffer = vi.fn();
const listOfferTemplates = vi.fn();
vi.mock('../api/offers', () => ({
  listEnrolmentOffers: (...a: unknown[]) => listEnrolmentOffers(...a) as unknown,
  createEnrolmentOffer: (...a: unknown[]) => createEnrolmentOffer(...a) as unknown,
  listOfferTemplates: (...a: unknown[]) => listOfferTemplates(...a) as unknown,
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
  },
}));

import OfferSection from '../components/OfferSection';

function renderSection(status: string | undefined) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <OfferSection enrolmentId="enr-1" jobTitle="Backend Engineer" applicationStatus={status} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const TEMPLATE: OfferTemplate = {
  id: 'tpl-1',
  name: 'Standard engineer',
  employment_type: 'full_time',
  currency: 'INR',
  pay_period: 'annual',
  probation_months: 3,
  notice_period_days: 30,
  benefits: null,
  terms: null,
  valid_days: 7,
};

const OFFER: OfferOut = {
  id: 'offer-1',
  enrolment_id: 'enr-1',
  requisition_id: 'req-1',
  template_id: null,
  status: 'draft',
  job_title: 'Backend Engineer',
  employment_type: null,
  start_date: null,
  location: null,
  base_salary: '1200000.00',
  currency: 'INR',
  pay_period: null,
  bonus: null,
  equity: null,
  benefits: null,
  terms: null,
  probation_months: null,
  notice_period_days: null,
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
};

beforeEach(() => {
  vi.clearAllMocks();
  listEnrolmentOffers.mockResolvedValue([]);
  listOfferTemplates.mockResolvedValue([TEMPLATE]);
});

describe('OfferSection — create only once hired', () => {
  it('hides "Create offer" for a candidate who is not yet a hire', async () => {
    renderSection('interviewed');
    await screen.findByText(/An offer can be created once this application is recorded as a hire\./);
    expect(screen.queryByRole('button', { name: 'Create offer' })).not.toBeInTheDocument();
  });

  it('offers "Create offer" once the application is a hire', async () => {
    renderSection('hired');
    await screen.findByText('No offer yet for this application.');
    expect(screen.getByRole('button', { name: 'Create offer' })).toBeInTheDocument();
  });

  it('creates an offer with the chosen template and a base salary', async () => {
    const user = userEvent.setup();
    createEnrolmentOffer.mockResolvedValue(OFFER);
    renderSection('hired');
    await screen.findByText('No offer yet for this application.');

    await user.click(screen.getByRole('button', { name: 'Create offer' }));
    await user.selectOptions(screen.getByLabelText('Template (optional)'), 'tpl-1');
    await user.type(screen.getByLabelText('Base salary'), '1200000');

    await user.click(screen.getByRole('button', { name: 'Create offer' }));
    await waitFor(() =>
      expect(createEnrolmentOffer).toHaveBeenCalledWith('enr-1', {
        template_id: 'tpl-1',
        base_salary: '1200000',
        currency: 'INR',
      }),
    );
  });

  it('takes the currency of the template chosen, not the form default', async () => {
    const user = userEvent.setup();
    listOfferTemplates.mockResolvedValue([
      TEMPLATE,
      { ...TEMPLATE, id: 'tpl-usd', name: 'US contractor', currency: 'USD' },
    ]);
    createEnrolmentOffer.mockResolvedValue(OFFER);
    renderSection('hired');
    await screen.findByText('No offer yet for this application.');

    await user.click(screen.getByRole('button', { name: 'Create offer' }));
    await user.selectOptions(screen.getByLabelText('Template (optional)'), 'tpl-usd');
    expect(screen.getByLabelText('Currency')).toHaveValue('USD');
    await user.type(screen.getByLabelText('Base salary'), '90000');
    await user.click(screen.getByRole('button', { name: 'Create offer' }));
    await waitFor(() =>
      expect(createEnrolmentOffer).toHaveBeenCalledWith('enr-1', {
        template_id: 'tpl-usd',
        base_salary: '90000',
        currency: 'USD',
      }),
    );
  });
});

describe('OfferSection — the list', () => {
  it('links each offer to its detail page', async () => {
    listEnrolmentOffers.mockResolvedValue([OFFER]);
    renderSection('hired');
    await screen.findByText(/Backend Engineer/);
    const link = screen.getByRole('link', { name: /Backend Engineer/ });
    expect(link).toHaveAttribute('href', '/hr/offers/offer-1');
    expect(screen.getByText('draft')).toBeInTheDocument();
  });
});
