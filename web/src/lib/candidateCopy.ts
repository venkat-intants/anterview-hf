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
import { MISSING, formatDate, uiLocale } from '@/lib/formatters';

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
 * The confirmation sentence for a submitted application, in the page language.
 *
 * The API also sends a `message`, but in English, so a Hindi or Telugu
 * applicant's confirmation screen used to end in English. The sentence follows
 * from `already_applied` alone, so it is chosen from that flag and an English
 * page reads the same as the server's own wording.
 */
export function applicationResultText(
  t: TFunction,
  result: { already_applied: boolean },
): string {
  return result.already_applied ? t('apply.alreadyAppliedDesc') : t('apply.receivedDesc');
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

/** Interview-session status as candidates read it ("In progress", not the code). */
export function sessionStatusText(t: TFunction, status: string): string {
  switch (status) {
    case 'completed':
      return t('history.statusCompleted');
    case 'in_progress':
      return t('history.statusInProgress');
    case 'abandoned':
      return t('history.statusAbandoned');
    case 'failed':
      return t('history.statusFailed');
    default:
      return status;
  }
}

/**
 * Dates, durations and session statuses on candidate pages, in the UI
 * language (audit CA-M19). The shared formatters default to English, which is
 * right for staff screens and wrong on a Hindi or Telugu page.
 */
export function useCandidateFormat(): {
  date: (iso: string | null | undefined) => string;
  duration: (seconds: number | null) => string;
  status: (status: string) => string;
} {
  const { t, i18n } = useTranslation();
  const locale = uiLocale(i18n.language);
  return {
    date: (iso) => formatDate(iso, locale),
    duration: (seconds) => {
      if (seconds === null) return MISSING;
      const whole = Math.max(0, Math.round(seconds));
      return t('history.durationMinSec', { m: Math.floor(whole / 60), s: whole % 60 });
    },
    status: (status) => sessionStatusText(t, status),
  };
}
