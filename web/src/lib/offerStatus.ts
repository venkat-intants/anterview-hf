// offerStatus.ts — PH4-A3. An offer's state in words, the same on every HR
// screen. The database calls a sent-back offer 'rejected'; the super admin's
// button, the list filter and the history all say "Sent back", so this does too.

import type { OfferStatus } from '@/api/offers';
import type { TagTone } from '@/design/components/primitives';

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

/**
 * One pill colour per offer state, on the list, the detail page and the
 * candidate drawer alike (audit OF-L6: "draft" was neutral on the list and
 * amber on the detail page, "sent" blue on one and green on the other).
 */
export const OFFER_STATUS_TONE: Record<OfferStatus, TagTone> = {
  draft: 'neutral',
  pending_approval: 'amber',
  approved: 'electric',
  rejected: 'ember',
  sent: 'electric',
  accepted: 'forest',
  declined: 'ember',
  expired: 'neutral',
  withdrawn: 'neutral',
};
