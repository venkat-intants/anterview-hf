// workloadPanel — the week presets are cut in India time, as the server counts
// days and ISO weeks (panel_workload.WORK_TZ), whatever the browser's zone
// (IV-L11), and the capacity field check mirrors the server's limits.

import { afterEach, describe, expect, it } from 'vitest';
import { capacityProblem, presetRange } from '../lib/workloadPanel';

const originalTz = process.env.TZ;
afterEach(() => {
  if (originalTz === undefined) delete process.env.TZ;
  else process.env.TZ = originalTz;
});

describe('presetRange', () => {
  it('starts "this week" at Monday 00:00 IST, even for a browser in New York', () => {
    process.env.TZ = 'America/New_York';
    // Sunday 20:00 in New York is already Monday 05:30 in India: the server
    // counts this instant in the NEW week, so the preset must too.
    const now = new Date('2026-09-28T00:00:00Z');
    const { start, end } = presetRange('this_week', now);
    expect(start).toBe('2026-09-27T18:30:00.000Z'); // Mon 28 Sep 00:00 IST
    expect(end).toBe('2026-10-04T18:30:00.000Z');
  });

  it('puts "next week" seven days after this one', () => {
    const now = new Date('2026-09-23T10:00:00Z'); // a Wednesday
    const { start, end } = presetRange('next_week', now);
    expect(start).toBe('2026-09-27T18:30:00.000Z');
    expect(end).toBe('2026-10-04T18:30:00.000Z');
  });

  it('keeps the 30-day preset anchored at now', () => {
    const now = new Date('2026-09-23T10:00:00Z');
    const { start, end } = presetRange('next_30_days', now);
    expect(start).toBe(now.toISOString());
    expect(Date.parse(end) - Date.parse(start)).toBe(30 * 24 * 60 * 60 * 1000);
  });
});

describe('capacityProblem', () => {
  it('accepts blank (the default) and whole numbers in range', () => {
    expect(capacityProblem('', 24, 'Daily')).toBeNull();
    expect(capacityProblem('24', 24, 'Daily')).toBeNull();
  });

  it('refuses zero, fractions and values over the limit', () => {
    expect(capacityProblem('0', 24, 'Daily')).toMatch(/from 1 to 24/);
    expect(capacityProblem('2.5', 24, 'Daily')).toMatch(/whole number/);
    expect(capacityProblem('101', 100, 'Weekly')).toMatch(/Weekly limit/);
  });
});
