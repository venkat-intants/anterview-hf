/**
 * Polling intervals for views that show work happening elsewhere — A5.
 *
 * The HR console had no polling at all and `refetchOnWindowFocus` disabled
 * globally, so a manager watching a cohort sit an exam saw a frozen page until
 * they pressed browser refresh. Nothing was broken; it simply looked as though
 * nothing was happening, which is most of why the product read as manual.
 *
 * Two intervals rather than one, because "is this list still accurate?" and
 * "is this thing I am waiting on finished?" deserve different answers:
 *
 *   LIVE_POLL_MS   list and dashboard views a manager leaves open
 *   ACTIVE_POLL_MS a view whose whole purpose is waiting for a result
 *
 * `refetchIntervalInBackground` is deliberately left at its default (false)
 * everywhere: a tab nobody is looking at should not keep a Neon connection
 * busy, and `refetchOnWindowFocus` already refreshes the moment they return.
 */

/** Lists and dashboards left open in the background. */
export const LIVE_POLL_MS = 30_000;

/** Views that exist to watch something finish (attempts landing, results arriving). */
export const ACTIVE_POLL_MS = 12_000;
