// offerStatus.ts — PH4-A3. An offer's state in words, the same on every HR
// screen. The database calls a sent-back offer 'rejected'; the super admin's
// button, the list filter and the history all say "Sent back", so this does too.

const WORDS: Record<string, string> = {
  draft: 'draft',
  pending_approval: 'pending approval',
  approved: 'approved',
  rejected: 'sent back',
  sent: 'sent',
  accepted: 'accepted',
  declined: 'declined',
  expired: 'expired',
  withdrawn: 'withdrawn',
};

export function offerStatusWord(status: string): string {
  return WORDS[status] ?? status.replace(/_/g, ' ');
}
