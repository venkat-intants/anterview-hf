// StagesAtRiskWidget — PH4-O1. A compact list of applications overdue or due
// soon against their stage SLA, so a manager sees who to chase without
// opening every opening in turn. Informational only: nothing here moves a
// candidate or changes a status. Each row opens the candidate's drawer — where
// a person acts on any stage, not only the final decision — and the whole list
// is one click away on the Stages at risk page.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { GlassCard } from '@/design/components/primitives';
import { CheckCircle2 } from '@/design/components/icons';
import { getSlaBoard, type SlaBoardRow } from '@/api/stageSla';
import AtRiskRow from '@/components/AtRiskRow';
import CandidateDrawer from '@/components/CandidateDrawer';

const VISIBLE = 6;

export default function StagesAtRiskWidget(): JSX.Element {
  const [open, setOpen] = useState<SlaBoardRow | null>(null);
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
        <p className="text-[13px] text-muted-foreground">Could not check stage SLAs just now.</p>
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
              <AtRiskRow row={r} onOpen={setOpen} />
            </li>
          ))}
        </ul>
      ) : null}

      {rows.length > 0 ? (
        <Link
          to="/hr/stages-at-risk"
          className="mt-3 inline-block text-[12px] text-[var(--ui-info)] hover:underline"
        >
          {rows.length > VISIBLE ? `View all ${rows.length}` : 'View the full list'}
        </Link>
      ) : null}

      <CandidateDrawer
        applicantId={open?.applicant_id ?? null}
        enrolmentId={open?.enrolment_id ?? null}
        onClose={() => setOpen(null)}
      />
    </GlassCard>
  );
}
