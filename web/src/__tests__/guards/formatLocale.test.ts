// Guard: dates, times, numbers and money are formatted with an explicit locale
// (docs/UI-CONVENTIONS.md, "Formatting"; audit F2).
//
// A bare toLocaleString() renders in whatever the browser is set to, so one
// HR manager saw lakh grouping and D/M order and the next saw million grouping
// and M/D. lib/formatters is the one place that turns a value into text; this
// guard fails on any toLocaleString / toLocaleDateString / toLocaleTimeString
// call, or any `new Intl.DateTimeFormat` / `Intl.NumberFormat`, that passes no
// locale (no argument, `undefined`, or `[]`) anywhere else.

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import ts from 'typescript';
import { describe, expect, it } from 'vitest';

import { SRC, lineOf, parse, rel, sourceFiles } from './sourceScan';

const OWNER = 'lib/formatters.ts';

function noLocale(args: ts.NodeArray<ts.Expression> | undefined): boolean {
  const first = args?.[0];
  if (!first) return true;
  if (ts.isIdentifier(first) && first.text === 'undefined') return true;
  return ts.isArrayLiteralExpression(first) && first.elements.length === 0;
}

function violations(file: string, text: string): string[] {
  if (rel(file) === OWNER) return [];
  const source = parse(file, text);
  const out: string[] = [];
  const visit = (node: ts.Node): void => {
    if (
      ts.isCallExpression(node) &&
      ts.isPropertyAccessExpression(node.expression) &&
      /^toLocale(?:Date|Time)?String$/.test(node.expression.name.text) &&
      noLocale(node.arguments)
    ) {
      out.push(`${rel(file)}:${lineOf(source, node)} ${node.getText(source).slice(0, 80)}`);
    }
    if (
      (ts.isNewExpression(node) || ts.isCallExpression(node)) &&
      ts.isPropertyAccessExpression(node.expression) &&
      node.expression.expression.getText(source) === 'Intl' &&
      /^(?:DateTimeFormat|NumberFormat)$/.test(node.expression.name.text) &&
      noLocale(node.arguments) &&
      // Intl.DateTimeFormat().resolvedOptions() reads the browser's zone; it formats nothing.
      !(
        ts.isPropertyAccessExpression(node.parent) && node.parent.name.text === 'resolvedOptions'
      )
    ) {
      out.push(`${rel(file)}:${lineOf(source, node)} ${node.getText(source).slice(0, 80)}`);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return out;
}

describe('every formatted date, time and number names its locale', () => {
  it('has no locale-less toLocale*String or Intl formatter outside lib/formatters', () => {
    const all = sourceFiles().flatMap((f) => violations(f, readFileSync(f, 'utf-8')));
    expect(all, `use lib/formatters (formatDate, formatDateTime, formatTime, formatNumber, formatMoney):\n${all.join('\n')}`).toEqual([]);
  });

  it('catches the three spellings of "no locale" and accepts an explicit one', () => {
    const sample = [
      'export const a = (d: Date) => d.toLocaleString();',
      'export const b = (d: Date) => d.toLocaleTimeString(undefined, { hour: "2-digit" });',
      'export const c = (d: Date) => d.toLocaleDateString([], {});',
      'export const d = () => new Intl.NumberFormat();',
      'export const z = () => Intl.DateTimeFormat().resolvedOptions().timeZone;',
      "export const e = (d: Date) => d.toLocaleDateString('en-IN');",
      'export const f = (n: number, locale: string) => n.toLocaleString(locale);',
      "export const g = () => new Intl.DateTimeFormat('en-IN');",
    ].join('\n');
    expect(violations(join(SRC, '__probe__.ts'), sample)).toHaveLength(4);
  });
});
