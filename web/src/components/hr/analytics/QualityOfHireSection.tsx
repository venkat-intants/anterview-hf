// QualityOfHireSection — hire-cohort metrics, always visually distinct (C1-7).

import {
  HIRE_COHORT_METRICS,
  findDefinition,
  metricLabel,
  type CountMetricResult,
  type FunnelGroup,
  type MetricDefinitionsResponse,
  type MetricPart,
} from '@/api/metrics';
import MetricCell from './MetricCell';

export default function QualityOfHireSection({
  group,
  definitions,
  onInfo,
  onDrillDown,
}: {
  group: FunnelGroup | undefined;
  definitions: MetricDefinitionsResponse | undefined;
  onInfo: (name: string) => void;
  onDrillDown: (name: string, part: MetricPart) => void;
}): JSX.Element {
  const hires = group?.metrics.hires as CountMetricResult | undefined;
  if (!group || !hires || hires.value === 0) {
    return <p className="text-[13px] text-muted-foreground">No hires in this period.</p>;
  }
  const names = HIRE_COHORT_METRICS.filter((m) => group.metrics[m]);
  return (
    <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 lg:grid-cols-3">
      {names.map((name) => {
        const result = group.metrics[name];
        const def = findDefinition(definitions, name, result?.version);
        return (
          <div key={name} className="rounded-[12px] border border-border p-3.5">
            <p className="text-[11.5px] font-medium uppercase tracking-wide text-[var(--ui-faint)]">
              {metricLabel(name, def)}
            </p>
            <div className="mt-1.5">
              <MetricCell
                name={name}
                result={result}
                definition={def}
                onInfo={onInfo}
                onDrillDown={onDrillDown}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
