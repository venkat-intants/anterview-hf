// Tests for the evidence banner (PH5-E1) — the control that stops an
// unsourced answer reading as sourced. It must show exactly one of THREE
// sentences, chosen only by `citationState`, since that value is computed
// server-side: 'sourced' (records read AND a claim is tied to one),
// 'unattributed' (records read but NOTHING is tied to any of them — the
// mixed-turn gap criteria 4/5 name), or 'unread' (no records read at all).

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import EvidenceBanner from '../components/agent/EvidenceBanner';

describe('EvidenceBanner', () => {
  it('says records were used and claims are marked when citationState is sourced', () => {
    render(<EvidenceBanner citationState="sourced" />);
    expect(
      screen.getByText(/Answered from your records\. Claims marked \[S…\] link to the source\./),
    ).toBeInTheDocument();
    expect(screen.queryByText(/No records were read/)).not.toBeInTheDocument();
    expect(screen.queryByText(/but no claim in it is tied/)).not.toBeInTheDocument();
  });

  it('says no records were read when citationState is unread', () => {
    render(<EvidenceBanner citationState="unread" />);
    expect(
      screen.getByText(
        /No records were read for this answer — this is the assistant's own reading\./,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Answered from your records/)).not.toBeInTheDocument();
  });

  it('cautions, rather than confirms, when citationState is unattributed', () => {
    render(<EvidenceBanner citationState="unattributed" />);
    expect(
      screen.getByText(
        /Records were read for this answer, but no claim in it is tied to a specific one — read it as the assistant's own summary, not a sourced fact\./,
      ),
    ).toBeInTheDocument();
    // It must never be mistaken for either of the other two states.
    expect(screen.queryByText(/^Answered from your records/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^No records were read/)).not.toBeInTheDocument();
  });
});
