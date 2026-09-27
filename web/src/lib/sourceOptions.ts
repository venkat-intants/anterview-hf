// sourceOptions.ts — PH5 wave-1 follow-up (B) shared logic: the source
// vocabulary's options, read from the metric layer's definitions rather than
// hand-kept here. Split into its own .ts file for the same reason
// decisionReasons.ts is: react-refresh/only-export-components fires on a file
// exporting both a component and a plain hook, and this repo lints with
// --max-warnings 0.

import { useQuery } from '@tanstack/react-query';
import {
  getMetricDefinitions,
  sourceOptionsFromDefinitions,
  type SourceOption,
} from '@/api/metrics';

/**
 * The `source` dimension's option list, shared under the SAME query key the
 * analytics page uses — one cached fetch, not one per consumer (Applicants'
 * upload form, CandidateDrawer's source label, HRAnalyticsPage's filter).
 * Falls back to a tiny built-in list (see sourceOptionsFromDefinitions) if
 * the definitions call fails, so a picker never renders empty.
 */
export function useSourceOptions(): { options: SourceOption[]; isLoading: boolean } {
  const q = useQuery({
    queryKey: ['hr', 'metrics', 'definitions'],
    queryFn: getMetricDefinitions,
    staleTime: 5 * 60_000,
  });
  return { options: sourceOptionsFromDefinitions(q.data), isLoading: q.isLoading };
}
