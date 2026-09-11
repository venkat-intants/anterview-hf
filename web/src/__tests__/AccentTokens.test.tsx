// --accent and --surface-accent must stay two different tokens.
//
// --accent is the brand SIGNAL (a hex). shadcn's hover/active SURFACE is
// --surface-accent (an HSL triple, consumed as hsl(var(--surface-accent))).
// They used to share the name --accent in the same block, so the hex won and
// each mode broke differently: `bg-accent` compiled to hsl(#0088ff) in dark —
// not a colour, so ghost/outline button and dropdown-item hovers rendered
// nothing — and the light block's re-declaration turned every var(--accent)
// into a bare HSL triple. Neither produces a console error; the only symptom is
// a hover that does nothing, which is why this is asserted rather than trusted.

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';

// cwd is web/ under vitest. Comments are stripped first: the note in index.css
// explaining this bug contains the string "--accent:", and a test that matches
// its subject's prose passes or fails on the prose.
const css = readFileSync('src/index.css', 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
const tailwind = readFileSync('tailwind.config.js', 'utf8');

/** Every declared value of `--name`, in source order. Line-based because the
 *  file is CRLF and a multiline-anchored regex proved easy to get quietly wrong. */
function declared(name: string): string[] {
  return css
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.startsWith(`--${name}:`))
    .map((line) => line.slice(name.length + 3).replace(/;.*$/, '').trim());
}

describe('accent tokens stay separate', () => {
  it('declares --accent only ever as a hex', () => {
    const values = declared('accent');
    expect(values.length).toBeGreaterThan(0);
    for (const v of values) expect(v).toMatch(/^#[0-9a-f]{3,8}$/i);
  });

  it('declares --surface-accent only ever as an HSL triple', () => {
    const values = declared('surface-accent');
    expect(values.length).toBeGreaterThan(0);
    for (const v of values) expect(v).toMatch(/^[\d.]+ [\d.]+% [\d.]+%$/);
  });

  it('defines a surface accent for both modes', () => {
    // One for :root/.dark and one for html[data-mode='light']. A single
    // definition means one mode inherits the other's hover colour.
    expect(declared('surface-accent').length).toBeGreaterThanOrEqual(2);
  });

  it('points the Tailwind accent colour at the surface, not the signal', () => {
    // hsl(var(--accent)) is the exact expression that compiled to hsl(#0088ff).
    expect(tailwind).toContain("DEFAULT: 'hsl(var(--surface-accent))'");
    expect(tailwind).not.toContain("'hsl(var(--accent))'");
  });
});
