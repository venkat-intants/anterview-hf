// PH4-D3 — an application's code evidence, counted, on the decision queue and
// the candidate drawer. The endpoint and the queue field existed and were
// backend-tested, but no screen read them — found by the acceptance evidence
// pass. Counts only, and a signal is never worded as a finding.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { codeEvidenceLabel } from '../lib/codeEvidenceLabel';

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
  it('keeps an unreviewed signal apart from a recorded finding', () => {
    expect(codeEvidenceLabel({ signal_count: 2, finding_count: 1 })).toBe(
      '2 similarity signals (unreviewed) · 1 integrity finding recorded',
    );
  });

  it('says only what there is', () => {
    expect(codeEvidenceLabel({ signal_count: 1, finding_count: 0 })).toBe(
      '1 similarity signal (unreviewed)',
    );
    expect(codeEvidenceLabel({ signal_count: 0, finding_count: 3 })).toBe(
      '3 integrity findings recorded',
    );
  });
});

describe('CodeEvidenceCounts — the candidate drawer', () => {
  it('shows the counts and points at the attempt, where the evidence is', async () => {
    getCodeEvidenceSummary.mockResolvedValue({ signal_count: 1, finding_count: 0 });
    renderCounts();
    expect(await screen.findByText('1 similarity signal (unreviewed)')).toBeInTheDocument();
    expect(screen.getByText(/Similar code is not evidence of misconduct on its own/)).toBeInTheDocument();
    expect(getCodeEvidenceSummary).toHaveBeenCalledWith('en-1');
  });

  it('renders nothing when the application has no code evidence', async () => {
    getCodeEvidenceSummary.mockResolvedValue({ signal_count: 0, finding_count: 0 });
    const { container } = renderCounts();
    // Wait for the query to settle, then confirm nothing was drawn.
    await vi.waitFor(() => expect(getCodeEvidenceSummary).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });
});
