// OfferSection — PH4-A3. The candidate drawer's own view of an application's
// offer(s): the "New offer" control is offered only once the application
// is recorded as a hire (D-05: the decision itself is made elsewhere), and
// only while no other offer is in play for it.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
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
      <MemoryRouter initialEntries={['/drawer']}>
        <Routes>
          <Route
            path="/drawer"
            element={
              <OfferSection
                enrolmentId="enr-1"
                jobTitle="Backend Engineer"
                applicationStatus={status}
              />
            }
          />
          <Route path="/hr/offers/:offerId" element={<p>Offer detail page</p>} />
        </Routes>
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

describe('OfferSection - create only once hired', () => {
  it('hides "New offer" for a candidate who is not yet a hire', async () => {
    renderSection('interviewed');
    await screen.findByText(/An offer can be created once this application is recorded as a hire\./);
    expect(screen.queryByRole('button', { name: 'New offer' })).not.toBeInTheDocument();
  });

  it('offers "New offer" once the application is a hire', async () => {
    renderSection('hired');
    await screen.findByText('No offer yet for this application.');
    expect(screen.getByRole('button', { name: 'New offer' })).toBeInTheDocument();
  });

  it.each(['draft', 'pending_approval', 'approved', 'rejected', 'sent'] as const)(
    'does not offer a second offer while one is %s (audit OF-L8)',
    async (status) => {
      listEnrolmentOffers.mockResolvedValue([
        { ...OFFER, status, expires_at: '2099-01-01T00:00:00.000Z' },
      ]);
      renderSection('hired');
      expect(
        await screen.findByText(/This application already has an offer in progress/),
      ).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'New offer' })).not.toBeInTheDocument();
    },
  );

  it('says an accepted offer is final, without promising a new one after it ends', async () => {
    listEnrolmentOffers.mockResolvedValue([
      { ...OFFER, status: 'accepted', expires_at: '2099-01-01T00:00:00.000Z' },
    ]);
    renderSection('hired');
    expect(
      await screen.findByText(/This application already has an accepted offer/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/declined, withdrawn or expired/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'New offer' })).not.toBeInTheDocument();
  });

  it.each([
    ['withdrawn', null],
    ['declined', null],
    ['expired', null],
    ['sent', '2020-01-01T00:00:00.000Z'], // past its deadline: expired in fact
  ] as const)('offers a new offer once the last one is %s', async (status, expires) => {
    listEnrolmentOffers.mockResolvedValue([{ ...OFFER, status, expires_at: expires }]);
    renderSection('hired');
    expect(await screen.findByRole('button', { name: 'New offer' })).toBeInTheDocument();
  });

  it('says when the templates could not be loaded, instead of an empty choice', async () => {
    const user = userEvent.setup();
    listOfferTemplates.mockRejectedValue(new Error('down'));
    renderSection('hired');
    await user.click(await screen.findByRole('button', { name: 'New offer' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/You can still start from scratch/);
  });

  it('opens the new offer once it is created', async () => {
    const user = userEvent.setup();
    createEnrolmentOffer.mockResolvedValue({ ...OFFER, id: 'offer-9' });
    renderSection('hired');
    await user.click(await screen.findByRole('button', { name: 'New offer' }));
    await user.type(screen.getByLabelText('Base salary'), '1200000');
    await user.click(screen.getByRole('button', { name: 'Create offer' }));
    expect(await screen.findByText('Offer detail page')).toBeInTheDocument();
  });

  it('does not create with a currency that is not three letters', async () => {
    const user = userEvent.setup();
    renderSection('hired');
    await user.click(await screen.findByRole('button', { name: 'New offer' }));
    await user.type(screen.getByLabelText('Base salary'), '1200000');
    await user.clear(screen.getByLabelText('Currency'));
    await user.type(screen.getByLabelText('Currency'), 'IN');
    expect(screen.getByRole('button', { name: 'Create offer' })).toBeDisabled();
    expect(screen.getByText(/three-letter code/)).toBeInTheDocument();
  });

  it('creates an offer with the chosen template and a base salary', async () => {
    const user = userEvent.setup();
    createEnrolmentOffer.mockResolvedValue(OFFER);
    renderSection('hired');
    await screen.findByText('No offer yet for this application.');

    await user.click(screen.getByRole('button', { name: 'New offer' }));
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

    await user.click(screen.getByRole('button', { name: 'New offer' }));
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

describe('OfferSection - the list', () => {
  it('links each offer to its detail page', async () => {
    listEnrolmentOffers.mockResolvedValue([OFFER]);
    renderSection('hired');
    await screen.findByText(/Backend Engineer/);
    const link = screen.getByRole('link', { name: /Backend Engineer/ });
    expect(link).toHaveAttribute('href', '/hr/offers/offer-1');
    expect(screen.getByText('Draft')).toBeInTheDocument();
  });
});
