// GroupComparisonTable — a row per source or opening, one column per
// governed metric present for the selected cohort.

import {
  orderedMetricNames,
  findDefinition,
  metricLabel,
  type CohortBasis,
  type FunnelGroup,
  type GroupByOption,
  type MetricDefinitionsResponse,
  type MetricPart,
} from '@/api/metrics';
import MetricCell from './MetricCell';
import type { FilterState } from './types';

export default function GroupComparisonTable({
  groups,
  definitions,
  cohort,
  groupBy,
  filters,
  onInfo,
  openDrillDown,
}: {
  groups: FunnelGroup[];
  definitions: MetricDefinitionsResponse | undefined;
  cohort: CohortBasis;
  groupBy: GroupByOption;
  filters: FilterState;
  onInfo: (name: string) => void;
  openDrillDown: (
    metric: string,
    part: MetricPart,
    cohort: CohortBasis,
    requisitionId?: string,
    source?: string,
  ) => void;
}): JSX.Element | null {
  if (groups.length === 0) return null;
  const names = orderedMetricNames(
    Array.from(new Set(groups.flatMap((g) => Object.keys(g.metrics)))),
    cohort,
  );

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] border-collapse text-left text-[12.5px]">
        <thead>
          <tr className="border-b border-border">
            <th className="py-2 pr-3 font-medium text-[var(--ui-faint)]">
              {groupBy === 'source' ? 'Source' : 'Opening'}
            </th>
            <th className="py-2 pr-3 font-medium text-[var(--ui-faint)]">In progress</th>
            {names.map((name) => {
              // A column header names one metric shared across every row —
              // no single group's result to pin a version against, so this
              // reads the definition presently in force.
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
          {groups.map((group) => {
            const groupReq =
              groupBy === 'requisition'
                ? (group.key ?? undefined)
                : filters.requisitionId || undefined;
            const groupSrc =
              groupBy === 'source' ? (group.key ?? undefined) : filters.source || undefined;
            return (
              <tr key={group.key ?? 'all'} className="border-b border-border last:border-0">
                <td className="py-2 pr-3 font-medium text-foreground">{group.label}</td>
                <td className="py-2 pr-3 text-muted-foreground">{group.in_progress}</td>
                {names.map((name) => {
                  const result = group.metrics[name];
                  const def = findDefinition(definitions, name, result?.version);
                  return (
                    <td key={name} className="py-2 pr-3">
                      <MetricCell
                        name={name}
                        result={result}
                        definition={def}
                        onInfo={onInfo}
                        onDrillDown={(m, part) =>
                          openDrillDown(m, part, cohort, groupReq, groupSrc)
                        }
                      />
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
