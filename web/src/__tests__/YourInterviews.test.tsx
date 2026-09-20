// YourInterviews — PH4-A2. The candidate's own read of their interview
// loops: loading/error/empty states, choosing an offered time (including the
// two 409s the server can now answer), the calendar download, and the load-
// bearing negative: there is NEVER a reschedule or cancel control here
// (A2 #23) — only HR can move a booked session.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { CandidateLoop } from '../api/scheduling';

const api = {
  listMyInterviewLoops: vi.fn(),
  getMySessionSlots: vi.fn(),
  bookMySlot: vi.fn(),
  downloadMyLoopIcs: vi.fn(),
};
vi.mock('../api/scheduling', () => ({
  listMyInterviewLoops: (...a: unknown[]) => api.listMyInterviewLoops(...a) as unknown,
  getMySessionSlots: (...a: unknown[]) => api.getMySessionSlots(...a) as unknown,
  bookMySlot: (...a: unknown[]) => api.bookMySlot(...a) as unknown,
  downloadMyLoopIcs: (...a: unknown[]) => api.downloadMyLoopIcs(...a) as unknown,
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
  },
}));

import YourInterviews from '../components/candidate/YourInterviews';
import i18n from '../lib/i18n';

function loop(over: Partial<CandidateLoop> = {}): CandidateLoop {
  return {
    id: 'loop-1',
    title: 'Interviews',
    job_title: 'Backend Engineer',
    status: 'scheduled',
    timezone: 'Asia/Kolkata',
    self_schedule: false,
    sessions: [
      {
        id: 'sess-1',
        title: 'Panel interview',
        duration_minutes: 45,
        starts_at: '2026-09-25T05:00:00.000Z',
        ends_at: '2026-09-25T05:45:00.000Z',
        location: 'Google Meet',
        status: 'scheduled',
        interviewers: ['Asha Rao'],
      },
    ],
    ...over,
  };
}

function awaitingLoop(): CandidateLoop {
  return loop({
    self_schedule: true,
    sessions: [
      {
        id: 'sess-2',
        title: 'Panel interview',
        duration_minutes: 45,
        starts_at: null,
        ends_at: null,
        location: null,
        status: 'awaiting_slot',
        interviewers: [],
      },
    ],
  });
}

function renderComp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <YourInterviews />
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  vi.clearAllMocks();
  await i18n.changeLanguage('en');
});

describe('YourInterviews — states', () => {
  it('shows a loading state', () => {
    api.listMyInterviewLoops.mockImplementation(() => new Promise(() => undefined));
    renderComp();
    expect(screen.getByText('Loading your interviews…')).toBeInTheDocument();
  });

  it('shows an error, not an empty state, on failure', async () => {
    api.listMyInterviewLoops.mockRejectedValue(new Error('Network down'));
    renderComp();
    expect(await screen.findByText('Network down')).toBeInTheDocument();
    expect(screen.queryByText('No interviews scheduled yet.')).not.toBeInTheDocument();
  });

  it('shows an empty state when there are none', async () => {
    api.listMyInterviewLoops.mockResolvedValue([]);
    renderComp();
    expect(await screen.findByText('No interviews scheduled yet.')).toBeInTheDocument();
  });
});

describe('YourInterviews — a scheduled session', () => {
  it('shows the job title, time, duration, location and interviewers', async () => {
    api.listMyInterviewLoops.mockResolvedValue([loop()]);
    renderComp();
    expect(await screen.findByText('Backend Engineer')).toBeInTheDocument();
    expect(screen.getByText(/45 min/)).toBeInTheDocument();
    expect(screen.getByText(/Google Meet/)).toBeInTheDocument();
    expect(screen.getByText('With Asha Rao')).toBeInTheDocument();
  });

  it('never offers to reschedule or cancel — only HR can move a booked session (A2 #23)', async () => {
    api.listMyInterviewLoops.mockResolvedValue([loop()]);
    renderComp();
    await screen.findByText('Backend Engineer');
    expect(screen.queryByRole('button', { name: /reschedule/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /cancel/i })).not.toBeInTheDocument();
    expect(screen.getByText('To change this time, contact the hiring team.')).toBeInTheDocument();
  });
});

describe('YourInterviews — choosing a time', () => {
  it('groups offered times and lets the candidate pick one', async () => {
    api.listMyInterviewLoops.mockResolvedValue([awaitingLoop()]);
    api.getMySessionSlots.mockResolvedValue(['2026-09-25T05:00:00.000Z', '2026-09-25T06:00:00.000Z']);
    const user = userEvent.setup();
    renderComp();

    await user.click(await screen.findByRole('button', { name: 'Choose a time' }));
    await waitFor(() => expect(api.getMySessionSlots).toHaveBeenCalledWith('loop-1', 'sess-2'));
    expect(await screen.findAllByRole('button', { name: /\d{2}:\d{2}/ })).toHaveLength(2);
  });

  it('books the chosen time with the browser timezone', async () => {
    api.listMyInterviewLoops.mockResolvedValue([awaitingLoop()]);
    api.getMySessionSlots.mockResolvedValue(['2026-09-25T05:00:00.000Z']);
    api.bookMySlot.mockResolvedValue({
      session_id: 'sess-2',
      starts_at: '2026-09-25T05:00:00.000Z',
      all_booked: true,
    });
    const user = userEvent.setup();
    renderComp();

    await user.click(await screen.findByRole('button', { name: 'Choose a time' }));
    await user.click(await screen.findByRole('button', { name: /\d{2}:\d{2}/ }));

    await waitFor(() =>
      expect(api.bookMySlot).toHaveBeenCalledWith('loop-1', {
        session_id: 'sess-2',
        starts_at: '2026-09-25T05:00:00.000Z',
        timezone: expect.any(String) as string,
      }),
    );
    expect(toastSuccess).toHaveBeenCalled();
  });

  it('says so, rather than erroring, once the application is decided and no times remain', async () => {
    api.listMyInterviewLoops.mockResolvedValue([awaitingLoop()]);
    api.getMySessionSlots.mockResolvedValue([]);
    const user = userEvent.setup();
    renderComp();

    await user.click(await screen.findByRole('button', { name: 'Choose a time' }));
    expect(
      await screen.findByText('No times are available for this interview any more.'),
    ).toBeInTheDocument();
  });

  it('surfaces the server’s 409 when a slot is taken or the schedule has closed, and refreshes', async () => {
    api.listMyInterviewLoops.mockResolvedValue([awaitingLoop()]);
    api.getMySessionSlots.mockResolvedValue(['2026-09-25T05:00:00.000Z']);
    api.bookMySlot.mockRejectedValue(new Error('This schedule is not open for choosing times.'));
    const user = userEvent.setup();
    renderComp();

    await user.click(await screen.findByRole('button', { name: 'Choose a time' }));
    await user.click(await screen.findByRole('button', { name: /\d{2}:\d{2}/ }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('This schedule is not open for choosing times.'),
    );
    // Both the offered times and the loop list are refreshed after a refusal.
    await waitFor(() => expect(api.getMySessionSlots).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(api.listMyInterviewLoops).toHaveBeenCalledTimes(2));
  });
});

describe('YourInterviews — calendar download', () => {
  it('downloads the loop’s calendar file', async () => {
    api.listMyInterviewLoops.mockResolvedValue([loop()]);
    api.downloadMyLoopIcs.mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderComp();

    await user.click(await screen.findByRole('button', { name: 'Add to calendar (.ics)' }));
    await waitFor(() => expect(api.downloadMyLoopIcs).toHaveBeenCalledWith('loop-1'));
  });

  it('surfaces a download failure', async () => {
    api.listMyInterviewLoops.mockResolvedValue([loop()]);
    api.downloadMyLoopIcs.mockRejectedValue(new Error('Could not reach the server'));
    const user = userEvent.setup();
    renderComp();

    await user.click(await screen.findByRole('button', { name: 'Add to calendar (.ics)' }));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith('Could not reach the server'));
  });
});
