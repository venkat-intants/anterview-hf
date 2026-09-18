// StagesAtRisk (/hr/stages-at-risk) — PH4-O1. Every application against its
// stage SLA, worst first: the full list behind the HR-home widget, which shows
// six. Any stage, not only the final decision — an application can be overdue
// on an exam or an AI interview too. Each row opens the candidate's drawer.
// Informational only: nothing here moves a candidate or changes a status.
// English-only by design (CLAUDE.md — the HR console is not translated).

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { GlassCard, SegTabs } from '@/design/components/primitives';
import { CheckCircle2 } from '@/design/components/icons';
import { getSlaBoard, type SlaBoardRow, type SlaState } from '@/api/stageSla';
import AtRiskRow from '@/components/AtRiskRow';
import CandidateDrawer from '@/components/CandidateDrawer';

const FILTERS = [
  { key: 'at_risk', label: 'Overdue and due soon' },
  { key: 'overdue', label: 'Overdue' },
  { key: 'all', label: 'Every stage with an SLA' },
] as const;

type FilterKey = (typeof FILTERS)[number]['key'];

const STATES: Record<FilterKey, SlaState[]> = {
  at_risk: ['overdue', 'due_soon'],
  overdue: ['overdue'],
  all: ['overdue', 'due_soon', 'on_track'],
};

const EMPTY: Record<FilterKey, string> = {
  at_risk: 'Nothing overdue or due soon.',
  overdue: 'Nothing overdue.',
  all: 'No application is in a stage with an SLA.',
};

export default function StagesAtRisk(): JSX.Element {
  const [filter, setFilter] = useState<FilterKey>('at_risk');
  const [open, setOpen] = useState<SlaBoardRow | null>(null);
  const board = useQuery({
    queryKey: ['hr', 'stage-sla', 'page', filter],
    queryFn: () => getSlaBoard({ state: STATES[filter] }),
    staleTime: 60_000,
  });
  const rows = board.data ?? [];

  return (
    <div className="mx-auto max-w-[1120px] px-6 py-8 lg:px-8">
      <header>
        <h1 className="text-[28px] font-semibold tracking-[-1px] text-foreground">Stages at risk</h1>
        <p className="mt-1 text-[14px] text-muted-foreground">
          Applications against the SLA their stage owner answers for, worst first. Open one to
          act on it — nothing on this page moves a candidate.
        </p>
      </header>

      <div className="mt-5">
        <SegTabs tabs={[...FILTERS]} active={filter} onChange={(k) => setFilter(k as FilterKey)} />
      </div>

      <GlassCard className="mt-5 p-5">
        {board.isLoading ? (
          <p className="text-[13px] text-muted-foreground">Loading…</p>
        ) : board.isError ? (
          <p className="text-[13px] text-[var(--ui-danger)]">Could not check stage SLAs just now.</p>
        ) : rows.length === 0 ? (
          <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
            <CheckCircle2 className="h-4 w-4 text-[var(--ui-ok)]" aria-hidden="true" />
            {EMPTY[filter]}
          </p>
        ) : (
          <>
            <p className="mb-3 text-[12px] text-muted-foreground">
              {rows.length} application{rows.length === 1 ? '' : 's'}
            </p>
            <ul className="flex flex-col gap-2">
              {rows.map((r) => (
                <li key={r.enrolment_id}>
                  <AtRiskRow row={r} onOpen={setOpen} detailed />
                </li>
              ))}
            </ul>
          </>
        )}
      </GlassCard>

      <CandidateDrawer
        applicantId={open?.applicant_id ?? null}
        enrolmentId={open?.enrolment_id ?? null}
        onClose={() => setOpen(null)}
      />
    </div>
  );
}
