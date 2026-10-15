/**
 * UX round 3, stream D — the signed-in candidate pages are framed by `<Page>`.
 *
 * AppShell's <main> owns the only gutters, so a page renders
 * `<Page width="form|default|workspace">` and never sets `mx-auto`, `max-w-*`
 * and padding on its own outermost wrapper (that doubled the padding and moved
 * the title's left edge from page to page). Reads the source of each page this
 * stream owns and fails if such a wrapper has come back, and checks each one
 * actually uses `<Page`.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const PAGES = path.resolve(__dirname, '../pages');

const FRAMED = [
  'Dashboard.tsx',
  'Applications.tsx',
  'Onboarding.tsx',
  'JobsList.tsx',
  'History.tsx',
  'Resume.tsx',
  'Profile.tsx',
  'ProfileView.tsx',
  'StartInterview.tsx',
];

/** A className that is a page frame: centred, width-capped and padded. */
function frameWrappers(source: string): string[] {
  const hits: string[] = [];
  for (const m of source.matchAll(/className="([^"]*)"/g)) {
    const cls = m[1];
    if (
      /\bmx-auto\b/.test(cls) &&
      /\bmax-w-\[/.test(cls) &&
      /(^|\s)(?:[a-z]+:)?p[xy]-/.test(cls) &&
      // A frame is a block-level wrapper; centred copy and chips are not.
      !/\b(truncate|animate-spin|text-center|rounded)/.test(cls)
    ) {
      hits.push(cls);
    }
  }
  return hits;
}

describe('candidate pages are framed by <Page>', () => {
  it.each(FRAMED)('%s renders <Page and no hand-rolled frame', (file) => {
    const source = readFileSync(path.join(PAGES, file), 'utf8');
    expect(source, `${file} should render <Page`).toMatch(/<Page\b/);
    expect(frameWrappers(source), `${file} has a hand-rolled frame`).toEqual([]);
  });
});
