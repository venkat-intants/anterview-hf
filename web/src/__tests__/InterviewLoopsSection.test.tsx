// InterviewLoopsSection — PH4-A2. HR's view of an application's interview
// loops: loading/error/empty states, creating a loop, adding a session
// (including the "Schedule anyway" retry on an availability 409), and that a
// session cannot be marked completed/no-show before it has started.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ApiError } from '../api/client';
import type { InterviewLoop, InterviewSession } from '../api/scheduling';

const schedulingApi = {
  listLoopsForEnrolment: vi.fn(),
  createLoop: vi.fn(),
  addSession: vi.fn(),
  sendLoop: vi.fn(),
  cancelLoop: vi.fn(),
  downloadHrLoopIcs: vi.fn(),
  rescheduleSession: vi.fn(),
  setSessionOutcome: vi.fn(),
  getSessionSlots: vi.fn(),
};
vi.mock('../api/scheduling', () => ({
  listLoopsForEnrolment: (...a: unknown[]) => schedulingApi.listLoopsForEnrolment(...a) as unknown,
  createLoop: (...a: unknown[]) => schedulingApi.createLoop(...a) as unknown,
  addSession: (...a: unknown[]) => schedulingApi.addSession(...a) as unknown,
  sendLoop: (...a: unknown[]) => schedulingApi.sendLoop(...a) as unknown,
  cancelLoop: (...a: unknown[]) => schedulingApi.cancelLoop(...a) as unknown,
  downloadHrLoopIcs: (...a: unknown[]) => schedulingApi.downloadHrLoopIcs(...a) as unknown,
  rescheduleSession: (...a: unknown[]) => schedulingApi.rescheduleSession(...a) as unknown,
  setSessionOutcome: (...a: unknown[]) => schedulingApi.setSessionOutcome(...a) as unknown,
  getSessionSlots: (...a: unknown[]) => schedulingApi.getSessionSlots(...a) as unknown,
}));

const listInterviewers = vi.fn();
vi.mock('../api/scorecards', () => ({
  listInterviewers: (...a: unknown[]) => listInterviewers(...a) as unknown,
}));

const workflowsApi = { listWorkflows: vi.fn(), getWorkflow: vi.fn() };
vi.mock('../api/workflows', () => ({
  listWorkflows: (...a: unknown[]) => workflowsApi.listWorkflows(...a) as unknown,
  getWorkflow: (...a: unknown[]) => workflowsApi.getWorkflow(...a) as unknown,
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
  },
}));

import InterviewLoopsSection from '../components/InterviewLoopsSection';

const ROUND_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
const IV_ID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';

function session(over: Partial<InterviewSession> = {}): InterviewSession {
  return {
    id: 'sess-1',
    round_id: ROUND_ID,
    round_title: 'Panel interview',
    title: 'Panel interview',
    position: 0,
    duration_minutes: 45,
    starts_at: '2099-01-01T05:00:00.000Z',
    ends_at: '2099-01-01T05:45:00.000Z',
    location: 'Google Meet',
    status: 'scheduled',
    booked_by: 'hr',
    interviewers: [{ user_id: IV_ID, name: 'Rekha Iyer', scorecard_id: 'sc-1', scorecard_status: 'assigned' }],
    ...over,
  };
}

function loop(over: Partial<InterviewLoop> = {}): InterviewLoop {
  return {
    id: 'loop-1',
    enrolment_id: 'en-1',
    title: 'Interviews',
    status: 'scheduled',
    candidate_timezone: 'Asia/Kolkata',
    buffer_minutes: 15,
    self_schedule: false,
    sent_at: null,
    itinerary_version: 0,
    cancelled_at: null,
    sessions: [session()],
    ...over,
  };
}

function renderSection(requisitionId: string | null = 'req-1') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <InterviewLoopsSection enrolmentId="en-1" requisitionId={requisitionId} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listInterviewers.mockResolvedValue([{ user_id: IV_ID, full_name: 'Rekha Iyer', email: 'r@x.com', role: 'interviewer' }]);
  workflowsApi.listWorkflows.mockResolvedValue([{ id: 'wf-1', status: 'published', rounds: 1 }]);
  workflowsApi.getWorkflow.mockResolvedValue({
    id: 'wf-1',
    rounds: [{ id: ROUND_ID, title: 'Panel interview', kind: 'human_review' }],
  });
});

describe('InterviewLoopsSection — loading/error/empty', () => {
  it('shows a loading state', () => {
    schedulingApi.listLoopsForEnrolment.mockImplementation(() => new Promise(() => undefined));
    renderSection();
    expect(screen.getByText('Loading…')).toBeInTheDocument();
  });

  it('shows an error state on failure', async () => {
    schedulingApi.listLoopsForEnrolment.mockRejectedValue(new Error('boom'));
    renderSection();
    expect(
      await screen.findByText("Could not load this application's interview schedule."),
    ).toBeInTheDocument();
  });

  it('shows an empty state when there is no loop yet', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([]);
    renderSection();
    expect(await screen.findByText('No interview loop yet for this application.')).toBeInTheDocument();
  });
});

describe('InterviewLoopsSection — creating a loop', () => {
  it('creates a loop with the chosen title and settings', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([]);
    schedulingApi.createLoop.mockResolvedValue(loop());
    const user = userEvent.setup();
    renderSection();

    await screen.findByText('No interview loop yet for this application.');
    await user.click(screen.getByRole('button', { name: 'New loop' }));
    await user.clear(screen.getByLabelText('Title'));
    await user.type(screen.getByLabelText('Title'), 'Onsite loop');
    await user.click(screen.getByRole('button', { name: 'Create loop' }));

    await waitFor(() =>
      expect(schedulingApi.createLoop).toHaveBeenCalledWith(
        'en-1',
        expect.objectContaining({ title: 'Onsite loop', candidate_timezone: 'Asia/Kolkata' }),
      ),
    );
    expect(toastSuccess).toHaveBeenCalledWith('Interview loop created');
  });
});

describe('InterviewLoopsSection — adding a session', () => {
  it('adds a session with the chosen round, interviewer and time', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([loop({ sessions: [] })]);
    schedulingApi.addSession.mockResolvedValue(loop());
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('button', { name: 'Add session' }));
    await user.selectOptions(screen.getByLabelText('Round'), ROUND_ID);
    await user.click(screen.getByLabelText('Rekha Iyer'));
    await user.type(screen.getByLabelText('Start time'), '2099-01-01T10:30');
    await user.click(screen.getByRole('button', { name: 'Add session' }));

    await waitFor(() =>
      expect(schedulingApi.addSession).toHaveBeenCalledWith(
        'loop-1',
        expect.objectContaining({
          round_id: ROUND_ID,
          interviewer_user_ids: [IV_ID],
          allow_outside_availability: false,
        }),
      ),
    );
  });

  it('offers "Schedule anyway" on an availability 409, and resends allowing it', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([loop({ sessions: [] })]);
    schedulingApi.addSession
      .mockRejectedValueOnce(
        new ApiError(
          '1 of the interviewers has not marked that time as available. Choose a time inside their availability, or schedule it anyway.',
          409,
        ),
      )
      .mockResolvedValueOnce(loop());
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('button', { name: 'Add session' }));
    await user.selectOptions(screen.getByLabelText('Round'), ROUND_ID);
    await user.click(screen.getByLabelText('Rekha Iyer'));
    await user.type(screen.getByLabelText('Start time'), '2099-01-01T10:30');
    await user.click(screen.getByRole('button', { name: 'Add session' }));

    expect(await screen.findByText(/has not marked that time as available/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Schedule anyway' }));

    await waitFor(() =>
      expect(schedulingApi.addSession).toHaveBeenLastCalledWith(
        'loop-1',
        expect.objectContaining({ allow_outside_availability: true }),
      ),
    );
  });

  it('shows every other 409 as-is, with no retry offered', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([loop({ sessions: [] })]);
    schedulingApi.addSession.mockRejectedValue(
      new ApiError('One of the interviewers is already booked at that time.', 409),
    );
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('button', { name: 'Add session' }));
    await user.selectOptions(screen.getByLabelText('Round'), ROUND_ID);
    await user.click(screen.getByLabelText('Rekha Iyer'));
    await user.type(screen.getByLabelText('Start time'), '2099-01-01T10:30');
    await user.click(screen.getByRole('button', { name: 'Add session' }));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('One of the interviewers is already booked at that time.'));
    expect(screen.queryByRole('button', { name: 'Schedule anyway' })).not.toBeInTheDocument();
  });
});

describe('InterviewLoopsSection — a session’s controls', () => {
  it('hides completed/no-show before the session has started', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([
      loop({ sessions: [session({ starts_at: '2099-01-01T05:00:00.000Z' })] }),
    ]);
    renderSection();

    // Waits for the session row to render. Not the title/round-title text
    // itself: RTL's default text matcher only concatenates an element's
    // OWN direct text-node children, and the round title sits inside a
    // nested <span> — a real span in the markup, not a test bug.
    await screen.findByText('Rekha Iyer');
    expect(screen.queryByRole('button', { name: 'Mark completed' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Mark no-show' })).not.toBeInTheDocument();
    expect(
      screen.getByText('Completed/no-show can be recorded once it has started.'),
    ).toBeInTheDocument();
  });

  it('offers completed/no-show once the session has started', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([
      loop({ sessions: [session({ starts_at: '2020-01-01T05:00:00.000Z' })] }),
    ]);
    schedulingApi.setSessionOutcome.mockResolvedValue({
      session_id: 'sess-1',
      status: 'completed',
      loop_status: 'scheduled',
    });
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('button', { name: 'Mark completed' }));
    await waitFor(() => expect(schedulingApi.setSessionOutcome).toHaveBeenCalledWith('sess-1', 'completed', null));
  });

  it('pre-fills reschedule with the session’s LOCAL wall time', async () => {
    const originalTz = process.env.TZ;
    process.env.TZ = 'America/New_York';
    try {
      schedulingApi.listLoopsForEnrolment.mockResolvedValue([loop()]);
      const user = userEvent.setup();
      renderSection();

      await user.click(await screen.findByRole('button', { name: 'Reschedule' }));
      const input = screen.getByLabelText<HTMLInputElement>('New start time');
      const { toLocalInputValue } = await import('../lib/localDatetime');
      expect(input.value).toBe(toLocalInputValue('2099-01-01T05:00:00.000Z'));
    } finally {
      // See HRInterviews.test.tsx's identical guard: assigning `undefined`
      // sets the literal string "undefined" rather than unsetting TZ, which
      // broke every later Intl.DateTimeFormat call in this file.
      if (originalTz === undefined) delete process.env.TZ;
      else process.env.TZ = originalTz;
    }
  });
});

describe('InterviewLoopsSection — loop-level controls', () => {
  it('sends the loop to the candidate', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([loop()]);
    schedulingApi.sendLoop.mockResolvedValue({ loop_id: 'loop-1', status: 'scheduled', sent: 'itinerary', version: 1 });
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('button', { name: /send to candidate/i }));
    await waitFor(() => expect(schedulingApi.sendLoop).toHaveBeenCalledWith('loop-1'));
    expect(toastSuccess).toHaveBeenCalledWith('Sent the schedule to the candidate');
  });

  it('downloads the loop’s calendar file', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([loop()]);
    schedulingApi.downloadHrLoopIcs.mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('button', { name: /download calendar/i }));
    await waitFor(() => expect(schedulingApi.downloadHrLoopIcs).toHaveBeenCalledWith('loop-1'));
  });

  it('cancels the loop after the two-step confirm', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([loop()]);
    schedulingApi.cancelLoop.mockResolvedValue({ loop_id: 'loop-1', status: 'cancelled', sessions_cancelled: 1 });
    const user = userEvent.setup();
    renderSection();

    const footer = (await screen.findByRole('button', { name: 'Cancel loop' })).closest('div') as HTMLElement;
    await user.click(within(footer).getByRole('button', { name: 'Cancel loop' }));
    await user.click(within(footer).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(schedulingApi.cancelLoop).toHaveBeenCalledWith('loop-1', null));
  });
});
