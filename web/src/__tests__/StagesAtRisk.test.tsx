// StagesAtRisk — PH4-O1. The full list behind the widget: every row, any
// stage, filterable, each opening the candidate's drawer.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import StagesAtRisk from '../pages/hr/StagesAtRisk';
import type { SlaBoardRow } from '../api/stageSla';

const getSlaBoard = vi.fn();
vi.mock('../api/stageSla', () => ({
  getSlaBoard: (...a: unknown[]) => getSlaBoard(...a) as unknown,
}));

vi.mock('../components/CandidateDrawer', () => ({
  default: ({ applicantId }: { applicantId: string | null }) =>
    applicantId ? <div role="dialog">{`drawer ${applicantId}`}</div> : null,
}));

function row(i: number, over: Partial<SlaBoardRow> = {}): SlaBoardRow {
  return {
    enrolment_id: `en-${i}`, applicant_id: `ap-${i}`, requisition_id: `req-${i}`,
    opening_title: `Opening ${i}`, full_name: `Candidate ${i}`, stage: 'Aptitude exam',
    owner_user_id: null, owner_name: null, open_exceptions: 0, state: 'overdue', sla_hours: 24,
    entered_at: '2026-09-16T00:00:00.000Z', due_at: '2026-09-17T00:00:00.000Z',
    hours_remaining: -30, ...over,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <StagesAtRisk />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe('StagesAtRisk', () => {
  it('lists every application at risk, not six, with time over and exceptions', async () => {
    getSlaBoard.mockResolvedValue([
      ...Array.from({ length: 8 }, (_, i) => row(i)),
      row(9, { open_exceptions: 2, hours_remaining: 5, state: 'due_soon' }),
    ]);
    renderPage();
    expect(await screen.findAllByRole('button', { name: /Open details/ })).toHaveLength(9);
    expect(screen.getByText('9 applications')).toBeTruthy();
    expect(screen.getAllByText(/30 h over/).length).toBe(8);
    expect(screen.getByText(/due soon \(5 h left\).*2 open exceptions/)).toBeTruthy();
  });

  it('filters by state, including stages still on track', async () => {
    getSlaBoard.mockResolvedValue([]);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('Nothing overdue or due soon.');
    expect(getSlaBoard).toHaveBeenLastCalledWith({ state: ['overdue', 'due_soon'] });
    await user.click(screen.getByRole('tab', { name: 'Every stage with an SLA' }));
    await waitFor(() =>
      expect(getSlaBoard).toHaveBeenLastCalledWith({ state: ['overdue', 'due_soon', 'on_track'] }),
    );
    expect(await screen.findByText('No application is in a stage with an SLA.')).toBeTruthy();
  });

  it('opens the candidate a row names', async () => {
    getSlaBoard.mockResolvedValue([row(1)]);
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: /Candidate 1 — Aptitude exam/ }));
    expect(screen.getByRole('dialog')).toHaveTextContent('drawer ap-1');
  });
});
