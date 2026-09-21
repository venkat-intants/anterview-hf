// PH4-D3 — an application's code evidence, counted, on the decision queue and
// the candidate drawer. The endpoint and the queue field existed and were
// backend-tested, but no screen read them — found by the acceptance evidence
// pass. Counts only, and a signal is never worded as a finding.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { codeEvidenceLabel } from '../lib/codeEvidenceLabel';
import type { CodeEvidenceSummary } from '../api/codeEvidence';

function counts(over: Partial<CodeEvidenceSummary> = {}): CodeEvidenceSummary {
  return {
    signal_count: 0,
    unreviewed_signal_count: 0,
    finding_count: 0,
    no_concern_count: 0,
    follow_up_count: 0,
    confirmed_count: 0,
    ...over,
  };
}

const getCodeEvidenceSummary = vi.fn();
vi.mock('../api/codeEvidence', () => ({
  getCodeEvidenceSummary: (...a: unknown[]) => getCodeEvidenceSummary(...a) as unknown,
}));

import CodeEvidenceCounts from '../components/hr/CodeEvidenceCounts';

function renderCounts() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CodeEvidenceCounts enrolmentId="en-1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe('codeEvidenceLabel', () => {
  it('names each finding by its outcome, and only unreviewed signals as awaiting review', () => {
    expect(
      codeEvidenceLabel(
        counts({
          signal_count: 3,
          unreviewed_signal_count: 1,
          finding_count: 3,
          confirmed_count: 1,
          follow_up_count: 1,
          no_concern_count: 1,
        }),
      ),
    ).toBe(
      '1 similarity signal awaiting review · 1 integrity concern confirmed · ' +
        '1 finding flagged for follow-up · 1 reviewed: no concern',
    );
  });

  // Security review D3 M3: a candidate HR reviewed and cleared read as
  // "2 similarity signals (unreviewed) · 1 integrity finding recorded" on the
  // screen where the hiring decision is made.
  it('never makes a cleared candidate look flagged', () => {
    const label = codeEvidenceLabel(
      counts({ signal_count: 2, unreviewed_signal_count: 0, finding_count: 1, no_concern_count: 1 }),
    );
    expect(label).toBe('1 reviewed: no concern');
    expect(label).not.toMatch(/unreviewed|awaiting|concern confirmed|follow-up/);
  });
});

describe('CodeEvidenceCounts — the candidate drawer', () => {
  it('shows the counts and points at the attempt, where the evidence is', async () => {
    getCodeEvidenceSummary.mockResolvedValue(counts({ signal_count: 1, unreviewed_signal_count: 1 }));
    renderCounts();
    expect(await screen.findByText('1 similarity signal awaiting review')).toBeInTheDocument();
    expect(screen.getByText(/Similar code is not evidence of misconduct on its own/)).toBeInTheDocument();
    expect(getCodeEvidenceSummary).toHaveBeenCalledWith('en-1');
  });

  it('renders nothing when the application has no code evidence', async () => {
    getCodeEvidenceSummary.mockResolvedValue(counts());
    const { container } = renderCounts();
    // Wait for the query to settle, then confirm nothing was drawn.
    await vi.waitFor(() => expect(getCodeEvidenceSummary).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });
});
