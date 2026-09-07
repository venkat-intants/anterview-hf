// ProposalPreview — D7. What the copilot drafted, drawn where it would land.
//
// A workflow proposal rendered as a generic review card would be a JSON blob
// next to a canvas, and the user would have to hold the shape in their head to
// judge it. The thing being proposed is a diagram, so it is previewed as one:
// ghost rounds, in position, in the chain they would form.
//
// This component is the commit gate, and it keeps the two rules every commit
// gate in this codebase keeps:
//
//   1. Never auto-apply. No useEffect, no "accept all". The request fires from
//      a click and nowhere else.
//   2. Say plainly what the button does. Here that means saying what it does
//      NOT do: applying puts rounds on the canvas as a DRAFT. No candidate can
//      see a draft. Publishing is a separate, later, human act — and an HR
//      manager who believed otherwise would have been misled by this screen.
//
// The preview renders the proposal's own commit body rather than anything the
// assistant said in prose. If the two ever disagreed, what the button sends is
// the truth, so that is what gets drawn.

import { useState } from 'react';
import { commitProposal, type Proposal } from '@/api/agent';
import { Check, Loader2, Sparkles, X } from '@/design/components/icons';
import { StatusTag } from '@/design/components/primitives';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import type { RoundKind } from '@/api/workflows';
import { ROUND_KIND_META } from './roundKinds';

/** One round as it appears inside a proposal's commit body. */
interface DraftedRound {
  title?: string;
  kind?: string;
  pass_threshold?: number | null;
  deadline_days?: number;
  criteria?: { id: string; name: string }[];
}

interface Props {
  proposal: Proposal;
  /** How many rounds the canvas already holds — a single round appends after them. */
  existingRounds: number;
  onApplied: () => void;
  onDismiss: () => void;
}

function isKnownKind(kind: string | undefined): kind is RoundKind {
  return Boolean(kind && kind in ROUND_KIND_META);
}

/** The rounds this proposal would add, whichever shape it came in. */
function draftedRounds(proposal: Proposal): DraftedRound[] {
  const body = proposal.commit.body;
  if (proposal.kind === 'workflow' && Array.isArray(body.rounds)) {
    return body.rounds as DraftedRound[];
  }
  if (proposal.kind === 'workflow_round' && typeof body.kind === 'string') {
    return [body as DraftedRound];
  }
  return [];
}

function GhostRound({ round, index }: { round: DraftedRound; index: number }) {
  const meta = isKnownKind(round.kind) ? ROUND_KIND_META[round.kind] : null;
  const Icon = meta?.icon;
  return (
    <li className="flex flex-col">
      {index > 0 ? (
        <span className="ml-[26px] h-5 w-px bg-[var(--accent)]/30" aria-hidden="true" />
      ) : null}
      <div className="flex items-center gap-3 rounded-[16px] border border-dashed border-[var(--accent)]/50 bg-[var(--accent)]/[0.05] p-3.5">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] bg-white/[0.06]">
          {Icon ? (
            <Icon className="h-[18px] w-[18px] text-[#d5d7da]" aria-hidden="true" />
          ) : null}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="truncate text-[14px] font-medium text-white">
              {round.title ?? 'Untitled round'}
            </span>
            {meta ? <StatusTag tone={meta.tone}>{meta.label}</StatusTag> : null}
          </span>
          <span className="mt-0.5 flex flex-wrap items-center gap-x-2.5 text-[11.5px] text-[#70757c]">
            {round.pass_threshold !== null && round.pass_threshold !== undefined ? (
              <span>advance at {round.pass_threshold}%</span>
            ) : null}
            {round.criteria && round.criteria.length > 0 ? (
              <span>{round.criteria.map((c) => c.name).join(', ')}</span>
            ) : null}
          </span>
        </span>
      </div>
    </li>
  );
}

/** A settings proposal has no rounds — show the switches it would flip. */
function SettingsDiff({ body }: { body: Record<string, unknown> }) {
  const LABELS: Record<string, string> = {
    auto_score_on_apply: 'Score resumes on arrival',
    auto_assign_first_round: 'Send the first round automatically',
    auto_advance_rounds: 'Advance between rounds automatically',
    reminders_enabled: 'Remind candidates',
    shortlist_ats_threshold: 'Auto-shortlist at ATS',
    hold_band: 'Send to you rather than past you (points)',
  };
  return (
    <ul className="flex list-none flex-col gap-1.5">
      {Object.entries(body).map(([key, value]) => (
        <li key={key} className="flex items-center gap-2 text-[12.5px] text-[#d5d7da]">
          <span className="flex-1">{LABELS[key] ?? key}</span>
          <span className="font-medium text-white">
            {value === true ? 'on' : value === false ? 'off' : String(value)}
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function ProposalPreview({
  proposal,
  existingRounds,
  onApplied,
  onDismiss,
}: Props): JSX.Element {
  const [state, setState] = useState<'idle' | 'applying' | 'failed'>('idle');
  const [error, setError] = useState('');

  const rounds = draftedRounds(proposal);
  const isSettings = proposal.kind === 'workflow_settings';
  const appends = proposal.kind === 'workflow_round';

  async function apply(): Promise<void> {
    setState('applying');
    setError('');
    try {
      await commitProposal(proposal);
      toast.success('Added to your draft');
      onApplied();
    } catch (err) {
      // Stay failed rather than resetting: the user needs to read why before
      // clicking again, and a silent reset invites an identical second failure.
      setState('failed');
      setError(err instanceof Error ? err.message : 'Could not apply that');
    }
  }

  return (
    <section
      aria-label="Assistant preview"
      className="mb-5 rounded-[18px] border border-[var(--accent)]/35 bg-[var(--accent)]/[0.04] p-4"
    >
      <header className="mb-3 flex flex-wrap items-center gap-2">
        <Sparkles className="h-4 w-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />
        <span className="text-[13px] font-medium text-white">{proposal.title}</span>
        <StatusTag tone="neutral">preview</StatusTag>
        <span className="ml-auto text-[11.5px] text-[#888b91]">
          {appends ? `would be added after round ${existingRounds}` : 'nothing has changed yet'}
        </span>
      </header>

      {proposal.rationale ? (
        <p className="mb-3 border-l-2 border-[var(--accent)]/50 pl-3 text-[12.5px] leading-relaxed text-[#b8babf]">
          {proposal.rationale}
        </p>
      ) : null}

      {isSettings ? (
        <SettingsDiff body={proposal.commit.body} />
      ) : rounds.length > 0 ? (
        <ol className="flex list-none flex-col">
          {rounds.map((r, i) => (
            <GhostRound key={`${r.title}-${i}`} round={r} index={i} />
          ))}
        </ol>
      ) : (
        // A shape this preview does not know how to draw. Falling back to the
        // raw request is better than rendering nothing and asking for approval
        // of something invisible.
        <pre className="overflow-x-auto rounded-[10px] bg-black/25 p-2 text-[11px] text-[#b8babf]">
          {proposal.commit.method} {proposal.commit.path}
          {'\n'}
          {JSON.stringify(proposal.commit.body, null, 2)}
        </pre>
      )}

      {/* The single most important sentence on this screen. */}
      <p className="mt-3 text-[11.5px] leading-relaxed text-[#888b91]">
        Accepting puts this on your canvas as a draft. No candidate sees a draft — you
        still review it and press publish yourself.
      </p>

      {state === 'failed' ? (
        <p className="mt-2 text-[12px] text-[#e6714f]" role="alert">
          {error}
        </p>
      ) : null}

      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          onClick={() => void apply()}
          disabled={state === 'applying'}
          className={cn(
            'inline-flex items-center gap-1.5 rounded-[10px] bg-[var(--accent)] px-4 py-2 text-[12.5px] font-medium text-black transition-opacity hover:opacity-90',
            state === 'applying' && 'opacity-50',
          )}
        >
          {state === 'applying' ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <Check className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {proposal.commit.label}
        </button>
        <button
          type="button"
          onClick={onDismiss}
          className="inline-flex items-center gap-1.5 rounded-[10px] border border-white/[0.12] px-4 py-2 text-[12.5px] text-[#d5d7da] hover:text-white"
        >
          <X className="h-3.5 w-3.5" aria-hidden="true" />
          Discard
        </button>
        <details className="ml-auto text-[11px] text-[#70757c]">
          <summary className="cursor-pointer select-none">What this sends</summary>
          <pre className="mt-2 max-w-[420px] overflow-x-auto rounded bg-black/25 p-2">
            {proposal.commit.method} {proposal.commit.path}
            {'\n'}
            {JSON.stringify(proposal.commit.body, null, 2)}
          </pre>
        </details>
      </div>
    </section>
  );
}
