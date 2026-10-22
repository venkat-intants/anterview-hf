// offerTrack.ts — the offer's approval track, derived only from the offer's own
// record: its `status`, its stamped dates, and its `history[]`. Nothing here is
// inferred from fields being filled in; a step is `done` because the server
// recorded it.
//
// Pure functions so the state machine is table-testable. The state machine
// itself lives server-side (services/data_gateway/app/offers.py); this mirrors
// which actions it allows from each status, no more.

import type { ApprovalStep, ApprovalStepState } from '@/design/components/approval-track';
import type { OfferHistoryEntry, OfferStatus } from '@/api/offers';
import { formatDate } from '@/lib/formatters';

/** The slice of an offer the track reads. */
export interface TrackOffer {
  status: OfferStatus;
  created_at: string | null;
  submitted_at: string | null;
  decided_at: string | null;
  approval_note: string | null;
  sent_at: string | null;
  expires_at: string | null;
  first_viewed_at: string | null;
  responded_at: string | null;
  decline_reason: string | null;
  withdrawn_at: string | null;
  withdraw_reason: string | null;
  preboarding_completed_at: string | null;
  history: OfferHistoryEntry[];
}

/** The word shown for a step in the `returned` state; it differs by outcome. */
const RETURNED_WORD: Partial<Record<OfferStatus, string>> = {
  rejected: 'Sent back',
  declined: 'Declined',
  expired: 'Expired',
  withdrawn: 'Withdrawn',
};

export interface OfferTrackModel {
  steps: ApprovalStep[];
  stateLabels: Record<ApprovalStepState, string>;
}

function dateOf(iso: string | null | undefined): string | undefined {
  return iso ? formatDate(iso) : undefined;
}

/* ── A sent offer past its deadline ──────────────────────────────────────── */

/** The slice of an offer that says whether it has lapsed. */
export interface ExpiringOffer {
  status: OfferStatus;
  expires_at?: string | null;
}

/**
 * True for a sent offer whose deadline has passed. The server expires such an
 * offer the next time anyone reads it (audit OF-F4), but a page left open past
 * the deadline still holds "sent", so the UI treats it as expired too: no
 * Re-send, no Withdraw, no "Candidate responds".
 */
export function isLapsed(o: ExpiringOffer, now: number = Date.now()): boolean {
  if (o.status !== 'sent' || !o.expires_at) return false;
  const at = Date.parse(o.expires_at);
  return !Number.isNaN(at) && at <= now;
}

/** The status to show and act on: `expired` for a lapsed sent offer. */
export function effectiveOfferStatus(o: ExpiringOffer, now: number = Date.now()): OfferStatus {
  return isLapsed(o, now) ? 'expired' : o.status;
}

/**
 * A history entry that counts as HR sending the offer. A candidate who opens
 * their offer from the portal gets a fresh link, which the server records as
 * `resent` with `details.by = 'candidate_portal'`; that is not HR sending it
 * (audit OF-L3).
 */
function isStaffSend(h: OfferHistoryEntry): boolean {
  if (h.action === 'sent') return true;
  return h.action === 'resent' && h.details?.by !== 'candidate_portal';
}

function lastEvent(
  history: OfferHistoryEntry[],
  ...actions: string[]
): OfferHistoryEntry | undefined {
  for (let i = history.length - 1; i >= 0; i -= 1) {
    if (actions.includes(history[i].action)) return history[i];
  }
  return undefined;
}

function lastStaffSend(history: OfferHistoryEntry[]): OfferHistoryEntry | undefined {
  for (let i = history.length - 1; i >= 0; i -= 1) {
    if (isStaffSend(history[i])) return history[i];
  }
  return undefined;
}

const KEYS = ['draft', 'submitted', 'approved', 'sent', 'answered'] as const;
const LABELS = ['Draft', 'Submitted', 'Approved', 'Sent', 'Answered'] as const;

/** How many steps are complete: the index of the first step that is not. */
function stepsDone(o: TrackOffer): number {
  switch (o.status) {
    case 'draft':
      return 0;
    case 'pending_approval':
      return 2;
    case 'rejected':
      return 2;
    case 'approved':
      return 3;
    case 'sent':
      return 4;
    case 'accepted':
    case 'declined':
    case 'expired':
      return 5;
    case 'withdrawn':
      // Withdrawn from wherever it had got to; the record says how far.
      if (o.sent_at) return 4;
      if (lastEvent(o.history, 'approved')) return 3;
      if (o.submitted_at) return 2;
      return 1;
    default:
      return 0;
  }
}

/**
 * The five steps Draft → Submitted → Approved → Sent → Answered.
 *
 * - completed steps carry who did it and when (from `history[]`);
 * - the pending step names who must act next;
 * - a sent-back, declined, expired or withdrawn offer shows its reason inline
 *   on a `returned` step, in place of the step that did not happen.
 */
export function offerTrack(record: TrackOffer, now: number = Date.now()): OfferTrackModel {
  const o: TrackOffer = { ...record, status: effectiveOfferStatus(record, now) };
  const done = stepsDone(o);
  const events: Record<(typeof KEYS)[number], { actor?: string; at?: string }> = {
    draft: {
      actor: lastEvent(o.history, 'created')?.actor_name ?? undefined,
      at: dateOf(o.created_at),
    },
    submitted: {
      actor: lastEvent(o.history, 'submitted')?.actor_name ?? undefined,
      at: dateOf(o.submitted_at),
    },
    approved: {
      actor: lastEvent(o.history, 'approved')?.actor_name ?? undefined,
      at: dateOf(o.decided_at),
    },
    sent: { actor: lastStaffSend(o.history)?.actor_name ?? undefined, at: dateOf(o.sent_at) },
    answered: { at: dateOf(o.responded_at) },
  };

  const steps: ApprovalStep[] = KEYS.map((key, i) => ({
    key,
    label: LABELS[i],
    state: 'todo' as ApprovalStepState,
    ...(i < done ? events[key] : {}),
  }));
  for (let i = 0; i < Math.min(done, steps.length); i += 1) steps[i].state = 'done';

  if (o.status === 'accepted') steps[4].actor = 'Accepted by the candidate';

  // Which step must be acted on next, and by whom. Only a status where someone
  // has a move gets a `current` step.
  const WHO: Partial<Record<OfferStatus, string>> = {
    draft: 'HR manager submits it',
    pending_approval: 'Company admin decides',
    approved: 'HR manager sends it',
    sent: 'Candidate responds',
  };
  const who = WHO[o.status];
  if (who && done < steps.length) {
    steps[done].state = 'current';
    steps[done].actor = who;
    if (o.status === 'sent' && o.first_viewed_at)
      steps[done].at = `Opened ${dateOf(o.first_viewed_at)}`;
  }

  // The step that did not happen, with the reason it did not.
  const reasonStep = ((): { index: number; note: string; actor?: string; at?: string } | null => {
    switch (o.status) {
      case 'rejected':
        return {
          index: 2,
          note: o.approval_note ?? 'No note was given.',
          actor: events.approved.actor ?? lastEvent(o.history, 'rejected')?.actor_name ?? undefined,
          at: dateOf(o.decided_at),
        };
      case 'declined':
        return {
          index: 4,
          note: o.decline_reason ? `Decline reason: ${o.decline_reason}` : 'No reason was given.',
          at: dateOf(o.responded_at),
        };
      case 'expired':
        return {
          index: 4,
          note: o.expires_at
            ? `The offer window closed on ${dateOf(o.expires_at)} with no answer.`
            : 'The offer window closed with no answer.',
        };
      case 'withdrawn':
        return {
          index: Math.min(done, 4),
          note: o.withdraw_reason
            ? `Withdraw reason: ${o.withdraw_reason}`
            : 'No reason was given.',
          actor: lastEvent(o.history, 'withdrawn')?.actor_name ?? undefined,
          at: dateOf(o.withdrawn_at),
        };
      default:
        return null;
    }
  })();
  if (reasonStep) {
    const s = steps[reasonStep.index];
    s.state = 'returned';
    s.note = reasonStep.note;
    s.actor = reasonStep.actor;
    s.at = reasonStep.at;
  }

  return {
    steps,
    stateLabels: {
      done: 'Done',
      current: 'Waiting',
      todo: 'To do',
      returned: RETURNED_WORD[o.status] ?? 'Returned',
    },
  };
}

/* ── What HR can do next ─────────────────────────────────────────────────── */

/** The one forward step HR can take, if HR holds the move at all. */
export type OfferPrimary = 'submit' | 'send' | 'complete_preboarding' | null;

/**
 * The single legal next action for HR. Where the move belongs to someone else
 * (the approver, the candidate) or the offer is closed, there is no primary:
 * the track says who has to act, and only the quiet actions remain.
 */
export function offerPrimary(
  o: Pick<TrackOffer, 'status' | 'preboarding_completed_at'> & ExpiringOffer,
  now: number = Date.now(),
): OfferPrimary {
  switch (effectiveOfferStatus(o, now)) {
    case 'draft':
    case 'rejected':
      return 'submit';
    case 'approved':
      return 'send';
    case 'accepted':
      return o.preboarding_completed_at ? null : 'complete_preboarding';
    default:
      return null;
  }
}

export interface OfferSecondary {
  recall: boolean;
  reopen: boolean;
  resend: boolean;
  withdraw: boolean;
}

/** The quieter actions the state machine allows alongside the primary. */
export function offerSecondary(
  o: Pick<TrackOffer, 'status' | 'preboarding_completed_at'> & ExpiringOffer,
  now: number = Date.now(),
): OfferSecondary {
  const status = effectiveOfferStatus(o, now);
  return {
    recall: status === 'pending_approval',
    reopen: status === 'approved',
    resend: (status === 'sent' || status === 'accepted') && !o.preboarding_completed_at,
    withdraw: ['draft', 'pending_approval', 'approved', 'rejected', 'sent'].includes(status),
  };
}

/* ── What a row in the offers list needs from the reader ──────────────────── */

export interface OfferNeeds {
  /** One short line: whose move it is, in words. */
  text: string;
  /** True when the move is the HR reader's own. */
  mine: boolean;
}

/**
 * Where an offer stands, as the reader's to-do, from the list row's own status
 * and document counts. A closed offer needs nothing and says so.
 */
export function offerNeeds(
  o: {
    status: OfferStatus;
    expires_at?: string | null;
    preboarding_completed_at: string | null;
    documents: { mandatory_total: number; awaiting_review: number };
  },
  now: number = Date.now(),
): OfferNeeds {
  switch (effectiveOfferStatus(o, now)) {
    case 'draft':
      return { text: 'Yours to submit', mine: true };
    case 'rejected':
      return { text: 'Sent back - yours to revise', mine: true };
    case 'approved':
      return { text: 'Yours to send', mine: true };
    case 'pending_approval':
      return { text: 'With the company admin', mine: false };
    case 'sent':
      return { text: 'Waiting on the candidate', mine: false };
    case 'accepted':
      if (o.preboarding_completed_at) return { text: 'Preboarding complete', mine: false };
      if (o.documents.awaiting_review > 0) return { text: 'Documents to review', mine: true };
      return { text: 'Waiting on the candidate’s documents', mine: false };
    default:
      return { text: 'Closed', mine: false };
  }
}
