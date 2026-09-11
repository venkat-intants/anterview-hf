// OpeningDetails — who owns an opening, how many hires it wants, when it closes.
//
// Pinned: the owner list is the company's HR team (from the server, not typed
// in), Save sends only what changed, and clearing a field sends null rather
// than an empty string the server would refuse.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Requisition } from '../api/requisitions';

const listTeam = vi.fn();
const updateRequisition = vi.fn();
vi.mock('../api/requisitions', () => ({
  listTeam: (...a: unknown[]) => listTeam(...a) as unknown,
  updateRequisition: (...a: unknown[]) => updateRequisition(...a) as unknown,
  closingDateToIso: (d: string) => (d ? `${d}T23:59:59.000Z` : null),
}));
vi.mock('../lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

import OpeningDetails from '../components/OpeningDetails';

const REQ = {
  id: 'req-1',
  title: 'Welder',
  level: 'mid',
  status: 'open',
  owner_user_id: 'u-1',
  owner_name: 'Hema HR',
  target_hires: 2,
  closes_at: null,
} as unknown as Requisition;

function renderIt(req: Requisition = REQ) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <OpeningDetails requisition={req} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listTeam.mockResolvedValue([
    { id: 'u-1', full_name: 'Hema HR', email: 'hema@x.test' },
    { id: 'u-2', full_name: 'Kiran Colleague', email: 'kiran@x.test' },
  ]);
  updateRequisition.mockResolvedValue(REQ);
});

describe('OpeningDetails', () => {
  it('offers the HR team as owners and shows the current one', async () => {
    renderIt();
    await screen.findByRole('option', { name: 'Kiran Colleague' });
    expect(screen.getByLabelText('Owner')).toHaveValue('u-1');
  });

  it('cannot save until something changes', async () => {
    renderIt();
    await screen.findByRole('option', { name: 'Kiran Colleague' });
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('sends only what changed', async () => {
    const user = userEvent.setup();
    renderIt();
    await screen.findByRole('option', { name: 'Kiran Colleague' });
    await user.selectOptions(screen.getByLabelText('Owner'), 'u-2');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(updateRequisition).toHaveBeenCalledWith('req-1', { owner_user_id: 'u-2' }),
    );
  });

  it('clears the target with null, not an empty string', async () => {
    const user = userEvent.setup();
    renderIt();
    await screen.findByRole('option', { name: 'Kiran Colleague' });
    await user.clear(screen.getByLabelText('Hires wanted'));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(updateRequisition).toHaveBeenCalledWith('req-1', { target_hires: null }),
    );
  });

  it('sends a closing date as the end of that day', async () => {
    const user = userEvent.setup();
    renderIt();
    await screen.findByRole('option', { name: 'Kiran Colleague' });
    await user.type(screen.getByLabelText('Closes on'), '2099-01-31');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(updateRequisition).toHaveBeenCalledWith('req-1', {
        closes_at: '2099-01-31T23:59:59.000Z',
      }),
    );
  });
});
