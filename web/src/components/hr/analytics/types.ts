// types.ts — shapes shared across the split HR analytics components.
// FilterState lives here (rather than in HRAnalyticsPage, which still owns
// the state) so GroupComparisonTable does not import a TYPE back out of the
// page module — a type-only import would be erased and therefore harmless
// either way, but keeping the shared shape in its own module makes the
// dependency direction unambiguous.

import type { CohortBasis, GroupByOption } from '@/api/metrics';

export interface FilterState {
  cohort: CohortBasis;
  from: string;
  to: string;
  requisitionId: string;
  source: string;
  groupBy: 'none' | GroupByOption;
}

/** Structural — matches a react-query result without importing its generic type. */
export interface QueryLike<T> {
  data?: T;
  isLoading: boolean;
  isError: boolean;
}
