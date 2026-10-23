// Guard: Pill stays retired (docs/UI-CONVENTIONS.md, "Buttons"; audit A1).
//
// Pill was a second button system with no size option, so every call site set
// its own padding. All of its uses are now Button, and the export is gone. This
// fails if anything declares, exports, imports or renders a component named
// Pill again. Names that merely contain the word (DocStatusPill, PillSelect)
// are other components and are not matched.

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import ts from 'typescript';
import { describe, expect, it } from 'vitest';

import { SRC, lineOf, parse, rel, sourceFiles } from './sourceScan';

function violations(file: string, text: string): string[] {
  const source = parse(file, text);
  const out: string[] = [];
  const hit = (node: ts.Node) => out.push(`${rel(file)}:${lineOf(source, node)}`);
  const visit = (node: ts.Node): void => {
    if (
      (ts.isImportSpecifier(node) || ts.isExportSpecifier(node)) &&
      (node.name.text === 'Pill' || node.propertyName?.text === 'Pill')
    ) {
      hit(node);
    } else if (
      (ts.isVariableDeclaration(node) || ts.isFunctionDeclaration(node) || ts.isClassDeclaration(node)) &&
      node.name &&
      ts.isIdentifier(node.name) &&
      node.name.text === 'Pill'
    ) {
      hit(node);
    } else if (
      (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) &&
      node.tagName.getText(source) === 'Pill'
    ) {
      hit(node);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return out;
}

describe('Pill is retired', () => {
  it('is not declared, exported, imported or rendered anywhere', () => {
    const all = sourceFiles().flatMap((f) => violations(f, readFileSync(f, 'utf-8')));
    expect(all, `use Button from @/components/ui/button:\n${all.join('\n')}`).toEqual([]);
  });

  it('matches the component, not names that contain the word', () => {
    const sample = [
      "import { Pill, Field } from '@/design/components/primitives';",
      'export const Pill = () => null;',
      'export const A = () => <Pill>Go</Pill>;',
      'export const DocStatusPill = () => null;',
      'export const B = () => <DocStatusPill />;',
    ].join('\n');
    expect(violations(join(SRC, '__probe__.tsx'), sample)).toHaveLength(3);
  });
});
