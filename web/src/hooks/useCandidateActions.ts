// useCandidateActions — the candidate's applications, scheduled interviews and
// offers, folded into the ordered "what is waiting on me" queue.
//
// Request budget, because the database behind this is billed by wake-ups:
//   - applications: always, one request;
//   - interview loops and offers: when there is at least one application, or
//     when the applications call FAILED (a failure says nothing about whether an
//     offer exists), or when the caller sets `alwaysFetchSecondary` (the
//     Applications page, which is the page for them). Only an account whose
//     applications loaded and came back empty skips them, so a practice-only
//     account pays for the applications call and nothing else;
//   - the three share cache keys with the Applications page and its sections,
//     so opening that page after the dashboard re-uses them;
//   - a long staleTime and NO refetchInterval. The queue changes when the
//     candidate acts or when they next open the page, not on a timer.

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listMyApplications, type MyApplication } from '@/api/applications';
import { listMyInterviewLoops, type CandidateLoop } from '@/api/scheduling';
import { listMyOffers, type CandidateOffer } from '@/api/offers';
import {
  deriveCandidateActions,
  type CandidateAction,
  type PracticeContext,
} from '@/lib/candidateActions';

/** Shared cache keys. The sections on /applications read the same two. */
export const CANDIDATE_KEYS = {
  applications: ['my-applications'],
  loops: ['me', 'interview-loops'],
  offers: ['me', 'offers'],
} as const;

const LONG_STALE_MS = 5 * 60 * 1000;

interface Options {
  /** Set false to fetch nothing (a guest or a staff session on a candidate page). */
  enabled?: boolean;
  /** Lets the queue end in a practice step when there is no open application. */
  practice?: PracticeContext;
  /** Fetch loops and offers even when the applications list is empty. */
  alwaysFetchSecondary?: boolean;
}

export interface CandidateActionsResult {
  applications: MyApplication[];
  loops: CandidateLoop[];
  offers: CandidateOffer[];
  actions: CandidateAction[];
  /** True until the applications call has answered, and (when there are any) the other two. */
  isLoading: boolean;
  /** The applications call failed. A failed loops/offers call just contributes nothing to the queue. */
  isError: boolean;
  error: unknown;
  /** The loops / offers call failed, so a section that would list them can still say so. */
  loopsFailed: boolean;
  offersFailed: boolean;
  /** Any of the three calls failed: the queue may be missing something that is waiting. */
  anyFailed: boolean;
  /** Re-run only the calls that failed. */
  refetchFailed: () => void;
}

export function useCandidateActions({
  enabled = true,
  practice,
  alwaysFetchSecondary = false,
}: Options = {}): CandidateActionsResult {
  const appsQuery = useQuery({
    queryKey: CANDIDATE_KEYS.applications,
    queryFn: listMyApplications,
    enabled,
    staleTime: LONG_STALE_MS,
    retry: false,
    throwOnError: false,
  });

  const applications = useMemo(() => appsQuery.data ?? [], [appsQuery.data]);
  const hasApplications = applications.length > 0;
  // A failed applications call is "unknown", not "none": look for the rest.
  const fetchSecondary = enabled && (alwaysFetchSecondary || hasApplications || appsQuery.isError);

  const loopsQuery = useQuery({
    queryKey: CANDIDATE_KEYS.loops,
    queryFn: listMyInterviewLoops,
    enabled: fetchSecondary,
    staleTime: LONG_STALE_MS,
    retry: false,
    throwOnError: false,
  });

  const offersQuery = useQuery({
    queryKey: CANDIDATE_KEYS.offers,
    queryFn: listMyOffers,
    enabled: fetchSecondary,
    staleTime: LONG_STALE_MS,
    retry: false,
    throwOnError: false,
  });

  const loops = useMemo(() => loopsQuery.data ?? [], [loopsQuery.data]);
  const offers = useMemo(() => offersQuery.data ?? [], [offersQuery.data]);

  const planReady = practice?.planReady;
  const focusCompetency = practice?.focusCompetency;
  const interviewsTaken = practice?.interviewsTaken;
  const actions = useMemo(
    () =>
      deriveCandidateActions(
        applications,
        loops,
        offers,
        planReady === undefined || focusCompetency === undefined || interviewsTaken === undefined
          ? undefined
          : { planReady, focusCompetency, interviewsTaken },
      ),
    [applications, loops, offers, planReady, focusCompetency, interviewsTaken],
  );

  return {
    applications,
    loops,
    offers,
    actions,
    isLoading:
      appsQuery.isLoading || (fetchSecondary && (loopsQuery.isLoading || offersQuery.isLoading)),
    isError: appsQuery.isError,
    error: appsQuery.error,
    loopsFailed: loopsQuery.isError,
    offersFailed: offersQuery.isError,
    anyFailed: appsQuery.isError || loopsQuery.isError || offersQuery.isError,
    refetchFailed: () => {
      if (appsQuery.isError) void appsQuery.refetch();
      if (loopsQuery.isError) void loopsQuery.refetch();
      if (offersQuery.isError) void offersQuery.refetch();
    },
  };
}
