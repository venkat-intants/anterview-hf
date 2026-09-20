// examLocks — PH4-D1. The two lock sentences reused verbatim from
// services/data_gateway/app/exam_locks.py (PUBLISHED_REASON, TAKEN_REASON), so
// the banner shown before a write is attempted reads exactly like the 409 a
// write would get back if it went ahead anyway.
//
// `roundLockReason` covers the case the frontend can know WITHOUT a write:
// once `status === 'published'` the round is locked, full stop — the backend
// checks that before it ever looks at attempts (see `round_lock_reason`).
// A round genuinely cannot carry attempts while `draft`: the DB trigger
// (`exam_rounds_frozen`) refuses a published→draft transition whenever
// attempts exist, and `ExamRound.status` only ever holds `draft` or
// `published`. So TAKEN_REASON is not reachable from round metadata alone —
// it can only ever come back as the text of a 409 from an actual write, which
// every mutation here shows verbatim (see hr_rounds.py, hr_exams.py,
// hr_coding.py, question_banks.py callers).

import type { RoundStatus } from '@/api/exams';

export const PUBLISHED_REASON =
  'This round is published — its content is fixed. Unpublish it while nobody has ' +
  'taken it, or duplicate it to make changes.';

export const TAKEN_REASON = 'This round has been taken — its content is fixed.';

/** The proactive half of the lock — see the module comment for why this is
 *  the only case knowable before a write is attempted. */
export function roundLockReason(status: RoundStatus): string | null {
  return status === 'published' ? PUBLISHED_REASON : null;
}
