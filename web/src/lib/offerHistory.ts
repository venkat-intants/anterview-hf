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
