// useHrHomeQueries — the two lists on the HR home that are shown both as a panel
// and as a count beside its heading. One definition of each query, so the panel
// and the count are read from the very same cache entry and cannot disagree.

import { useQuery } from '@tanstack/react-query';
import { getAttention } from '@/api/attention';
import { getSlaBoard } from '@/api/stageSla';

/** The company's findings: what needs a human in this company's hiring, now. */
export function useAttention() {
  return useQuery({
    queryKey: ['hr-attention'],
    // Wrapped: passed bare, the query context would land in getAttention's
    // optional opening filter.
    queryFn: () => getAttention(),
    // Fresh enough to be trusted, not so fresh that leaving the dashboard open
    // re-runs the aggregate queries every minute. Deliberately no polling: the
    // events that change what needs attention (a submission, a completed
    // interview, a lapsed link) arrive as notifications, and the bell
    // invalidates this query when one does — see lib/liveRefresh.
    staleTime: 5 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });
}

/** Applications overdue or due soon against their stage SLA. */
export function useStagesAtRisk() {
  return useQuery({
    queryKey: ['hr', 'stage-sla', 'at-risk'],
    queryFn: () => getSlaBoard({ state: ['overdue', 'due_soon'] }),
    staleTime: 60_000,
    retry: false,
    throwOnError: false,
  });
}
