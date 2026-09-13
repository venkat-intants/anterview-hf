// E3 — the company super admin's hiring board.
//
// Pinned: every opening with its numbers; health as a chip and a reason;
// "Not published" as its own state; filtering by health; each row opens that
// opening's read-only dashboard.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { BoardOpening, HiringBoard as Board } from '../api/companyBoard';

const getHiringBoard = vi.fn();
vi.mock('../api/companyBoard', () => ({
  getHiringBoard: (...a: unknown[]) => getHiringBoard(...a) as unknown,
}));

import HiringBoard from '../pages/superadmin/HiringBoard';

function opening(over: Partial<BoardOpening>): BoardOpening {
  return {
    requisition_id: 'r', title: 'Opening', location: null, status: 'open',
    workflow_state: 'published', published_version: 1, draft_version: null,
    accepting_applications: true, applied: 0, in_play: 0, target_hires: null, hired: 0,
    awaiting_decision: 0, closes_at: null,
    health: { band: 'on_track', reason: '', projected_fill_date: null, hires_per_week: null,
              assumed_hire_ratio: false },
    ...over,
  };
}

const BOARD: Board = {
  generated_at: new Date().toISOString(),
  summary: { not_published: 1, at_risk: 1, watch: 0, on_track: 1 },
  openings: [
    opening({ requisition_id: 'np', title: 'Designer', workflow_state: 'draft',
              published_version: null, draft_version: 2, applied: 3, in_play: 3,
              health: { band: 'not_published', reason: 'It is taking applications, but no workflow is published.',
                        projected_fill_date: null, hires_per_week: null, assumed_hire_ratio: false } }),
    opening({ requisition_id: 'ar', title: 'Python Developer', location: 'Hyderabad',
              applied: 40, in_play: 12, target_hires: 5, hired: 1, awaiting_decision: 4,
              health: { band: 'at_risk', reason: 'The target fills around 2027-01-10, after the closing date (2026-10-01).',
                        projected_fill_date: '2027-01-10', hires_per_week: 0.2, assumed_hire_ratio: true } }),
    opening({ requisition_id: 'ot', title: 'QA Engineer', published_version: 3, draft_version: 4,
              target_hires: 2, hired: 2,
              health: { band: 'on_track', reason: 'Target met: 2 of 2 hired.', projected_fill_date: null,
                        hires_per_week: null, assumed_hire_ratio: false } }),
  ],
};

function renderBoard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <HiringBoard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getHiringBoard.mockResolvedValue(BOARD);
});

describe('the hiring board (E3)', () => {
  it('shows each opening with its numbers and workflow state', async () => {
    renderBoard();
    const row = await screen.findByTestId('board-row-ar');
    expect(within(row).getByText('Python Developer')).toBeTruthy();
    expect(within(row).getByText('Hyderabad')).toBeTruthy();
    expect(within(row).getByText('40')).toBeTruthy();
    expect(within(row).getByText('12')).toBeTruthy();
    expect(within(row).getByText('1 of 5')).toBeTruthy();
    expect(within(row).getByText('4')).toBeTruthy();
    expect(within(screen.getByTestId('board-row-ot')).getByText('v3 live · v4 draft')).toBeTruthy();
    expect(within(screen.getByTestId('board-row-np')).getByText('v2 draft only')).toBeTruthy();
  });

  it('gives health as a chip and a reason, with "Not published" as its own state', async () => {
    renderBoard();
    const risky = await screen.findByTestId('board-row-ar');
    expect(within(risky).getByText('At risk')).toBeTruthy();
    expect(within(risky).getByText(/after the closing date/)).toBeTruthy();
    const unpublished = screen.getByTestId('board-row-np');
    expect(within(unpublished).getByText('Not published')).toBeTruthy();
    expect(within(unpublished).getByText(/no workflow is published/)).toBeTruthy();
  });

  it('filters by health', async () => {
    const user = userEvent.setup();
    renderBoard();
    await screen.findByTestId('board-row-ar');
    await user.click(screen.getByRole('button', { name: 'At risk · 1' }));
    expect(screen.getByTestId('board-row-ar')).toBeTruthy();
    expect(screen.queryByTestId('board-row-ot')).toBeNull();
    expect(screen.queryByTestId('board-row-np')).toBeNull();
  });

  it("opens an opening's read-only dashboard", async () => {
    renderBoard();
    const row = await screen.findByTestId('board-row-ar');
    expect(within(row).getByRole('link', { name: 'Python Developer' }).getAttribute('href')).toBe(
      '/superadmin/requisitions/ar',
    );
  });

  it('has no controls that change anything', async () => {
    renderBoard();
    await screen.findByTestId('board-row-ar');
    const buttons = screen.getAllByRole('button').map((b) => b.textContent ?? '');
    expect(buttons.every((t) => /·/.test(t))).toBe(true);
  });
});
