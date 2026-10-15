/**
 * UX round 3, stream F1 — the page-frame sweep.
 *
 * AppShell's <main> owns the only gutters. An in-shell page renders
 * `<Page width=...>` and never sets its own `mx-auto`, `max-w-` and padding
 * classes on the outermost wrapper (that is what made the title's left edge move and
 * doubled the padding). This reads the source of every page owned by the sweep
 * and fails if such a wrapper has come back.
 */
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const PAGES = path.resolve(__dirname, '../pages');

/** Directories whose pages are all in-shell (see the routes in App.tsx). */
const DIRS = ['admin', 'interviewer', 'hr', 'exam'];

/**
 * Files deliberately NOT checked.
 *  - exam/CodingTaking.tsx: rendered by PublicExam (/exam), which has no
 *    AppShell; it keeps its own centred frame.
 */
const ALLOWLIST = new Set(['exam/CodingTaking.tsx']);

function ownedFiles(): string[] {
  const out: string[] = [];
  for (const dir of DIRS) {
    for (const name of readdirSync(path.join(PAGES, dir))) {
      const rel = `${dir}/${name}`;
      if (name.endsWith('.tsx') && !ALLOWLIST.has(rel)) out.push(rel);
    }
  }
  return out;
}

/** A single className string that is a page frame: centred, width-capped, padded. */
function frameWrappers(source: string): string[] {
  const hits: string[] = [];
  for (const m of source.matchAll(/className="([^"]*)"/g)) {
    const cls = m[1];
    if (
      /\bmx-auto\b/.test(cls) &&
      /\bmax-w-/.test(cls) &&
      /(^|\s)(?:[a-z]+:)?p[xy]-/.test(cls) &&
      // A page frame is a block-level wrapper; cells and chips are not.
      !/\b(truncate|animate-spin|text-center|rounded)/.test(cls)
    ) {
      hits.push(cls);
    }
  }
  return hits;
}

describe('page frame sweep', () => {
  const files = ownedFiles();

  it('covers the owned directories', () => {
    expect(files).toContain('hr/Applicants.tsx');
    expect(files).toContain('admin/AdminOverview.tsx');
    expect(files).toContain('interviewer/InterviewerConsole.tsx');
    expect(files).not.toContain('exam/CodingTaking.tsx');
  });

  it.each(files)('%s has no mx-auto + max-w + padding wrapper', (rel) => {
    const source = readFileSync(path.join(PAGES, rel), 'utf8');
    expect(frameWrappers(source)).toEqual([]);
  });

  it('detects the old wrapper shape', () => {
    expect(
      frameWrappers('<div className="mx-auto max-w-[1120px] px-6 py-8 lg:px-8">'),
    ).toHaveLength(1);
    expect(
      frameWrappers('<div className="mx-auto max-w-[900px] space-y-6 px-0 py-2">'),
    ).toHaveLength(1);
    expect(frameWrappers('<Page width="default">')).toHaveLength(0);
  });
});
