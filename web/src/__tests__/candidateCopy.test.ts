// Audit CA-M21 / CA-L10: candidate pages speak the candidate's language, even
// when the server's error sentence is English, and never print a raw level code.

import { describe, it, expect, afterEach } from 'vitest';
import i18n from '../lib/i18n';
import { ApiError } from '../api/client';
import { candidateErrorMessage, levelLabel, pageLanguage } from '../lib/candidateCopy';

afterEach(async () => {
  await i18n.changeLanguage('en');
});

describe('candidateErrorMessage', () => {
  const t = i18n.t.bind(i18n);

  it('shows the server sentence as written on an English page', () => {
    const err = new ApiError('Please confirm your email first.', 403, undefined, true);
    expect(candidateErrorMessage(err, 'Could not sign in.', t, 'en')).toBe(
      'Please confirm your email first.',
    );
  });

  it('replaces the English server sentence with the localised fallback on a Telugu page', async () => {
    await i18n.changeLanguage('te');
    const err = new ApiError('Please confirm your email first.', 403, undefined, true);
    expect(candidateErrorMessage(err, 'FALLBACK', i18n.t.bind(i18n), 'te')).toBe('FALLBACK');
  });

  it('keeps network and rate-limit failures distinct, in the page language', async () => {
    await i18n.changeLanguage('hi');
    const ht = i18n.t.bind(i18n);
    expect(candidateErrorMessage(new TypeError('Failed to fetch'), 'F', ht, 'hi')).toBe(
      ht('error.network'),
    );
    expect(candidateErrorMessage(new ApiError('slow down', 429), 'F', ht, 'hi')).toBe(
      ht('error.tooMany'),
    );
    expect(candidateErrorMessage(new ApiError('boom', 502), 'F', ht, 'hi')).toBe(
      `F ${ht('error.tryLater')}`,
    );
  });
});

describe('levelLabel / pageLanguage', () => {
  it('maps level codes to words', () => {
    const t = i18n.t.bind(i18n);
    expect(levelLabel(t, 'mid')).toBe('Mid level');
    expect(levelLabel(t, 'entry')).toBe('Entry level');
    expect(levelLabel(t, 'Principal')).toBe('Principal');
  });

  it('narrows any locale to the three the product speaks', () => {
    expect(pageLanguage('te-IN')).toBe('te');
    expect(pageLanguage('fr')).toBe('en');
    expect(pageLanguage(undefined)).toBe('en');
  });
});
