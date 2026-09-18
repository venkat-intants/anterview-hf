// StagesAtRiskWidget — PH4-O1. A compact list of applications overdue or due
// soon against their stage SLA, so a manager sees who to chase without
// opening every opening's decision queue in turn. Informational only: nothing
// here moves a candidate or changes a status — it links to the decision queue
// where a person acts.

import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { GlassCard } from '@/design/components/primitives';
import { AlertTriangle, CheckCircle2 } from '@/design/components/icons';
import { cn } from '@/lib/utils';
import { getSlaBoard } from '@/api/stageSla';

const VISIBLE = 6;

export default function StagesAtRiskWidget(): JSX.Element {
  const board = useQuery({
    queryKey: ['hr', 'stage-sla', 'at-risk'],
    queryFn: () => getSlaBoard({ state: ['overdue', 'due_soon'] }),
    staleTime: 60_000,
    retry: false,
    throwOnError: false,
  });

  const rows = board.data ?? [];

  return (
    <GlassCard className="p-5">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h3 className="text-[15px] font-semibold text-foreground">Stages at risk</h3>
        {rows.length > 0 ? (
          <span className="text-[12px] text-muted-foreground">{rows.length}</span>
        ) : null}
      </div>

      {board.isLoading ? (
        <div className="h-[58px] animate-pulse rounded-[12px] bg-[var(--ui-inset-soft)]" />
      ) : null}

      {board.isError ? (
        <p className="text-[13px] text-muted-foreground">
          Could not check stage SLAs just now.
        </p>
      ) : null}

      {!board.isLoading && !board.isError && rows.length === 0 ? (
        <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
          <CheckCircle2 className="h-4 w-4 text-[var(--ui-ok)]" aria-hidden="true" />
          Nothing overdue or due soon.
        </p>
      ) : null}

      {rows.length > 0 ? (
        <ul className="flex flex-col gap-2">
          {rows.slice(0, VISIBLE).map((r) => (
            <li key={r.enrolment_id}>
              <Link
                to={`/hr/requisitions/${r.requisition_id}/decisions`}
                className="flex items-start gap-2.5 rounded-[12px] border border-border bg-[var(--ui-inset-soft)] p-3 text-left transition-colors hover:border-[var(--ui-line-strong)] focus:outline-none focus-visible:border-[var(--accent)]"
              >
                <AlertTriangle
                  className={cn(
                    'mt-0.5 h-4 w-4 shrink-0',
                    r.state === 'overdue' ? 'text-[var(--ui-danger)]' : 'text-[var(--ui-warn)]',
                  )}
                  aria-hidden="true"
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-medium text-foreground">
                    {r.full_name} &mdash; {r.stage}
                  </span>
                  <span className="block truncate text-[11.5px] text-muted-foreground">
                    {r.opening_title} &middot; {r.state === 'overdue' ? 'overdue' : 'due soon'}
                    {r.owner_name ? ` · ${r.owner_name}` : ''}
                  </span>
                </span>
              </Link>
            </li>
          ))}
          {rows.length > VISIBLE ? (
            <li className="pl-1 text-[11.5px] text-muted-foreground">
              +{rows.length - VISIBLE} more
            </li>
          ) : null}
        </ul>
      ) : null}
    </GlassCard>
  );
}
