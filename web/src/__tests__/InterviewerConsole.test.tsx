// InterviewerConsole (/interviewer) — D4-1. An interviewer's own queue,
// scoped entirely server-side. What matters here:
//   • state badges read clearly, especially LATE;
//   • each row links to ITS OWN scorecard, not a shared one;
//   • empty/loading/error states are distinct, not one blank screen.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { InterviewerAssignment } from '../api/interviewer';
import type { AvailabilityWindow, InterviewerSession } from '../api/scheduling';

const listAssignments = vi.fn();
vi.mock('../api/interviewer', () => ({
  listAssignments: (...a: unknown[]) => listAssignments(...a) as unknown,
}));

// PH4-A2/O5 — "Upcoming interviews" and "My availability" always mount below
// the assignment list.
const schedulingApi = {
  getMySessions: vi.fn(),
  getMyAvailability: vi.fn(),
  addMyAvailability: vi.fn(),
  removeMyAvailability: vi.fn(),
};
vi.mock('../api/scheduling', () => ({
  getMySessions: (...a: unknown[]) => schedulingApi.getMySessions(...a) as unknown,
  getMyAvailability: (...a: unknown[]) => schedulingApi.getMyAvailability(...a) as unknown,
  addMyAvailability: (...a: unknown[]) => schedulingApi.addMyAvailability(...a) as unknown,
  removeMyAvailability: (...a: unknown[]) => schedulingApi.removeMyAvailability(...a) as unknown,
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
    info: vi.fn(),
    warning: vi.fn(),
  },
}));

import InterviewerConsole from '../pages/interviewer/InterviewerConsole';

function renderConsole() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <InterviewerConsole />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const LATE: InterviewerAssignment = {
  scorecard_id: 'sc-late',
  state: 'late',
  status: 'in_progress',
  due_at: '2026-09-01T10:00:00.000Z',
  submitted_at: null,
  candidate_name: 'Asha Rao',
  job_title: 'Backend Engineer',
  round_title: 'Panel interview',
  is_correction: false,
};

const ASSIGNED: InterviewerAssignment = {
  scorecard_id: 'sc-assigned',
  state: 'assigned',
  status: 'assigned',
  due_at: '2026-09-20T10:00:00.000Z',
  submitted_at: null,
  candidate_name: 'Bhavya Nair',
  job_title: 'Frontend Engineer',
  round_title: 'System design',
  is_correction: false,
};

const SUBMITTED: InterviewerAssignment = {
  scorecard_id: 'sc-submitted',
  state: 'submitted',
  status: 'submitted',
  due_at: null,
  submitted_at: '2026-09-05T10:00:00.000Z',
  candidate_name: 'Chetan Iyer',
  job_title: 'SRE',
  round_title: 'Panel interview',
  is_correction: true,
};

beforeEach(() => {
  vi.clearAllMocks();
  schedulingApi.getMySessions.mockResolvedValue([]);
  schedulingApi.getMyAvailability.mockResolvedValue([]);
});

describe('InterviewerConsole - listing assignments', () => {
  it('shows each assignment with its state, candidate, role and round', async () => {
    listAssignments.mockResolvedValue([LATE, ASSIGNED, SUBMITTED]);
    renderConsole();

    expect(await screen.findByText('Asha Rao')).toBeInTheDocument();
    expect(screen.getByText('Bhavya Nair')).toBeInTheDocument();
    expect(screen.getByText('Chetan Iyer')).toBeInTheDocument();
    expect(screen.getByText(/Backend Engineer/)).toBeInTheDocument();
    // Both Asha's and Chetan's rows are on Panel interview — two is correct.
    expect(screen.getAllByText(/Panel interview/).length).toBe(2);
    expect(screen.getByText(/System design/)).toBeInTheDocument();
  });

  it('marks a late assignment clearly, distinct from merely assigned or submitted', async () => {
    listAssignments.mockResolvedValue([LATE, ASSIGNED, SUBMITTED]);
    renderConsole();

    await screen.findByText('Asha Rao');
    expect(screen.getByText('Late')).toBeInTheDocument();
    expect(screen.getByText('Assigned')).toBeInTheDocument();
    expect(screen.getByText('Submitted')).toBeInTheDocument();
  });

  it('flags a correction round distinctly from a first pass', async () => {
    listAssignments.mockResolvedValue([SUBMITTED]);
    renderConsole();

    expect(await screen.findByText('Correction')).toBeInTheDocument();
  });

  it('links each row to its own scorecard, not a shared one', async () => {
    listAssignments.mockResolvedValue([LATE, ASSIGNED]);
    renderConsole();

    await screen.findByText('Asha Rao');
    const ashaLink = screen.getByRole('link', { name: /Asha Rao/i });
    const bhavyaLink = screen.getByRole('link', { name: /Bhavya Nair/i });
    expect(ashaLink).toHaveAttribute('href', '/interviewer/scorecards/sc-late');
    expect(bhavyaLink).toHaveAttribute('href', '/interviewer/scorecards/sc-assigned');
  });

  it('shows an empty state rather than a blank page when nothing is assigned', async () => {
    listAssignments.mockResolvedValue([]);
    renderConsole();

    expect(await screen.findByText('Nothing assigned yet')).toBeInTheDocument();
  });

  it('surfaces a load failure rather than an empty state, with a retry', async () => {
    listAssignments.mockRejectedValue(new Error('Could not reach the server'));
    renderConsole();

    expect(await screen.findByText('Could not load your interviews.')).toBeInTheDocument();
    expect(screen.getByText('Could not reach the server')).toBeInTheDocument();
    expect(screen.queryByText('Nothing assigned yet')).not.toBeInTheDocument();
  });

  it('gives the assignments list its own heading (AD-F30)', async () => {
    listAssignments.mockResolvedValue([ASSIGNED]);
    renderConsole();
    expect(await screen.findByRole('heading', { name: 'Scorecards to write' })).toBeInTheDocument();
  });

  it('shows a loading state before the assignments arrive', () => {
    listAssignments.mockImplementation(() => new Promise(() => undefined));
    renderConsole();

    expect(screen.getByText(/Loading your interviews/)).toBeInTheDocument();
  });

  it('never shows another interviewer’s work - only what the endpoint returns', async () => {
    // Scoping is server-side; the console renders exactly what came back.
    listAssignments.mockResolvedValue([ASSIGNED]);
    renderConsole();

    await screen.findByText('Bhavya Nair');
    expect(screen.queryByText('Asha Rao')).not.toBeInTheDocument();
    expect(screen.queryByText('Chetan Iyer')).not.toBeInTheDocument();
  });
});

describe('InterviewerConsole - page copy', () => {
  it('says a scorecard never decides an outcome', async () => {
    listAssignments.mockResolvedValue([]);
    renderConsole();

    const heading = await screen.findByRole('heading', { name: /my interviews/i });
    expect(
      within(heading.parentElement as HTMLElement).getByText(/nothing here decides an outcome/i),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// PH4-A2/O5 — Upcoming interviews + My availability
// ---------------------------------------------------------------------------

const SESSION: InterviewerSession = {
  id: 'sess-1',
  title: 'Panel interview',
  starts_at: '2026-09-25T10:00:00.000Z',
  ends_at: '2026-09-25T10:45:00.000Z',
  duration_minutes: 45,
  location: 'Meet link',
  status: 'scheduled',
  candidate: 'Asha Rao',
  job_title: 'Backend Engineer',
  scorecard_id: 'sc-1',
};

const WINDOW: AvailabilityWindow = {
  id: 'win-1',
  starts_at: '2026-09-22T04:00:00.000Z',
  ends_at: '2026-09-22T10:00:00.000Z',
};

describe('InterviewerConsole - upcoming interviews', () => {
  beforeEach(() => {
    listAssignments.mockResolvedValue([]);
  });

  it('lists only the caller’s own sessions, with a link to that session’s scorecard', async () => {
    schedulingApi.getMySessions.mockResolvedValue([SESSION]);
    renderConsole();

    expect(await screen.findByText('Asha Rao')).toBeInTheDocument();
    expect(screen.getByText(/Backend Engineer/)).toBeInTheDocument();
    const link = screen.getByRole('link', { name: 'Open scorecard' });
    expect(link).toHaveAttribute('href', '/interviewer/scorecards/sc-1');
  });

  it('shows an empty state when nothing is scheduled', async () => {
    schedulingApi.getMySessions.mockResolvedValue([]);
    renderConsole();

    expect(await screen.findByText('Nothing scheduled right now.')).toBeInTheDocument();
  });

  it('surfaces a load failure for the sessions list as an alert', async () => {
    schedulingApi.getMySessions.mockRejectedValue(new Error('Could not reach the server'));
    renderConsole();

    const title = await screen.findByText('Could not load your interview sessions.');
    expect(title.closest('[role="alert"]')).not.toBeNull();
  });

  it('labels past sessions honestly, with readable statuses (AD-F30)', async () => {
    schedulingApi.getMySessions.mockResolvedValue([{ ...SESSION, status: 'no_show' }]);
    renderConsole();

    expect(
      await screen.findByRole('heading', { name: 'Your interview sessions' }),
    ).toBeInTheDocument();
    expect(await screen.findByText('No show')).toBeInTheDocument();
  });
});

describe('InterviewerConsole - my availability', () => {
  beforeEach(() => {
    listAssignments.mockResolvedValue([]);
  });

  it('lists existing windows and adds a new one', async () => {
    schedulingApi.getMyAvailability.mockResolvedValueOnce([]).mockResolvedValue([WINDOW]);
    schedulingApi.addMyAvailability.mockResolvedValue(WINDOW);
    const user = userEvent.setup();
    renderConsole();

    expect(await screen.findByText('No windows set yet.')).toBeInTheDocument();

    await user.type(screen.getByLabelText('From'), '2026-09-22T10:00');
    await user.type(screen.getByLabelText('To'), '2026-09-22T16:00');
    await user.click(screen.getByRole('button', { name: 'Add window' }));

    await waitFor(() => expect(schedulingApi.addMyAvailability).toHaveBeenCalled());
    expect(toastSuccess).toHaveBeenCalled();
  });

  it('deletes a window only after a confirm, naming the window (AD-F33, B3)', async () => {
    schedulingApi.getMyAvailability.mockResolvedValue([WINDOW]);
    schedulingApi.removeMyAvailability.mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderConsole();

    // The label carries the window's own dates, not a generic "this window".
    await user.click(await screen.findByRole('button', { name: /^Delete the window .+ to .+/ }));
    expect(schedulingApi.removeMyAvailability).not.toHaveBeenCalled();
    const group = screen.getByRole('group', { name: /^Delete the window/ });
    await user.click(within(group).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(schedulingApi.removeMyAvailability).toHaveBeenCalledWith('win-1'));
  });

  it('asks the server for every window it can show, not just four weeks (AD-F11)', async () => {
    schedulingApi.getMyAvailability.mockResolvedValue([]);
    renderConsole();

    await waitFor(() => expect(schedulingApi.getMyAvailability).toHaveBeenCalled());
    const arg = schedulingApi.getMyAvailability.mock.calls[0]?.[0] as {
      start: string;
      end: string;
    };
    const days = (Date.parse(arg.end) - Date.parse(arg.start)) / 86_400_000;
    expect(Math.round(days)).toBe(92);
    expect(screen.getByText(/at most 14 days/)).toBeInTheDocument();
  });

  it('refuses a window longer than 14 days before sending it (AD-F11)', async () => {
    schedulingApi.getMyAvailability.mockResolvedValue([]);
    const user = userEvent.setup();
    renderConsole();
    await screen.findByText('No windows set yet.');

    const start = new Date(Date.now() + 2 * 86_400_000);
    const end = new Date(start.getTime() + 20 * 86_400_000);
    const local = (d: Date) => {
      const pad = (n: number) => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T10:00`;
    };
    await user.type(screen.getByLabelText('From'), local(start));
    await user.type(screen.getByLabelText('To'), local(end));

    expect(await screen.findByText(/A window can be at most 14 days/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add window' })).toBeDisabled();
    expect(schedulingApi.addMyAvailability).not.toHaveBeenCalled();
  });

  it('refuses a window further out than the list can show (AD-F11)', async () => {
    schedulingApi.getMyAvailability.mockResolvedValue([]);
    const user = userEvent.setup();
    renderConsole();
    await screen.findByText('No windows set yet.');

    const start = new Date(Date.now() + 120 * 86_400_000);
    const pad = (n: number) => String(n).padStart(2, '0');
    const day = `${start.getFullYear()}-${pad(start.getMonth() + 1)}-${pad(start.getDate())}`;
    await user.type(screen.getByLabelText('From'), `${day}T10:00`);
    await user.type(screen.getByLabelText('To'), `${day}T16:00`);

    expect(await screen.findByText(/up to 92 days ahead/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add window' })).toBeDisabled();
  });
});
