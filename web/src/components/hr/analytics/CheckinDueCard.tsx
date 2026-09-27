// CheckinDueCard — "Check-ins due" (PH5 wave-1 follow-up A2), a card inside
// Quality of hire. Clicking a row opens the SHARED CandidateDrawer (see
// HRAnalyticsPage) — `GET /hr/checkins/due` rows carry `applicant_id`, so
// there is nothing check-in-specific to build here beyond the list itself.

import { formatDate } from '@/lib/formatters';
import type { CheckinDueRow } from '@/api/checkins';

export default function CheckinDueCard({
  rows,
  isLoading,
  isError,
  onOpen,
}: {
  rows: CheckinDueRow[];
  isLoading: boolean;
  isError: boolean;
  onOpen: (row: CheckinDueRow) => void;
}): JSX.Element {
  return (
    <div className="mt-5 rounded-[14px] border border-border p-4">
      <h3 className="text-[13px] font-semibold text-foreground">Check-ins due</h3>
      <p className="mt-1 text-[11.5px] text-muted-foreground">
        Hires at least 80 days in with no check-in recorded yet, oldest first.
      </p>
      <div className="mt-3">
        {isLoading ? (
          <p className="text-[12.5px] text-muted-foreground">Loading…</p>
        ) : isError ? (
          <p className="text-[12.5px] text-[var(--ui-danger)]">
            Could not load check-ins due just now.
          </p>
        ) : rows.length === 0 ? (
          <p className="text-[12.5px] text-muted-foreground">No check-ins due.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {rows.map((row) => (
              <li key={row.enrolment_id}>
                <button
                  type="button"
                  onClick={() => onOpen(row)}
                  className="flex w-full items-center justify-between gap-3 rounded-[10px] border border-border bg-[var(--ui-inset-soft)] p-2.5 text-left transition-colors hover:border-[var(--ui-line-strong)] focus:outline-none focus-visible:border-[var(--accent)]"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-[12.5px] font-medium text-foreground">
                      {row.full_name}
                    </span>
                    <span className="block truncate text-[11px] text-muted-foreground">
                      {row.job_title}
                    </span>
                  </span>
                  <span className="shrink-0 text-right text-[11px] text-[var(--ui-faint)]">
                    started {formatDate(row.employment_start)}
                    <br />
                    {row.days_since_start}d ago
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
