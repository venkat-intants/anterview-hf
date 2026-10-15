// StagesAtRiskWidget — PH4-O1. A compact list of applications overdue or due
// soon against their stage SLA, so a manager sees who to chase without
// opening every opening in turn. Informational only: nothing here moves a
// candidate or changes a status. Each row opens the candidate's drawer — where
// a person acts on any stage, not only the final decision — and the whole list
// is one click away on the Stages at risk page.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useStagesAtRisk } from '@/hooks/useHrHomeQueries';
import { SectionCard } from '@/design/components/page';
import { Skeleton } from '@/components/ui/skeleton';
import { CheckCircle2, Clock } from '@/design/components/icons';
import type { SlaBoardRow } from '@/api/stageSla';
import AtRiskRow from '@/components/AtRiskRow';
import CandidateDrawer from '@/components/CandidateDrawer';
import { SLA_SCOPE_NOTE } from '@/components/stageSlaCopy';

const VISIBLE = 6;

export default function StagesAtRiskWidget({
  className,
  bare = false,
}: {
  className?: string;
  /** Only the list: no card, title or count badge, and each row without its own
   *  box, for a page that supplies the frame (the HR home). */
  bare?: boolean;
}): JSX.Element {
  const [open, setOpen] = useState<SlaBoardRow | null>(null);
  const board = useStagesAtRisk();

  const rows = board.data ?? [];

  const body = (
    <>
      {board.isLoading ? <Skeleton className="h-[58px] rounded-[12px]" /> : null}

      {board.isError ? (
        <p role="alert" className="text-[13px] text-muted-foreground">
          Could not check stage SLAs just now.
        </p>
      ) : null}

      {!board.isLoading && !board.isError && rows.length === 0 ? (
        <>
          <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
            <CheckCircle2 className="h-4 w-4 text-[var(--ui-ok)]" aria-hidden="true" />
            Nothing overdue or due soon.
          </p>
          <p className="mt-1 text-[12px] leading-snug text-muted-foreground">{SLA_SCOPE_NOTE}</p>
        </>
      ) : null}

      {rows.length > 0 ? (
        <ul
          className={
            bare
              ? 'flex flex-col divide-y divide-border [&_button]:rounded-none [&_button]:border-0 [&_button]:bg-transparent [&_button]:px-0 [&_button]:py-3'
              : 'flex flex-col gap-2'
          }
        >
          {rows.slice(0, VISIBLE).map((r) => (
            <li key={r.enrolment_id}>
              <AtRiskRow row={r} onOpen={setOpen} />
            </li>
          ))}
        </ul>
      ) : null}

      {rows.length > 0 ? (
        <Link
          to="/hr/stages-at-risk"
          className="mt-3 inline-flex min-h-10 items-center text-[13px] font-semibold text-[var(--ui-info)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          {rows.length > VISIBLE ? `View all ${rows.length}` : 'View the full list'}
        </Link>
      ) : null}

      <CandidateDrawer
        applicantId={open?.applicant_id ?? null}
        enrolmentId={open?.enrolment_id ?? null}
        onClose={() => setOpen(null)}
      />
    </>
  );

  if (bare) return <div className={className}>{body}</div>;

  return (
    <SectionCard
      title="Stages at risk"
      icon={Clock}
      className={className}
      action={
        rows.length > 0 ? (
          <span className="rounded-full bg-[var(--ui-inset-strong)] px-2.5 py-1 text-[12px] font-semibold text-[var(--ui-soft)]">
            {rows.length}
          </span>
        ) : null
      }
    >
      {body}
    </SectionCard>
  );
}
