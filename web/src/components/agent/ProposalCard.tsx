// ProposalCard — the review-and-commit gate for one agent-drafted action.
//
// This component is the entire "human commits" half of the agent design. The
// backend cannot act; it only drafts. Nothing happens until someone presses
// the button here, and pressing it fires the request with THIS user's
// credentials against the normal authorised endpoint.
//
// Two rules this file must keep:
//   1. Never auto-commit. No useEffect, no "approve all", no retry-on-mount.
//   2. Always show risk_note before the button. It is set whenever committing
//      reaches a real candidate (an email that cannot be unsent), and burying
//      it would defeat the point of asking.

import { useState } from 'react';
import { commitProposal, type Proposal } from '@/api/agent';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { toast } from '@/lib/toast';

type CommitState = 'idle' | 'committing' | 'done' | 'failed';

/** Human-readable label per proposal kind, for the card's tag. */
const KIND_LABEL: Record<Proposal['kind'], string> = {
  interview_invite: 'Interview invite',
  exam: 'Exam',
  exam_questions: 'Exam questions',
  email: 'Email',
  job_description: 'Job description',
  shortlist: 'Shortlist',
  pipeline_decision: 'Decision',
  note: 'Note',
};

interface ProposalCardProps {
  proposal: Proposal;
  /** Called after a successful commit so the parent can refetch lists. */
  onCommitted?: (proposal: Proposal) => void;
}

export default function ProposalCard({ proposal, onCommitted }: ProposalCardProps): JSX.Element {
  const [state, setState] = useState<CommitState>('idle');
  const [error, setError] = useState<string>('');

  async function handleCommit(): Promise<void> {
    setState('committing');
    setError('');
    try {
      await commitProposal(proposal);
      setState('done');
      toast.success(`${proposal.title} — done`);
      onCommitted?.(proposal);
    } catch (err) {
      // Stay on 'failed' rather than reverting to 'idle': the user needs to
      // see WHY before deciding to try again, and a silent reset invites a
      // second click that fails identically.
      setState('failed');
      setError(err instanceof Error ? err.message : 'Request failed');
      toast.error(`Could not complete: ${proposal.title}`);
    }
  }

  const isDone = state === 'done';

  return (
    <GlassCard className="p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <StatusTag tone={isDone ? 'forest' : 'electric'}>
              {isDone ? 'Completed' : KIND_LABEL[proposal.kind]}
            </StatusTag>
            {!isDone && <StatusTag tone="neutral">Needs your approval</StatusTag>}
          </div>
          <h4 className="font-semibold text-[15px] truncate">{proposal.title}</h4>
          <p className="text-sm opacity-70 mt-0.5">{proposal.summary}</p>
        </div>
      </div>

      {proposal.rationale && (
        <p className="text-sm opacity-60 border-l-2 border-[var(--accent)] pl-3">
          {proposal.rationale}
        </p>
      )}

      {/* The exact request that will be sent. Shown because "trust me" is not
          a reviewable proposition — the user should be able to see what the
          button does before pressing it. */}
      <details className="text-xs opacity-60">
        <summary className="cursor-pointer select-none">What this will do</summary>
        <pre className="mt-2 p-2 rounded bg-black/20 overflow-x-auto">
          {proposal.commit.method} {proposal.commit.path}
          {'\n'}
          {JSON.stringify(proposal.commit.body, null, 2)}
        </pre>
      </details>

      {proposal.citations.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {proposal.citations.map((c) => (
            <a
              key={`${c.kind}:${c.id}`}
              href={c.href ?? '#'}
              className="text-xs px-2 py-0.5 rounded-full bg-white/5 hover:bg-white/10 transition"
            >
              {c.label}
            </a>
          ))}
        </div>
      )}

      {proposal.risk_note && !isDone && (
        <div
          className="text-sm rounded-lg px-3 py-2 flex gap-2"
          style={{ background: 'rgba(255,183,100,0.12)', color: '#ffb764' }}
          role="alert"
        >
          <span aria-hidden>⚠</span>
          <span>{proposal.risk_note}</span>
        </div>
      )}

      {state === 'failed' && (
        <p className="text-sm" style={{ color: '#ff6b6b' }} role="alert">
          {error}
        </p>
      )}

      <div className="flex items-center gap-2 pt-1">
        <button
          type="button"
          onClick={() => void handleCommit()}
          disabled={state === 'committing' || isDone}
          className="px-3 py-1.5 rounded-lg text-sm font-medium transition disabled:opacity-50 disabled:cursor-not-allowed"
          style={{ background: 'var(--accent)', color: '#08131f' }}
        >
          {state === 'committing'
            ? 'Working…'
            : isDone
              ? 'Done'
              : proposal.commit.label}
        </button>
        {state === 'failed' && (
          <button
            type="button"
            onClick={() => void handleCommit()}
            className="px-3 py-1.5 rounded-lg text-sm bg-white/5 hover:bg-white/10 transition"
          >
            Retry
          </button>
        )}
      </div>
    </GlassCard>
  );
}
