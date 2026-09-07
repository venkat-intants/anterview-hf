// Tests for the Attention Required panel.
//
// The spec's rules for this surface are the ones worth pinning, because each
// of them is a way the panel quietly becomes useless:
//
//   • every alert is actionable — a finding links to the thing that needs
//     doing, and its citations link to the specific records;
//   • no noise for normal activity — when nothing is wrong it says so once,
//     rather than inventing something to report;
//   • severity is not carried by colour alone;
//   • a failed check must never read as "nothing needs attention", which is
//     also the good news and therefore the one wrong answer available.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AttentionBoard, AttentionItem } from '../api/attention';

const getAttention = vi.fn();
vi.mock('../api/attention', () => ({
  getAttention: (...a: unknown[]) => getAttention(...a) as unknown,
}));

import AttentionPanel from '../components/AttentionPanel';

function item(over: Partial<AttentionItem> = {}): AttentionItem {
  return {
    watcher: 'stalled_applicants',
    severity: 'warning',
    title: '7 applicants stalled over 7 days',
    body: 'Nobody has moved them since last week.',
    link: '/hr/pipeline',
    dedupe_key: 'stalled:a,b,c',
    citations: [
      { kind: 'applicant', id: 'ap-1', label: 'Asha Rao', href: '/hr/applicants/ap-1' },
    ],
    ...over,
  };
}

function board(items: AttentionItem[]): AttentionBoard {
  return { generated_at: new Date().toISOString(), total: items.length, items };
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AttentionPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getAttention.mockResolvedValue(board([item()]));
});

describe('AttentionPanel', () => {
  it('states the problem and how many there are', async () => {
    renderPanel();
    expect(await screen.findByText('7 applicants stalled over 7 days')).toBeInTheDocument();
    expect(screen.getByText('1 item')).toBeInTheDocument();
  });

  it('makes every finding actionable', async () => {
    renderPanel();
    const link = await screen.findByRole('link', { name: /7 applicants stalled/ });
    expect(link).toHaveAttribute('href', '/hr/pipeline');
  });

  it('names the specific records a finding is about', async () => {
    renderPanel();
    expect(await screen.findByText('Asha Rao')).toBeInTheDocument();
  });

  it('caps the named records so one finding cannot bury the next', async () => {
    getAttention.mockResolvedValue(
      board([
        item({
          citations: Array.from({ length: 9 }, (_, i) => ({
            kind: 'applicant',
            id: `ap-${i}`,
            label: `Candidate ${i}`,
            href: null,
          })),
        }),
      ]),
    );
    renderPanel();
    expect(await screen.findByText('Candidate 0')).toBeInTheDocument();
    expect(screen.queryByText('Candidate 8')).not.toBeInTheDocument();
    expect(screen.getByText('+5 more')).toBeInTheDocument();
  });

  it('does not render a dead link for a finding with nowhere to go', async () => {
    getAttention.mockResolvedValue(board([item({ link: null })]));
    renderPanel();
    await screen.findByText('7 applicants stalled over 7 days');
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('carries severity in words, not colour alone', async () => {
    // Colour fails a colourblind reader and disappears in a screenshot — which
    // is exactly how this panel gets shared with the person who has to act.
    getAttention.mockResolvedValue(board([item({ severity: 'critical' })]));
    renderPanel();
    await screen.findByText('7 applicants stalled over 7 days');
    expect(screen.getByText(/Critical/)).toBeInTheDocument();
  });

  it('keeps the server ordering, worst first', async () => {
    getAttention.mockResolvedValue(
      board([
        item({ severity: 'critical', title: 'Erasure due', dedupe_key: '1' }),
        item({ severity: 'warning', title: 'Stalled', dedupe_key: '2' }),
        item({ severity: 'info', title: 'Question too easy', dedupe_key: '3' }),
      ]),
    );
    const { container } = renderPanel();
    await screen.findByText('Erasure due');
    const text = container.textContent ?? '';
    expect(text.indexOf('Erasure due')).toBeLessThan(text.indexOf('Stalled'));
    expect(text.indexOf('Stalled')).toBeLessThan(text.indexOf('Question too easy'));
  });

  it('says nothing needs attention, once, when nothing does', async () => {
    getAttention.mockResolvedValue(board([]));
    renderPanel();
    expect(
      await screen.findByText('Nothing needs your attention right now.'),
    ).toBeInTheDocument();
    // And does not invent a count to display.
    expect(screen.queryByText(/0 items/)).not.toBeInTheDocument();
  });

  it('never reports a failed check as good news', async () => {
    // The one wrong answer available: "nothing needs attention" is also what
    // success looks like, so a broken check must say something different.
    getAttention.mockRejectedValue(new Error('boom'));
    renderPanel();
    expect(await screen.findByText(/Could not check for issues/)).toBeInTheDocument();
    expect(
      screen.queryByText('Nothing needs your attention right now.'),
    ).not.toBeInTheDocument();
  });

  it('shows a loading state rather than a premature all-clear', () => {
    getAttention.mockReturnValue(new Promise(() => undefined));
    renderPanel();
    expect(
      screen.queryByText('Nothing needs your attention right now.'),
    ).not.toBeInTheDocument();
  });
});
