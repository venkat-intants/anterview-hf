// Shared machinery for the UI-convention guard tests (docs/UI-CONVENTIONS.md,
// "Guards"). Every guard walks the TypeScript AST of the app's source, the same
// approach as noLongDashes.test.ts, so comments are never mistaken for UI and an
// identifier is never mistaken for copy.
//
// This is a helper, not a test: vitest only collects *.test.ts(x).

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, resolve, sep } from 'node:path';

import ts from 'typescript';

export const SRC = resolve(__dirname, '..', '..');

/** Path relative to src, with forward slashes on every OS (allowlists use these). */
export function rel(file: string): string {
  return relative(SRC, file).split(sep).join('/');
}

/** Every non-test .ts / .tsx file under src (tests, fixtures and guards excluded). */
export function sourceFiles(): string[] {
  const out: string[] = [];
  const walk = (dir: string): void => {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) {
        if (name === '__tests__' || name === 'node_modules') continue;
        walk(full);
      } else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) && !name.endsWith('.d.ts')) {
        out.push(full);
      }
    }
  };
  walk(SRC);
  return out.sort();
}

export function parse(file: string, text = readFileSync(file, 'utf-8')): ts.SourceFile {
  return ts.createSourceFile(
    file,
    text,
    ts.ScriptTarget.Latest,
    true,
    file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
}

export interface Hit {
  file: string;
  line: number;
  text: string;
}

export function lineOf(source: ts.SourceFile, node: ts.Node): number {
  return source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1;
}

// ── Visible text ─────────────────────────────────────────────────────────────

/** JSX attributes whose string value a person never reads. */
const HIDDEN_ATTRS = new Set([
  'className', 'id', 'htmlFor', 'to', 'href', 'key', 'type', 'name', 'role', 'src',
  'value', 'defaultValue', 'autoComplete', 'inputMode', 'method', 'action', 'target',
  'rel', 'lang', 'dir', 'form', 'accept', 'pattern', 'step', 'min', 'max', 'variant',
  'size', 'tone', 'width', 'icon', 'as', 'side', 'align', 'mode', 'kind', 'status',
  'aria-labelledby', 'aria-describedby', 'aria-controls', 'aria-owns', 'aria-live',
  'aria-current', 'aria-haspopup', 'aria-expanded', 'aria-hidden', 'aria-invalid',
  'aria-busy', 'aria-modal', 'aria-pressed', 'aria-selected', 'aria-checked',
  'aria-orientation', 'aria-sort', 'aria-autocomplete', 'aria-errormessage',
  'testId', 'persistKey', 'storageKey', 'queryKey',
]);

/** Calls whose string arguments are keys, classes, selectors or matchers. */
const HIDDEN_CALLEES = new Set([
  't', 'cn', 'clsx', 'cva', 'twMerge', 'require', 'includes', 'startsWith', 'endsWith',
  'indexOf', 'lastIndexOf', 'match', 'matchAll', 'test', 'split', 'replace', 'replaceAll',
  'get', 'set', 'has', 'delete', 'append', 'getAll', 'getItem', 'setItem', 'removeItem',
  'addEventListener', 'removeEventListener', 'querySelector', 'querySelectorAll',
  'getElementById', 'closest', 'createElement', 'getAttribute', 'setAttribute',
  'removeAttribute', 'hasAttribute', 'setRequestHeader', 'getPropertyValue', 'setProperty', 'matchMedia',
  'navigate', 'fetch', 'apiFetch', 'request', 'post', 'put', 'patch', 'encodeURIComponent',
  'log', 'warn', 'error', 'info', 'debug', 'useTranslation', 'invalidateQueries',
  'getQueryData', 'setQueryData', 'Symbol', 'RegExp', 'localeCompare', 'Intl',
]);

function calleeName(expr: ts.LeftHandSideExpression): string | undefined {
  if (ts.isIdentifier(expr)) return expr.text;
  if (ts.isPropertyAccessExpression(expr)) return expr.name.text;
  return undefined;
}

/** An identifier-like string: an enum value, key, route, class or id. */
function looksLikeIdentifier(text: string): boolean {
  return !/\s/.test(text) && !/^[A-Z]/.test(text);
}

/** A Tailwind class list held in a constant: most tokens carry - : [ or /. */
function looksLikeClassList(text: string): boolean {
  const tokens = text.trim().split(/\s+/);
  if (tokens.some((tok) => !/^[!a-z0-9\-:[\]()/.%,_#&>=*@'"+~]+$/.test(tok))) return false;
  const styled = tokens.filter((tok) => /[-:[/]/.test(tok)).length;
  return styled * 2 >= tokens.length;
}

function isHiddenContext(node: ts.Node): boolean {
  const parent = node.parent;
  if (!parent) return false;
  if (
    ts.isImportDeclaration(parent) ||
    ts.isExportDeclaration(parent) ||
    ts.isExternalModuleReference(parent) ||
    ts.isLiteralTypeNode(parent) ||
    ts.isImportTypeNode(parent)
  ) {
    return true;
  }
  if ((ts.isPropertyAssignment(parent) || ts.isPropertySignature(parent) || ts.isPropertyDeclaration(parent) || ts.isMethodDeclaration(parent) || ts.isEnumMember(parent)) && parent.name === node) {
    return true;
  }
  if (ts.isElementAccessExpression(parent) && parent.argumentExpression === node) return true;
  if (ts.isCaseClause(parent)) return true;
  if (ts.isBinaryExpression(parent)) {
    const op = parent.operatorToken.kind;
    if (
      op === ts.SyntaxKind.EqualsEqualsEqualsToken ||
      op === ts.SyntaxKind.ExclamationEqualsEqualsToken ||
      op === ts.SyntaxKind.EqualsEqualsToken ||
      op === ts.SyntaxKind.ExclamationEqualsToken ||
      op === ts.SyntaxKind.InKeyword
    ) {
      return true;
    }
  }
  if (ts.isJsxAttribute(parent)) {
    const name = parent.name.getText();
    return HIDDEN_ATTRS.has(name) || name.startsWith('data-') || /^on[A-Z]/.test(name);
  }
  if (ts.isCallExpression(parent) || ts.isNewExpression(parent)) {
    const name = calleeName(parent.expression);
    if (name && HIDDEN_CALLEES.has(name)) return true;
  }
  // The string is a JSX expression whose attribute is hidden: className={'...'}.
  if (ts.isJsxExpression(parent) && parent.parent && ts.isJsxAttribute(parent.parent)) {
    return isHiddenContext(parent);
  }
  return false;
}

/**
 * Every literal a person can read in a source file: JSX text, the string
 * attributes that render (aria-label, title, placeholder, label, ...), and
 * prose strings elsewhere (toast copy, i18n values, error fallbacks). Keys,
 * classes, routes, enum values and comparison operands are excluded.
 */
export function visibleText(file: string, source: ts.SourceFile = parse(file)): Hit[] {
  const out: Hit[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isJsxText(node)) {
      const text = node.getText(source).replace(/\s+/g, ' ').trim();
      if (text) out.push({ file: rel(file), line: lineOf(source, node), text });
    } else if (
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node)
    ) {
      // A template part is judged by the whole template's context.
      const target = ts.isTemplateSpan(node.parent)
        ? node.parent.parent
        : ts.isTemplateExpression(node.parent)
          ? node.parent
          : node;
      const text = node.text;
      if (text.trim() && !isHiddenContext(target) && !looksLikeIdentifier(text) && !looksLikeClassList(text)) {
        out.push({ file: rel(file), line: lineOf(source, node), text });
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return out;
}

// ── Allowlists (ratchet guards) ──────────────────────────────────────────────

/** Read an allowlist: one src-relative path per line; blank lines and # comments ignored. */
export function readAllowlist(name: string): string[] {
  const path = join(__dirname, 'allowlists', `${name}.txt`);
  return readFileSync(path, 'utf-8')
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#'));
}
