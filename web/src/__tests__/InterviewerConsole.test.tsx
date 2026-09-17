// InterviewerConsole (/interviewer) — D4-1. An interviewer's own queue,
// scoped entirely server-side. What matters here:
//   • state badges read clearly, especially LATE;
//   • each row links to ITS OWN scorecard, not a shared one;
//   • empty/loading/error states are distinct, not one blank screen.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { InterviewerAssignment } from '../api/interviewer';

const listAssignments = vi.fn();
vi.mock('../api/interviewer', () => ({
  listAssignments: (...a: unknown[]) => listAssignments(...a) as unknown,
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
});

describe('InterviewerConsole — listing assignments', () => {
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

  it('surfaces a load failure rather than an empty state', async () => {
    listAssignments.mockRejectedValue(new Error('Could not reach the server'));
    renderConsole();

    expect(await screen.findByText('Could not reach the server')).toBeInTheDocument();
  });

  it('shows a loading state before the assignments arrive', () => {
    listAssignments.mockImplementation(() => new Promise(() => undefined));
    renderConsole();

    expect(screen.getByText(/Loading your interviews/)).toBeInTheDocument();
  });

  it('never shows another interviewer’s work — only what the endpoint returns', async () => {
    // Scoping is server-side; the console renders exactly what came back.
    listAssignments.mockResolvedValue([ASSIGNED]);
    renderConsole();

    await screen.findByText('Bhavya Nair');
    expect(screen.queryByText('Asha Rao')).not.toBeInTheDocument();
    expect(screen.queryByText('Chetan Iyer')).not.toBeInTheDocument();
  });
});

describe('InterviewerConsole — page copy', () => {
  it('says a scorecard never decides an outcome', async () => {
    listAssignments.mockResolvedValue([]);
    renderConsole();

    const heading = await screen.findByRole('heading', { name: /my interviews/i });
    expect(within(heading.parentElement as HTMLElement).getByText(/nothing here decides an outcome/i)).toBeInTheDocument();
  });
});
