// Offers — PH4-A3/A4. The company-wide list: status in words, expiry, and
// each accepted offer's document progress, filterable by state including
// the synthetic "preboarding" filter.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
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
      expires_at: '2026-09-15T00:00:00.000Z',
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

describe('Offers — the list', () => {
  it('shows the candidate, job title, status in words and document progress', async () => {
    renderPage();
    await screen.findByText('Kiran Rao');
    expect(screen.getByText('Backend Engineer')).toBeInTheDocument();
    expect(screen.getByText('accepted')).toBeInTheDocument();
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

  it('shows an error state', async () => {
    listCompanyOffers.mockRejectedValue(new Error('Could not load offers.'));
    renderPage();
    expect(await screen.findByText('Could not load offers.')).toBeInTheDocument();
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
