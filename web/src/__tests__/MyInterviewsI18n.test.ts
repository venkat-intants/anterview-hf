// The myInterviews i18n bundle (PH4-A2) — candidate-facing, so EN/HI/TE per
// CLAUDE.md. Same two properties CandidateLocalisation.test.tsx checks for
// its own namespace list: the bundles stay in step (a key present in `en`
// but missing from `hi`/`te` falls back to English silently), and HI/TE are
// actually translated rather than copied.

import { describe, it, expect, beforeAll, afterEach } from 'vitest';
import i18n from '../lib/i18n';

const LANGS = ['en', 'hi', 'te'] as const;

type Bundle = Record<string, unknown>;

function leaves(obj: Bundle, prefix = ''): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object') Object.assign(out, leaves(v as Bundle, path));
    else out[path] = String(v);
  }
  return out;
}

function myInterviews(lang: string): Record<string, string> {
  const bundle = i18n.getResourceBundle(lang, 'translation') as Record<string, Bundle>;
  return leaves(bundle.myInterviews ?? {});
}

describe('myInterviews i18n bundle', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('en');
  });
  afterEach(async () => {
    await i18n.changeLanguage('en');
  });

  it('exists in all three languages', () => {
    for (const lang of LANGS) {
      expect(Object.keys(myInterviews(lang)).length, `${lang}.myInterviews is missing`).toBeGreaterThan(0);
    }
  });

  it('has identical keys in all three bundles', () => {
    const [en, hi, te] = LANGS.map((l) => Object.keys(myInterviews(l)).sort());
    expect(hi, `missing from hi: ${en.filter((k) => !hi.includes(k)).join(', ')}`).toEqual(en);
    expect(te, `missing from te: ${en.filter((k) => !te.includes(k)).join(', ')}`).toEqual(en);
  });

  it('is actually translated, not copied from English', () => {
    const en = myInterviews('en');
    for (const lang of ['hi', 'te'] as const) {
      const other = myInterviews(lang);
      for (const [key, value] of Object.entries(en)) {
        expect(other[key], `${lang}.myInterviews.${key} is still the English string`).not.toBe(value);
      }
    }
  });

  it('keeps its interpolation placeholders in every language', () => {
    const en = myInterviews('en');
    for (const lang of ['hi', 'te'] as const) {
      const other = myInterviews(lang);
      for (const [key, value] of Object.entries(en)) {
        const expected = [...value.matchAll(/\{\{(\w+)\}\}/g)].map((m) => m[1]).sort();
        const actual = [...(other[key] ?? '').matchAll(/\{\{(\w+)\}\}/g)].map((m) => m[1]).sort();
        expect(actual, `${lang}.myInterviews.${key} placeholder mismatch`).toEqual(expected);
      }
    }
  });
});
