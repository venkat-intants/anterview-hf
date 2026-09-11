/**
 * Refresh the views an event just made stale — A5.
 *
 * Polling keeps a page roughly current; it does not know that anything has
 * happened. The notification feed does: every one of these kinds is written
 * at the moment the backend state it describes changed. So when the bell sees
 * a notification it has not seen before, the queries that event affects are
 * invalidated and whatever is on screen catches up — the scorecard list the
 * moment a scorecard lands, instead of up to thirty seconds later, and the
 * attention panel (which deliberately does not poll: it runs aggregates)
 * without anyone refreshing the tab.
 *
 * Cheap by construction: invalidateQueries refetches only queries that are
 * currently mounted. Everything else is marked stale and refetched when the
 * user next opens it, which is what would have happened anyway.
 *
 * Keys are prefixes (TanStack Query matches `['hr', 'exam']` against
 * `['hr', 'exam', id, 'attempts']`). A kind missing from the map refreshes
 * nothing — a new event type is safe to add on the backend before this file
 * knows about it.
 */

import { useEffect, useRef } from 'react';
import { useQueryClient, type QueryClient, type QueryKey } from '@tanstack/react-query';

// Views downstream of any candidate result: where they sit, and who is waiting.
const RESULT_VIEWS: QueryKey[] = [
  ['hr', 'applicants'],
  ['hr', 'applicant'],
  ['hr', 'pipeline'],
  ['hr', 'analytics'],
  ['hr', 'decision-queue'],
  ['hr', 'requisition-dashboard'],
  ['hr-attention'],
];

export const REFRESH_ON: Record<string, QueryKey[]> = {
  // HR
  interview_completed: [['hr', 'interviews'], ...RESULT_VIEWS],
  exam_submitted: [['hr', 'exam'], ...RESULT_VIEWS],
  auto_advance: [['hr', 'interviews'], ...RESULT_VIEWS],
  link_expired: [['hr', 'interviews'], ['hr', 'exam'], ['hr', 'applicants'], ['hr-attention']],
  bulk_upload: [['hr', 'applicants'], ['hr', 'analytics'], ['hr', 'requisition-dashboard']],
  review_due: [['hr', 'decision-queue'], ['hr', 'requisition-dashboard'], ['hr-attention']],
  applicant_scored: [['hr', 'applicants'], ['hr', 'pipeline']],
  // Candidate
  interview_invite: [['my-applications'], ['my-application']],
  results_ready: [['my-applications'], ['my-application']],
};

/** The distinct query prefixes to refresh for a batch of new notification kinds. */
export function queriesToRefresh(kinds: Iterable<string>): QueryKey[] {
  const seen = new Set<string>();
  const out: QueryKey[] = [];
  for (const kind of kinds) {
    for (const key of REFRESH_ON[kind] ?? []) {
      const id = JSON.stringify(key);
      if (!seen.has(id)) {
        seen.add(id);
        out.push(key);
      }
    }
  }
  return out;
}

export function refreshFor(client: QueryClient, kinds: Iterable<string>): void {
  for (const queryKey of queriesToRefresh(kinds)) {
    void client.invalidateQueries({ queryKey });
  }
}

/**
 * Watch a notification list and refresh for each notification not seen before.
 *
 * The first list only records what is already there: those events are old,
 * and whatever page opened with them fetched its data after they happened, so
 * refreshing for them would be a burst of requests that changes nothing.
 */
export function useLiveRefresh(items: ReadonlyArray<{ id: string; kind: string }> | undefined): void {
  const client = useQueryClient();
  const seen = useRef<Set<string> | null>(null);
  useEffect(() => {
    if (!items) return;
    const known = seen.current;
    if (known === null) {
      seen.current = new Set(items.map((n) => n.id));
      return;
    }
    const fresh = items.filter((n) => !known.has(n.id));
    if (fresh.length === 0) return;
    for (const n of fresh) known.add(n.id);
    refreshFor(client, fresh.map((n) => n.kind));
  }, [items, client]);
}
