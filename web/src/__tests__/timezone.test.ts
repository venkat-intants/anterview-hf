import { describe, expect, it } from 'vitest';
import { formatSessionWhen } from '../lib/timezone';

// 2026-09-21 05:00 UTC: 10:30 on Monday in Kolkata, 06:00 on Monday in London,
// and still 22:00 on Sunday in Los Angeles.
const AT = '2026-09-21T05:00:00Z';

describe('formatSessionWhen', () => {
  it('names the day, not only the time (a list of sessions must say which day)', () => {
    const text = formatSessionWhen(AT, 'Asia/Kolkata', 'Asia/Kolkata');
    expect(text).toMatch(/Mon/);
    expect(text).toMatch(/21/);
    expect(text).toMatch(/10:30/);
    expect(text).not.toMatch(/your time/);
  });

  it('adds the reader’s time alone when it is the same day for them', () => {
    const text = formatSessionWhen(AT, 'Asia/Kolkata', 'Europe/London');
    expect(text).toMatch(/10:30/);
    expect(text).toMatch(/\(06:00 your time\)/);
  });

  it('adds the reader’s day as well when it falls on another day for them', () => {
    const text = formatSessionWhen(AT, 'Asia/Kolkata', 'America/Los_Angeles');
    expect(text).toMatch(/\(Sun.*22:00 your time\)/);
  });
});
