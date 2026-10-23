// formatters.ts — the one place dates, money and scores become text (audit X6,
// X11, X13, X18). All functions are pure and have no side-effects.
//
// Every formatter takes an explicit locale. A bare toLocaleString() renders in
// whatever the browser is set to, so the same screen showed lakh grouping to
// one HR manager and million grouping to the next, and D/M order to one and M/D
// to another. Staff screens use the default 'en-IN'; candidate screens pass
// uiLocale(i18n.language) so a date reads in the visitor's language.
//
// The missing-value placeholder is '-' everywhere.

/** The placeholder for a value that is not there. */
export const MISSING = '-';

// ── Locale ─────────────────────────────────────────────────────────────────────

const UI_LOCALES: Record<string, string> = { en: 'en-IN', hi: 'hi-IN', te: 'te-IN' };

/** The locale to format in for a UI language code ("hi" -> "hi-IN"). */
export function uiLocale(language?: string | null): string {
  const base = (language ?? '').split('-')[0].toLowerCase();
  return UI_LOCALES[base] ?? 'en-IN';
}

// ── Date / duration ────────────────────────────────────────────────────────────

function parseInstant(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "21 Oct 2026": a short date. '-' for a missing or unreadable value. */
export function formatDate(iso: string | null | undefined, locale = 'en-IN'): string {
  const d = parseInstant(iso);
  if (!d) return MISSING;
  return d.toLocaleDateString(locale, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

export interface DateTimeOptions {
  /** Defaults to 'en-IN'. */
  locale?: string;
  /** IANA zone to show the time in. Defaults to the browser's own zone. */
  timeZone?: string;
}

/**
 * "21 Oct 2026, 15:30 IST": a date and a 24-hour time, ALWAYS with the zone.
 * Three zones used to reach one person for one event (UTC in the email, IST in
 * the reminder, unlabelled browser time on screen), so a time on its own is
 * not enough to act on. '-' for a missing or unreadable value.
 */
export function formatDateTime(iso: string | null | undefined, opts: DateTimeOptions = {}): string {
  const d = parseInstant(iso);
  if (!d) return MISSING;
  const fmt = (timeZone: string | undefined): string =>
    new Intl.DateTimeFormat(opts.locale ?? 'en-IN', {
      timeZone,
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
      timeZoneName: 'short',
    }).format(d);
  try {
    return fmt(opts.timeZone);
  } catch {
    // An unknown zone name throws; the browser's own zone is still labelled.
    return fmt(undefined);
  }
}

/**
 * "15:30" or, with `seconds`, "15:30:05": a 24-hour clock time for an event
 * already past (a log line, a "checked at"). It carries no zone, so it is not
 * for a future or scheduled time: use formatDateTime for those. '-' for a
 * missing or unreadable value.
 */
export function formatTime(
  iso: string | null | undefined,
  opts: { locale?: string; seconds?: boolean } = {},
): string {
  const d = parseInstant(iso);
  if (!d) return MISSING;
  return d.toLocaleTimeString(opts.locale ?? 'en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    ...(opts.seconds ? { second: '2-digit' } : {}),
    hourCycle: 'h23',
  });
}

/** Format a duration in whole seconds as "Xm Ys", or MISSING ('-') when null. */
export function formatDuration(seconds: number | null): string {
  if (seconds === null) return MISSING;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

// ── Numbers ────────────────────────────────────────────────────────────────────

/** "12,34,567": a count grouped for the locale. '-' for a missing value. */
export function formatNumber(n: number | null | undefined, locale = 'en-IN'): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return MISSING;
  return n.toLocaleString(locale);
}

/**
 * "1 candidate", "3 candidates", "12,400 resumes": a count with a real plural,
 * never "candidate(s)". `many` defaults to `one` + "s". Staff screens only;
 * candidate text pluralises through i18n (`_one` / `_other`).
 */
export function plural(count: number, one: string, many = `${one}s`, locale = 'en-IN'): string {
  return `${formatNumber(count, locale)} ${count === 1 ? one : many}`;
}

// ── Money ──────────────────────────────────────────────────────────────────────

/**
 * "₹12,00,000": an amount in its currency, whole units, grouped for the
 * locale. A missing currency gives the grouped number alone; a currency code
 * Intl does not know gives "XYZ 12,00,000". '-' for a missing amount.
 */
export function formatMoney(
  amount: number | string | null | undefined,
  currency?: string | null,
  locale = 'en-IN',
): string {
  if (amount === null || amount === undefined || amount === '') return MISSING;
  const n = typeof amount === 'number' ? amount : Number(amount);
  if (!Number.isFinite(n)) return MISSING;
  const code = (currency ?? '').trim().toUpperCase();
  const grouped = n.toLocaleString(locale, { maximumFractionDigits: 0 });
  if (!code) return grouped;
  try {
    return new Intl.NumberFormat(locale, {
      style: 'currency',
      currency: code,
      maximumFractionDigits: 0,
    }).format(n);
  } catch {
    return `${code} ${grouped}`;
  }
}

// ── Scores ─────────────────────────────────────────────────────────────────────

/**
 * Which scale a score is on. Resume (ATS), exam and assessed-round scores are
 * out of 100; an interview composite is out of 10. They are not comparable,
 * which is why the unit is always printed: one pipeline card used to show
 * "7.8/10", "72%" and "78/100" side by side, and admin screens printed "7.05"
 * with no unit at all.
 */
export type ScoreKind = 'resume' | 'exam' | 'round' | 'interview';

export interface ScoreOptions {
  /** False drops the "/100" or "/10" (for an aria-label that says "out of 10"). */
  unit?: boolean;
}

/**
 * "78/100" for resume, exam and round scores (whole numbers); "7.8/10" for an
 * interview (one decimal). '-' for a missing value.
 */
export function formatScore(
  kind: ScoreKind,
  value: number | null | undefined,
  opts: ScoreOptions = {},
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return MISSING;
  const outOf = kind === 'interview' ? 10 : 100;
  // Math.round first: toFixed rounds the binary value, so 7.05 (stored as
  // 7.0499...) would print 7.0 while every other screen rounds it to 7.1.
  const shown =
    kind === 'interview' ? (Math.round(value * 10) / 10).toFixed(1) : String(Math.round(value));
  return opts.unit === false ? shown : `${shown}/${outOf}`;
}

// ── Language labels ────────────────────────────────────────────────────────────

/** Map of BCP-47 language codes to display names (Day-1 languages). */
export const LANGUAGE_LABELS: Record<string, string> = {
  en: 'English',
  hi: 'Hindi',
  te: 'Telugu',
};

/** Return the display name for a language code, falling back to the uppercased code. */
export function languageLabel(code: string): string {
  return LANGUAGE_LABELS[code] ?? code.toUpperCase();
}

// ── Session status badge ───────────────────────────────────────────────────────

/** Union of the Badge variant values used for session status. */
export type StatusVariant = 'default' | 'secondary' | 'outline' | 'destructive';

/** Map a session status string to a human-readable label and Badge variant. */
export function statusProps(status: string): { label: string; variant: StatusVariant } {
  switch (status) {
    case 'completed':
      return { label: 'Completed', variant: 'default' };
    case 'in_progress':
      return { label: 'In progress', variant: 'secondary' };
    case 'abandoned':
      return { label: 'Abandoned', variant: 'outline' };
    case 'failed':
      return { label: 'Failed', variant: 'destructive' };
    default:
      return { label: status, variant: 'outline' };
  }
}
