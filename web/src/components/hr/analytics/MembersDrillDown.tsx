// MembersDrillDown — the auditable people behind a count or rate. A Radix
// Sheet: focus trap, Escape, the overlay click, the Close button and focus
// return to the number that opened it.

import { useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { isTransientApiError } from '@/api/client';
import {
  findDefinition,
  getAnalyticsMembers,
  metricLabel,
  sourceLabel,
  type CohortBasis,
  type MetricDefinitionsResponse,
  type MetricPart,
} from '@/api/metrics';
import { QueryErrorPanel } from '@/components/QueryState';
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { formatDate } from '@/lib/formatters';
import { appPath } from '@/lib/safeUrl';

export default function MembersDrillDown({
  metricName,
  part,
  definitions,
  query,
  onClose,
  onOpenCandidate,
}: {
  metricName: string;
  part: MetricPart;
  definitions: MetricDefinitionsResponse | undefined;
  query: {
    cohort: CohortBasis;
    from?: string;
    to?: string;
    requisition_id?: string;
    source?: string;
    /** PH5-E4 — narrows to one interviewer-score band; each row then also
     *  carries `interviewer_score`/`evidence_href`. */
    score_band?: string;
  };
  onClose: () => void;
  onOpenCandidate: (row: { applicant_id: string; enrolment_id: string }) => void;
}): JSX.Element {
  // Opened by name alone (no version in hand) — the CURRENT definition.
  const def = findDefinition(definitions, metricName);
  const returnFocusTo = useRef<HTMLElement | null>(null);
  const members = useQuery({
    queryKey: ['hr', 'analytics', 'members', metricName, part, query],
    queryFn: () => getAnalyticsMembers({ metric: metricName, part, ...query }),
    retry: (count, error) => isTransientApiError(error) && count < 2,
  });

  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent
        // Opened by a plain button, not a Radix trigger, so Radix has nothing to
        // return focus to: hand it back to whatever opened this.
        onOpenAutoFocus={() => {
          returnFocusTo.current =
            document.activeElement instanceof HTMLElement ? document.activeElement : null;
        }}
        onCloseAutoFocus={(e) => {
          e.preventDefault();
          returnFocusTo.current?.focus();
        }}
        side="right"
        aria-describedby={undefined}
        className="flex w-full flex-col gap-0 overflow-y-auto sm:max-w-[480px]"
      >
        <SheetTitle className="pr-10 text-[17px] font-bold leading-snug">
          {def ? metricLabel(def.name, def) : metricName}
        </SheetTitle>

        {members.isLoading ? (
          <div className="mt-4 flex flex-col gap-2" aria-busy="true">
            <span className="sr-only">Loading…</span>
            <Skeleton className="h-16 rounded-[12px]" />
            <Skeleton className="h-16 rounded-[12px]" />
            <Skeleton className="h-16 rounded-[12px]" />
          </div>
        ) : members.isError ? (
          <QueryErrorPanel
            className="mt-4"
            title="Could not load these records."
            error={members.error}
            onRetry={() => void members.refetch()}
            retrying={members.isFetching}
          />
        ) : members.data && members.data.rows.length === 0 ? (
          <p className="mt-4 text-[13px] text-muted-foreground">
            Nobody is behind this number for the current filters.
          </p>
        ) : members.data ? (
          <>
            <p className="mt-3 text-[13px] text-muted-foreground">
              {members.data.truncated
                ? `Showing 200 of ${members.data.total}`
                : `${members.data.total} ${members.data.total === 1 ? 'person' : 'people'}`}
            </p>
            <ul className="mt-3 flex flex-col gap-2">
              {members.data.rows.map((row) => (
                <li
                  key={row.enrolment_id}
                  className="rounded-[12px] border border-border bg-[var(--ui-inset-soft)]"
                >
                  <button
                    type="button"
                    onClick={() =>
                      onOpenCandidate({
                        applicant_id: row.applicant_id,
                        enrolment_id: row.enrolment_id,
                      })
                    }
                    className="flex w-full flex-col items-start gap-0.5 rounded-[12px] p-3 text-left transition-colors hover:bg-[var(--ui-inset)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                  >
                    <span className="text-[14px] font-semibold text-foreground">
                      {row.candidate_name}
                    </span>
                    <span className="text-[12px] text-muted-foreground">
                      {row.requisition_title ?? 'No opening on file'} ·{' '}
                      {sourceLabel(row.source, definitions)} · applied {formatDate(row.applied_at)}
                    </span>
                    {/* PH5-E4 — only present for a score-band drill-down (the
                    outcome-signals table); an ordinary members list never
                    shows this. */}
                    {row.interviewer_score != null ? (
                      <span className="text-[12px] text-muted-foreground">
                        Interviewer score {row.interviewer_score.toFixed(1)}
                      </span>
                    ) : null}
                  </button>
                  {row.evidence_href ? (
                    <Link
                      to={appPath(row.evidence_href, '/')}
                      className="block px-3 pb-2.5 text-[12px] text-[var(--ui-info)] underline decoration-dotted underline-offset-2 hover:opacity-80"
                    >
                      Evidence trail
                    </Link>
                  ) : null}
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
