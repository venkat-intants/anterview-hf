// Every string a candidate can see must exist in EN, HI and TE.
//
// CLAUDE.md hard constraint 5. The reason this needs a test rather than care is
// that i18next is configured with `fallbackLng: 'en'`, which makes a missing
// translation indistinguishable from a present one at the call site AND in the
// browser during development, where the reviewer is reading English anyway. The
// locale dictionaries are plain untyped objects, so TypeScript says nothing
// either.
//
// Five keys had gone missing that way, and three of them in the subtle shape
// this test exists to catch: HI and TE spelled a COUNTED key without i18next's
// plural suffix (`questionsCount` rather than `questionsCount_one`/`_other`).
// i18next does not fall back from `key_other` to `key`, so every Hindi and
// Telugu candidate read the English plural on their own exam intro screen — a
// bug no amount of reading the Hindi dictionary would reveal, because the key
// was right there.

import { describe, it, expect } from 'vitest';
import i18n from '../lib/i18n';

const DAY_ONE_LANGUAGES = ['hi', 'te'] as const;

/** Every leaf key path in a resource bundle, dot-joined. */
function keyPaths(node: unknown, prefix = ''): string[] {
  if (node === null || typeof node !== 'object') return [prefix];
  return Object.entries(node as Record<string, unknown>).flatMap(([k, v]) =>
    keyPaths(v, prefix ? `${prefix}.${k}` : k),
  );
}

function bundle(lng: string): string[] {
  return keyPaths(i18n.getResourceBundle(lng, 'translation'));
}

describe('i18n — EN / HI / TE parity', () => {
  it('has a real English dictionary to compare against', () => {
    // Guards the guard: if the bundle failed to load, every comparison below
    // would pass against an empty set and this file would prove nothing.
    expect(bundle('en').length).toBeGreaterThan(500);
  });

  it.each(DAY_ONE_LANGUAGES)('%s translates every English key', (lng) => {
    const english = bundle('en');
    const translated = new Set(bundle(lng));
    const missing = english.filter((k) => !translated.has(k));
    expect(missing, `${missing.length} key(s) fall back to English in ${lng}`).toEqual([]);
  });

  it.each(DAY_ONE_LANGUAGES)('%s has no key English does not have', (lng) => {
    // The other direction, and not pedantry: an unsuffixed `questionsCount` left
    // behind next to the suffixed pair reads as a translated string and is never
    // looked up. A key with no English counterpart is dead or misspelled.
    const english = new Set(bundle('en'));
    const orphans = bundle(lng).filter((k) => !english.has(k));
    expect(orphans, `${lng} has key(s) no English key matches`).toEqual([]);
  });

  it.each(DAY_ONE_LANGUAGES)('%s spells every counted key with both plural forms', (lng) => {
    // Hindi and Telugu both have the `one`/`other` categories, so a counted key
    // needs both suffixes in both languages. Derived from the English side,
    // which is where a new counted string is added first.
    const suffixed = bundle('en').filter((k) => k.endsWith('_one') || k.endsWith('_other'));
    expect(suffixed.length).toBeGreaterThan(0);
    const translated = new Set(bundle(lng));
    for (const key of suffixed) {
      expect(translated.has(key), `${lng} is missing ${key}`).toBe(true);
      const stem = key.replace(/_(one|other)$/, '');
      expect(translated.has(stem), `${lng} still has the unsuffixed ${stem}`).toBe(false);
    }
  });

  it.each(DAY_ONE_LANGUAGES)('%s keeps every interpolation placeholder', (lng) => {
    // A dropped {{count}} or {{remaining}} renders the literal braces to the
    // candidate, and a renamed one renders nothing at all.
    const en = i18n.getResourceBundle('en', 'translation') as Record<string, unknown>;
    const other = i18n.getResourceBundle(lng, 'translation') as Record<string, unknown>;

    function read(obj: Record<string, unknown>, path: string): unknown {
      return path.split('.').reduce<unknown>(
        (acc, part) =>
          acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[part] : undefined,
        obj,
      );
    }

    const mismatched: string[] = [];
    for (const path of bundle('en')) {
      const source = read(en, path);
      const target = read(other, path);
      if (typeof source !== 'string' || typeof target !== 'string') continue;
      const names = (s: string) => [...s.matchAll(/\{\{(\w+)/g)].map((m) => m[1]).sort();
      if (names(source).join(',') !== names(target).join(',')) mismatched.push(path);
    }
    expect(mismatched, `${lng} placeholder mismatch`).toEqual([]);
  });
});
