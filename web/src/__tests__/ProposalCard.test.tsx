// Tests for ProposalCard: its citation strip (PH5-E1), its citation-state
// banner, and the commit gate itself (audit CP-F1, CP-F3, CP-F6, CP-L1,
// CP-L7, CP-L10).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useState, type ComponentProps } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import type { Proposal } from '../api/agent';
import { ApiError } from '../api/client';
import { ProposalRejectedError } from '../api/proposalGuard';

const commitProposal = vi.fn();
vi.mock('../api/agent', () => ({
  commitProposal: (...a: unknown[]) => commitProposal(...a) as unknown,
}));
vi.mock('../lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import ProposalCard, {
  PROPOSAL_MAX_AGE_MS,
  type CommitOutcome,
} from '../components/agent/ProposalCard';

const PROPOSAL: Proposal = {
  id: 'p-1',
  kind: 'interview_invite',
  title: 'Invite Asha Rao',
  summary: 'Send an interview invite for the Backend Engineer opening.',
  commit: { method: 'POST', path: '/hr/interviews', body: {}, label: 'Send invite' },
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
  risk_note: 'Sends a real email to the candidate. This cannot be unsent.',
  created_at: new Date().toISOString(),
};

function renderCard(
  proposal: Proposal = PROPOSAL,
  citationState?: 'sourced' | 'unattributed' | 'unread',
  extra: Partial<ComponentProps<typeof ProposalCard>> = {},
) {
  return render(
    <MemoryRouter>
      <ProposalCard proposal={proposal} citationState={citationState} {...extra} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ProposalCard - citations', () => {
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
// proposal's own rationale has no marker convention of its own to check). It
// carries the compact form: the full sentence sits above the reply already.
// ---------------------------------------------------------------------------
describe('ProposalCard - citation state banner', () => {
  it('renders nothing when no citationState is given', () => {
    renderCard(PROPOSAL, undefined);
    expect(screen.queryByText(/^Drafted from your records/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Drafted without reading/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Drafted after reading records/)).not.toBeInTheDocument();
  });

  it('shows the sourced state', () => {
    renderCard(PROPOSAL, 'sourced');
    expect(screen.getByText('Drafted from your records.')).toBeInTheDocument();
  });

  it('shows the unread state', () => {
    renderCard(PROPOSAL, 'unread');
    expect(screen.getByText('Drafted without reading any records.')).toBeInTheDocument();
  });

  it('shows the unattributed caution - the adversarial case this exists for', () => {
    renderCard(PROPOSAL, 'unattributed');
    expect(screen.getByText(/Drafted after reading records, but not tied/)).toBeInTheDocument();
  });

  it('never repeats the full answer-level sentence on a card', () => {
    renderCard(PROPOSAL, 'unattributed');
    expect(screen.queryByText(/Records were read for this answer/)).not.toBeInTheDocument();
  });
});

describe('ProposalCard - commit gate', () => {
  it('shows the risk note as a note, not an assertive alert', () => {
    renderCard();
    expect(screen.getByRole('note')).toHaveTextContent(/cannot be unsent/);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('renders an approved proposal as done, with no live commit button', () => {
    // The conversation owns the outcome; a card mounted again (the panel was
    // closed and reopened) must not offer "Send invite" a second time.
    renderCard(PROPOSAL, undefined, { outcome: { state: 'done' }, onOutcome: vi.fn() });
    expect(screen.getByText('Completed')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Send invite' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Done' })).toBeDisabled();
  });

  it('reports each step to the conversation when controlled', async () => {
    commitProposal.mockResolvedValue({ magic_link: 'https://app.example/interview-invite#tok' });
    const onOutcome = vi.fn();
    renderCard(PROPOSAL, undefined, { onOutcome });
    await userEvent.setup().click(screen.getByRole('button', { name: 'Send invite' }));

    expect(onOutcome).toHaveBeenNthCalledWith(1, 'p-1', { state: 'committing' });
    // The one-time link is a bearer token: it stays in the card and never
    // reaches the outcome the conversation keeps across close and reopen.
    expect(onOutcome).toHaveBeenNthCalledWith(2, 'p-1', { state: 'done' });
  });

  it('shows the one-time interview link the commit returned, and moves focus to the result', async () => {
    commitProposal.mockResolvedValue({ magic_link: 'https://app.example/interview-invite#tok' });
    renderCard();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Send invite' }));

    expect(await screen.findByText('https://app.example/interview-invite#tok')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy link' })).toBeInTheDocument();
    expect(screen.getByText('Approved.')).toHaveFocus();
  });

  it('offers no retry for a refusal that would fail the same way again', async () => {
    commitProposal.mockRejectedValue(
      new ApiError('Applicant must be shortlisted or have passed an exam before an interview.', 409),
    );
    renderCard();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Send invite' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/must be shortlisted/);
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Send invite' })).not.toBeInTheDocument();
  });

  it('stops asking for approval once a refusal leaves nothing to click', async () => {
    commitProposal.mockRejectedValue(
      new ApiError('Applicant must be shortlisted or have passed an exam before an interview.', 409),
    );
    renderCard();
    expect(screen.getByText('Needs your approval')).toBeInTheDocument();
    expect(screen.getByText('Nothing is sent until you click.')).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Send invite' }));

    await screen.findByRole('alert');
    expect(screen.getByText('Not sent')).toBeInTheDocument();
    expect(screen.queryByText('Needs your approval')).not.toBeInTheDocument();
    expect(screen.queryByText('Nothing is sent until you click.')).not.toBeInTheDocument();
  });

  it('keeps asking for approval while a transient failure can still be retried', async () => {
    commitProposal.mockRejectedValue(new ApiError('', 503, undefined, false));
    renderCard();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Send invite' }));

    await screen.findByRole('alert');
    expect(screen.getByText('Needs your approval')).toBeInTheDocument();
    expect(screen.queryByText('Not sent')).not.toBeInTheDocument();
  });

  it('offers exactly one retry for a transient failure', async () => {
    commitProposal.mockRejectedValueOnce(new ApiError('', 503, undefined, false));
    commitProposal.mockResolvedValueOnce({});
    const user = userEvent.setup();
    renderCard();
    await user.click(screen.getByRole('button', { name: 'Send invite' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/Try again in a moment/);
    const retries = screen.getAllByRole('button', { name: /Try again|Send invite|Retry/ });
    expect(retries).toHaveLength(1);
    await user.click(retries[0]);
    expect(commitProposal).toHaveBeenCalledTimes(2);
    expect(await screen.findByText('Completed')).toBeInTheDocument();
  });

  it('says a dropped connection may have gone through instead of showing "Failed to fetch"', async () => {
    commitProposal.mockRejectedValue(new TypeError('Failed to fetch'));
    renderCard();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Send invite' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/Could not confirm whether this went through\. Check AI interviews/);
    expect(alert).not.toHaveTextContent(/Failed to fetch/);
  });

  it('never says "Not sent" for a dropped connection on a draft that has since expired', async () => {
    commitProposal.mockRejectedValue(new TypeError('Failed to fetch'));
    const { rerender } = renderCard();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Send invite' }));
    await screen.findByRole('alert');

    // The same card, now past the approval window.
    rerender(
      <MemoryRouter>
        <ProposalCard
          proposal={PROPOSAL}
          receivedAt={Date.now() - PROPOSAL_MAX_AGE_MS - 60_000}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole('alert')).toHaveTextContent(/Could not confirm whether this went through/);
    expect(screen.queryByText('Not sent')).not.toBeInTheDocument();
    expect(screen.getByText('Check first')).toBeInTheDocument();
    expect(screen.queryByText('Needs your approval')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
  });

  it('still says "Not sent" for a server error on a draft that has since expired', async () => {
    commitProposal.mockRejectedValue(new ApiError('', 503, undefined, false));
    const { rerender } = renderCard();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Send invite' }));
    await screen.findByRole('alert');

    rerender(
      <MemoryRouter>
        <ProposalCard
          proposal={PROPOSAL}
          receivedAt={Date.now() - PROPOSAL_MAX_AGE_MS - 60_000}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText('Not sent')).toBeInTheDocument();
    expect(screen.queryByText('Check first')).not.toBeInTheDocument();
  });

  it('shows the guard\'s reason for a refused proposal and offers no retry', async () => {
    commitProposal.mockRejectedValue(new ProposalRejectedError('unsafe request path'));
    renderCard();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Send invite' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This proposal was not sent: unsafe request path.',
    );
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
  });

  it('refuses a stale draft and asks for a fresh one', async () => {
    renderCard(PROPOSAL, undefined, { receivedAt: Date.now() - PROPOSAL_MAX_AGE_MS - 60_000 });
    const button = screen.getByRole('button', { name: 'Send invite' });
    expect(button).toBeDisabled();
    expect(screen.getByText(/more than 30 minutes old/)).toBeInTheDocument();
    expect(screen.getByText(/Drafted \d+ min ago/)).toBeInTheDocument();
    await userEvent.setup().click(button);
    expect(commitProposal).not.toHaveBeenCalled();
  });

  it('refuses at click time a draft that expired since the clock last ticked', async () => {
    const mountedAt = Date.now();
    renderCard(PROPOSAL, undefined, { receivedAt: mountedAt - PROPOSAL_MAX_AGE_MS + 5_000 });
    const button = screen.getByRole('button', { name: 'Send invite' });
    expect(button).toBeEnabled();
    const later = vi.spyOn(Date, 'now').mockReturnValue(mountedAt + 10_000);
    try {
      await userEvent.setup().click(button);
    } finally {
      later.mockRestore();
    }
    expect(commitProposal).not.toHaveBeenCalled();
  });

  it('keeps the outcome across an unmount when the parent owns it', async () => {
    commitProposal.mockResolvedValue({ magic_link: 'https://app.example/interview-invite#tok' });
    function Host(): JSX.Element {
      const [shown, setShown] = useState(true);
      const [outcomes, setOutcomes] = useState<Record<string, CommitOutcome>>({});
      return (
        <MemoryRouter>
          <button type="button" onClick={() => setShown((s) => !s)}>
            toggle
          </button>
          {shown && (
            <ProposalCard
              proposal={PROPOSAL}
              outcome={outcomes[PROPOSAL.id]}
              onOutcome={(id, o) => setOutcomes((prev) => ({ ...prev, [id]: o }))}
            />
          )}
        </MemoryRouter>
      );
    }
    const user = userEvent.setup();
    render(<Host />);
    await user.click(screen.getByRole('button', { name: 'Send invite' }));
    await screen.findByText('Completed');
    expect(screen.getByText('https://app.example/interview-invite#tok')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'toggle' }));
    await user.click(screen.getByRole('button', { name: 'toggle' }));

    expect(screen.getByText('Completed')).toBeInTheDocument();
    // The outcome survives the remount; the one-time link does not.
    expect(screen.queryByText('https://app.example/interview-invite#tok')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Copy link' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Send invite' })).not.toBeInTheDocument();
    expect(commitProposal).toHaveBeenCalledTimes(1);
  });
});
