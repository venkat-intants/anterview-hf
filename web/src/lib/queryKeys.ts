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
