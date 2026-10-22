// The Go-live apply card: the link is offered only when it would work, the
// toasts say what happened, and "Link copied" means it was copied.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const updateRequisition = vi.fn();
vi.mock('../api/requisitions', async () => {
  const actual = await vi.importActual<typeof import('../api/requisitions')>('../api/requisitions');
  return { ...actual, updateRequisition: (...a: unknown[]) => updateRequisition(...a) as unknown };
});

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
    error: (...a: unknown[]) => toastError(...a) as unknown,
  },
}));

import { PublicApplyCard } from '../components/hr/opening/GoLivePanel';

function renderCard(props: Partial<Parameters<typeof PublicApplyCard>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PublicApplyCard
        requisitionId="req-1"
        enabled
        status="open"
        approved
        hasWorkflow
        {...props}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  updateRequisition.mockResolvedValue({});
});

describe('PublicApplyCard - the link', () => {
  it('offers the link while the opening is live', () => {
    renderCard({ closesAt: '2999-01-01T00:00:00Z' });
    expect(screen.getByRole('button', { name: 'Copy the application link' })).toBeInTheDocument();
  });

  it('withholds the link once the closing date has passed, and says why (HR-M9)', () => {
    renderCard({ closesAt: '2020-01-01T00:00:00Z' });
    expect(screen.getByTestId('apply-closing-date-passed')).toHaveTextContent(
      /closing date has passed/i,
    );
    expect(screen.queryByRole('button', { name: 'Copy the application link' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Open the application page' })).toBeNull();
  });
});

describe('PublicApplyCard - feedback (HR-L11)', () => {
  it('says applications were switched off, not "closed"', async () => {
    const user = userEvent.setup();
    renderCard();
    await user.click(screen.getByRole('switch', { name: 'Accept public applications' }));
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith('Public applications switched off'),
    );
  });

  it('toasts "Link copied" only after the copy worked', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    renderCard();
    await user.click(screen.getByRole('button', { name: 'Copy the application link' }));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith('Link copied'));
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/apply/req-1`);
  });

  it('says the copy failed when the clipboard refuses', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockRejectedValue(new Error('denied'));
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    renderCard();
    await user.click(screen.getByRole('button', { name: 'Copy the application link' }));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(toastSuccess).not.toHaveBeenCalledWith('Link copied');
  });

  it('says the copy failed when there is no clipboard API', async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    renderCard();
    await user.click(screen.getByRole('button', { name: 'Copy the application link' }));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(toastSuccess).not.toHaveBeenCalled();
  });
});
