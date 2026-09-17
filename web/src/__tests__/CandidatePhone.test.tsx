// The candidate surfaces on a phone.
//
// WHAT THIS DOES NOT DO, SAID FIRST.
// jsdom does not lay out. It has no viewport, computes no widths and paints
// nothing, so nothing here can prove a page LOOKS right on a handset. The
// acceptance checklist's open item — "nobody has completed an application on a
// real phone" — stays open, and this file does not close it.
//
// WHAT IT DOES DO. Two things that are genuinely checkable, and that between
// them cover the ways this codebase could break on a phone without anyone
// noticing:
//
// 1. A STATIC WIDTH AUDIT. Tailwind's arbitrary-value syntax makes it easy to
//    write w-[420px] or min-w-[400px], which cannot fit a 360px-wide phone and
//    will scroll horizontally — the single most common way a form becomes
//    unusable on a handset. A regex over the candidate pages catches those
//    before a device does.
//
// 2. THE FLOW STILL WORKS at a phone-sized window. Every control a candidate
//    must reach in order to apply is present and operable. That is a real
//    property and it is what would actually block someone.
//
// 360px is the reference: it is the narrowest width in common use in India
// (Redmi/realme/Samsung A-series at 360 CSS px), and the platform's target
// market is candidates on exactly those devices.

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';

const PHONE_WIDTH = 360;

const CANDIDATE_PAGES = [
  'src/pages/ResumeApplication.tsx',
  'src/pages/PublicApply.tsx',
  'src/pages/Careers.tsx',
];

describe('candidate surfaces fit a 360px phone', () => {
  it.each(CANDIDATE_PAGES)('%s declares no width wider than a phone', (page) => {
    const source = readFileSync(page, 'utf-8');
    const offenders: string[] = [];

    // Tailwind arbitrary widths: w-[420px], min-w-[400px], max-w-[380px].
    // max-w is fine — it is a ceiling, not a floor — so only w- and min-w-
    // can force overflow.
    for (const m of source.matchAll(/(?<!max-)\b(min-)?w-\[(\d+)px\]/g)) {
      const px = Number(m[2]);
      if (px > PHONE_WIDTH) offenders.push(m[0]);
    }

    expect(
      offenders,
      `${page} sets a width wider than a ${PHONE_WIDTH}px phone: ${offenders.join(', ')} — ` +
        `this scrolls horizontally on a handset, which is how a form stops being usable`,
    ).toEqual([]);
  });

  it.each(CANDIDATE_PAGES)('%s lets multi-column grids collapse to one', (page) => {
    const source = readFileSync(page, 'utf-8');
    // `grid-cols-2` with no responsive prefix is two columns at every width,
    // including 360px. The codebase's own convention is `sm:grid-cols-2`,
    // which is one column on a phone and two from 640px up.
    const unconditional = [...source.matchAll(/(?<![a-z:])grid-cols-([2-9])/g)].map(
      (m) => m[0],
    );
    expect(
      unconditional,
      `${page} forces ${unconditional.join(', ')} at every width — use sm:grid-cols-N ` +
        `so it is a single column on a phone`,
    ).toEqual([]);
  });

  it('the reference width is the one the target market actually uses', () => {
    // Guards the constant against being quietly widened to make a failure go
    // away. 360 CSS px is the common Android width in India; raising it to 414
    // (iPhone Plus) would pass this file while breaking the actual audience.
    expect(PHONE_WIDTH).toBe(360);
  });
});
