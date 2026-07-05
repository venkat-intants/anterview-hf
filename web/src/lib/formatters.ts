// formatters.ts — shared formatting helpers used across Dashboard, History, and Resume.
// All functions are pure and have no side-effects.

// ── Date / duration ────────────────────────────────────────────────────────────

/** Format an ISO date string as a short localised date (en-IN). */
export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

/** Format a duration in whole seconds as "Xm Ys", or "—" when null. */
export function formatDuration(seconds: number | null): string {
  if (seconds === null) return '—';
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
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
      return { label: 'In Progress', variant: 'secondary' };
    case 'abandoned':
      return { label: 'Abandoned', variant: 'outline' };
    case 'failed':
      return { label: 'Failed', variant: 'destructive' };
    default:
      return { label: status, variant: 'outline' };
  }
}
