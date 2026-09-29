// Tests for ProposalCard's citation strip (PH5-E1) — it used to draw its own
// full-reloading <a href> with no kind signal; it now goes through the same
// shared CitationChips renderer as every other surface.

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { Proposal } from '../api/agent';

vi.mock('../api/agent', () => ({
  commitProposal: vi.fn(),
}));
vi.mock('../lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import ProposalCard from '../components/agent/ProposalCard';

const PROPOSAL: Proposal = {
  id: 'p-1',
  kind: 'interview_invite',
  title: 'Invite Asha Rao',
  summary: 'Send an interview invite for the Backend Engineer opening.',
  commit: { method: 'POST', path: '/hr/applicants/ap-1/invite', body: {}, label: 'Send invite' },
  rationale: 'Scored highest on the written exam.',
  citations: [
    {
      kind: 'applicant',
      id: 'ap-1',
      label: 'Asha Rao',
      href: '/hr/applicants/ap-1',
      ref: '',
      locator: null,
    },
  ],
  risk_note: null,
  created_at: '2026-09-02T00:00:00.000Z',
};

function renderCard(
  proposal: Proposal = PROPOSAL,
  citationState?: 'sourced' | 'unattributed' | 'unread',
) {
  return render(
    <MemoryRouter>
      <ProposalCard proposal={proposal} citationState={citationState} />
    </MemoryRouter>,
  );
}

describe('ProposalCard — citations', () => {
  it('renders a citation as a real link via the shared chip renderer', () => {
    renderCard();
    const link = screen.getByRole('link', { name: /Asha Rao/ });
    expect(link).toHaveAttribute('href', '/hr/applicants/ap-1');
  });

  it('renders no citation strip when the proposal has none', () => {
    renderCard({ ...PROPOSAL, citations: [] });
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// PH5-E1 criteria 4/5 — the citation-state banner. ProposalCard is one of the
// five surfaces required to carry it, using the CHAT TURN's own state (a
// proposal's own rationale has no marker convention of its own to check).
// ---------------------------------------------------------------------------
describe('ProposalCard — citation state banner', () => {
  it('renders nothing when no citationState is given', () => {
    renderCard(PROPOSAL, undefined);
    expect(screen.queryByText(/Answered from your records/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No records were read/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Records were read for this answer, but/)).not.toBeInTheDocument();
  });

  it('shows the sourced banner', () => {
    renderCard(PROPOSAL, 'sourced');
    expect(
      screen.getByText(/Answered from your records\. Claims marked/),
    ).toBeInTheDocument();
  });

  it('shows the unread banner', () => {
    renderCard(PROPOSAL, 'unread');
    expect(screen.getByText(/No records were read for this answer/)).toBeInTheDocument();
  });

  it('shows the unattributed caution — the adversarial case this exists for', () => {
    // The whole point: a proposal drafted alongside a reply whose every
    // marker was invented must be flagged here too, not just above the reply.
    renderCard(PROPOSAL, 'unattributed');
    expect(
      screen.getByText(/Records were read for this answer, but no claim in it is tied/),
    ).toBeInTheDocument();
  });
});
