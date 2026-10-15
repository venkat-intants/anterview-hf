// MetricGlossary — every definition, reachable regardless of role.

import { SectionCard } from '@/design/components/page';
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
    <SectionCard title="Metric glossary" data-testid="metric-glossary">
      {definitionsQuery.isLoading ? (
        <p className="text-[13px] text-muted-foreground">Loading…</p>
      ) : definitionsQuery.isError ? (
        <p role="alert" className="text-[13px] text-[var(--ui-danger)]">
          Could not load metric definitions just now.
        </p>
      ) : definitionsQuery.data ? (
        <>
          <p className="text-[12px] text-muted-foreground">
            registry {definitionsQuery.data.registry_hash.slice(0, 10)}…
          </p>
          <ul className="mt-3 flex flex-col gap-2">
            {current.map((m) => (
              <li
                key={`${m.name}@${m.version}`}
                className="flex flex-wrap items-center justify-between gap-3 rounded-[12px] border border-border p-3"
              >
                <div className="min-w-0">
                  <p className="truncate text-[14px] font-semibold text-foreground">
                    {metricLabel(m.name, m)}
                  </p>
                  <p className="mt-0.5 truncate text-[13px] text-muted-foreground">
                    {m.description}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => onInfo(m.name)}
                  className="min-h-10 shrink-0 rounded-full px-2 text-[13px] font-semibold text-[var(--ui-info)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                >
                  How is this calculated?
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </SectionCard>
  );
}
