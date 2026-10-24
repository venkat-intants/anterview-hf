// Re-audit (exams): minutesToSeconds read "0" or a negative number as null
// ("no limit"), so typing 0 into a time-limit field cleared the limit and
// toasted "Time limit saved". Only blank means no limit now.

import { describe, expect, it } from 'vitest';
import { minutesToSeconds, parseMinutes } from '../lib/examEditor';

describe('parseMinutes', () => {
  it('reads blank as no limit', () => {
    expect(parseMinutes('')).toEqual({ kind: 'none' });
    expect(parseMinutes('   ')).toEqual({ kind: 'none' });
  });

  it('reads whole minutes as seconds, rounding a fraction', () => {
    expect(parseMinutes('15')).toEqual({ kind: 'limit', seconds: 900 });
    expect(parseMinutes(' 2.6 ')).toEqual({ kind: 'limit', seconds: 180 });
  });

  it('calls 0, a negative number, under half a minute or junk invalid', () => {
    for (const raw of ['0', '-5', '0.4', 'abc', 'Infinity']) {
      expect(parseMinutes(raw)).toEqual({ kind: 'invalid' });
    }
  });
});

describe('minutesToSeconds', () => {
  it('never yields 0 seconds', () => {
    expect(minutesToSeconds('0.4')).toBeNull();
    expect(minutesToSeconds('10')).toBe(600);
  });
});
