// AvailabilityTab (panel) — PH4-A2/O5. HR reads and edits ANY interviewer's
// availability, scoped by a chosen interviewer, with loading/error/empty
// states and add/remove.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const schedulingApi = {
  getInterviewerAvailability: vi.fn(),
  addInterviewerAvailability: vi.fn(),
  removeInterviewerAvailability: vi.fn(),
};
vi.mock('../api/scheduling', () => ({
  getInterviewerAvailability: (...a: unknown[]) => schedulingApi.getInterviewerAvailability(...a) as unknown,
  addInterviewerAvailability: (...a: unknown[]) => schedulingApi.addInterviewerAvailability(...a) as unknown,
  removeInterviewerAvailability: (...a: unknown[]) => schedulingApi.removeInterviewerAvailability(...a) as unknown,
}));

const listInterviewers = vi.fn();
vi.mock('../api/scorecards', () => ({
  listInterviewers: (...a: unknown[]) => listInterviewers(...a) as unknown,
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
  },
}));

import AvailabilityTab from '../components/panel/AvailabilityTab';

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AvailabilityTab />
    </QueryClientProvider>,
  );
}

/** The <select> renders before its options do (they arrive with the
 *  interviewers query), so wait on an OPTION before selecting it. */
async function chooseInterviewer(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await screen.findByRole('option', { name: 'Rekha Iyer' });
  await user.selectOptions(screen.getByLabelText('Interviewer'), 'iv-1');
}

beforeEach(() => {
  vi.clearAllMocks();
  listInterviewers.mockResolvedValue([
    { user_id: 'iv-1', full_name: 'Rekha Iyer', email: 'r@x.com', role: 'interviewer' },
  ]);
});

describe('AvailabilityTab (panel) — choosing an interviewer', () => {
  it('asks for an interviewer before showing anything else', async () => {
    renderTab();
    expect(
      await screen.findByText('Choose an interviewer to see and edit their availability.'),
    ).toBeInTheDocument();
    expect(schedulingApi.getInterviewerAvailability).not.toHaveBeenCalled();
  });

  it('loads that interviewer’s windows once chosen', async () => {
    schedulingApi.getInterviewerAvailability.mockResolvedValue([]);
    const user = userEvent.setup();
    renderTab();

    await chooseInterviewer(user);
    await waitFor(() => expect(schedulingApi.getInterviewerAvailability).toHaveBeenCalledWith('iv-1'));
    expect(await screen.findByText('No windows set yet.')).toBeInTheDocument();
  });

  it('shows an error state', async () => {
    schedulingApi.getInterviewerAvailability.mockRejectedValue(new Error('boom'));
    const user = userEvent.setup();
    renderTab();

    await chooseInterviewer(user);
    expect(await screen.findByText('boom')).toBeInTheDocument();
  });

  it('lists existing windows', async () => {
    schedulingApi.getInterviewerAvailability.mockResolvedValue([
      { id: 'win-1', starts_at: '2026-09-22T04:00:00.000Z', ends_at: '2026-09-22T10:00:00.000Z' },
    ]);
    const user = userEvent.setup();
    renderTab();

    await chooseInterviewer(user);
    expect(await screen.findByRole('button', { name: 'Remove this window' })).toBeInTheDocument();
  });
});

describe('AvailabilityTab (panel) — adding and removing', () => {
  it('adds a window for the chosen interviewer', async () => {
    schedulingApi.getInterviewerAvailability.mockResolvedValue([]);
    schedulingApi.addInterviewerAvailability.mockResolvedValue({
      id: 'win-1',
      starts_at: '2026-09-22T10:00:00.000Z',
      ends_at: '2026-09-22T16:00:00.000Z',
    });
    const user = userEvent.setup();
    renderTab();

    await chooseInterviewer(user);
    await screen.findByText('No windows set yet.');
    await user.type(screen.getByLabelText('From'), '2026-09-22T10:00');
    await user.type(screen.getByLabelText('To'), '2026-09-22T16:00');
    await user.click(screen.getByRole('button', { name: 'Add window for Rekha Iyer' }));

    await waitFor(() => expect(schedulingApi.addInterviewerAvailability).toHaveBeenCalledWith('iv-1', expect.any(Object)));
    expect(toastSuccess).toHaveBeenCalledWith('Availability added');
  });

  it('removes a window on request', async () => {
    schedulingApi.getInterviewerAvailability.mockResolvedValue([
      { id: 'win-1', starts_at: '2026-09-22T04:00:00.000Z', ends_at: '2026-09-22T10:00:00.000Z' },
    ]);
    schedulingApi.removeInterviewerAvailability.mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderTab();

    await chooseInterviewer(user);
    await user.click(await screen.findByRole('button', { name: 'Remove this window' }));

    await waitFor(() => expect(schedulingApi.removeInterviewerAvailability).toHaveBeenCalledWith('win-1'));
    expect(toastSuccess).toHaveBeenCalledWith('Removed');
  });

  it('surfaces a failed add rather than pretending it worked', async () => {
    schedulingApi.getInterviewerAvailability.mockResolvedValue([]);
    schedulingApi.addInterviewerAvailability.mockRejectedValue(new Error('That overlaps a window already set.'));
    const user = userEvent.setup();
    renderTab();

    await chooseInterviewer(user);
    await screen.findByText('No windows set yet.');
    await user.type(screen.getByLabelText('From'), '2026-09-22T10:00');
    await user.type(screen.getByLabelText('To'), '2026-09-22T16:00');
    await user.click(screen.getByRole('button', { name: 'Add window for Rekha Iyer' }));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('That overlaps a window already set.'));
  });
});
