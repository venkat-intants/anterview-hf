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

function renderCard(proposal: Proposal = PROPOSAL) {
  return render(
    <MemoryRouter>
      <ProposalCard proposal={proposal} />
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
