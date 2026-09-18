// timezone.ts — formatting one instant in an arbitrary IANA zone, and in the
// reader's own. PH4-A2 shows every interview time in (at least) two zones —
// the person reading it, and the candidate the loop was created for — and
// this is the one place that math lives so a session row and a candidate
// card cannot drift into disagreeing with each other.
//
// No date library: native Intl.DateTimeFormat covers everything used here,
// and the stack forbids adding a package without checking its bundle cost
// (CLAUDE.md) for something this small.

/** The IANA zone this browser is running in, e.g. "Asia/Kolkata". */
export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

/** "10:30" — 24-hour clock, in the given zone. `locale` only changes how
 *  digits/separators render (candidate screens pass the visitor's own
 *  language so a date reads in it, per CLAUDE.md's EN/HI/TE rule). */
export function timeInZone(iso: string, tz: string, locale = 'en-IN'): string {
  return new Intl.DateTimeFormat(locale, {
    timeZone: tz,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(iso));
}

/**
 * The zone abbreviation Intl knows for this instant/zone pair ("IST",
 * "GMT+5:30", ...) — whatever the runtime's ICU data names it. Never
 * hardcoded, because the same IANA zone can abbreviate differently across
 * runtimes and the wrong guess is worse than the verbose one Intl gives back.
 */
export function zoneAbbrev(iso: string, tz: string): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz,
    hour: '2-digit',
    minute: '2-digit',
    timeZoneName: 'short',
  }).formatToParts(new Date(iso));
  return parts.find((p) => p.type === 'timeZoneName')?.value ?? tz;
}

/**
 * "10:30 IST (06:00 your time)" — the candidate's zone first (named), then
 * the reader's own. Used everywhere HR reads a session time that belongs to
 * a candidate in a different zone.
 */
export function formatSessionWhen(iso: string, candidateTz: string): string {
  const theirs = `${timeInZone(iso, candidateTz)} ${zoneAbbrev(iso, candidateTz)}`;
  const mine = timeInZone(iso, browserTimezone());
  if (candidateTz === browserTimezone()) return theirs;
  return `${theirs} (${mine} your time)`;
}

/** "Mon 21 Sep, 10:30" — a full day + time label in one zone. */
export function formatDayTime(iso: string, tz: string, locale = 'en-IN'): string {
  return new Intl.DateTimeFormat(locale, {
    timeZone: tz,
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(iso));
}

/** A grouping key for "which calendar day is this, in this zone" — "2026-09-21". */
export function dayKey(iso: string, tz: string): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: tz,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(iso));
}

/** "Mon 21 Sep" — a day heading, in a given zone, with no time component. */
export function dayHeading(iso: string, tz: string, locale = 'en-IN'): string {
  return new Intl.DateTimeFormat(locale, {
    timeZone: tz,
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  }).format(new Date(iso));
}

/**
 * A date-ONLY string ("2026-09-21", as the workload report's `by_day` and
 * `over_allocated_days` use) rendered as "21 Sep" — with no timezone
 * conversion, because the string already names a specific calendar day and
 * shifting it through another zone could show the wrong date.
 */
export function isoDateLabel(dateStr: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr);
  if (!m) return dateStr;
  const dt = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return new Intl.DateTimeFormat('en-IN', { timeZone: 'UTC', day: 'numeric', month: 'short' }).format(dt);
}

/** An ISO week string ("2026-W38", as `over_allocated_weeks` uses) as words. */
export function isoWeekLabel(weekStr: string): string {
  const m = /^(\d{4})-W(\d{2})$/.exec(weekStr);
  if (!m) return weekStr;
  return `week ${Number(m[2])}, ${m[1]}`;
}
