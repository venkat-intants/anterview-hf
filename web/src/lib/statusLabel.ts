// statusLabel - every status value the HR console shows, as words (audit X10,
// X12).
//
// Raw values leaked onto screens next to title-case pills: "held" and "open" in
// the applicants modal, the candidate drawer and the opening header; "awaiting
// slot" and "no show" on interview sessions. And one state had two names:
// a returned approval said "Sent back" on the offer screens and "Changes
// requested" elsewhere.
//
// The same stored value means different things in different places ('rejected'
// is a candidate decision, but on an offer or an approval it means the approver
// sent it back; 'published' is "Live" for an exam), so a label is looked up by
// DOMAIN. An unknown value is never shown raw: it is humanised ("on_hold" ->
// "On hold") so a person can still read and report it.
//
// Labels are sentence case. Staff screens are English-only by decision, so
// these are plain strings, not i18n keys.

export type StatusDomain =
  | 'candidate'
  | 'opening'
  | 'approval'
  | 'offer'
  | 'session'
  | 'loop'
  | 'invite'
  | 'exam'
  | 'round'
  | 'task'
  | 'scorecard'
  | 'accommodation'
  | 'workflow'
  | 'review'
  | 'bankQuestion'
  | 'corpus';

/** "Sent back": the one name for an approver returning something to its author. */
const SENT_BACK = 'Sent back';

const LABELS: Record<StatusDomain, Record<string, string>> = {
  // An application on an opening (enrolment, applicant, pipeline row).
  candidate: {
    new: 'New',
    shortlisted: 'Shortlisted',
    held: 'Held',
    interviewed: 'Interviewed',
    hired: 'Hired',
    rejected: 'Rejected',
    withdrawn: 'Withdrawn',
    exam_passed: 'Exam passed',
  },
  opening: { open: 'Open', paused: 'Paused', closed: 'Closed' },
  approval: {
    draft: 'Not submitted',
    pending_approval: 'Awaiting approval',
    approved: 'Approved',
    rejected: SENT_BACK,
  },
  offer: {
    draft: 'Draft',
    pending_approval: 'Awaiting approval',
    approved: 'Approved',
    rejected: SENT_BACK,
    sent: 'Sent',
    accepted: 'Accepted',
    declined: 'Declined',
    expired: 'Expired',
    withdrawn: 'Withdrawn',
  },
  session: {
    awaiting_slot: 'Awaiting slot',
    scheduled: 'Scheduled',
    cancelled: 'Cancelled',
    completed: 'Completed',
    no_show: 'No show',
  },
  loop: { draft: 'Draft', scheduled: 'Scheduled', completed: 'Completed', cancelled: 'Cancelled' },
  // An AI interview invite. 'consumed' is the link having been opened.
  invite: {
    invited: 'Invited',
    consumed: 'In progress',
    completed: 'Completed',
    expired: 'Expired',
    revoked: 'Revoked',
  },
  exam: { draft: 'Draft', published: 'Live', closed: 'Closed' },
  round: { draft: 'Draft', published: 'Published' },
  task: {
    assigned: 'Assigned',
    in_progress: 'In progress',
    submitted: 'Submitted',
    expired: 'Expired',
    withdrawn: 'Withdrawn',
  },
  scorecard: {
    assigned: 'Assigned',
    in_progress: 'In progress',
    submitted: 'Submitted',
    withdrawn: 'Withdrawn',
  },
  accommodation: { active: 'Active', revoked: 'Revoked' },
  workflow: { draft: 'Draft', published: 'Published', archived: 'Archived' },
  review: {
    draft: 'Draft',
    in_review: 'In review',
    changes_requested: SENT_BACK,
    approved: 'Approved',
  },
  bankQuestion: {
    draft: 'Draft',
    in_review: 'In review',
    approved: 'Approved',
    retired: 'Retired',
  },
  corpus: { parsed: 'Parsed', indexing: 'Indexing', indexed: 'Indexed', failed: 'Failed' },
};

/** "on_hold" -> "On hold": readable, for a value this build has never seen. */
export function humaniseStatus(status: string): string {
  const words = status.replace(/[_-]+/g, ' ').trim().toLowerCase();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : '';
}

/**
 * The words for `status` in `domain`. '-' for no status; an unknown value is
 * humanised rather than shown raw.
 */
export function statusLabel(status: string | null | undefined, domain: StatusDomain): string {
  if (!status) return '-';
  return LABELS[domain][status] ?? humaniseStatus(status);
}

/** Every value a domain knows, for building filters and tests. */
export function knownStatuses(domain: StatusDomain): string[] {
  return Object.keys(LABELS[domain]);
}
