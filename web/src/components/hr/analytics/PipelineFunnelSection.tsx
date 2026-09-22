// PipelineFunnelSection — Applied → Screened → Assessment → Interview →
// Selected → Hired, each step a governed count; rates carry their own
// num/denom.

import {
  PIPELINE_COUNT_METRICS,
  PIPELINE_RATE_METRICS,
  metricLabel,
  type CountMetricResult,
  type FunnelGroup,
  type MetricDefinitionsResponse,
  type MetricPart,
} from '@/api/metrics';
import MetricCell from './MetricCell';

export default function PipelineFunnelSection({
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
  if (!group) {
    return <p className="text-[13px] text-muted-foreground">No applications in this period.</p>;
  }

  const stepNames = PIPELINE_COUNT_METRICS.filter((m) => m !== 'rejections' && group.metrics[m]);
  if (stepNames.length === 0) {
    return (
      <p className="text-[13px] text-muted-foreground">
        Pipeline counts use the application or decision cohort.
      </p>
    );
  }

  const applications = group.metrics.applications as CountMetricResult | undefined;
  if ((applications?.value ?? 0) === 0) {
    return <p className="text-[13px] text-muted-foreground">No applications in this period.</p>;
  }

  const max = Math.max(1, ...stepNames.map((m) => (group.metrics[m] as CountMetricResult).value));
  const rejections = group.metrics.rejections as CountMetricResult | undefined;
  const rateNames = PIPELINE_RATE_METRICS.filter((m) => group.metrics[m]);

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[12px] text-muted-foreground">
        {group.in_progress} still in progress
        {rejections ? ` · ${rejections.value.toLocaleString('en-IN')} rejected` : ''}
      </p>

      <div className="flex flex-col gap-2.5">
        {stepNames.map((name) => {
          const def = definitions?.metrics.find((m) => m.name === name);
          const result = group.metrics[name] as CountMetricResult;
          const pct = Math.round((result.value / max) * 100);
          return (
            <div key={name} className="flex items-center gap-3.5">
              <div className="w-[104px] shrink-0 text-[13px] text-[var(--ui-soft)]">
                {metricLabel(name, def)}
              </div>
              <div className="h-7 flex-1 overflow-hidden rounded-[8px] bg-[var(--ui-inset)]">
                {pct > 0 ? (
                  <div
                    className="h-full rounded-[8px] bg-[linear-gradient(90deg,var(--accent),#a887dc)]"
                    style={{ width: `${pct}%` }}
                  />
                ) : null}
              </div>
              <div className="w-[132px] shrink-0">
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

      {rateNames.length > 0 ? (
        <div className="flex flex-wrap gap-x-6 gap-y-2 border-t border-border pt-3">
          {rateNames.map((name) => {
            const def = definitions?.metrics.find((m) => m.name === name);
            return (
              <div key={name} className="flex items-center gap-2 text-[12.5px]">
                <span className="text-[var(--ui-faint)]">{metricLabel(name, def)}:</span>
                <MetricCell
                  name={name}
                  result={group.metrics[name]}
                  definition={def}
                  onInfo={onInfo}
                  onDrillDown={onDrillDown}
                />
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
