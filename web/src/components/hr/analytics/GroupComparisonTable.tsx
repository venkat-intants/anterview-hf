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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import MetricCell from './MetricCell';
import type { FilterState } from './types';

/** The compact analytics density: these tables sit inside a card, not a page. */
const HEAD = 'h-auto px-0 py-2 pr-3 font-semibold text-muted-foreground';
const CELL = 'p-0 py-2 pr-3';

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
    <Table className="min-w-[720px] border-collapse text-left text-[13px]">
      <TableHeader className="sticky top-0 z-10 bg-card">
        <TableRow className="border-b border-border hover:bg-transparent">
          <TableHead className={HEAD}>{groupBy === 'source' ? 'Source' : 'Opening'}</TableHead>
          <TableHead className={HEAD}>In progress</TableHead>
          {names.map((name) => {
            // A column header names one metric shared across every row —
            // no single group's result to pin a version against, so this
            // reads the definition presently in force.
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
        {groups.map((group) => {
          const groupReq =
            groupBy === 'requisition'
              ? (group.key ?? undefined)
              : filters.requisitionId || undefined;
          const groupSrc =
            groupBy === 'source' ? (group.key ?? undefined) : filters.source || undefined;
          return (
            <TableRow
              key={group.key ?? 'all'}
              className="border-b border-border last:border-0 hover:bg-[var(--ui-inset-soft)]"
            >
              <TableCell className={`${CELL} font-medium text-foreground`}>{group.label}</TableCell>
              <TableCell className={`${CELL} text-muted-foreground`}>{group.in_progress}</TableCell>
              {names.map((name) => {
                const result = group.metrics[name];
                const def = findDefinition(definitions, name, result?.version);
                return (
                  <TableCell key={name} className={CELL}>
                    <MetricCell
                      name={name}
                      result={result}
                      definition={def}
                      onInfo={onInfo}
                      onDrillDown={(m, part) => openDrillDown(m, part, cohort, groupReq, groupSrc)}
                    />
                  </TableCell>
                );
              })}
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
