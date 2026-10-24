// CalibrationJudgementsDialog — "Show judgements": the drill-down behind one
// interviewer's calibration row, or one interviewer x criterion cell
// (PH5-E4 criterion 13). Named down to the candidate, so this is a stricter
// read than the aggregate report and the server enforces it the same way:
// 422 ("Too few candidates to show") when that cell is itself suppressed —
// shown here as plain text, never as a generic error — and 404 for an
// interviewer outside the company.

import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ApiError, isTransientApiError } from '@/api/client';
import { getCalibrationJudgements } from '@/api/scheduling';
import { formatDate } from '@/lib/formatters';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { appPath } from '@/lib/safeUrl';
import { errorMessage } from '@/lib/errorMessage';

function signedGap(n: number | null): string {
  if (n === null) return '-';
  return `${n > 0 ? '+' : ''}${n.toFixed(1)}`;
}

export default function CalibrationJudgementsDialog({
  interviewerId,
  interviewerName,
  start,
  end,
  requisitionId,
  roundId,
  criterionKey,
  onClose,
}: {
  interviewerId: string;
  interviewerName: string;
  start: string;
  end: string;
  requisitionId: string | null;
  roundId: string | null;
  /** Set when opened from one criterion's breakdown row, narrowing the drill-down
   *  to that frozen criterion instead of the interviewer's whole period. */
  criterionKey?: string | null;
  onClose: () => void;
}): JSX.Element {
  const judgements = useQuery({
    queryKey: [
      'hr',
      'panel',
      'calibration',
      'judgements',
      interviewerId,
      start,
      end,
      requisitionId,
      roundId,
      criterionKey ?? null,
    ],
    queryFn: () =>
      getCalibrationJudgements({
        interviewerId,
        start,
        end,
        requisitionId,
        roundId,
        criterionKey,
      }),
    retry: (count, error) => isTransientApiError(error) && count < 2,
  });

  // The server's 422 ("Too few candidates to show") is a plain fact about
  // this cell, not a failure — shown as the dialog's body text, never as an
  // error banner.
  const suppressedMessage =
    judgements.error instanceof ApiError && judgements.error.status === 422
      ? judgements.error.message
      : null;
  const notFoundMessage =
    judgements.error instanceof ApiError && judgements.error.status === 404
      ? judgements.error.message
      : null;

  // Mounted only while open (the tab renders it conditionally), so the sheet is
  // always open and closing it hands control back to the parent.
  return (
    <Sheet open onOpenChange={(open) => (open ? undefined : onClose())}>
      <SheetContent
        side="right"
        aria-describedby={undefined}
        className="flex w-full flex-col overflow-y-auto sm:max-w-[480px]"
      >
        <SheetHeader className="pr-10 text-left">
          <SheetTitle className="text-[17px] font-semibold text-foreground">
            {interviewerName}
          </SheetTitle>
        </SheetHeader>

        {judgements.isLoading ? (
          <div className="mt-4 flex flex-col gap-2" aria-busy="true" aria-label="Loading judgements">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : suppressedMessage ? (
          <p className="mt-4 text-[13px] text-muted-foreground">{suppressedMessage}</p>
        ) : notFoundMessage ? (
          <p className="mt-4 text-[13px] text-muted-foreground">{notFoundMessage}</p>
        ) : judgements.isError ? (
          <p className="mt-4 text-[13px] text-[var(--ui-danger)]">
            {errorMessage(judgements.error, 'Could not load these judgements just now.')}
          </p>
        ) : judgements.data && judgements.data.rows.length === 0 ? (
          <p className="mt-4 text-[13px] text-muted-foreground">
            No shared judgements for the current filters.
          </p>
        ) : judgements.data ? (
          <>
            <p className="mt-3 text-[12px] text-muted-foreground">
              {judgements.data.truncated
                ? `Showing ${judgements.data.rows.length} of ${judgements.data.total}, newest first`
                : `${judgements.data.total} judgement${judgements.data.total === 1 ? '' : 's'}`}
            </p>
            <ul className="mt-3 flex flex-col gap-2">
              {judgements.data.rows.map((row) => (
                <li
                  key={`${row.scorecard_id}-${row.criterion_key}`}
                  className="rounded-[10px] border border-border bg-[var(--ui-inset-soft)] p-3"
                >
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="text-[13px] font-medium text-foreground">
                      {row.candidate_name}
                    </span>
                    <span className="text-[13px] font-semibold text-foreground">
                      {row.score}
                      <span className="ml-1 text-[12px] font-normal text-muted-foreground">
                        panel {row.panel_mean ?? '-'} ({row.panel_size})
                      </span>
                    </span>
                  </div>
                  <p className="mt-0.5 text-[12px] text-muted-foreground">
                    {row.requisition_title ?? 'No opening on file'} · {row.round_title} ·{' '}
                    {row.competency_name}
                  </p>
                  <p className="mt-1 flex items-center justify-between gap-2 text-[12px]">
                    <span className="text-muted-foreground">
                      Gap {signedGap(row.gap)} · submitted {formatDate(row.submitted_at)}
                    </span>
                    <Link
                      to={appPath(row.evidence_href, '/')}
                      className="text-[var(--ui-info)] underline decoration-dotted underline-offset-2 hover:opacity-80"
                    >
                      Evidence trail
                    </Link>
                  </p>
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
