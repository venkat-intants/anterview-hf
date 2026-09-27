// Tests for the evidence banner (PH5-E1) — the control that stops an
// unsourced answer reading as sourced. It must show exactly one of two
// sentences, chosen only by `evidenceUsed`, since that value is computed
// server-side from whether a tool result actually carried a citation.

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import EvidenceBanner from '../components/agent/EvidenceBanner';

describe('EvidenceBanner', () => {
  it('says records were used when evidenceUsed is true', () => {
    render(<EvidenceBanner evidenceUsed />);
    expect(
      screen.getByText(/Answered from your records\. Claims marked \[S…\] link to the source\./),
    ).toBeInTheDocument();
    expect(screen.queryByText(/No records were read/)).not.toBeInTheDocument();
  });

  it('says no records were read when evidenceUsed is false', () => {
    render(<EvidenceBanner evidenceUsed={false} />);
    expect(
      screen.getByText(
        /No records were read for this answer — this is the assistant's own reading\./,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Answered from your records/)).not.toBeInTheDocument();
  });
});
