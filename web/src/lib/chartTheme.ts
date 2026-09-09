/**
 * One chart vocabulary for every recharts surface.
 *
 * `TOOLTIP_STYLE` was defined three times — AdminOverview, AdminAnalytics,
 * HRAnalytics — each with the same hardcoded `#1c1c1e` on `#f5f5f7`. In light
 * mode that renders a black tooltip carrying dark-blue series text over a white
 * chart: the one element a reader hovers deliberately was the least readable
 * thing on the page. Three copies also meant a fix had to be made three times
 * and would drift the first time it wasn't.
 *
 * These values are CSS variables rather than hexes because recharts writes
 * `contentStyle` straight onto the element, so `var()` resolves against the
 * active mode at paint — one definition covers both.
 *
 * The mode-agnostic parts (radius, font size, the categorical palette) live
 * here too, so a new chart inherits the system instead of inventing one.
 */

/** Tooltip surface. Follows the mode; see --chart-* in index.css. */
export const TOOLTIP_STYLE = {
  background: 'var(--chart-tooltip-bg)',
  border: '1px solid var(--ui-line-strong)',
  borderRadius: 12,
  fontSize: 12,
  color: 'var(--chart-strong)',
  boxShadow: 'var(--ui-shadow-card)',
} as const;

/** Axis labels and gridlines. */
export const AXIS_STYLE = {
  stroke: 'var(--chart-text)',
  fontSize: 11,
} as const;

export const GRID_STROKE = 'var(--chart-grid)';

/**
 * Deterministic categorical palette — Signal-Blue leads, cool secondaries
 * follow. Fixed order on purpose: a series must not change colour because a
 * filter reordered the data. These are chosen to hold contrast on both the
 * white and the near-black chart ground, which is why none of them is a pastel.
 */
export const PALETTE = [
  '#0088ff', // Signal-Blue — the primary series anchor
  '#7c5cd6', // violet
  '#12a150', // green
  '#c2410c', // ember
  '#0e7490', // teal
  '#a21caf', // magenta
] as const;
