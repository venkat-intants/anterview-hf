// YourOffers — PH4-A3. A signed-in candidate's own offers, and that opening
// one navigates only through the same-origin/`/offer`-pathname guard (no
// open redirect) — see safeUrl.test.ts for sameOriginUrl itself.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { CandidateOffer } from '../api/offers';

const listMyOffers = vi.fn();
const getMyOfferLink = vi.fn();
vi.mock('../api/offers', () => ({
  listMyOffers: (...a: unknown[]) => listMyOffers(...a) as unknown,
  getMyOfferLink: (...a: unknown[]) => getMyOfferLink(...a) as unknown,
}));

// Navigating an offer link uses window.location.assign, and jsdom's location
// is not writable — replace it once, module-wide, same fix Applications.test.tsx
// uses for "Start interview".
let assignedTo: string | null = null;
const assign = vi.fn((url: string) => {
  assignedTo = url;
});
Object.defineProperty(window, 'location', {
  writable: true,
  value: { ...window.location, assign },
});

import YourOffers from '../components/candidate/YourOffers';

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <YourOffers />
    </QueryClientProvider>,
  );
}

const OFFER: CandidateOffer = {
  id: 'offer-1',
  status: 'sent',
  company: 'Acme Corp',
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
  expires_at: '2026-10-10T00:00:00.000Z',
  responded_at: null,
  preboarding_completed_at: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  assignedTo = null;
  listMyOffers.mockResolvedValue([OFFER]);
});

describe('YourOffers — states', () => {
  it('renders nothing when there are no offers and nothing has failed', async () => {
    listMyOffers.mockResolvedValue([]);
    const { container } = renderSection();
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('shows the job, company and compensation', async () => {
    renderSection();
    await screen.findByText('Backend Engineer');
    expect(screen.getByText('Acme Corp')).toBeInTheDocument();
  });

  it('shows a loading state', () => {
    listMyOffers.mockImplementation(() => new Promise(() => undefined));
    renderSection();
    expect(screen.getByText('Loading your offers…')).toBeInTheDocument();
  });

  it('shows an error, not an empty state, on failure', async () => {
    listMyOffers.mockRejectedValue(new Error('Network down'));
    renderSection();
    expect(await screen.findByText('Network down')).toBeInTheDocument();
  });
});

describe('YourOffers — opening a link', () => {
  it('navigates to a same-origin /offer link', async () => {
    getMyOfferLink.mockResolvedValue({ url: `${window.location.origin}/offer#freshtoken` });
    const user = userEvent.setup();
    renderSection();
    await screen.findByText('Backend Engineer');

    await user.click(screen.getByRole('button', { name: 'Open offer' }));
    await waitFor(() => expect(assignedTo).toBe(`${window.location.origin}/offer#freshtoken`));
  });

  it('refuses to navigate to a link that is not same-origin /offer', async () => {
    getMyOfferLink.mockResolvedValue({ url: 'https://evil.example.com/offer#freshtoken' });
    const user = userEvent.setup();
    renderSection();
    await screen.findByText('Backend Engineer');

    await user.click(screen.getByRole('button', { name: 'Open offer' }));
    expect(await screen.findByText('Could not open your offer.')).toBeInTheDocument();
    expect(assignedTo).toBeNull();
  });
});
