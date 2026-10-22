// openingList.ts — what the openings LIST can honestly say about an opening.
//
// The list payload carries the opening's own fields and its candidate counts. It
// does NOT carry whether a job description has been published or a hiring
// process (workflow) is live; only the opening's page holds those
// (lib/openingSetup.ts) — UNLESS the server says so. Newer backends add two
// optional booleans to each row, `has_published_jd` and `has_published_workflow`;
// when present they make the first two dots truthful (done / not done), and when
// absent (an older backend) those dots stay "open to check". So the five-dot
// progress indicator has these kinds of dot, and the difference is the point:
//
//   done      the list proves it       (approved; accepting applications)
//   waiting   the list proves it is in someone else's hands (pending approval)
//   open      the list proves it is NOT done (not submitted, sent back, switched off)
//   unknown   the list cannot tell      -> "open to check", never guessed
//             (only when the server did not send the field)
//   optional  never ticked              (preboarding documents)
//
// Nothing here infers a stage from another: an approved, live opening whose row
// carries no `has_published_*` field still shows stages 1 and 2 as "open to
// check", because the list does not prove them.

import type { Requisition } from '@/api/requisitions';

export type DotState = 'done' | 'waiting' | 'open' | 'unknown' | 'optional';

export interface SetupDot {
  key: 'role' | 'process' | 'approval' | 'golive' | 'documents';
  label: string;
  state: DotState;
  /** Spoken and shown as the tooltip: "Budget and approval: awaiting approval". */
  text: string;
}

type ListFields = Pick<
  Requisition,
  | 'status'
  | 'approval_status'
  | 'public_apply_enabled'
  | 'awaiting_decision'
  | 'total_enrolments'
  | 'has_published_jd'
  | 'has_published_workflow'
  | 'closes_at'
>;

type LiveFields = Pick<
  Requisition,
  'status' | 'public_apply_enabled' | 'approval_status' | 'closes_at' | 'has_published_workflow'
>;

/** The closing date has passed: the public gate refuses applications from then on. */
export function closingDatePassed(r: Pick<Requisition, 'closes_at'>, now: Date = new Date()): boolean {
  return !!r.closes_at && new Date(r.closes_at).getTime() <= now.getTime();
}

/**
 * Why an opening whose status is 'open' is NOT actually taking applications,
 * or null when it is. Mirrors the server's public gate (publishing.py), so the
 * list can no longer say "accepting applications" for an opening the careers
 * page refuses (QA ANTHIRE-002/003). `has_published_workflow` absent (older
 * backend) is not read as missing.
 */
export function notLiveReason(r: LiveFields, now: Date = new Date()): string | null {
  if (r.status !== 'open') return null;
  if (closingDatePassed(r, now)) return 'closing date passed';
  if (r.approval_status !== 'approved') return 'not approved yet';
  if (r.has_published_workflow === false) return 'no live workflow';
  if (!r.public_apply_enabled) return 'applications switched off';
  return null;
}

export function isAcceptingApplications(r: LiveFields, now: Date = new Date()): boolean {
  return r.status === 'open' && notLiveReason(r, now) === null;
}

/** A stage the server may or may not have told us about. Absent -> unknown. */
function serverStage(
  key: 'role' | 'process',
  label: string,
  known: boolean | undefined,
  doneText: string,
  openText: string,
): SetupDot {
  if (known === true) return { key, label, state: 'done', text: `${label}: ${doneText}` };
  if (known === false) return { key, label, state: 'open', text: `${label}: ${openText}` };
  return { key, label, state: 'unknown', text: `${label}: open to check` };
}

export function setupDots(r: ListFields): SetupDot[] {
  const approval: SetupDot = (() => {
    const label = 'Budget and approval';
    switch (r.approval_status) {
      case 'approved':
        return { key: 'approval', label, state: 'done', text: `${label}: approved` } as const;
      case 'pending_approval':
        return { key: 'approval', label, state: 'waiting', text: `${label}: waiting for approval` } as const;
      case 'rejected':
        return { key: 'approval', label, state: 'open', text: `${label}: changes requested` } as const;
      default:
        return { key: 'approval', label, state: 'open', text: `${label}: not submitted` } as const;
    }
  })();
  const live = isAcceptingApplications(r);
  return [
    serverStage('role', 'Role and posting', r.has_published_jd, 'job description published', 'no published job description'),
    serverStage('process', 'Hiring process', r.has_published_workflow, 'workflow published', 'no published workflow'),
    approval,
    {
      key: 'golive',
      label: 'Go live',
      state: live ? 'done' : 'open',
      text: live ? 'Go live: accepting applications' : 'Go live: not accepting applications',
    },
    {
      key: 'documents',
      label: 'Preboarding documents',
      state: 'optional',
      text: 'Preboarding documents: optional',
    },
  ];
}

/** How many of the four checkable stages the list can prove. Documents never count. */
export function provenCount(dots: readonly SetupDot[]): number {
  return dots.filter((d) => d.state === 'done').length;
}

export interface NextAction {
  /** "Next: submit for approval" — the arrow is added by the renderer. */
  text: string;
  to: string;
  /** True when the action is waiting on someone else rather than on you. */
  passive?: boolean;
}

/**
 * The single next action for an opening, as a link. Setup actions go to the
 * Setup tab, where the hub opens the right stage; decisions go to the queue.
 */
export function nextAction(r: Requisition): NextAction | null {
  const base = `/hr/requisitions/${r.id}`;
  if (r.awaiting_decision > 0 && r.status !== 'closed') {
    return {
      text: `Next: decide on ${r.awaiting_decision}`,
      to: `${base}/decisions`,
    };
  }
  if (r.status === 'closed') {
    return r.awaiting_decision > 0
      ? { text: `Next: decide on ${r.awaiting_decision}`, to: `${base}/decisions` }
      : null;
  }
  if (r.status === 'paused') {
    // HR-M8: the server refuses to reopen while the closing date has passed,
    // so "reopen" would send HR to a button that only earns a 409.
    return closingDatePassed(r)
      ? { text: 'Paused: set a new closing date to reopen, or close it', to: `${base}?tab=setup` }
      : { text: 'Paused: reopen or close', to: base };
  }
  // Setup runs JD -> hiring process -> approval -> go live. Only a stage the
  // server SAYS is missing is named; an absent field is never read as missing.
  // Approval can be submitted while these are still open, so when it is already
  // with someone else the text says both are moving. A sent-back opening keeps
  // its own, more specific instruction below.
  if (r.approval_status !== 'rejected') {
    const gap =
      r.has_published_jd === false
        ? 'publish the job description'
        : r.has_published_workflow === false
          ? 'set up the hiring process'
          : null;
    if (gap) {
      return {
        text:
          r.approval_status === 'pending_approval'
            ? `Next: ${gap} (approval is in progress)`
            : `Next: ${gap}`,
        to: `${base}?tab=setup`,
      };
    }
  }
  switch (r.approval_status) {
    case 'draft':
      return { text: 'Next: submit for approval', to: `${base}?tab=setup` };
    case 'rejected':
      return { text: 'Next: revise and resubmit', to: `${base}?tab=setup` };
    case 'pending_approval':
      return { text: 'Waiting for approval', to: `${base}?tab=setup`, passive: true };
    default:
      break;
  }
  if (closingDatePassed(r)) {
    // Applications stopped on their own. With nobody left in it the opening
    // closes itself (data_gateway app/auto_close.py); with candidates still in
    // progress it stays open — nobody is rejected automatically. (Anyone
    // waiting on a DECISION was already sent to the queue above; these are
    // mid-round, waiting on themselves.)
    const waiting = r.unresolved ?? 0;
    return waiting > 0
      ? {
          text: `Closing date passed: ${waiting} still in progress - close it once they finish`,
          to: `${base}?tab=pipeline`,
        }
      : { text: 'Closing date passed: closing automatically', to: base, passive: true };
  }
  if (!isAcceptingApplications(r)) {
    const allProven = r.has_published_jd === true && r.has_published_workflow === true;
    return { text: allProven ? 'Next: go live' : 'Next: finish setup', to: `${base}?tab=setup` };
  }
  return { text: 'Open the pipeline', to: `${base}?tab=pipeline`, passive: true };
}

/**
 * Sort rank, lowest first: openings that need a person come before those that
 * are waiting on someone else, before the ones that are simply running.
 */
export function attentionRank(r: Requisition): number {
  if (r.awaiting_decision > 0) return 0;
  if (r.status === 'closed') return 7;
  if (r.status === 'paused') return 6;
  if (r.approval_status === 'rejected') return 1;
  if (r.approval_status === 'draft') return 2;
  if (r.approval_status === 'approved' && !r.public_apply_enabled) return 3;
  if (r.approval_status === 'pending_approval') return 4;
  return 5;
}

export type OpeningSort = 'needs-you' | 'newest' | 'title';

export function sortOpenings(rows: readonly Requisition[], by: OpeningSort): Requisition[] {
  const out = [...rows];
  const newest = (a: Requisition, b: Requisition): number =>
    b.created_at.localeCompare(a.created_at);
  if (by === 'title') return out.sort((a, b) => a.title.localeCompare(b.title));
  if (by === 'newest') return out.sort(newest);
  return out.sort(
    (a, b) =>
      attentionRank(a) - attentionRank(b) || b.awaiting_decision - a.awaiting_decision || newest(a, b),
  );
}

/** Case-insensitive match on title, department and location. */
export function matchesQuery(r: Requisition, q: string): boolean {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  return [r.title, r.department, r.location, r.level]
    .filter((v): v is string => Boolean(v))
    .some((v) => v.toLowerCase().includes(needle));
}
