// AC-12 — closing an opening that still has undecided candidates.
//
// The sign-off's wording is "a requisition cannot be closed while candidates
// remain unresolved; closing prompts a decision on each". Before this, the
// server returned 200 with a count nothing read, so an opening closed and the
// people in it were stranded with nobody prompted.
//
// What is pinned here:
//
//   • the refusal becomes a PROMPT, not an error toast — a 409 on this path is
//     a question, and reporting it as a failure would tell the manager the
//     close broke rather than that people are waiting;
//   • the prompt offers the decision queue BEFORE it offers "close anyway";
//   • closing anyway is possible, because refusing outright would strand the
//     opening instead of the candidates;
//   • nothing in the copy says or implies rejection (D-05).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { RequisitionDashboard } from '../api/requisitions';

// vi.hoisted, because vi.mock is lifted above every top-level statement: a
// class declared normally is still in its temporal dead zone when the factory
// runs. The component branches on `instanceof`, so the error class the test
// throws MUST be the same one the module exports — a look-alike stub would let
// a broken check pass.
const mocks = vi.hoisted(() => {
  class UnresolvedCandidatesError extends Error {
    readonly unresolved: number;
    constructor(unresolved: number, message: string) {
      super(message);
      this.name = 'UnresolvedCandidatesError';
      this.unresolved = unresolved;
    }
  }
  return {
    getRequisitionDashboard: vi.fn(),
    setRequisitionStatus: vi.fn(),
    updateRequisition: vi.fn(),
    UnresolvedCandidatesError,
  };
});

vi.mock('../api/requisitions', () => mocks);

const { getRequisitionDashboard, setRequisitionStatus, UnresolvedCandidatesError } = mocks;

import RequisitionDashboardPage from '../pages/hr/RequisitionDashboard';

function dash(over: Partial<RequisitionDashboard['requisition']> = {}): RequisitionDashboard {
  return {
    requisition: {
      id: 'req-1',
      title: 'Backend Engineer',
      level: 'mid',
      status: 'open',
      jd_text: null,
      target_hires: 2,
      closes_at: null,
      owner_user_id: null,
      from_backfill: false,
      public_apply_enabled: false,
      created_at: new Date().toISOString(),
      total_enrolments: 9,
      hired: 0,
      awaiting_decision: 3,
      funnel: [],
      department: null,
      location: null,
      employment_type: null,
      experience_min_years: null,
      experience_max_years: null,
      salary_min: null,
      salary_max: null,
      salary_currency: null,
      salary_visible: false,
      responsibilities: [],
      required_skills: [],
      nice_to_have_skills: [],
      ...over,
    },
    rounds: [],
    has_published_workflow: true,
    median_days_in_stage: 4,
    still_being_read: 0,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/hr/requisitions/req-1']}>
        <Routes>
          <Route path="/hr/requisitions/:requisitionId" element={<RequisitionDashboardPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getRequisitionDashboard.mockResolvedValue(dash());
});

describe('closing an opening (AC-12)', () => {
  it('offers a close action at all — it had none before', async () => {
    renderPage();
    expect(await screen.findByRole('button', { name: 'Close' })).toBeInTheDocument();
  });

  it('prompts with the count instead of reporting a failure', async () => {
    const user = userEvent.setup();
    setRequisitionStatus.mockRejectedValue(
      new UnresolvedCandidatesError(3, '3 candidate(s) are still in this opening.'),
    );
    renderPage();
    await user.click(await screen.findByRole('button', { name: 'Close' }));

    expect(await screen.findByRole('alertdialog')).toBeInTheDocument();
    expect(screen.getByText(/3 candidates still undecided/)).toBeInTheDocument();
  });

  it('offers the decision queue before offering to close anyway', async () => {
    const user = userEvent.setup();
    setRequisitionStatus.mockRejectedValue(new UnresolvedCandidatesError(3, 'x'));
    renderPage();
    await user.click(await screen.findByRole('button', { name: 'Close' }));

    const review = await screen.findByRole('link', { name: /Review 3/ });
    expect(review).toHaveAttribute('href', '/hr/requisitions/req-1/decisions');
  });

  it('never implies the candidates are rejected', async () => {
    // D-05: a closed opening does not end anybody's candidacy, and the copy on
    // the one screen that could suggest otherwise must not.
    const user = userEvent.setup();
    setRequisitionStatus.mockRejectedValue(new UnresolvedCandidatesError(3, 'x'));
    renderPage();
    await user.click(await screen.findByRole('button', { name: 'Close' }));

    const dialog = await screen.findByRole('alertdialog');
    expect(dialog.textContent).toMatch(/does not reject them/i);
    expect(dialog.textContent).not.toMatch(/\breject(ed|ing)\b(?!.*not)/i);
  });

  it('closes anyway once the manager acknowledges', async () => {
    const user = userEvent.setup();
    setRequisitionStatus
      .mockRejectedValueOnce(new UnresolvedCandidatesError(3, 'x'))
      .mockResolvedValueOnce({ ...dash().requisition, status: 'closed' });
    renderPage();
    await user.click(await screen.findByRole('button', { name: 'Close' }));
    await user.click(await screen.findByRole('button', { name: /Close anyway/ }));

    await waitFor(() =>
      expect(setRequisitionStatus).toHaveBeenLastCalledWith('req-1', 'closed', {
        acknowledgeUnresolved: true,
      }),
    );
  });

  it('lets the manager back out without closing', async () => {
    const user = userEvent.setup();
    setRequisitionStatus.mockRejectedValue(new UnresolvedCandidatesError(3, 'x'));
    renderPage();
    await user.click(await screen.findByRole('button', { name: 'Close' }));
    await user.click(await screen.findByRole('button', { name: 'Cancel' }));

    await waitFor(() =>
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument(),
    );
    expect(setRequisitionStatus).toHaveBeenCalledTimes(1);
  });

  it('does not prompt when everyone is already settled', async () => {
    const user = userEvent.setup();
    getRequisitionDashboard.mockResolvedValue(dash({ awaiting_decision: 0 }));
    setRequisitionStatus.mockResolvedValue({ ...dash().requisition, status: 'closed' });
    renderPage();
    await user.click(await screen.findByRole('button', { name: 'Close' }));

    await waitFor(() => expect(setRequisitionStatus).toHaveBeenCalled());
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('offers Reopen rather than Close on a closed opening', async () => {
    getRequisitionDashboard.mockResolvedValue(dash({ status: 'closed' }));
    renderPage();
    expect(await screen.findByRole('button', { name: 'Reopen' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Close' })).not.toBeInTheDocument();
  });
});
