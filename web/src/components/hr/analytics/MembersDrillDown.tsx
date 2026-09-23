// MembersDrillDown — the auditable people behind a count or rate.

import { useQuery } from '@tanstack/react-query';
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
import { formatDate } from '@/lib/formatters';
import { X } from '@/design/components/icons';
import { useDialogFocus } from '@/hooks/useDialogFocus';

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
  };
  onClose: () => void;
  onOpenCandidate: (row: { applicant_id: string; enrolment_id: string }) => void;
}): JSX.Element {
  // Opened by name alone (no version in hand) — the CURRENT definition.
  const def = findDefinition(definitions, metricName);
  const members = useQuery({
    queryKey: ['hr', 'analytics', 'members', metricName, part, query],
    queryFn: () => getAnalyticsMembers({ metric: metricName, part, ...query }),
    retry: (count, error) => isTransientApiError(error) && count < 2,
  });
  const panelRef = useDialogFocus<HTMLElement>(onClose);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button
        type="button"
        aria-label="Close candidate list"
        onClick={onClose}
        className="absolute inset-0 bg-black/50"
      />
      <aside
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="members-drilldown-title"
        tabIndex={-1}
        className="relative flex h-full w-full max-w-[480px] flex-col overflow-y-auto border-l border-border bg-card p-6 outline-none"
      >
        <div className="flex items-start justify-between gap-3">
          <h2 id="members-drilldown-title" className="text-[17px] font-semibold text-foreground">
            {def ? metricLabel(def.name, def) : metricName}
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

        {members.isLoading ? (
          <p className="mt-4 text-[13px] text-muted-foreground">Loading…</p>
        ) : members.isError ? (
          <p className="mt-4 text-[13px] text-[var(--ui-danger)]">
            Could not load these records just now.
          </p>
        ) : members.data && members.data.rows.length === 0 ? (
          <p className="mt-4 text-[13px] text-muted-foreground">
            Nobody is behind this number for the current filters.
          </p>
        ) : members.data ? (
          <>
            <p className="mt-3 text-[12px] text-muted-foreground">
              {members.data.truncated
                ? `Showing 200 of ${members.data.total}`
                : `${members.data.total} ${members.data.total === 1 ? 'person' : 'people'}`}
            </p>
            <ul className="mt-3 flex flex-col gap-2">
              {members.data.rows.map((row) => (
                <li key={row.enrolment_id}>
                  <button
                    type="button"
                    onClick={() =>
                      onOpenCandidate({
                        applicant_id: row.applicant_id,
                        enrolment_id: row.enrolment_id,
                      })
                    }
                    className="flex w-full flex-col items-start gap-0.5 rounded-[10px] border border-border bg-[var(--ui-inset-soft)] p-3 text-left transition-colors hover:border-[var(--ui-line-strong)] focus:outline-none focus-visible:border-[var(--accent)]"
                  >
                    <span className="text-[13px] font-medium text-foreground">
                      {row.candidate_name}
                    </span>
                    <span className="text-[11.5px] text-muted-foreground">
                      {row.requisition_title ?? 'No opening on file'} ·{' '}
                      {sourceLabel(row.source, definitions)} · applied {formatDate(row.applied_at)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </aside>
    </div>
  );
}
