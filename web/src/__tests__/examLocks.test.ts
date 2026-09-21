// examLocks — PH4-D1. The two lock sentences must match
// services/data_gateway/app/exam_locks.py EXACTLY, and `roundLockReason` must
// answer the one case knowable without a write (see the module comment).

import { describe, it, expect } from 'vitest';
import { PUBLISHED_REASON, TAKEN_REASON, roundLockReason } from '../lib/examLocks';

describe('examLocks', () => {
  it('matches the backend sentences verbatim', () => {
    expect(PUBLISHED_REASON).toBe(
      'This round is published — its content is fixed. Unpublish it while nobody has ' +
        'taken it, or duplicate it to make changes.',
    );
    expect(TAKEN_REASON).toBe('This round has been taken — its content is fixed.');
  });

  it('locks a published round with PUBLISHED_REASON', () => {
    expect(roundLockReason('published')).toBe(PUBLISHED_REASON);
  });

  it('leaves a draft round unlocked', () => {
    expect(roundLockReason('draft')).toBeNull();
  });
});
