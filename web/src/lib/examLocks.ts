// examLocks: PH4-D1. The lock sentences reused verbatim from
// services/data_gateway/app/exam_locks.py (PUBLISHED_REASON, TAKEN_REASON), so
// the banner shown before a write is attempted reads exactly like the 409 a
// write would get back if it went ahead anyway.
//
// `roundLockReason` covers what the frontend can know WITHOUT a write: once
// `status === 'published'` the round is locked, and once anyone has sat it
// (`attempt_count > 0`, carried by the structure read since audit E2) it can
// never be unpublished either: the DB trigger (`exam_rounds_frozen`) refuses a
// published -> draft transition whenever attempts exist, and the server now
// says so up front (UNPUBLISH_TAKEN_REASON) rather than "reload and try
// again". A round cannot carry attempts while `draft`, for the same reason.

import type { RoundStatus } from '@/api/exams';

export const PUBLISHED_REASON =
  'This round is published - its content is fixed. Unpublish it while nobody has ' +
  'taken it, or duplicate it to make changes.';

export const TAKEN_REASON = 'This round has been taken - its content is fixed.';

/** The sentence for a locked round, or null while it can still be edited.
 *  A taken round gets TAKEN_REASON: "unpublish it" is no longer a way out. */
export function roundLockReason(status: RoundStatus, attemptCount = 0): string | null {
  if (attemptCount > 0) return `${TAKEN_REASON} Duplicate it to make changes.`;
  return status === 'published' ? PUBLISHED_REASON : null;
}

/** Whether Unpublish is a real option: published, and nobody has sat it. */
export function canUnpublishRound(status: RoundStatus, attemptCount = 0): boolean {
  return status === 'published' && attemptCount === 0;
}
