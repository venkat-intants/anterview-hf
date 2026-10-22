// WorkloadTab — PH4-O5. Loading/error/empty, every flag said in words (never
// colour alone), and the inline capacity edit (including "blank = company
// default").

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { WorkloadResponse } from '../api/scheduling';

const schedulingApi = {
  getWorkload: vi.fn(),
  setInterviewerCapacity: vi.fn(),
};
vi.mock('../api/scheduling', () => ({
  getWorkload: (...a: unknown[]) => schedulingApi.getWorkload(...a) as unknown,
  setInterviewerCapacity: (...a: unknown[]) => schedulingApi.setInterviewerCapacity(...a) as unknown,
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
  },
}));

import WorkloadTab from '../components/panel/WorkloadTab';

function workload(over: Partial<WorkloadResponse> = {}): WorkloadResponse {
  return {
    start: '2026-09-21T00:00:00.000Z',
    end: '2026-09-28T00:00:00.000Z',
    timezone: 'Asia/Kolkata',
    defaults: { max_per_day: 4, max_per_week: 15 },
    interviewers: [
      {
        user_id: 'iv-1',
        name: 'Rekha Iyer',
        role: 'interviewer',
        sessions: 6,
        hours: 4.5,
        loops: 3,
        by_day: {},
        open_scorecards: 2,
        overdue_scorecards: 1,
        max_per_day: 4,
        max_per_week: 15,
        custom_max_per_day: null,
        custom_max_per_week: null,
        over_allocated_days: ['2026-09-21'],
        over_allocated_weeks: [],
        outside_availability: 2,
        conflicts: 0,
        flags: ['over_allocated', 'outside_availability', 'overdue_scorecards'],
      },
    ],
    ...over,
  };
}

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <WorkloadTab />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe('WorkloadTab - states', () => {
  it('shows a loading state', () => {
    schedulingApi.getWorkload.mockImplementation(() => new Promise(() => undefined));
    renderTab();
    expect(screen.getByText('Loading…')).toBeInTheDocument();
  });

  it('shows an error state', async () => {
    schedulingApi.getWorkload.mockRejectedValue(new Error('boom'));
    renderTab();
    expect(await screen.findByText('Could not load the panel workload.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('shows an empty state', async () => {
    schedulingApi.getWorkload.mockResolvedValue(workload({ interviewers: [] }));
    renderTab();
    expect(await screen.findByText('No one can be booked for interviews yet. Your super admin adds interviewers under Team.')).toBeInTheDocument();
  });
});

describe('WorkloadTab - flags in words', () => {
  it('states every flag as a sentence, never colour alone', async () => {
    schedulingApi.getWorkload.mockResolvedValue(workload());
    renderTab();

    // "Sept", not "Sep": that's this runtime's en-IN short-month form
    // (Intl.DateTimeFormat), not a typo — isoDateLabel is a thin wrapper
    // around it, so the test asserts what it actually produces.
    expect(await screen.findByText('Over the daily limit on 21 Sept')).toBeInTheDocument();
    expect(screen.getByText('2 interviews outside their availability')).toBeInTheDocument();
    expect(screen.getByText('1 overdue scorecard')).toBeInTheDocument();
    // The colour-coded badge still carries a count in words alongside it.
    expect(screen.getByText('3 flags')).toBeInTheDocument();
  });

  it('says so in words when nothing is flagged', async () => {
    schedulingApi.getWorkload.mockResolvedValue(
      workload({
        interviewers: [
          {
            ...workload().interviewers[0],
            over_allocated_days: [],
            outside_availability: 0,
            overdue_scorecards: 0,
            conflicts: 0,
            flags: [],
          },
        ],
      }),
    );
    renderTab();
    expect(await screen.findByText('No flags this period.')).toBeInTheDocument();
  });

  it('switches period presets', async () => {
    schedulingApi.getWorkload.mockResolvedValue(workload());
    const user = userEvent.setup();
    renderTab();

    await screen.findByText('Rekha Iyer');
    expect(schedulingApi.getWorkload).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', { name: 'Next 30 days' }));
    await waitFor(() => expect(schedulingApi.getWorkload).toHaveBeenCalledTimes(2));
  });
});

describe('WorkloadTab - capacity edit', () => {
  it('saves a new daily/weekly limit', async () => {
    schedulingApi.getWorkload.mockResolvedValue(workload());
    schedulingApi.setInterviewerCapacity.mockResolvedValue({
      user_id: 'iv-1',
      max_sessions_per_day: 6,
      max_sessions_per_week: 15,
    });
    const user = userEvent.setup();
    renderTab();

    const dayInput = await screen.findByLabelText('Daily limit for Rekha Iyer');
    await user.clear(dayInput);
    await user.type(dayInput, '6');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    // The weekly field was blank (the default) and stays blank: saving the
    // daily limit does not pin the weekly default as an override (IV-M13).
    await waitFor(() =>
      expect(schedulingApi.setInterviewerCapacity).toHaveBeenCalledWith('iv-1', {
        max_sessions_per_day: 6,
        max_sessions_per_week: null,
      }),
    );
    expect(toastSuccess).toHaveBeenCalled();
  });

  it('resets an override to the company default', async () => {
    schedulingApi.getWorkload.mockResolvedValue(
      workload({
        interviewers: [
          { ...workload().interviewers[0], max_per_day: 2, custom_max_per_day: 2 },
        ],
      }),
    );
    schedulingApi.setInterviewerCapacity.mockResolvedValue({
      user_id: 'iv-1',
      max_sessions_per_day: null,
      max_sessions_per_week: null,
    });
    const user = userEvent.setup();
    renderTab();

    await screen.findByText('Rekha Iyer');
    expect(screen.getByText(/Custom limits: daily 2, weekly 15/)).toBeInTheDocument();
    expect(screen.getByLabelText<HTMLInputElement>('Daily limit for Rekha Iyer').value).toBe('2');
    await user.click(screen.getByRole('button', { name: 'Reset to company default' }));

    await waitFor(() =>
      expect(schedulingApi.setInterviewerCapacity).toHaveBeenCalledWith('iv-1', {
        max_sessions_per_day: null,
        max_sessions_per_week: null,
      }),
    );
  });
});

describe('WorkloadTab - default versus override (IV-M13)', () => {
  it('leaves the fields blank when the company default applies, and Save idle', async () => {
    schedulingApi.getWorkload.mockResolvedValue(workload());
    renderTab();

    const day = await screen.findByLabelText<HTMLInputElement>('Daily limit for Rekha Iyer');
    expect(day.value).toBe('');
    expect(day).toHaveAttribute('placeholder', 'Default (4)');
    expect(screen.getByText(/Using the company default/)).toBeInTheDocument();
    // Nothing changed, so there is nothing to save (and no default to pin).
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Reset to company default' })).not.toBeInTheDocument();
  });

  it('explains a limit out of range beside the field instead of sending it', async () => {
    schedulingApi.getWorkload.mockResolvedValue(workload());
    const user = userEvent.setup();
    renderTab();

    await user.type(await screen.findByLabelText('Weekly limit for Rekha Iyer'), '0');
    expect(screen.getByText(/Weekly limit must be a whole number from 1 to 100/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });
});

describe('WorkloadTab - period and labels (IV-L11, IV-L14)', () => {
  it('groups the period buttons as pressed toggles, not a tablist', async () => {
    schedulingApi.getWorkload.mockResolvedValue(workload());
    renderTab();

    await screen.findByText('Rekha Iyer');
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Period' })).toBeInTheDocument();
    expect(screen.getByText('Open scorecards (now)')).toBeInTheDocument();
    expect(screen.getByText(/counted in India time \(IST\)/)).toBeInTheDocument();
  });
});
