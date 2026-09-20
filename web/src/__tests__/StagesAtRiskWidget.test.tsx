// StagesAtRiskWidget — PH4-O1. Who to chase, said in words, linking to the
// decision queue where a person acts; nothing on it changes anything.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import StagesAtRiskWidget from '../components/StagesAtRiskWidget';
import type { SlaBoardRow } from '../api/stageSla';

const getSlaBoard = vi.fn();
vi.mock('../api/stageSla', () => ({
  getSlaBoard: (...a: unknown[]) => getSlaBoard(...a) as unknown,
}));

function row(i: number, over: Partial<SlaBoardRow> = {}): SlaBoardRow {
  return {
    enrolment_id: `en-${i}`, requisition_id: `req-${i}`, opening_title: `Opening ${i}`,
    full_name: `Candidate ${i}`, stage: 'Panel', owner_user_id: null, owner_name: null,
    open_exceptions: 0, state: 'overdue', sla_hours: 24, entered_at: '2026-09-16T00:00:00.000Z',
    due_at: '2026-09-17T00:00:00.000Z', hours_remaining: -10, ...over,
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

  it('states each state in words and links to that opening’s decision queue', async () => {
    getSlaBoard.mockResolvedValue([
      row(1, { owner_name: 'Rekha' }),
      row(2, { state: 'due_soon', hours_remaining: 3 }),
    ]);
    renderWidget();
    const first = await screen.findByRole('link', { name: /Candidate 1 — Panel/ });
    expect(first).toHaveAttribute('href', '/hr/requisitions/req-1/decisions');
    expect(first).toHaveTextContent('overdue');
    expect(first).toHaveTextContent('Rekha');
    expect(screen.getByRole('link', { name: /Candidate 2 — Panel/ })).toHaveTextContent('due soon');
  });

  it('shows six and counts the rest', async () => {
    getSlaBoard.mockResolvedValue(Array.from({ length: 9 }, (_, i) => row(i)));
    renderWidget();
    expect(await screen.findAllByRole('link')).toHaveLength(6);
    expect(screen.getByText('+3 more')).toBeInTheDocument();
  });

  it('says it could not check, rather than that all is well', async () => {
    getSlaBoard.mockRejectedValue(new Error('down'));
    renderWidget();
    expect(await screen.findByText('Could not check stage SLAs just now.')).toBeInTheDocument();
    expect(screen.queryByText('Nothing overdue or due soon.')).not.toBeInTheDocument();
  });
});
