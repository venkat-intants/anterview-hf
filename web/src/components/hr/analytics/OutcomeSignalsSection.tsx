// OutcomeSignalsSection — PH5-E4. "Interview scores and later outcomes
// (signals)": rows are bands on the mean CURRENT human interviewer scorecard
// score (never an AI interview score, which is purged 90 days after the
// session); columns are the governed hire-cohort metrics (or, for the
// decision cohort, the share hired). A SIGNAL — nothing here changes a
// candidate's status or score, and every cell reuses Wave 1's MetricCell so
// the number is rendered exactly as the server sent it, suppression included.

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { isTransientApiError } from '@/api/client';
import {
  OUTCOME_DECISION_METRICS,
  OUTCOME_HIRE_METRICS,
  findDefinition,
  getOutcomeSignals,
  metricLabel,
  type MetricDefinitionsResponse,
  type MetricPart,
  type OutcomeCohort,
} from '@/api/metrics';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { QueryState } from '@/components/QueryState';
import { Skeleton } from '@/components/ui/skeleton';
import { SegTabs } from '@/design/components/primitives';
import { SectionCard } from '@/design/components/page';
import { Info } from '@/design/components/icons';
import MetricCell from './MetricCell';
import MetricInfoDialog from './MetricInfoDialog';
import MembersDrillDown from './MembersDrillDown';

const COHORT_TABS = [
  { key: 'hire', label: 'Hire date' },
  { key: 'decision', label: 'Decision date' },
] as const;

/** The compact analytics density: this table sits inside a card, not a page. */
const HEAD = 'h-auto px-0 py-2 pr-3 font-semibold text-muted-foreground';
const CELL = 'p-0 py-2 pr-3';

interface DrillTarget {
  metric: string;
  part: MetricPart;
  cohort: OutcomeCohort;
  scoreBand: string | null;
}

export default function OutcomeSignalsSection({
  filters,
  definitions,
  onOpenCandidate,
}: {
  filters: { from?: string; to?: string; requisition_id?: string; source?: string };
  definitions: MetricDefinitionsResponse | undefined;
  onOpenCandidate: (row: { applicant_id: string; enrolment_id: string }) => void;
}): JSX.Element {
  const [cohort, setCohort] = useState<OutcomeCohort>('hire');
  const [infoMetric, setInfoMetric] = useState<string | null>(null);
  const [drillDown, setDrillDown] = useState<DrillTarget | null>(null);

  const signals = useQuery({
    queryKey: [
      'hr',
      'analytics',
      'outcome-signals',
      cohort,
      filters.from,
      filters.to,
      filters.requisition_id,
      filters.source,
    ],
    queryFn: () => getOutcomeSignals({ cohort, ...filters }),
    retry: (count, error) => isTransientApiError(error) && count < 2,
  });

  const names = cohort === 'hire' ? OUTCOME_HIRE_METRICS : OUTCOME_DECISION_METRICS;
  // Rows are the score bands (below_3, 3_to_4, 4_plus, none) — the overall
  // "All" group this endpoint also returns is not shown as a row here; a
  // band table with an "All" row would double-count every hire.
  const bandGroupsOf = (data: NonNullable<typeof signals.data>) =>
    data.groups.filter((g) => g.key !== null);

  return (
    <SectionCard
      title="Interview scores and later outcomes (signals)"
      data-testid="outcome-signals"
    >
      <div className="flex items-start gap-2.5 rounded-[12px] border border-[var(--ui-info)]/30 bg-[var(--ui-info-wash)] p-3.5">
        <Info size={15} className="mt-0.5 shrink-0 text-[var(--ui-info)]" aria-hidden="true" />
        <p className="text-[13px] leading-relaxed text-foreground">
          Signals about past hires. They never change any candidate&apos;s status or scores. Only
          human interviewer scores are used; AI interview scores are deleted after 90 days and are
          never compared with outcomes.
        </p>
      </div>

      <div className="mt-3">
        <SegTabs
          tabs={[...COHORT_TABS]}
          active={cohort}
          onChange={(k) => setCohort(k as OutcomeCohort)}
        />
      </div>

      <div className="mt-4">
        <QueryState
          query={signals}
          errorTitle="Could not load outcome signals."
          loading={
            <div className="flex flex-col gap-2" aria-busy="true">
              <span className="sr-only">Loading…</span>
              <Skeleton className="h-9" />
              <Skeleton className="h-9" />
              <Skeleton className="h-9" />
            </div>
          }
          isEmpty={(data) => bandGroupsOf(data).length === 0}
          empty={<p className="text-[13px] text-muted-foreground">No data for this period.</p>}
        >
          {(data) => (
            <Table className="min-w-[640px] border-collapse text-left text-[13px]">
              <TableHeader className="sticky top-0 z-10 bg-card">
                <TableRow className="border-b border-border hover:bg-transparent">
                  <TableHead className={HEAD}>Interviewer score</TableHead>
                  {names.map((name) => {
                    const def = findDefinition(definitions, name);
                    return (
                      <TableHead key={name} className={HEAD}>
                        {metricLabel(name, def)}
                      </TableHead>
                    );
                  })}
                </TableRow>
              </TableHeader>
              <TableBody>
                {bandGroupsOf(data).map((g) => (
                  <TableRow
                    key={g.key}
                    className="border-b border-border last:border-0 hover:bg-[var(--ui-inset-soft)]"
                  >
                    <TableCell className={`${CELL} font-medium text-foreground`}>
                      {g.label}
                    </TableCell>
                    {names.map((name) => {
                      const result = g.metrics[name];
                      const def = findDefinition(definitions, name, result?.version);
                      return (
                        <TableCell key={name} className={CELL}>
                          <MetricCell
                            name={name}
                            result={result}
                            definition={def}
                            onInfo={setInfoMetric}
                            onDrillDown={(m, part) =>
                              setDrillDown({ metric: m, part, cohort, scoreBand: g.key })
                            }
                          />
                        </TableCell>
                      );
                    })}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </QueryState>
      </div>

      {infoMetric ? (
        <MetricInfoDialog
          metricName={infoMetric}
          definitions={definitions}
          onClose={() => setInfoMetric(null)}
        />
      ) : null}

      {drillDown ? (
        <MembersDrillDown
          metricName={drillDown.metric}
          part={drillDown.part}
          definitions={definitions}
          query={{
            cohort: drillDown.cohort,
            from: filters.from,
            to: filters.to,
            requisition_id: filters.requisition_id,
            source: filters.source,
            score_band: drillDown.scoreBand ?? undefined,
          }}
          onClose={() => setDrillDown(null)}
          onOpenCandidate={onOpenCandidate}
        />
      ) : null}
    </SectionCard>
  );
}
