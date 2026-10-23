// The structural UI rules that are migrated file by file, so they are guarded
// by a ratchet (uiRatchets.test.ts) rather than all at once. Each rule says
// whether one source file breaks it. Kept apart from the test so the rules can
// be read, and probed, on their own.

import ts from 'typescript';

import { lineOf, parse, rel } from './sourceScan';

export interface StructuralRule {
  /** Also the allowlist's file name: allowlists/<id>.txt. */
  id: string;
  summary: string;
  /** What to use instead. */
  use: string;
  /** Files that own the primitive itself, or a documented exception. Never allowlisted. */
  exempt: readonly string[];
  /** Line numbers in this file that break the rule. */
  check: (source: ts.SourceFile) => number[];
}

function tagName(node: ts.JsxOpeningElement | ts.JsxSelfClosingElement, source: ts.SourceFile): string {
  return node.tagName.getText(source);
}

function jsxTags(source: ts.SourceFile): (ts.JsxOpeningElement | ts.JsxSelfClosingElement)[] {
  const out: (ts.JsxOpeningElement | ts.JsxSelfClosingElement)[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) out.push(node);
    ts.forEachChild(node, visit);
  };
  visit(source);
  return out;
}

function attr(
  node: ts.JsxOpeningElement | ts.JsxSelfClosingElement,
  name: string,
): ts.JsxAttribute | undefined {
  return node.attributes.properties.find(
    (p): p is ts.JsxAttribute => ts.isJsxAttribute(p) && p.name.getText() === name,
  );
}

/** The literal value of a string attribute (`x="a"` or `x={'a'}`), else undefined. */
function staticValue(a: ts.JsxAttribute | undefined): string | undefined {
  const init = a?.initializer;
  if (!init) return undefined;
  if (ts.isStringLiteral(init)) return init.text;
  if (ts.isJsxExpression(init) && init.expression) {
    const e = init.expression;
    if (ts.isStringLiteral(e) || ts.isNoSubstitutionTemplateLiteral(e)) return e.text;
  }
  return undefined;
}

/** Every string piece in an expression. */
function stringsIn(node: ts.Node): string[] {
  const out: string[] = [];
  const visit = (n: ts.Node): void => {
    if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) out.push(n.text);
    else if (ts.isTemplateHead(n) || ts.isTemplateMiddle(n) || ts.isTemplateTail(n)) out.push(n.text);
    ts.forEachChild(n, visit);
  };
  visit(node);
  return out;
}

/** Module-level `const NAME = <strings>` in a file, so className={PRIMARY} resolves. */
function constStrings(source: ts.SourceFile): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const stmt of source.statements) {
    if (!ts.isVariableStatement(stmt)) continue;
    for (const d of stmt.declarationList.declarations) {
      if (ts.isIdentifier(d.name) && d.initializer) out.set(d.name.text, stringsIn(d.initializer));
    }
  }
  return out;
}

function classText(
  node: ts.JsxOpeningElement | ts.JsxSelfClosingElement,
  consts: Map<string, string[]>,
): string {
  const a = attr(node, 'className');
  if (!a?.initializer) return '';
  const parts = stringsIn(a.initializer);
  const visit = (n: ts.Node): void => {
    if (ts.isIdentifier(n) && consts.has(n.text)) parts.push(...(consts.get(n.text) ?? []));
    ts.forEachChild(n, visit);
  };
  visit(a.initializer);
  return parts.join(' ');
}

const PRIMARY = /(?:^|\s)bg-primary(?=\s|$)/;
/** A pulsing placeholder. `motion-safe:animate-pulse` is a status indicator (a
 *  live dot), not a placeholder, and stops under reduced motion, so it is allowed. */
function isPlaceholderPulse(text: string): boolean {
  return text
    .split(/\s+/)
    .some((tok) => /^(?:[\w-]+:)*animate-pulse$/.test(tok) && !tok.startsWith('motion-safe:'));
}
const NON_TEXT_INPUTS = new Set(['checkbox', 'radio', 'file', 'hidden', 'range', 'color', 'submit', 'button', 'reset']);

export const STRUCTURAL_RULES: StructuralRule[] = [
  {
    id: 'raw-primary-button',
    summary: 'a raw <button> styled as the primary action (bg-primary)',
    use: 'Button from @/components/ui/button (raw <button> only for toggles, chips, tabs and grid cells)',
    exempt: ['components/ui/button.tsx'],
    check: (source) => {
      const consts = constStrings(source);
      return jsxTags(source)
        .filter((n) => tagName(n, source) === 'button' && PRIMARY.test(classText(n, consts)))
        .map((n) => lineOf(source, n));
    },
  },
  {
    id: 'native-form-control',
    summary: 'a native <select>, <textarea> or text-like <input>',
    use: 'Input, Textarea or SelectControl from @/components/ui (checkbox, radio, file, hidden and range inputs stay native)',
    exempt: [
      'components/ui/input.tsx',
      'components/ui/textarea.tsx',
      'components/ui/select-control.tsx',
      // Field draws the shared control box around a bare input (it carries a leading icon).
      'design/components/primitives.tsx',
    ],
    check: (source) =>
      jsxTags(source)
        .filter((n) => {
          const tag = tagName(n, source);
          if (tag === 'select' || tag === 'textarea') return true;
          if (tag !== 'input') return false;
          const type = staticValue(attr(n, 'type'));
          return !(type && NON_TEXT_INPUTS.has(type));
        })
        .map((n) => lineOf(source, n)),
  },
  {
    id: 'raw-table',
    summary: 'a raw <table>',
    use: 'Table, TableHeader, TableBody, TableRow, TableHead, TableCell from @/components/ui/table',
    exempt: ['components/ui/table.tsx'],
    check: (source) =>
      jsxTags(source)
        .filter((n) => tagName(n, source) === 'table')
        .map((n) => lineOf(source, n)),
  },
  {
    id: 'hand-built-dialog',
    summary: 'a hand-built role="dialog" modal',
    use: 'Dialog or Sheet from @/components/ui (Radix: focus trap, Escape, focus return)',
    exempt: [
      // Documented exceptions (docs/UI-CONVENTIONS.md, "Dialogs"): the two consent
      // gates and the two full-screen candidate surfaces own their whole viewport.
      'components/ConsentModal.tsx',
      'pages/exam/CameraConsentModal.tsx',
      'features/interview/LiveKitInterview.tsx',
      'pages/PublicExam.tsx',
    ],
    check: (source) =>
      jsxTags(source)
        .filter((n) => {
          const role = staticValue(attr(n, 'role'));
          return role === 'dialog' || role === 'alertdialog';
        })
        .map((n) => lineOf(source, n)),
  },
  {
    id: 'hand-built-skeleton',
    summary: 'a hand-built animate-pulse placeholder',
    use: 'Skeleton from @/components/ui/skeleton (or QueryState, which renders it)',
    exempt: ['components/ui/skeleton.tsx'],
    check: (source) => {
      const lines = new Set<number>();
      const visit = (n: ts.Node): void => {
        if (
          (ts.isStringLiteral(n) ||
            ts.isNoSubstitutionTemplateLiteral(n) ||
            ts.isTemplateHead(n) ||
            ts.isTemplateMiddle(n) ||
            ts.isTemplateTail(n)) &&
          isPlaceholderPulse(n.text)
        ) {
          lines.add(lineOf(source, n));
        }
        ts.forEachChild(n, visit);
      };
      visit(source);
      return [...lines];
    },
  },
];

/** Every (rule, file) breach in one file, keyed by rule id. */
export function breaches(file: string, text: string): Map<string, number[]> {
  const source = parse(file, text);
  const out = new Map<string, number[]>();
  for (const rule of STRUCTURAL_RULES) {
    if (rule.exempt.includes(rel(file))) continue;
    const lines = rule.check(source);
    if (lines.length) out.set(rule.id, lines);
  }
  return out;
}
