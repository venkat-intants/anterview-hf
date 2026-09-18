// offerOutcome.ts — PH4-A3. How an offer ended, in words, for the places HR
// reads a hired candidate. The outcome sits BESIDE the hiring decision: an
// accepted or declined offer never changes the decision itself.

import type { TagTone } from '@/design/components/primitives';
import type { OfferOutcome } from '@/api/pipeline';

const TAGS: Record<OfferOutcome, { label: string; tone: TagTone }> = {
  offer_accepted: { label: 'Offer accepted', tone: 'forest' },
  offer_declined: { label: 'Offer declined', tone: 'ember' },
  offer_expired: { label: 'Offer expired', tone: 'amber' },
  offer_withdrawn: { label: 'Offer withdrawn', tone: 'neutral' },
};

export function offerOutcomeTag(
  outcome: OfferOutcome | null | undefined,
): { label: string; tone: TagTone } | null {
  return outcome ? (TAGS[outcome] ?? null) : null;
}
