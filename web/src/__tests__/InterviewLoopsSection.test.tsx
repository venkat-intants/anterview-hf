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

function renderSection(
  requisitionId: string | null = 'req-1',
  opts: { applicationStatus?: string; client?: QueryClient } = {},
) {
  const client =
    opts.client ?? new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <InterviewLoopsSection
        enrolmentId="en-1"
        requisitionId={requisitionId}
        applicationStatus={opts.applicationStatus}
      />
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

describe('InterviewLoopsSection - loading/error/empty', () => {
  it('shows a loading state', () => {
    schedulingApi.listLoopsForEnrolment.mockImplementation(() => new Promise(() => undefined));
    renderSection();
    expect(screen.getByLabelText('Loading the interview schedule')).toHaveAttribute('aria-busy', 'true');
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

describe('InterviewLoopsSection - creating a loop', () => {
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

describe('InterviewLoopsSection - adding a session', () => {
  it('adds a session with the chosen round, interviewer and time', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([loop({ sessions: [] })]);
    schedulingApi.addSession.mockResolvedValue(loop());
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('button', { name: 'Add interview' }));
    await user.selectOptions(screen.getByLabelText('Round'), ROUND_ID);
    await user.click(screen.getByLabelText('Rekha Iyer'));
    await user.type(screen.getByLabelText(/^Start time/), '2099-01-01T10:30');
    await user.click(screen.getByRole('button', { name: 'Add interview' }));

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

    await user.click(await screen.findByRole('button', { name: 'Add interview' }));
    await user.selectOptions(screen.getByLabelText('Round'), ROUND_ID);
    await user.click(screen.getByLabelText('Rekha Iyer'));
    await user.type(screen.getByLabelText(/^Start time/), '2099-01-01T10:30');
    await user.click(screen.getByRole('button', { name: 'Add interview' }));

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

    await user.click(await screen.findByRole('button', { name: 'Add interview' }));
    await user.selectOptions(screen.getByLabelText('Round'), ROUND_ID);
    await user.click(screen.getByLabelText('Rekha Iyer'));
    await user.type(screen.getByLabelText(/^Start time/), '2099-01-01T10:30');
    await user.click(screen.getByRole('button', { name: 'Add interview' }));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('One of the interviewers is already booked at that time.'));
    expect(screen.queryByRole('button', { name: 'Schedule anyway' })).not.toBeInTheDocument();
  });
});

describe('InterviewLoopsSection - a session’s controls', () => {
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
      screen.getByText('Completed or no-show can be recorded once it has started.'),
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
    // Permanent, so the first click only arms it (IV-M7).
    expect(schedulingApi.setSessionOutcome).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Mark completed' }));
    await waitFor(() => expect(schedulingApi.setSessionOutcome).toHaveBeenCalledWith('sess-1', 'completed', null));
  });

  it('lets HR set a time for a session still waiting on the candidate', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([
      loop({
        self_schedule: true,
        sessions: [session({ status: 'awaiting_slot', starts_at: null, ends_at: null })],
      }),
    ]);
    schedulingApi.rescheduleSession.mockResolvedValue({ session_id: 'sess-1', starts_at: 'x' });
    const user = userEvent.setup();
    renderSection();

    // The session pill is in words, not "awaiting slot" (audit X12).
    expect(await screen.findByText('Awaiting slot')).toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: 'Set a time' }));
    const input = screen.getByLabelText<HTMLInputElement>(/^New start time/);
    expect(input.value).toBe('');
    await user.type(input, '2099-01-02T10:00');
    await user.click(screen.getByRole('button', { name: 'Save new time' }));
    await waitFor(() =>
      expect(schedulingApi.rescheduleSession).toHaveBeenCalledWith(
        'sess-1',
        expect.objectContaining({ starts_at: new Date('2099-01-02T10:00').toISOString() }),
      ),
    );
    expect(toastSuccess).toHaveBeenCalledWith('Interview time set');
  });

  it('shows the day of a session, not only its time', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([loop()]);
    renderSection();
    await screen.findByText('Rekha Iyer');
    // 2099-01-01 is a Thursday.
    expect(screen.getByText(/Thu.*1 Jan.*·\s*45 min/)).toBeInTheDocument();
  });

  it('pre-fills reschedule with the session’s LOCAL wall time', async () => {
    const originalTz = process.env.TZ;
    process.env.TZ = 'America/New_York';
    try {
      schedulingApi.listLoopsForEnrolment.mockResolvedValue([loop()]);
      const user = userEvent.setup();
      renderSection();

      await user.click(await screen.findByRole('button', { name: 'Reschedule' }));
      const input = screen.getByLabelText<HTMLInputElement>(/^New start time/);
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

describe('InterviewLoopsSection - loop-level controls', () => {
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
    // Armed: the confirm repeats the action and the way out says Keep, so
    // "Cancel" never means both (audit X5).
    expect(within(footer).getByRole('button', { name: 'Keep' })).toBeInTheDocument();
    await user.click(within(footer).getByRole('button', { name: 'Cancel loop' }));

    await waitFor(() => expect(schedulingApi.cancelLoop).toHaveBeenCalledWith('loop-1', null));
  });
});

// ---------------------------------------------------------------------------
// Audit wave 2 (interviews)
// ---------------------------------------------------------------------------

describe('InterviewLoopsSection - a decided application (IV-M11)', () => {
  it('offers no create, send or move controls once hired, but still records outcomes', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([
      loop({ sessions: [session({ starts_at: '2020-01-01T05:00:00.000Z' })] }),
    ]);
    renderSection('req-1', { applicationStatus: 'hired' });

    await screen.findByText('Rekha Iyer');
    expect(screen.getByText(/a final decision \(hired\) is recorded/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'New loop' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add interview' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /send to candidate/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reschedule' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Mark completed' })).toBeInTheDocument();
  });

  it('keeps every control for an application still in play', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([loop()]);
    renderSection('req-1', { applicationStatus: 'shortlisted' });

    await screen.findByText('Rekha Iyer');
    expect(screen.getByRole('button', { name: 'New loop' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add interview' })).toBeInTheDocument();
  });
});

describe('InterviewLoopsSection - sending again (IV-M8)', () => {
  it('names a repeat send as a resend and confirms it first', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([
      loop({ sent_at: '2026-10-01T10:00:00Z', itinerary_version: 1 }),
    ]);
    schedulingApi.sendLoop.mockResolvedValue({
      loop_id: 'loop-1',
      status: 'scheduled',
      sent: 'itinerary',
      version: 2,
    });
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('button', { name: 'Resend schedule' }));
    expect(schedulingApi.sendLoop).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Resend to candidate' }));
    await waitFor(() => expect(schedulingApi.sendLoop).toHaveBeenCalledWith('loop-1'));
    expect(toastSuccess).toHaveBeenCalledWith('Sent the updated schedule to the candidate');
  });

  it('says the candidate was told when an interview is added to a sent loop', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([
      loop({ sessions: [], sent_at: '2026-10-01T10:00:00Z' }),
    ]);
    schedulingApi.addSession.mockResolvedValue({ session_id: 's', status: 'scheduled' });
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('button', { name: 'Add interview' }));
    await user.selectOptions(screen.getByLabelText('Round'), ROUND_ID);
    await user.click(screen.getByLabelText('Rekha Iyer'));
    await user.type(screen.getByLabelText(/^Start time/), '2099-01-01T10:30');
    await user.click(screen.getByRole('button', { name: 'Add interview' }));

    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith(
        'Interview added and the candidate was sent the update',
      ),
    );
  });
});

describe('InterviewLoopsSection - cancelling and outcomes (IV-M6, IV-M7)', () => {
  it('asks for the reason inside the cancel step and warns when the loop closes too', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([loop()]);
    schedulingApi.setSessionOutcome.mockResolvedValue({
      session_id: 'sess-1',
      status: 'cancelled',
      loop_status: 'cancelled',
    });
    const user = userEvent.setup();
    renderSection();

    await screen.findByText('Rekha Iyer');
    // No free-standing reason box next to Mark completed / no-show any more.
    expect(screen.queryByLabelText(/cancel reason for panel interview/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /cancel interview/i }));

    const panel = screen.getByRole('group', { name: 'Cancel Panel interview' });
    expect(within(panel).getByText(/the loop closes too/i)).toBeInTheDocument();
    await user.type(within(panel).getByLabelText(/reason/i), 'Interviewer ill');
    await user.click(within(panel).getByRole('button', { name: 'Cancel interview' }));

    await waitFor(() =>
      expect(schedulingApi.setSessionOutcome).toHaveBeenCalledWith(
        'sess-1',
        'cancelled',
        'Interviewer ill',
      ),
    );
    expect(toastSuccess).toHaveBeenCalledWith('Interview cancelled. The loop is now cancelled.');
  });

  it('backs out of the cancel step with Keep, sending nothing', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([loop()]);
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('button', { name: /cancel interview/i }));
    await user.click(screen.getByRole('button', { name: 'Keep' }));
    expect(screen.queryByRole('group', { name: 'Cancel Panel interview' })).not.toBeInTheDocument();
    expect(schedulingApi.setSessionOutcome).not.toHaveBeenCalled();
  });

  it('confirms a no-show before recording it', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([
      loop({ sessions: [session({ starts_at: '2020-01-01T05:00:00.000Z' })] }),
    ]);
    schedulingApi.setSessionOutcome.mockResolvedValue({
      session_id: 'sess-1',
      status: 'no_show',
      loop_status: 'completed',
    });
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('button', { name: 'Mark no-show' }));
    expect(schedulingApi.setSessionOutcome).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Mark no-show' }));
    await waitFor(() =>
      expect(schedulingApi.setSessionOutcome).toHaveBeenCalledWith('sess-1', 'no_show', null),
    );
    expect(toastSuccess).toHaveBeenCalledWith('Marked as no-show. The loop is now completed.');
  });

  it('keeps a cancelled interview listed, with no controls', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([
      loop({
        sessions: [
          session(),
          session({ id: 'sess-2', title: 'Culture chat', status: 'cancelled', interviewers: [] }),
        ],
      }),
    ]);
    renderSection();

    await screen.findByText('Rekha Iyer');
    expect(screen.getByText('Cancelled')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /cancel interview/i })).toHaveLength(1);
  });
});

describe('InterviewLoopsSection - form limits (IV-M2, IV-L10)', () => {
  it('caps the interviewers at six', async () => {
    listInterviewers.mockResolvedValue(
      Array.from({ length: 7 }, (_, i) => ({
        user_id: `iv-${i}`,
        full_name: `Interviewer ${i}`,
        email: `iv${i}@x.test`,
        role: 'interviewer',
      })),
    );
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([loop({ sessions: [] })]);
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('button', { name: 'Add interview' }));
    for (let i = 0; i < 6; i += 1) {
      await user.click(await screen.findByLabelText(`Interviewer ${i}`));
    }
    expect(screen.getByLabelText('Interviewer 6')).toBeDisabled();
    expect(screen.getByText(/at most 6 interviewers/i)).toBeInTheDocument();
  });

  it('explains a cleared duration instead of sending 0', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([loop({ sessions: [] })]);
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('button', { name: 'Add interview' }));
    await user.selectOptions(screen.getByLabelText('Round'), ROUND_ID);
    await user.click(screen.getByLabelText('Rekha Iyer'));
    await user.type(screen.getByLabelText(/^Start time/), '2099-01-01T10:30');
    await user.clear(screen.getByLabelText('Duration (minutes)'));

    expect(screen.getByText('Duration must be 15 to 480 minutes.')).toBeInTheDocument();
    const submit = screen
      .getAllByRole('button', { name: 'Add interview' })
      .find((b) => b.textContent === 'Add interview' && b.getAttribute('aria-expanded') === null);
    expect(submit).toBeDisabled();
  });

  it('shows a failed interviewer list as an error, not "no interviewers"', async () => {
    listInterviewers.mockRejectedValue(new Error('boom'));
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([loop({ sessions: [] })]);
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('button', { name: 'Add interview' }));
    expect(await screen.findByText('Could not load interviewers.')).toBeInTheDocument();
    expect(screen.queryByText('No interviewers set up yet.')).not.toBeInTheDocument();
  });

  it('caps the loop cancel reason at the server limit', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([loop()]);
    renderSection();

    expect(await screen.findByLabelText('Cancel reason for Interviews')).toHaveAttribute(
      'maxLength',
      '500',
    );
  });
});

describe('InterviewLoopsSection - times and zones (IV-M5, IV-L7)', () => {
  it("names the zone HR types in and previews the candidate's time", async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([
      loop({ sessions: [], candidate_timezone: 'Pacific/Auckland' }),
    ]);
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('button', { name: 'Add interview' }));
    const input = screen.getByLabelText(/^Start time \(your time, /);
    await user.type(input, '2099-01-01T10:30');
    expect(screen.getByText(/^For the candidate: /)).toBeInTheDocument();
  });

  it('says how many free times there are and shows more on request', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([loop()]);
    schedulingApi.getSessionSlots.mockResolvedValue(
      Array.from({ length: 45 }, (_, i) =>
        new Date(Date.UTC(2099, 0, 2, 4, 0) + i * 15 * 60_000).toISOString(),
      ),
    );
    const user = userEvent.setup();
    renderSection();

    await user.click(await screen.findByRole('button', { name: 'Reschedule' }));
    await user.click(screen.getByRole('button', { name: 'Show free times' }));
    expect(await screen.findByText(/Showing 30 of 45/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Show more free times' }));
    expect(screen.getByText(/Showing 45 of 45/)).toBeInTheDocument();
  });
});

describe('InterviewLoopsSection - calendar, heading and refresh (IV-L8, IV-M9)', () => {
  it('offers no calendar file for a loop with no timed interview', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([
      loop({ status: 'draft', sessions: [] }),
    ]);
    renderSection();

    await screen.findByText('No interviews in this loop yet.');
    expect(screen.queryByRole('button', { name: /download calendar/i })).not.toBeInTheDocument();
  });

  it('is headed "Interview schedule"', async () => {
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([]);
    renderSection();
    expect(await screen.findByRole('button', { name: /interview schedule/i })).toBeInTheDocument();
  });

  it('refreshes the whole HR console after a change, not only the loop list', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const spy = vi.spyOn(client, 'invalidateQueries');
    schedulingApi.listLoopsForEnrolment.mockResolvedValue([loop()]);
    schedulingApi.sendLoop.mockResolvedValue({
      loop_id: 'loop-1',
      status: 'scheduled',
      sent: 'itinerary',
      version: 1,
    });
    const user = userEvent.setup();
    renderSection('req-1', { client });

    await user.click(await screen.findByRole('button', { name: /send to candidate/i }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ queryKey: ['hr'] }));
  });
});
