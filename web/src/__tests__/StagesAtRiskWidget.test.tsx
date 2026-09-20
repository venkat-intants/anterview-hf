// StagesAtRiskWidget — PH4-O1. Who to chase, said in words; each row opens
// the candidate's drawer, and the full list is one click away. Nothing on it
// changes anything.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import StagesAtRiskWidget from '../components/StagesAtRiskWidget';
import type { SlaBoardRow } from '../api/stageSla';

const getSlaBoard = vi.fn();
vi.mock('../api/stageSla', () => ({
  getSlaBoard: (...a: unknown[]) => getSlaBoard(...a) as unknown,
}));

// The drawer has its own tests; here it only has to open for the right person.
vi.mock('../components/CandidateDrawer', () => ({
  default: ({ applicantId, enrolmentId }: { applicantId: string | null; enrolmentId?: string | null }) =>
    applicantId ? <div role="dialog">{`drawer ${applicantId} ${enrolmentId ?? ''}`}</div> : null,
}));

function row(i: number, over: Partial<SlaBoardRow> = {}): SlaBoardRow {
  return {
    enrolment_id: `en-${i}`, applicant_id: `ap-${i}`, requisition_id: `req-${i}`,
    opening_title: `Opening ${i}`, full_name: `Candidate ${i}`, stage: 'Panel',
    owner_user_id: null, owner_name: null, open_exceptions: 0, state: 'overdue', sla_hours: 24,
    entered_at: '2026-09-16T00:00:00.000Z', due_at: '2026-09-17T00:00:00.000Z',
    hours_remaining: -10, ...over,
  };
}

function renderWidget() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <StagesAtRiskWidget />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe('StagesAtRiskWidget', () => {
  it('asks only for overdue and due-soon stages', async () => {
    getSlaBoard.mockResolvedValue([]);
    renderWidget();
    await screen.findByText('Nothing overdue or due soon.');
    expect(getSlaBoard).toHaveBeenCalledWith({ state: ['overdue', 'due_soon'] });
  });

  it('states each state in words, and a row opens that candidate', async () => {
    getSlaBoard.mockResolvedValue([
      row(1, { owner_name: 'Rekha' }),
      row(2, { state: 'due_soon', hours_remaining: 3 }),
    ]);
    const user = userEvent.setup();
    renderWidget();
    const first = await screen.findByRole('button', { name: /Candidate 1 — Panel.*overdue/ });
    expect(first).toHaveTextContent('Rekha');
    expect(screen.getByRole('button', { name: /Candidate 2 — Panel.*due soon/ })).toBeTruthy();
    await user.click(first);
    expect(screen.getByRole('dialog')).toHaveTextContent('drawer ap-1 en-1');
  });

  it('shows six, and links to all of them', async () => {
    getSlaBoard.mockResolvedValue(Array.from({ length: 9 }, (_, i) => row(i)));
    renderWidget();
    expect(await screen.findAllByRole('button', { name: /Open details/ })).toHaveLength(6);
    expect(screen.getByRole('link', { name: 'View all 9' })).toHaveAttribute(
      'href',
      '/hr/stages-at-risk',
    );
  });

  it('says it could not check, rather than that all is well', async () => {
    getSlaBoard.mockRejectedValue(new Error('down'));
    renderWidget();
    expect(await screen.findByText('Could not check stage SLAs just now.')).toBeInTheDocument();
    expect(screen.queryByText('Nothing overdue or due soon.')).not.toBeInTheDocument();
  });
});
