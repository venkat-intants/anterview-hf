// Guard: the visible-text rules of docs/UI-CONVENTIONS.md ("Words" and
// "Feedback copy") cannot regress.
//
// It reads every user-visible literal in the app (sourceScan.visibleText: JSX
// text, rendering attributes such as aria-label / title / placeholder, and
// prose strings such as toast copy, error fallbacks and every i18n value), and
// fails on a phrase the glossary retired. Identifiers, routes, class lists,
// i18n keys, enum values and comparison operands are not visible text and are
// never checked, so `onRetry`, '/login' and `case 'posting':` are fine.
//
// A literal whose wording is fixed for a reason outside this guide (today: the
// consent and automated-decision disclosures, which change only with legal
// review) carries a `// ui-copy-exempt: <reason>` comment on the line above;
// the marker covers the first string after it and nothing else.

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { SRC, parse, sourceFiles, visibleText, type Hit } from './sourceScan';

interface Rule {
  id: string;
  /** What to write instead, shown in the failure. */
  use: string;
  test: (text: string) => boolean;
}

const re = (pattern: RegExp) => (text: string) => pattern.test(text);

const RULES: Rule[] = [
  { id: 'mock-interview', use: 'Practice interview', test: re(/\bmock interviews?\b/i) },
  {
    id: 'log-in',
    use: 'Sign in / Sign out',
    test: (t) =>
      /\bLog ?(?:in|out)\b/.test(t) ||
      /\blog (?:in|out)\b/.test(t) ||
      /(?:^|[\s(“"])log(?:in|out)(?=[\s.,;:!?)”"]|$)/.test(t),
  },
  { id: 'retry', use: 'Try again', test: re(/\bRetry\b/) },
  { id: 'couldnt', use: 'Could not ...', test: re(/\bCouldn[’']t\b/i) },
  { id: 'failed-to', use: 'Could not <verb> <object>.', test: re(/\bFailed to\b/i) },
  { id: 'something-went-wrong', use: 'Could not <verb> <object>. <what to do>.', test: re(/\bSomething went wrong\b/i) },
  {
    // A bare "Upload failed" / "AI generation failed." used as the whole message.
    id: 'x-failed',
    use: 'Could not <verb> <object>.',
    test: (t) => /^[A-Z][\w-]*(?: [\w-]+){0,3} failed[.!]?$/.test(t.trim()),
  },
  { id: 'lazy-plural', use: 'a real plural ("1 question", "2 questions")', test: re(/\w\(s\)/) },
  { id: 'position', use: 'opening (staff) or job (candidate)', test: re(/\bpositions?\b/i) },
  { id: 'posting', use: 'opening, or the opening’s public details', test: re(/\bpostings?\b/i) },
  { id: 'cv', use: 'resume', test: re(/\bCVs?\b/) },
  {
    id: 'american-spelling',
    use: 'British spelling (authorisation, organisation, personalise, cancelled)',
    test: re(
      /\b(?:authori[z]|organi[z]|personali[z]|customi[z]|optimi[z]|summari[z]|recogni[z]|prioriti[z]|finali[z]|categori[z]|minimi[z]|maximi[z]|utili[z]|analy[z]|behavior|cancele?d|canceling)/i,
    ),
  },
  {
    id: 'practice-verb',
    use: 'practise (the verb)',
    test: re(/\b(?:to|can|and|you|will) practice\b(?! (?:interviews?|jobs?|roles?|plans?|sessions?|mode)\b)/i),
  },
];

/** Lines (1-based) whose first string literal is exempt, per file. */
function exemptLines(text: string): Set<number> {
  const lines = text.split(/\r?\n/);
  const out = new Set<number>();
  lines.forEach((line, i) => {
    if (!line.includes('ui-copy-exempt:')) return;
    for (let j = i + 1; j < lines.length; j++) {
      if (/['"`]/.test(lines[j])) {
        out.add(j + 1);
        return;
      }
    }
  });
  return out;
}

function violations(file: string, text: string): string[] {
  const exempt = exemptLines(text);
  return visibleText(file, parse(file, text))
    .filter((h: Hit) => !exempt.has(h.line))
    .flatMap((h) =>
      RULES.filter((r) => r.test(h.text)).map(
        (r) => `${h.file}:${h.line} [${r.id}] "${h.text.slice(0, 90)}" - use ${r.use}`,
      ),
    );
}

describe('visible text follows the glossary', () => {
  const files = sourceFiles();

  it('scans the app, including the i18n catalogue', () => {
    expect(files.length).toBeGreaterThan(200);
    expect(files.some((f) => f.endsWith(join('lib', 'i18n.ts')))).toBe(true);
  });

  it('uses no retired phrase', () => {
    const all = files.flatMap((f) => violations(f, readFileSync(f, 'utf-8')));
    expect(all, all.join('\n')).toEqual([]);
  });

  it('catches each retired phrase, and ignores identifiers, keys, routes and comments', () => {
    const sample = [
      '// a comment saying Retry, CV and mock interview is fine',
      "import { x } from './postings';",
      'export const A = () => <button onClick={onRetry}>Retry</button>;',
      "export const B = { failed: 'Upload failed.' };",
      "export const C = 'Failed to load jobs.';",
      "export const D = `Moved ${n} candidate(s)`;",
      'export const E = () => <a href="/login">Log in</a>;',
      "export const F = () => <span title=\"View CV\" />;",
      "export const G = (s: string) => s === 'posting';",
      "export const H = { posting: t('jobs.posting') };",
      "export const I = 'Missing authorization code.';",
      "export const J = 'Ready to practice?';",
      "export const K = 'Go to practice interviews';",
      '// ui-copy-exempt: legal wording',
      "export const L = 'Store my CV';",
      "export const M = 'Your CV';",
      "export const N = () => toast.error('Failed to save.');",
      "export const O = () => toast.info('Retry in a minute.');",
      "export const P = () => console.error('Failed to save.');",
      "export const Q = () => logger.warn('Failed to save.');",
    ].join('\n');
    const hits = violations(join(SRC, '__probe__.tsx'), sample);
    const ids = hits.map((h) => /\[([\w-]+)\]/.exec(h)?.[1]);
    expect(ids).toEqual([
      'retry',
      'x-failed',
      'failed-to',
      'lazy-plural',
      'log-in',
      'cv',
      'american-spelling',
      'practice-verb',
      'cv',
      'failed-to',
      'retry',
    ]);
    // The exempt line is skipped; the one after it is not.
    expect(hits.some((h) => h.includes('Store my CV'))).toBe(false);
    expect(hits.some((h) => h.includes('Your CV'))).toBe(true);
  });
});
