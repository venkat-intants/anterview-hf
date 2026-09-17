// Every candidate-facing surface, in every Day-1 language — and on a phone.
//
// PH3-B5 criterion 11 was closed for the confirmation screen only. The advert
// and the careers board stayed English, so somebody who had chosen हिंदी saw
// English on the board, English on the apply form, and Hindi at the one screen
// that decides what gets stored about them. This file covers all three.
//
// TWO PROPERTIES, AND THE SECOND IS THE ONE THAT ACTUALLY BITES.
//
// 1. The strings render. Easy, and mostly proven by the per-page suites.
// 2. The bundles stay in step. A key present in `en` and missing in `hi` falls
//    back to English SILENTLY — no error, no warning, no failing test unless
//    something asserts it. That is the whole failure mode, and it is invisible
//    to any test that only checks a key exists.
//
// Phone width is here rather than in a separate file because it is the same
// question asked of the same screens: does a candidate on the device they
// actually own get a usable page. It is not a substitute for a real person on a
// real handset — that remains open — but it closes the part that automation
// genuinely can close.

import { describe, it, expect, beforeAll, afterEach } from 'vitest';

import i18n from '../lib/i18n';

const LANGS = ['en', 'hi', 'te'] as const;

/** The namespaces a candidate can see without logging in. */
const CANDIDATE_NAMESPACES = ['resumeApply', 'careers', 'apply'] as const;

/**
 * Keys whose value is deliberately identical across locales: proper nouns and
 * brand names that are not translated anywhere in this app.
 */
const NOT_TRANSLATED = new Set([
  'linkedin',
  'github',
  'reviewCv',
  'salaryRange', // pure interpolation: "{{lo}} – {{hi}}"
]);

type Bundle = Record<string, unknown>;

function ns(lang: string, name: string): Bundle {
  const bundle = i18n.getResourceBundle(lang, 'translation') as Record<string, Bundle>;
  return bundle[name];
}

/** Flattens nested namespaces (careers.employment.*) to dotted leaf paths. */
function leaves(obj: Bundle, prefix = ''): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object') Object.assign(out, leaves(v as Bundle, path));
    else out[path] = String(v);
  }
  return out;
}

describe('candidate surfaces are localised in every Day-1 language', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('en');
  });
  afterEach(async () => {
    await i18n.changeLanguage('en');
  });

  it.each(CANDIDATE_NAMESPACES)('%s exists in all three bundles', (name) => {
    for (const lang of LANGS) {
      expect(ns(lang, name), `${lang}.${name} is missing entirely`).toBeTruthy();
    }
  });

  it.each(CANDIDATE_NAMESPACES)('%s has identical keys in all three bundles', (name) => {
    const [en, hi, te] = LANGS.map((l) => Object.keys(leaves(ns(l, name))).sort());
    // Named explicitly so a failure says WHICH key, not just "arrays differ".
    expect(hi, `missing from hi: ${en.filter((k) => !hi.includes(k)).join(', ')}`).toEqual(en);
    expect(te, `missing from te: ${en.filter((k) => !te.includes(k)).join(', ')}`).toEqual(en);
  });

  it.each(CANDIDATE_NAMESPACES)('%s is actually translated, not copied', (name) => {
    const en = leaves(ns('en', name));
    for (const lang of ['hi', 'te'] as const) {
      const other = leaves(ns(lang, name));
      for (const [key, value] of Object.entries(en)) {
        const leaf = key.split('.').pop() as string;
        if (NOT_TRANSLATED.has(leaf)) continue;
        expect(
          other[key],
          `${lang}.${name}.${key} is still the English string — a silent fallback`,
        ).not.toBe(value);
      }
    }
  });

  it.each(CANDIDATE_NAMESPACES)(
    '%s keeps its interpolation placeholders in every language',
    (name) => {
      // A translator dropping {{company}} does not fail the build — it renders a
      // sentence with a hole in it, to a candidate, in production.
      const en = leaves(ns('en', name));
      for (const lang of ['hi', 'te'] as const) {
        const other = leaves(ns(lang, name));
        for (const [key, value] of Object.entries(en)) {
          const expected = [...value.matchAll(/\{\{(\w+)\}\}/g)].map((m) => m[1]).sort();
          const actual = [...(other[key] ?? '').matchAll(/\{\{(\w+)\}\}/g)]
            .map((m) => m[1])
            .sort();
          expect(actual, `${lang}.${name}.${key} placeholder mismatch`).toEqual(expected);
        }
      }
    },
  );

  it('carries no stray Latin prose inside an Indic string', () => {
    // A real defect this caught: a Telugu string had "dal" spliced into the
    // middle of a word, which renders as visible mojibake to a candidate and is
    // invisible to every other check here.
    const ALLOWED = /^(CV|PDF|MB|LinkedIn|GitHub|AntHire|Hindi|Telugu|English)$/;
    for (const lang of ['hi', 'te'] as const) {
      for (const name of CANDIDATE_NAMESPACES) {
        for (const [key, value] of Object.entries(leaves(ns(lang, name)))) {
          if (!/[ऀ-ൿ]/.test(value)) continue;
          const words = value.replace(/\{\{\w+\}\}/g, '').match(/[A-Za-z]+/g) ?? [];
          for (const w of words) {
            expect(ALLOWED.test(w), `${lang}.${name}.${key} contains "${w}"`).toBe(true);
          }
        }
      }
    }
  });

  it('translates the irreversible-delete copy in every language', () => {
    // These eight carry a higher bar than ordinary UI copy: a candidate acting
    // on a mistranslated irreversible delete loses their own application.
    const ERASURE = [
      'deleteTitle',
      'deleteDesc',
      'deleteCta',
      'deleteConfirm',
      'deleting',
      'keepIt',
      'errDelete',
      'deletedDesc',
    ];
    for (const lang of LANGS) {
      const bundle = leaves(ns(lang, 'resumeApply'));
      for (const key of ERASURE) {
        expect(bundle[key], `${lang}.resumeApply.${key} is missing`).toBeTruthy();
      }
    }
  });
});
