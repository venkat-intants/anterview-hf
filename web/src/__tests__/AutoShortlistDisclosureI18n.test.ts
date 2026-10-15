// Candidate notices that must stay true now that a workflow can shortlist on
// the ATS score with no person confirming (auto_shortlist; ACCEPTED-RISKS
// AR-11). Each one used to promise something that is no longer true in every
// configuration: "Your application is reviewed by people", "Nothing is decided
// automatically", "A person makes that decision", or an email to everyone.
//
// apply.humanReview matters most: it sits beside the consent checkbox, so it
// is what the candidate is told at the moment they agree to processing.

import { describe, it, expect } from 'vitest';
import i18n from '../lib/i18n';

const LANGS = ['en', 'hi', 'te'] as const;
const KEYS = [
  'apply.humanReview',
  'apply.reviewNote',
  'public.resumeNext',
  'public.reviewNext',
  'public.invite.next',
] as const;

function value(lang: string, key: string): string {
  const out = i18n.getResource(lang, 'translation', key) as unknown;
  if (typeof out !== 'string') throw new Error(`${lang}:${key} is missing`);
  return out;
}

describe('auto-shortlist disclosure', () => {
  it('the consent-point notice discloses automated progression without a person', () => {
    const en = value('en', 'apply.humanReview');
    expect(en).toMatch(/moved to the next stage automatically/);
    expect(en).toMatch(/without a person reviewing it first/);
    expect(en).toMatch(/Every hire or reject decision is made by a person/);
    expect(en).not.toMatch(/reviewed by people/);
  });

  it.each(LANGS)(
    '%s: no notice claims nothing is automatic or that a person always decides progression',
    (lang) => {
      for (const key of KEYS) {
        const v = value(lang, key);
        expect(v, `${lang}:${key}`).not.toMatch(
          /Nothing is decided automatically|nothing is decided automatically/,
        );
        expect(v, `${lang}:${key}`).not.toMatch(/A person makes that decision/);
      }
    },
  );

  it('the email is promised only to those who move forward', () => {
    expect(value('en', 'public.resumeNext')).toMatch(
      /If you move forward, we will email \{\{email\}\}/,
    );
    expect(value('en', 'apply.reviewNote')).toMatch(/If you move forward, you will get an email/);
  });

  it.each(LANGS.filter((l) => l !== 'en'))('%s is translated, not copied from English', (lang) => {
    for (const key of KEYS) {
      expect(value(lang, key), `${lang}:${key}`).not.toBe(value('en', key));
    }
  });

  it.each(LANGS)('%s keeps every placeholder', (lang) => {
    const ph = (s: string) => (s.match(/\{\{\w+\}\}/g) ?? []).sort();
    for (const key of KEYS) {
      expect(ph(value(lang, key)), `${lang}:${key}`).toEqual(ph(value('en', key)));
    }
  });
});
