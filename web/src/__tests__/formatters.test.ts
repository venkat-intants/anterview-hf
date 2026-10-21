// lib/formatters: one way to write a date, a time, an amount and a score
// (audit X6, X11, X13, X18).

import { describe, expect, it } from 'vitest';
import {
  MISSING,
  formatDate,
  formatDateTime,
  formatMoney,
  formatScore,
  uiLocale,
} from '../lib/formatters';

const INSTANT = '2026-10-21T10:00:00Z';

describe('formatScore', () => {
  it('writes resume, exam and round scores out of 100, as whole numbers', () => {
    expect(formatScore('resume', 78)).toBe('78/100');
    expect(formatScore('exam', 71.6)).toBe('72/100');
    expect(formatScore('round', 64.4)).toBe('64/100');
  });

  it('writes an interview out of 10 with one decimal', () => {
    expect(formatScore('interview', 7.05)).toBe('7.1/10');
    expect(formatScore('interview', 8)).toBe('8.0/10');
  });

  it('can drop the unit for a label that says it in words', () => {
    expect(formatScore('interview', 6.74, { unit: false })).toBe('6.7');
  });

  it('writes a missing score as a dash, never 0', () => {
    expect(formatScore('interview', null)).toBe(MISSING);
    expect(formatScore('exam', undefined)).toBe(MISSING);
    expect(formatScore('resume', Number.NaN)).toBe(MISSING);
    expect(formatScore('exam', 0)).toBe('0/100');
  });
});

describe('formatDate', () => {
  it('uses an explicit locale, not the browser default', () => {
    expect(formatDate(INSTANT)).toBe('21 Oct 2026');
  });

  it('writes a missing or unreadable date as a dash', () => {
    expect(formatDate(null)).toBe(MISSING);
    expect(formatDate(undefined)).toBe(MISSING);
    expect(formatDate('not a date')).toBe(MISSING);
  });
});

describe('formatDateTime', () => {
  it('always names the zone', () => {
    expect(formatDateTime(INSTANT, { timeZone: 'Asia/Kolkata' })).toBe('21 Oct 2026, 15:30 IST');
    expect(formatDateTime(INSTANT, { timeZone: 'UTC' })).toBe('21 Oct 2026, 10:00 UTC');
  });

  it('labels the browser zone when none is given', () => {
    const text = formatDateTime(INSTANT);
    // The test runner's zone is not fixed; what matters is that one is named.
    expect(text).toMatch(/^\d{1,2} Oct 2026, \d{2}:\d{2} \S+/);
  });

  it('falls back to the browser zone, still labelled, for an unknown zone', () => {
    expect(formatDateTime(INSTANT, { timeZone: 'Mars/Olympus' })).toMatch(/\d{2}:\d{2} \S+$/);
  });

  it('writes a missing time as a dash', () => {
    expect(formatDateTime(null)).toBe(MISSING);
    expect(formatDateTime('')).toBe(MISSING);
  });
});

describe('formatMoney', () => {
  it('groups in lakhs for en-IN and prints the currency', () => {
    expect(formatMoney(1200000, 'INR')).toBe('₹12,00,000');
    expect(formatMoney('1200000', 'inr')).toBe('₹12,00,000');
  });

  it('prints the grouped number alone without a currency', () => {
    expect(formatMoney(1200000, '')).toBe('12,00,000');
    expect(formatMoney(1200000, null)).toBe('12,00,000');
  });

  it('keeps an unknown currency code readable', () => {
    expect(formatMoney(5000, 'ZZ1')).toBe('ZZ1 5,000');
  });

  it('writes a missing amount as a dash', () => {
    expect(formatMoney(null, 'INR')).toBe(MISSING);
    expect(formatMoney('', 'INR')).toBe(MISSING);
    expect(formatMoney('lots', 'INR')).toBe(MISSING);
  });
});

describe('uiLocale', () => {
  it('maps the three UI languages and defaults to en-IN', () => {
    expect(uiLocale('en')).toBe('en-IN');
    expect(uiLocale('hi')).toBe('hi-IN');
    expect(uiLocale('te-IN')).toBe('te-IN');
    expect(uiLocale('fr')).toBe('en-IN');
    expect(uiLocale(undefined)).toBe('en-IN');
  });
});
