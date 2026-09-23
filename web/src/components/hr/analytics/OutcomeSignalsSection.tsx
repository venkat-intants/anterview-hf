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
import { GlassCard, SegTabs } from '@/design/components/primitives';
import { Info } from '@/design/components/icons';
import MetricCell from './MetricCell';
import MetricInfoDialog from './MetricInfoDialog';
import MembersDrillDown from './MembersDrillDown';

const COHORT_TABS = [
  { key: 'hire', label: 'Hire date' },
  { key: 'decision', label: 'Decision date' },
] as const;

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
  const bandGroups = (signals.data?.groups ?? []).filter((g) => g.key !== null);

  return (
    <GlassCard className="border-[rgba(var(--accent-rgb),0.22)] p-5" data-testid="outcome-signals">
      <h2 className="text-[15px] font-semibold text-foreground">
        Interview scores and later outcomes (signals)
      </h2>
      <div className="mt-3 flex items-start gap-2.5 rounded-[12px] border border-[rgba(var(--accent-rgb),0.3)] bg-[rgba(var(--accent-rgb),0.06)] p-3.5">
        <Info size={15} className="mt-0.5 shrink-0 text-[var(--ui-info)]" aria-hidden="true" />
        <p className="text-[12.5px] leading-relaxed text-[var(--ui-soft)]">
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
        {signals.isLoading ? (
          <p className="text-[13px] text-muted-foreground">Loading…</p>
        ) : signals.isError ? (
          <p className="text-[13px] text-[var(--ui-danger)]">
            Could not load outcome signals just now.
          </p>
        ) : bandGroups.length === 0 ? (
          <p className="text-[13px] text-muted-foreground">No data for this period.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] border-collapse text-left text-[12.5px]">
              <thead>
                <tr className="border-b border-border">
                  <th className="py-2 pr-3 font-medium text-[var(--ui-faint)]">
                    Interviewer score
                  </th>
                  {names.map((name) => {
                    const def = findDefinition(definitions, name);
                    return (
                      <th key={name} className="py-2 pr-3 font-medium text-[var(--ui-faint)]">
                        {metricLabel(name, def)}
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {bandGroups.map((g) => (
                  <tr key={g.key} className="border-b border-border last:border-0">
                    <td className="py-2 pr-3 font-medium text-foreground">{g.label}</td>
                    {names.map((name) => {
                      const result = g.metrics[name];
                      const def = findDefinition(definitions, name, result?.version);
                      return (
                        <td key={name} className="py-2 pr-3">
                          <MetricCell
                            name={name}
                            result={result}
                            definition={def}
                            onInfo={setInfoMetric}
                            onDrillDown={(m, part) =>
                              setDrillDown({ metric: m, part, cohort, scoreBand: g.key })
                            }
                          />
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
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
    </GlassCard>
  );
}
