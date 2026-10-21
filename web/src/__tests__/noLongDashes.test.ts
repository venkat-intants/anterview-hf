// No long dashes in UI text (audit X8).
//
// The rebrand sweep (cfce217) removed em and en dash CHARACTERS from visible
// text, but four survived as HTML entities (&mdash; / &ndash;), which a
// character search does not see and React renders as the same dash. This guard
// covers both spellings, in every .tsx file under src, in exactly the places a
// person can read: string literals, template literals, JSX text and JSX
// attribute strings. Comments are not UI and are skipped, which is why it walks
// the TypeScript AST rather than grepping lines.
//
// Test files are excluded: their strings are test names and fixtures, not text
// any user sees. The house style for a dash in UI copy is " - ", a colon, or
// parentheses.

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';

import ts from 'typescript';
import { describe, expect, it } from 'vitest';

const SRC = resolve(__dirname, '..');

const LONG_DASH = /[–—]|&[mn]dash;|&#821[12];|&#x201[34];|\\u201[34]/i;

function tsxFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      if (name === '__tests__' || name === 'node_modules') continue;
      out.push(...tsxFiles(full));
    } else if (name.endsWith('.tsx') && !/\.test\.tsx$/.test(name)) {
      out.push(full);
    }
  }
  return out;
}

/** Every user-readable literal in a .tsx source that contains a long dash. */
function scan(file: string, text: string): string[] {
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const found: string[] = [];
  const visit = (node: ts.Node): void => {
    if (
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node) ||
      ts.isJsxText(node)
    ) {
      // getText is the RAW source, so an entity is seen as written.
      const raw = node.getText(source);
      if (LONG_DASH.test(raw)) {
        const { line } = source.getLineAndCharacterOfPosition(node.getStart(source));
        found.push(`${relative(SRC, file)}:${line + 1}: ${raw.trim().slice(0, 80)}`);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found;
}

describe('UI text has no long dashes', () => {
  const files = tsxFiles(SRC);

  it('scans the app, not an empty directory', () => {
    // Guard the guard: a wrong root would pass vacuously.
    expect(files.length).toBeGreaterThan(100);
    expect(files.some((f) => f.endsWith(join('pages', 'StartInterview.tsx')))).toBe(true);
  });

  it('finds none, as characters or as entities', () => {
    const all = files.flatMap((f) => scan(f, readFileSync(f, 'utf-8')));
    expect(all, `replace with " - ", a colon or parentheses:\n${all.join('\n')}`).toEqual([]);
  });

  it('catches entities, characters, attributes and templates, and ignores comments', () => {
    const sample = [
      '// a comment with an em dash — is fine',
      '/* and a block comment &mdash; too */',
      'export const A = () => <p>one &mdash; two</p>;',
      "export const B = 'x – y';",
      'export const C = () => <input placeholder="10&ndash;20" />;',
      'export const D = (n: number) => `${n} — items`;',
      "export const E = 'a - b';",
    ].join('\n');
    const hits = scan(join(SRC, '__probe__.tsx'), sample);
    expect(hits).toHaveLength(4);
    expect(hits.join('\n')).not.toMatch(/comment/);
  });
});
