// Pure rules behind the public offer page's three steps and its documents count.
// Kept out of the component so each can be pinned by a table-driven test.

import type { PublicChecklistItem, PublicDocumentsChecklist } from '@/api/publicOffer';

export type OfferStepKey = 'review' | 'accept' | 'documents';

export interface OfferStepState {
  /** The step the candidate is on. */
  current: OfferStepKey;
  /** True once the hiring team has marked the joining documents complete. */
  complete: boolean;
}

/**
 * Which of Review -> Accept -> Joining documents the candidate is on, from what
 * the server holds:
 *  - `sent`: Review, or Accept once the candidate has opened the accept panel.
 *    (Declining is not a step on this path, so it leaves them on Review.)
 *  - `accepted`: Joining documents; `complete` when preboarding is marked done.
 * Any other status (declined, expired, withdrawn) has no steps: the offer is not
 * going anywhere, so the page says so instead of drawing a path.
 */
export function offerStep(
  status: string,
  preboardingCompletedAt: string | null | undefined,
  answering: 'accept' | 'decline' | null = null,
): OfferStepState | null {
  if (status === 'sent')
    return { current: answering === 'accept' ? 'accept' : 'review', complete: false };
  if (status === 'accepted')
    return { current: 'documents', complete: Boolean(preboardingCompletedAt) };
  return null;
}

export interface DocumentsRemaining {
  /** Required documents not yet verified (the server's own `outstanding`). */
  n: number;
  /** Required documents in all. */
  total: number;
  /** Of the `n`, how many are already sent and waiting on the hiring team. */
  inReview: number;
}

/**
 * "n of N still needed", from the checklist the server returned. `outstanding`
 * is the server's list of required documents that are not yet verified, so a
 * document that is sent and in review still counts as needed until HR verifies
 * it; `inReview` lets the page say which of them need nothing more from the
 * candidate.
 */
export function documentsRemaining(
  checklist: Pick<PublicDocumentsChecklist, 'items' | 'outstanding'>,
): DocumentsRemaining {
  const required: PublicChecklistItem[] = checklist.items.filter((i) => i.mandatory);
  const n = checklist.outstanding.length;
  return {
    n,
    total: Math.max(required.length, n),
    inReview: Math.min(n, required.filter((i) => i.state === 'submitted').length),
  };
}
