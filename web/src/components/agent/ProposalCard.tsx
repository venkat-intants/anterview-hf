// ProposalCard — the review-and-commit gate for one agent-drafted action.
//
// This component is the entire "human commits" half of the agent design. The
// backend cannot act; it only drafts. Nothing happens until someone presses
// the button here, and pressing it fires the request with THIS user's
// credentials against the normal authorised endpoint.
//
// Three rules this file must keep:
//   1. Never auto-commit. No useEffect, no "approve all", no retry-on-mount.
//   2. Always show risk_note before the button. It is set whenever committing
//      reaches a real candidate (an email that cannot be unsent), and burying
//      it would defeat the point of asking.
//   3. A committed proposal stays committed. The outcome is owned by the
//      conversation (CopilotPanel), not by this component, because closing
//      the panel unmounts every card: when the outcome lived here, reopening
//      showed an already-sent invite with a live "Send invite" button again,
//      and a second click revoked the link the candidate held and emailed
//      them twice (audit CP-F1). The local state below is only a fallback for
//      a card rendered with no conversation around it.
//   4. The one-time interview link never leaves this card. It is a bearer
//      token, so it lives in this component's own state only: closing the
//      panel unmounts the card and drops it, while the conversation keeps
//      just the "done" state that stops a second commit.

import { useEffect, useRef, useState } from 'react';
import { commitProposal, type CitationState, type Proposal } from '@/api/agent';
import { isTransientApiError } from '@/api/client';
import { ProposalRejectedError } from '@/api/proposalGuard';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { AlertTriangle, Copy } from '@/design/components/icons';
import { Button } from '@/components/ui/button';
import { errorMessage, isNetworkError } from '@/lib/errorMessage';
import { cn } from '@/lib/utils';
import { toast } from '@/lib/toast';
import CitationChips from './CitationChips';
import EvidenceBanner from './EvidenceBanner';

/** What happened when the user approved a proposal. Absent = not approved yet. */
export type CommitOutcome =
  | { state: 'committing' }
  | { state: 'done' }
  | {
      state: 'failed';
      message: string;
      /** False for a refusal that would fail the same way again. */
      retryable: boolean;
    };

/**
 * How long a draft stays approvable. A draft freezes the state it was written
 * against ("status new → shortlisted", "no live invite"), and approving one
 * written long ago acts on a record somebody may have changed since (audit
 * CP-F3). Past this the card asks for a fresh draft instead.
 */
export const PROPOSAL_MAX_AGE_MS = 30 * 60_000;

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
  workflow: 'Hiring process',
  workflow_round: 'Round',
  workflow_settings: 'Automation',
};

interface ProposalCardProps {
  proposal: Proposal;
  /** Called after a successful commit so the parent can refetch lists. */
  onCommitted?: (proposal: Proposal) => void;
  /**
   * PH5-E1 criteria 4/5 — the citation state of the CHAT TURN this proposal
   * was drafted in (see `CitationState`). A proposal's own `rationale` is
   * model-written prose with no marker convention of its own — nothing stamps
   * a `[S_]` ref onto `proposal.citations` the way a reply's inline markers
   * are validated — so the honest signal available for it is the turn's own
   * read of whether IT tied a claim to a record. Undefined when the caller
   * has no turn context (e.g. a bare render in isolation): the banner is then
   * omitted rather than guessed.
   */
  citationState?: CitationState;
  /**
   * The conversation's record of this proposal (rule 3 above). Pass it with
   * `onOutcome` to make the card controlled; omit both for a standalone card.
   */
  outcome?: CommitOutcome;
  onOutcome?: (proposalId: string, outcome: CommitOutcome) => void;
  /**
   * When this browser received the draft (ms since epoch). Ages are measured
   * from here rather than from the server's `created_at`, so a client clock
   * that runs fast cannot make a fresh draft look stale.
   */
  receivedAt?: number;
}

/** "just now", "4 min ago", "2 h ago". */
function draftedAgo(ms: number): string {
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  return `${Math.floor(minutes / 60)} h ago`;
}

/** The one-time link in a commit response, if the endpoint returned one. */
function linkFrom(response: unknown): string | null {
  if (!response || typeof response !== 'object') return null;
  const link = (response as { magic_link?: unknown }).magic_link;
  return typeof link === 'string' && link.startsWith('http') ? link : null;
}

/** The outcome of a failed commit, worded for what the user can do next. */
function failureFrom(err: unknown, proposal: Proposal): CommitOutcome {
  if (err instanceof ProposalRejectedError) {
    return { state: 'failed', message: err.message, retryable: false };
  }
  if (err instanceof Error && err.message === 'Session expired') {
    return { state: 'failed', message: errorMessage(err, ''), retryable: false };
  }
  if (isNetworkError(err)) {
    // No response arrived, so the request may or may not have been applied.
    // Say so rather than invite a blind second click.
    const where = proposal.kind === 'interview_invite' ? ' Check Interviews' : ' Check the record';
    return {
      state: 'failed',
      message: `Could not confirm whether this went through.${where} before you try again.`,
      retryable: true,
    };
  }
  return {
    state: 'failed',
    message: errorMessage(err, `Could not complete: ${proposal.title}.`),
    retryable: isTransientApiError(err),
  };
}

/**
 * How often an open card re-reads the clock. Half a minute, so the "Drafted
 * 4 min ago" label and the expiry notice never lag the real age by more than
 * that. The commit itself re-checks the age at click time regardless.
 */
const DRAFT_AGE_TICK_MS = 30_000;

/** Re-render every DRAFT_AGE_TICK_MS so the draft's age (and expiry) stays current. */
function useDraftAgeClock(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const id = window.setInterval(() => setNow(Date.now()), DRAFT_AGE_TICK_MS);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

export default function ProposalCard({
  proposal,
  onCommitted,
  citationState,
  outcome: controlledOutcome,
  onOutcome,
  receivedAt,
}: ProposalCardProps): JSX.Element {
  const [localOutcome, setLocalOutcome] = useState<CommitOutcome | undefined>();
  // Rule 4: held here, never in the outcome the conversation keeps.
  const [freshLink, setFreshLink] = useState<string | null>(null);
  const controlled = onOutcome !== undefined;
  const outcome = controlled ? controlledOutcome : localOutcome;
  const statusRef = useRef<HTMLParagraphElement>(null);
  // Set only by a commit made from THIS mounted card, so reopening the panel
  // on an already-done card never moves focus by itself.
  const focusStatusOnDone = useRef(false);

  const state = outcome?.state ?? 'idle';
  const isDone = state === 'done';
  const draftedAt = receivedAt ?? Date.parse(proposal.created_at);
  const now = useDraftAgeClock(!isDone && Number.isFinite(draftedAt));
  const age = Number.isFinite(draftedAt) ? Math.max(0, now - draftedAt) : 0;
  const expired = !isDone && state !== 'committing' && age > PROPOSAL_MAX_AGE_MS;

  useEffect(() => {
    // The commit button disables itself while working and stays disabled
    // once done, which drops keyboard focus to <body>. Hand it to the status
    // line instead so a keyboard user keeps their place (audit CP-F6).
    if (isDone && focusStatusOnDone.current) {
      focusStatusOnDone.current = false;
      statusRef.current?.focus();
    }
  }, [isDone]);

  function record(next: CommitOutcome): void {
    if (controlled) onOutcome(proposal.id, next);
    else setLocalOutcome(next);
  }

  async function handleCommit(): Promise<void> {
    if (state === 'committing' || isDone || expired) return;
    // The rendered clock may be up to one tick behind; the click is what counts.
    if (Number.isFinite(draftedAt) && Date.now() - draftedAt > PROPOSAL_MAX_AGE_MS) return;
    record({ state: 'committing' });
    focusStatusOnDone.current = true;
    try {
      const response = await commitProposal(proposal);
      setFreshLink(linkFrom(response));
      record({ state: 'done' });
      toast.success(`${proposal.title} - done`);
      onCommitted?.(proposal);
    } catch (err) {
      // Stay on 'failed' rather than reverting to 'idle': the user needs to
      // see WHY before deciding to try again, and a silent reset invites a
      // second click that fails identically.
      focusStatusOnDone.current = false;
      record(failureFrom(err, proposal));
      toast.error(`Could not complete: ${proposal.title}`);
    }
  }

  async function copyLink(link: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(link);
      toast.success('Link copied');
    } catch {
      toast.error('Could not copy. Select the link and copy it manually.');
    }
  }

  const failed = outcome?.state === 'failed' ? outcome : null;
  const link = isDone ? freshLink : null;

  return (
    <div role="group" aria-label={`Proposal: ${proposal.title}`}>
      {/* A dashed accent border marks this as something drafted and not yet
          done; it turns solid once the user has committed it. */}
      <GlassCard
        className={cn(
          'space-y-3 p-4',
          isDone ? 'border-[var(--ui-ok)]/40' : 'border-dashed border-[var(--ui-info)]/50',
        )}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="mb-1.5 flex flex-wrap items-center gap-2">
              <StatusTag tone={isDone ? 'forest' : 'electric'}>
                {isDone ? 'Completed' : KIND_LABEL[proposal.kind]}
              </StatusTag>
              {!isDone && <StatusTag tone="neutral">Needs your approval</StatusTag>}
            </div>
            <h4 className="break-words text-[15px] font-bold text-foreground">{proposal.title}</h4>
            <p className="mt-0.5 text-sm text-muted-foreground">{proposal.summary}</p>
            {Number.isFinite(draftedAt) && !isDone && (
              <p className="mt-0.5 text-[12.5px] text-muted-foreground">
                Drafted {draftedAgo(age)}
              </p>
            )}
          </div>
        </div>

        {proposal.rationale && (
          <p className="border-l-2 border-[var(--accent)] pl-3 text-sm text-muted-foreground">
            {proposal.rationale}
          </p>
        )}

        {/* The compact form: the full sentence already sits above the reply
            this card belongs to, and five cards repeating it word for word
            buried the cards themselves (audit CP-L10). */}
        {citationState !== undefined && (
          <EvidenceBanner citationState={citationState} variant="compact" />
        )}

        {/* The exact request that will be sent. Shown because "trust me" is not
          a reviewable proposition — the user should be able to see what the
          button does before pressing it. */}
        <details open className="text-xs text-muted-foreground">
          <summary className="cursor-pointer select-none rounded py-1 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]">
            What this will do
          </summary>
          <pre
            tabIndex={0}
            className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-[10px] bg-[var(--ui-inset)] p-3 font-mono text-[12px] text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            {proposal.commit.method} {proposal.commit.path}
            {'\n'}
            {JSON.stringify(proposal.commit.body, null, 2)}
          </pre>
        </details>

        <CitationChips citations={proposal.citations} variant="strip" />

        {/* A note, not an alert: it is on screen before the button, and a turn
            that drafts eight invites must not fire eight assertive
            announcements over everything else (audit CP-L7). */}
        {proposal.risk_note && !isDone && (
          <div
            className="flex gap-2 rounded-[12px] bg-[var(--ui-warn-wash)] px-3 py-2 text-sm font-medium text-[var(--ui-warn)]"
            role="note"
          >
            <AlertTriangle size={16} className="mt-0.5 flex-none" aria-hidden="true" />
            <span>{proposal.risk_note}</span>
          </div>
        )}

        {failed && (
          <p className="text-sm font-medium text-[var(--ui-danger)]" role="alert">
            {failed.message}
          </p>
        )}

        {expired && (
          <p className="text-sm text-muted-foreground">
            This draft is more than 30 minutes old, so the record may have changed. Ask again for
            a fresh one.
          </p>
        )}

        {link && (
          <div className="space-y-1.5 rounded-[12px] bg-[var(--ui-inset-soft)] px-3 py-2 text-sm">
            <p className="font-medium text-foreground">Interview link (shown once)</p>
            <p className="text-[12.5px] text-muted-foreground">
              The candidate has been emailed this link. Copy it now if you need to send it another
              way. It disappears when you close the assistant.
            </p>
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded bg-[var(--ui-inset)] px-2 py-1 font-mono text-[12px] text-foreground">
                {link}
              </code>
              <Button type="button" variant="outline" size="sm" onClick={() => void copyLink(link)}>
                <Copy size={14} aria-hidden="true" />
                Copy link
              </Button>
            </div>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
          {/* Nothing here takes focus on its own: the only way to commit is a
              deliberate click or keypress on this button. After a failure the
              one way forward is "Try again", and only when trying again could
              end differently (audit CP-L1). */}
          {failed ? (
            failed.retryable &&
            !expired && (
              <Button type="button" variant="outline" onClick={() => void handleCommit()}>
                Try again
              </Button>
            )
          ) : (
            <Button
              type="button"
              onClick={() => void handleCommit()}
              disabled={state === 'committing' || isDone || expired}
            >
              {state === 'committing' ? 'Working…' : isDone ? 'Done' : proposal.commit.label}
            </Button>
          )}
          {isDone ? (
            <p ref={statusRef} tabIndex={-1} className="text-[12.5px] text-muted-foreground">
              Approved.
            </p>
          ) : (
            state !== 'committing' &&
            !expired && (
              <span className="text-[12.5px] text-muted-foreground">
                Nothing is sent until you click.
              </span>
            )
          )}
        </div>
      </GlassCard>
    </div>
  );
}
