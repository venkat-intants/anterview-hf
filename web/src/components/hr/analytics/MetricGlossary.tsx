// MetricGlossary — every definition, reachable regardless of role.

import { GlassCard } from '@/design/components/primitives';
import { metricLabel, type MetricDefinitionsResponse } from '@/api/metrics';
import type { QueryLike } from './types';

export default function MetricGlossary({
  definitionsQuery,
  onInfo,
}: {
  definitionsQuery: QueryLike<MetricDefinitionsResponse>;
  onInfo: (name: string) => void;
}): JSX.Element {
  // Every metric name, current version only — a superseded entry (kept in
  // the response so an old drill-down or audit trail can still resolve its
  // exact version) would otherwise draw a second row for the same metric,
  // and it is never the one worth reading here.
  const current = definitionsQuery.data?.metrics.filter((m) => m.current) ?? [];

  return (
    <GlassCard className="p-5" data-testid="metric-glossary">
      <h2 className="text-[15px] font-semibold text-foreground">Metric glossary</h2>
      {definitionsQuery.isLoading ? (
        <p className="mt-3 text-[13px] text-muted-foreground">Loading…</p>
      ) : definitionsQuery.isError ? (
        <p className="mt-3 text-[13px] text-[var(--ui-danger)]">
          Could not load metric definitions just now.
        </p>
      ) : definitionsQuery.data ? (
        <>
          <p className="mt-1 text-[11px] text-[var(--ui-faint)]">
            registry {definitionsQuery.data.registry_hash.slice(0, 10)}…
          </p>
          <ul className="mt-3 flex flex-col gap-2">
            {current.map((m) => (
              <li
                key={`${m.name}@${m.version}`}
                className="flex items-center justify-between gap-3 rounded-[10px] border border-border p-3"
              >
                <div className="min-w-0">
                  <p className="truncate text-[13px] font-medium text-foreground">
                    {metricLabel(m.name, m)}
                  </p>
                  <p className="mt-0.5 truncate text-[11.5px] text-muted-foreground">
                    {m.description}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => onInfo(m.name)}
                  className="shrink-0 text-[12px] text-[var(--ui-info)] hover:underline focus:outline-none focus-visible:underline"
                >
                  How is this calculated?
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </GlassCard>
  );
}
