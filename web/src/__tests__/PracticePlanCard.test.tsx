// PracticePlanCard: a 403 and an outage are different answers.
//
// A 403 is an account with no self-serve plan (staff landing on /dashboard),
// and the card renders nothing. Any other failure is a candidate whose plan
// did not load, and treating it like the 403 made the dashboard's centrepiece
// vanish with no word and no way to try again (consistency audit D2).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ApiError } from '../api/client';
import type { PracticePlan } from '../api/onboarding';

const getPracticePlan = vi.fn();
vi.mock('../api/onboarding', () => ({
  getPracticePlan: (...a: unknown[]) => getPracticePlan(...a) as unknown,
}));

import PracticePlanCard from '../components/practice/PracticePlanCard';

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PracticePlanCard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('PracticePlanCard - a failed load', () => {
  it('renders nothing for an account with no plan (403)', async () => {
    getPracticePlan.mockRejectedValue(new ApiError('Forbidden', 403));
    const { container } = renderCard();
    await vi.waitFor(() => expect(getPracticePlan).toHaveBeenCalled());
    await vi.waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('says the plan could not load on an outage, and tries again on request', async () => {
    getPracticePlan.mockRejectedValueOnce(new ApiError('Bad gateway', 502));
    renderCard();
    expect(await screen.findByText('Could not load your practice plan.')).toBeInTheDocument();

    const notReady: PracticePlan = {
      ready: false,
      target_role: null,
      target_level: null,
      domain_family: null,
      domain_label: null,
      what_this_person_does: null,
      competencies: [],
      readiness: null,
      interviews_completed: 0,
      last_interview_at: null,
      focus_competency_id: null,
      focus_competency_name: null,
      never_probed: [],
    };
    getPracticePlan.mockResolvedValue(notReady);
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Make this yours')).toBeInTheDocument();
    expect(getPracticePlan).toHaveBeenCalledTimes(2);
  });

  it('treats a network failure as an outage too, not as "no plan"', async () => {
    getPracticePlan.mockRejectedValue(new TypeError('Failed to fetch'));
    renderCard();
    expect(await screen.findByText('Could not load your practice plan.')).toBeInTheDocument();
  });
});
