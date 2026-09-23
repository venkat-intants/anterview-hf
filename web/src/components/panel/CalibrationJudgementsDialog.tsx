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
import { X } from '@/design/components/icons';
import { useDialogFocus } from '@/hooks/useDialogFocus';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function signedGap(n: number | null): string {
  if (n === null) return '—';
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}`;
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
  const panelRef = useDialogFocus<HTMLElement>(onClose);
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

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button
        type="button"
        aria-label="Close judgements"
        onClick={onClose}
        className="absolute inset-0 bg-black/50"
      />
      <aside
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="calibration-judgements-title"
        tabIndex={-1}
        className="relative flex h-full w-full max-w-[480px] flex-col overflow-y-auto border-l border-border bg-card p-6 outline-none"
      >
        <div className="flex items-start justify-between gap-3">
          <h2
            id="calibration-judgements-title"
            className="text-[17px] font-semibold text-foreground"
          >
            {interviewerName}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 rounded-[8px] p-1.5 text-muted-foreground hover:text-foreground focus:outline-none focus-visible:text-foreground"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        {judgements.isLoading ? (
          <p className="mt-4 text-[13px] text-muted-foreground">Loading…</p>
        ) : suppressedMessage ? (
          <p className="mt-4 text-[13px] text-muted-foreground">{suppressedMessage}</p>
        ) : notFoundMessage ? (
          <p className="mt-4 text-[13px] text-muted-foreground">{notFoundMessage}</p>
        ) : judgements.isError ? (
          <p className="mt-4 text-[13px] text-[var(--ui-danger)]">
            {errText(judgements.error, 'Could not load these judgements just now.')}
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
                      <span className="ml-1 text-[11.5px] font-normal text-[var(--ui-faint)]">
                        panel {row.panel_mean ?? '—'} ({row.panel_size})
                      </span>
                    </span>
                  </div>
                  <p className="mt-0.5 text-[11.5px] text-muted-foreground">
                    {row.requisition_title ?? 'No opening on file'} · {row.round_title} ·{' '}
                    {row.competency_name}
                  </p>
                  <p className="mt-1 flex items-center justify-between gap-2 text-[11.5px]">
                    <span className="text-[var(--ui-faint)]">
                      Gap {signedGap(row.gap)} · submitted {formatDate(row.submitted_at)}
                    </span>
                    <Link
                      to={row.evidence_href}
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
      </aside>
    </div>
  );
}
