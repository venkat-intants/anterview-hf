// Query keys for the requisition (opening) family, in one place.
//
// WHY THIS FILE EXISTS
// Three panels once wrote `['requisition', id]` while the page read
// `['hr', 'requisition', id]`, and nothing read the first key, so "Submit for
// approval" refreshed nothing and the approval pill stayed on "Not submitted".
// Two spellings of one key cannot drift if there is only one spelling: anything
// that reads, invalidates or writes an opening goes through these builders.
//
// The record key is a PREFIX of the sub-resource keys (enrolments, document
// requirements), so invalidating `requisition(id)` also refreshes those, which
// is what the existing readers of `['hr', 'requisition', id, ...]` rely on.
//
// The second half of the file is the convention for invalidating after an HR
// mutation: by prefix, through invalidateAfterHrMutation.

import type { QueryClient, QueryKey } from '@tanstack/react-query';

export const queryKeys = {
  /** The opening itself: `getRequisition(id)`. */
  requisition: (id: string) => ['hr', 'requisition', id] as const,
  /** The opening's document requirements (preboarding). */
  requisitionDocumentRequirements: (id: string) =>
    ['hr', 'requisition', id, 'document-requirements'] as const,
  /** The opening's enrolments as the review screen reads them. */
  requisitionReviewEnrolments: (id: string) =>
    ['hr', 'requisition', id, 'enrolments', 'review'] as const,
  /** The opening's JD versions (draft and published). */
  jdHistory: (id: string) => ['jd-history', id] as const,
  /** The opening's scheduled go-live. */
  publishSchedule: (id: string) => ['publish-schedule', id] as const,
} as const;

// ── Invalidating after an HR mutation (audit X7) ──────────────────────────────
//
// THE CONVENTION. A mutation in the HR console invalidates by PREFIX, not by
// the one key the author happened to think of. Lists went stale because each
// mutation named a single exact key: deciding a candidate refreshed the drawer
// but not the decision queue; sending an offer refreshed the offer but not the
// offers list or the pipeline card that shows it. Those views all live under
// `['hr', ...]` (TanStack Query matches a prefix against every longer key), so:
//
//   onSuccess: () => invalidateAfterHrMutation(qc)
//
// refreshes every HR view that is on screen and marks the rest stale. It is
// cheap by construction: invalidateQueries refetches only MOUNTED queries;
// everything else refetches when it is next opened, which would have happened
// anyway. Pass extra prefixes for state that lives outside `['hr']`, e.g. the
// opening's JD history or a super-admin approval queue:
//
//   invalidateAfterHrMutation(qc, queryKeys.jdHistory(id), ['admin', 'offer-approvals'])
//
// Narrow invalidation is still right for high-frequency edits (typing into an
// autosaving field) where refetching the whole console per keystroke would be
// wasteful; say so in a comment at that call site.

/** Every HR console query key starts with this. */
export const HR_ROOT = ['hr'] as const;

/**
 * Prefixes outside `['hr']` that HR state changes also feed: the attention
 * panel (`StagesAtRisk`, the home widgets) keys on `['hr-attention', ...]`.
 */
export const HR_ADJACENT: readonly QueryKey[] = [['hr-attention']];

/** Invalidate each prefix (and so every key that starts with it). */
export async function invalidatePrefixes(
  qc: QueryClient,
  prefixes: readonly QueryKey[],
): Promise<void> {
  await Promise.all(prefixes.map((queryKey) => qc.invalidateQueries({ queryKey })));
}

/**
 * After a mutation that changes HR-visible state: refresh the whole HR console
 * (`['hr', ...]`), the attention panel, and any `extra` prefixes given.
 */
export function invalidateAfterHrMutation(
  qc: QueryClient,
  ...extra: readonly QueryKey[]
): Promise<void> {
  return invalidatePrefixes(qc, [HR_ROOT, ...HR_ADJACENT, ...extra]);
}
