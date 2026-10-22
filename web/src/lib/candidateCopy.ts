// Small helpers for candidate-facing copy: the page language, a job level as a
// word, and an error as a sentence in the candidate's language.
//
// Candidate pages are localised (EN/HI/TE), but the API writes its `detail`
// sentences in English. `errorMessage` shows those as written, which is right
// on an English page and wrong on a Telugu one: half the screen in Telugu and
// the one line that says what went wrong in English (audit CA-M21). So on a
// Hindi or Telugu page the server's sentence gives way to the caller's own
// localised fallback, with the network and rate-limit cases kept distinct
// because what to do about them differs.

import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { errorMessage, isNetworkError } from '@/lib/errorMessage';

export type CandidateLanguage = 'en' | 'hi' | 'te';

/** The UI language as one of the three the product speaks. */
export function pageLanguage(lng: string | undefined | null): CandidateLanguage {
  const base = (lng ?? 'en').slice(0, 2);
  return base === 'hi' || base === 'te' ? base : 'en';
}

function statusOf(err: unknown): number | null {
  const status: unknown = err instanceof Error ? (err as { status?: unknown }).status : null;
  return typeof status === 'number' ? status : null;
}

/**
 * The sentence to show a candidate for `err`. `fallback` is already translated.
 *
 *   English page   -> errorMessage(err, fallback), server sentence and all
 *   network failure -> error.network
 *   429            -> error.tooMany
 *   5xx            -> fallback + error.tryLater
 *   anything else  -> fallback
 */
export function candidateErrorMessage(
  err: unknown,
  fallback: string,
  t: TFunction,
  language: string | undefined,
): string {
  if (pageLanguage(language) === 'en') return errorMessage(err, fallback);
  if (isNetworkError(err)) return t('error.network');
  const status = statusOf(err);
  if (status === 429) return t('error.tooMany');
  if (status !== null && status >= 500) return `${fallback} ${t('error.tryLater')}`;
  return fallback;
}

/** `candidateErrorMessage` bound to the current translation and language. */
export function useCandidateError(): (err: unknown, fallback: string) => string {
  const { t, i18n } = useTranslation();
  return useCallback(
    (err: unknown, fallback: string) => candidateErrorMessage(err, fallback, t, i18n.language),
    [t, i18n.language],
  );
}

/**
 * A job level as candidates read it. The API's codes are `entry`, `mid` and
 * `senior`; printing them through "{{level}} level" gave "mid level". Anything
 * else is shown as the company wrote it.
 */
export function levelLabel(t: TFunction, level: string | null | undefined): string {
  switch ((level ?? '').toLowerCase()) {
    case 'entry':
    case 'junior':
      return t('jobs.levelEntry');
    case 'mid':
      return t('jobs.levelMid');
    case 'senior':
      return t('jobs.levelSenior');
    default:
      return level ?? '';
  }
}
