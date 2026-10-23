// Ratchet guards for the structural UI rules (docs/UI-CONVENTIONS.md,
// "Guards"): one Button, the shared form controls, ui/table, Radix dialogs and
// Skeleton.
//
// These rules are being migrated file by file, so they cannot fail on every
// file that breaks them today. Instead each rule has an ALLOWLIST,
// allowlists/<rule id>.txt, naming the files that broke it when the guard was
// written (one src-relative path per line, sorted). The test fails when a file
// NOT on its rule's list breaks the rule, so no new file can add a breach and
// no clean file can regress. It does not fail when a listed file becomes
// clean: migrating a file needs no edit to the list, and the lists are emptied
// in one cleanup step once the migrations land. Do not add a path to a list to
// get a change through; use the shared component.
//
// The rules themselves, and the files exempt from each (the primitive's own
// implementation and the documented exceptions), are in structuralRules.ts.

import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { SRC, readAllowlist, rel, sourceFiles } from './sourceScan';
import { STRUCTURAL_RULES, breaches } from './structuralRules';

const files = sourceFiles();
const found = new Map<string, Map<string, number[]>>(); // rule id -> file -> lines
for (const f of files) {
  for (const [rule, lines] of breaches(f, readFileSync(f, 'utf-8'))) {
    const byFile = found.get(rule) ?? new Map<string, number[]>();
    byFile.set(rel(f), lines);
    found.set(rule, byFile);
  }
}

describe.each(STRUCTURAL_RULES.map((r) => [r.id, r] as const))('ratchet: %s', (id, rule) => {
  const allowed = readAllowlist(id);

  it('adds no file to the files that break it', () => {
    const fresh = [...(found.get(id) ?? new Map<string, number[]>())]
      .filter(([file]) => !allowed.includes(file))
      .map(([file, lines]) => `${file}:${lines.join(',')}`);
    expect(fresh, `${rule.summary} in a file not on allowlists/${id}.txt - use ${rule.use}:\n${fresh.join('\n')}`).toEqual([]);
  });

  it('keeps its allowlist sorted, unique and free of exempt files', () => {
    expect([...allowed].sort()).toEqual(allowed);
    expect(new Set(allowed).size).toBe(allowed.length);
    expect(allowed.filter((p) => rule.exempt.includes(p))).toEqual([]);
    expect(allowed.filter((p) => !/^[\w./-]+\.tsx?$/.test(p) || p.startsWith('/'))).toEqual([]);
  });
});

describe('the ratchet rules catch what they say', () => {
  const probe = (text: string) => breaches(join(SRC, '__probe__.tsx'), text);

  it('flags each structure, and leaves the allowed forms alone', () => {
    const hits = probe(
      [
        "const PRIMARY = 'inline-flex bg-primary text-primary-foreground';",
        'export const A = () => <button className="rounded bg-primary px-4">Save</button>;',
        'export const B = () => <button className={PRIMARY}>Save</button>;',
        'export const C = () => <button className="bg-primary/10 text-primary">Chip</button>;',
        'export const D = () => <input value="" />;',
        'export const E = () => <input type="checkbox" />;',
        'export const F = () => <select />;',
        'export const G = () => <textarea />;',
        'export const H = () => <table />;',
        'export const I = () => <div role="dialog" />;',
        "export const J = () => <div className={cn('h-4 animate-pulse')} />;",
        'export const K = () => <div role="region" className="motion-safe:animate-pulse" />;',
      ].join('\n'),
    );
    expect(hits.get('raw-primary-button')).toEqual([2, 3]);
    expect(hits.get('native-form-control')).toEqual([5, 7, 8]);
    expect(hits.get('raw-table')).toEqual([9]);
    expect(hits.get('hand-built-dialog')).toEqual([10]);
    expect(hits.get('hand-built-skeleton')).toEqual([11]);
  });

  it('every rule has an allowlist file, and every allowlist a rule', () => {
    const lists = readdirSync(join(__dirname, 'allowlists'))
      .filter((n) => n.endsWith('.txt'))
      .map((n) => n.replace(/\.txt$/, ''))
      .sort();
    expect(lists).toEqual(STRUCTURAL_RULES.map((r) => r.id).sort());
  });
});
