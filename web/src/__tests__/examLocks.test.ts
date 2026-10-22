// examLocks — PH4-D1. The two lock sentences must match
// services/data_gateway/app/exam_locks.py EXACTLY, and `roundLockReason` must
// answer the one case knowable without a write (see the module comment).

import { describe, it, expect } from 'vitest';
import {
  PUBLISHED_REASON,
  TAKEN_REASON,
  canUnpublishRound,
  roundLockReason,
} from '../lib/examLocks';

describe('examLocks', () => {
  it('matches the backend sentences verbatim', () => {
    expect(PUBLISHED_REASON).toBe(
      'This round is published - its content is fixed. Unpublish it while nobody has ' +
        'taken it, or duplicate it to make changes.',
    );
    expect(TAKEN_REASON).toBe('This round has been taken - its content is fixed.');
  });

  it('locks a published round with PUBLISHED_REASON', () => {
    expect(roundLockReason('published')).toBe(PUBLISHED_REASON);
  });

  it('leaves a draft round unlocked', () => {
    expect(roundLockReason('draft')).toBeNull();
  });

  // Audit E2: a taken round used to offer Unpublish, which the server always
  // refused with "reload and try again". Its banner now names the real reason
  // and the only way forward.
  it('locks a taken round with the taken sentence and offers no unpublish', () => {
    expect(roundLockReason('published', 3)).toBe(
      'This round has been taken - its content is fixed. Duplicate it to make changes.',
    );
    expect(canUnpublishRound('published', 3)).toBe(false);
    expect(canUnpublishRound('published', 0)).toBe(true);
    expect(canUnpublishRound('draft', 0)).toBe(false);
  });
});
