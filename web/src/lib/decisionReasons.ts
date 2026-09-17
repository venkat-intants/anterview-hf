// decisionReasons.ts — O4 shared logic: fetching the taxonomy, filtering it
// by outcome, and the free-text length a chosen reason requires.
//
// Split from the DecisionReasonSelect component (rather than living in that
// .tsx file) for the same reason navSections.tsx is split from AppShell.tsx:
// react-refresh/only-export-components fires on a file that exports both
// components and plain values, and this repo lints with --max-warnings 0.

import { useQuery } from '@tanstack/react-query';
import { listDecisionReasons, type DecisionOutcome, type DecisionReason } from '@/api/scorecards';

/** Free-text reason length when the chosen reason carries no extra weight. */
export const MIN_REASON = 3;
/** Free-text reason length once the chosen reason's requires_explanation is set. */
export const MIN_REASON_EXPLAINED = 10;

export function useDecisionReasons() {
  return useQuery({
    queryKey: ['hr', 'decision-reasons'],
    queryFn: listDecisionReasons,
    staleTime: 5 * 60_000,
  });
}

/** The reasons offered for one outcome — 'both' reasons always qualify. */
export function reasonsFor(
  all: DecisionReason[] | undefined,
  outcome: DecisionOutcome | null,
): DecisionReason[] {
  if (!outcome) return [];
  return (all ?? []).filter((r) => r.applies_to === outcome || r.applies_to === 'both');
}

/** How many free-text characters the chosen reason needs. */
export function minReasonLength(chosen: DecisionReason | null | undefined): number {
  return chosen?.requires_explanation ? MIN_REASON_EXPLAINED : MIN_REASON;
}
