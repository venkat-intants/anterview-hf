// offerHistory.ts — PH4-A3. Each offer-history action in words, for HR's offer
// page and the super admin's approval page alike (they had drifted: the
// approval page lacked four of them). Covers every action offer_events allows.

const WORDS: Record<string, string> = {
  created: 'Created',
  updated: 'Updated',
  submitted: 'Submitted for approval',
  recalled: 'Recalled',
  reopened: 'Reopened for editing',
  approved: 'Approved',
  rejected: 'Sent back',
  sent: 'Sent to the candidate',
  resent: 'Re-sent to the candidate',
  withdrawn: 'Withdrawn',
  viewed: 'Opened by the candidate',
  code_requested: 'Candidate requested a code',
  accepted: 'Accepted',
  declined: 'Declined',
  expired: 'Expired',
  preboarding_completed: 'Preboarding marked complete',
  exported: 'HRMS export prepared',
};

export function offerActionWord(action: string): string {
  return WORDS[action] ?? action.replace(/_/g, ' ');
}

/**
 * One history entry in words. Same as offerActionWord, except that a candidate
 * opening their offer from the portal (recorded as `resent`, because it rotates
 * the link, with `details.by = 'candidate_portal'`) does not read as HR
 * re-sending it with the candidate's name as the sender (audit OF-L3).
 */
export function offerEntryWord(entry: {
  action: string;
  details?: Record<string, unknown> | null;
}): string {
  if (entry.action === 'resent' && entry.details?.by === 'candidate_portal') {
    return 'Candidate opened a fresh link from their portal';
  }
  return offerActionWord(entry.action);
}
