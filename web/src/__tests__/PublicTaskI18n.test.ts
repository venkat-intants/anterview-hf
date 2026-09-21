// The task i18n bundle (PH4-D4) — candidate-facing, so EN/HI/TE per
// CLAUDE.md. Same properties OfferI18n.test.ts pins for its own namespace:
// the three bundles stay in step, HI/TE are actually translated rather than
// copied, and every interpolation placeholder survives translation.

import { describe, it, expect, beforeAll, afterEach } from 'vitest';
import i18n from '../lib/i18n';

const LANGS = ['en', 'hi', 'te'] as const;
const NAMESPACES = ['task'] as const;

type Bundle = Record<string, unknown>;

// Genuinely identical by design, not a missed translation: an example https
// URL is not language content in any of the three scripts.
const NOT_LANGUAGE_CONTENT = new Set(['linkPlaceholder']);

function leaves(obj: Bundle, prefix = ''): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object') Object.assign(out, leaves(v as Bundle, path));
    else out[path] = String(v);
  }
  return out;
}

function namespace(lang: string, ns: string): Record<string, string> {
  const bundle = i18n.getResourceBundle(lang, 'translation') as Record<string, Bundle>;
  return leaves(bundle[ns] ?? {});
}

describe.each(NAMESPACES)('%s i18n bundle', (ns) => {
  beforeAll(async () => {
    await i18n.changeLanguage('en');
  });
  afterEach(async () => {
    await i18n.changeLanguage('en');
  });

  it('exists in all three languages', () => {
    for (const lang of LANGS) {
      expect(Object.keys(namespace(lang, ns)).length, `${lang}.${ns} is missing`).toBeGreaterThan(
        0,
      );
    }
  });

  it('has identical keys in all three bundles', () => {
    const [en, hi, te] = LANGS.map((l) => Object.keys(namespace(l, ns)).sort());
    expect(hi, `missing from hi: ${en.filter((k) => !hi.includes(k)).join(', ')}`).toEqual(en);
    expect(te, `missing from te: ${en.filter((k) => !te.includes(k)).join(', ')}`).toEqual(en);
  });

  it('is actually translated, not copied from English', () => {
    const en = namespace('en', ns);
    for (const lang of ['hi', 'te'] as const) {
      const other = namespace(lang, ns);
      for (const [key, value] of Object.entries(en)) {
        if (NOT_LANGUAGE_CONTENT.has(key)) continue;
        expect(other[key], `${lang}.${ns}.${key} is still the English string`).not.toBe(value);
      }
    }
  });

  it('keeps its interpolation placeholders in every language', () => {
    const en = namespace('en', ns);
    for (const lang of ['hi', 'te'] as const) {
      const other = namespace(lang, ns);
      for (const [key, value] of Object.entries(en)) {
        const expected = [...value.matchAll(/\{\{(\w+)\}\}/g)].map((m) => m[1]).sort();
        const actual = [...(other[key] ?? '').matchAll(/\{\{(\w+)\}\}/g)].map((m) => m[1]).sort();
        expect(actual, `${lang}.${ns}.${key} placeholder mismatch`).toEqual(expected);
      }
    }
  });
});
