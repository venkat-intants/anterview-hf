// Offers — PH4-A3/A4. The company-wide list: status in words, expiry, and
// each accepted offer's document progress, filterable by state including
// the synthetic "preboarding" filter.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { OfferListResponse } from '../api/offers';

const listCompanyOffers = vi.fn();
vi.mock('../api/offers', () => ({
  listCompanyOffers: (...a: unknown[]) => listCompanyOffers(...a) as unknown,
}));

import Offers from '../pages/hr/Offers';

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Offers />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const RESPONSE: OfferListResponse = {
  items: [
    {
      id: 'offer-1',
      enrolment_id: 'enr-1',
      requisition_id: 'req-1',
      status: 'accepted',
      job_title: 'Backend Engineer',
      candidate_name: 'Kiran Rao',
      sent_at: '2026-09-05T00:00:00.000Z',
      expires_at: '2099-09-15T00:00:00.000Z',
      responded_at: '2026-09-07T00:00:00.000Z',
      preboarding_completed_at: null,
      updated_at: '2026-09-07T00:00:00.000Z',
      documents: { mandatory_total: 2, mandatory_verified: 1, awaiting_review: 1 },
    },
  ],
  total: 1,
  limit: 25,
  offset: 0,
};

beforeEach(() => {
  vi.clearAllMocks();
  listCompanyOffers.mockResolvedValue(RESPONSE);
});

const rowOf = (over: Partial<OfferListResponse['items'][number]>): OfferListResponse => ({
  ...RESPONSE,
  items: [{ ...RESPONSE.items[0], ...over }],
});

describe('Offers - each row says what it needs from the reader', () => {
  it.each([
    ['draft', 'Yours to submit'],
    ['rejected', 'Sent back - yours to revise'],
    ['approved', 'Yours to send'],
    ['pending_approval', 'With the company admin'],
    ['sent', 'Waiting on the candidate'],
    ['declined', 'Closed'],
  ] as const)('%s -> %s', async (status, text) => {
    listCompanyOffers.mockResolvedValue(
      rowOf({ status, documents: { mandatory_total: 0, mandatory_verified: 0, awaiting_review: 0 } }),
    );
    renderPage();
    expect(await screen.findByTestId('offer-needs')).toHaveTextContent(text);
  });

  it('asks for a document review on an accepted offer with documents waiting', async () => {
    renderPage();
    expect(await screen.findByTestId('offer-needs')).toHaveTextContent('Documents to review');
  });
});

describe('Offers - the list', () => {
  it('shows the candidate, job title, status in words and document progress', async () => {
    renderPage();
    await screen.findByText('Kiran Rao');
    expect(screen.getByText('Backend Engineer')).toBeInTheDocument();
    expect(within(screen.getByRole('table')).getByText('Accepted')).toBeInTheDocument();
    expect(screen.getByText(/Documents 1\/2/)).toBeInTheDocument();
    expect(screen.getByText(/1 awaiting review/)).toBeInTheDocument();
    const link = screen.getByRole('link', { name: /Kiran Rao/ });
    expect(link).toHaveAttribute('href', '/hr/offers/offer-1');
  });

  it('shows an empty state', async () => {
    listCompanyOffers.mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
    renderPage();
    expect(await screen.findByText('No offers here.')).toBeInTheDocument();
  });

  it('shows an error state with a retry, never an empty list', async () => {
    const user = userEvent.setup();
    listCompanyOffers.mockRejectedValue(new Error('down'));
    renderPage();
    expect(await screen.findByText('Could not load offers.')).toBeInTheDocument();
    expect(screen.queryByText('No offers here.')).not.toBeInTheDocument();

    listCompanyOffers.mockResolvedValue(RESPONSE);
    await user.click(screen.getByRole('button', { name: /try again/i }));
    expect(await screen.findByText('Kiran Rao')).toBeInTheDocument();
  });

  it('reads a sent offer past its deadline as expired (audit OF-F4)', async () => {
    listCompanyOffers.mockResolvedValue(
      rowOf({
        status: 'sent',
        expires_at: '2020-01-01T00:00:00.000Z',
        documents: { mandatory_total: 0, mandatory_verified: 0, awaiting_review: 0 },
      }),
    );
    renderPage();
    expect(await screen.findByTestId('offer-needs')).toHaveTextContent('Closed');
    const table = screen.getByRole('table');
    expect(within(table).getByText('Expired')).toBeInTheDocument();
    expect(within(table).getByText(/^Expired \d/)).toBeInTheDocument();
    expect(within(table).queryByText('Sent')).not.toBeInTheDocument();
  });

  it('filters with the same words the pills use', async () => {
    renderPage();
    await screen.findByText('Kiran Rao');
    const filter = screen.getByLabelText('Status');
    expect(within(filter).getByRole('option', { name: 'Awaiting approval' })).toBeInTheDocument();
    expect(within(filter).getByRole('option', { name: 'Sent back' })).toBeInTheDocument();
  });

  it('filters by status, including the preboarding filter', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Kiran Rao');

    await user.selectOptions(screen.getByLabelText('Status'), 'preboarding');
    await waitFor(() =>
      expect(listCompanyOffers).toHaveBeenCalledWith({
        status: 'preboarding',
        limit: 25,
        offset: 0,
      }),
    );
  });
});
