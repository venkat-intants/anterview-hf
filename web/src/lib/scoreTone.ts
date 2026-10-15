// Text colour for a 0–100 score, on the status tokens so it holds in both modes.
// Returns a class rather than a hex because a hex does not follow the theme.

export function scoreTextClass(score: number | null | undefined): string {
  if (score == null) return 'text-[var(--ui-faint)]';
  if (score >= 85) return 'text-[var(--ui-ok)]';
  if (score >= 70) return 'text-[var(--ui-info)]';
  if (score >= 55) return 'text-[var(--ui-warn)]';
  return 'text-[var(--ui-danger)]';
}

/** Relevance of a search match: warm at high relevance, quiet at low. */
export function matchTextClass(score: number): string {
  if (score >= 70) return 'text-[var(--ui-ok)]';
  if (score >= 45) return 'text-[var(--ui-warn)]';
  return 'text-muted-foreground';
}
