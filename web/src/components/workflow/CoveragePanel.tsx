// CoveragePanel — D3. Does this process actually measure the job?
//
// This is the one check in the builder that catches a badly designed hiring
// process before a candidate meets it, and it is the reason the builder is
// worth having at all: a workflow can be perfectly valid — rounds in order,
// thresholds set, questions attached — and still never assess the thing the
// role is mostly about. Nothing else in the product would ever notice.
//
// Errors and warnings are kept visually distinct because they mean different
// things and the difference is load-bearing. An ERROR blocks publish and is the
// server's judgement, not this panel's. A WARNING is advisory and publish
// proceeds: "no round assesses Communication" may well be deliberate, so the
// builder says it once and gets out of the way rather than demanding a fix.

import { AlertTriangle, CheckCircle2, Info, Loader2 } from '@/design/components/icons';
import { cn } from '@/lib/utils';
import type { CoverageRow, ValidationReport } from '@/api/workflows';

interface Props {
  report: ValidationReport | undefined;
  loading: boolean;
}

/** 0 is a gap, 1–2 is healthy, 3+ is probably an accident. Mirrors build_coverage. */
function coverageTone(times: number): { cls: string; label: string } {
  if (times === 0) return { cls: 'text-[#ffb764]', label: 'not assessed' };
  if (times > 2) return { cls: 'text-[#ffb764]', label: `${times} rounds` };
  return { cls: 'text-[#27c93f]', label: times === 1 ? '1 round' : `${times} rounds` };
}

function CoverageBar({ row }: { row: CoverageRow }) {
  const tone = coverageTone(row.times_assessed);
  return (
    <li className="flex items-center gap-3 py-1.5">
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[12.5px] text-[#d5d7da]">
          {row.competency_name}
        </span>
        {row.assessed_in.length > 0 ? (
          <span className="block truncate text-[11px] text-[#70757c]">
            {row.assessed_in.join(', ')}
          </span>
        ) : null}
      </span>
      {/* Role weight, so a gap in something that barely matters reads
          differently from a gap in the main thing the job is. */}
      <span
        className="h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-white/[0.08]"
        title={`Weight in this role: ${Math.round(row.profile_weight * 100)}%`}
      >
        <span
          className="block h-full rounded-full bg-white/35"
          style={{ width: `${Math.min(100, Math.round(row.profile_weight * 100))}%` }}
        />
      </span>
      <span className={cn('w-[74px] shrink-0 text-right text-[11.5px]', tone.cls)}>
        {tone.label}
      </span>
    </li>
  );
}

export default function CoveragePanel({ report, loading }: Props): JSX.Element {
  if (loading && !report) {
    return (
      <div className="flex items-center gap-2 text-[12.5px] text-[#888b91]">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        Checking…
      </div>
    );
  }
  if (!report) {
    return (
      <p className="text-[12.5px] text-[#888b91]">
        Add a round to see what this process measures.
      </p>
    );
  }

  const gaps = report.coverage.filter((c) => c.times_assessed === 0).length;

  return (
    <div className="flex flex-col gap-4">
      <div
        className={cn(
          'flex items-start gap-2 rounded-[12px] border p-3 text-[12.5px] leading-relaxed',
          report.publishable
            ? 'border-[#27c93f]/25 bg-[#27c93f]/[0.06] text-[#d5d7da]'
            : 'border-[#e6714f]/30 bg-[#e6714f]/[0.07] text-[#d5d7da]',
        )}
      >
        {report.publishable ? (
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-[#27c93f]" aria-hidden="true" />
        ) : (
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[#e6714f]" aria-hidden="true" />
        )}
        <span>
          {report.publishable
            ? 'Ready to publish.'
            : `${report.errors.length} thing${report.errors.length === 1 ? '' : 's'} to fix before this can go live.`}
        </span>
      </div>

      {report.errors.length > 0 ? (
        <ul className="flex list-none flex-col gap-1.5">
          {report.errors.map((e) => (
            <li key={e} className="flex items-start gap-1.5 text-[12px] leading-relaxed text-[#e6714f]">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              {e}
            </li>
          ))}
        </ul>
      ) : null}

      {report.warnings.length > 0 ? (
        <ul className="flex list-none flex-col gap-1.5">
          {report.warnings.map((w) => (
            <li key={w} className="flex items-start gap-1.5 text-[12px] leading-relaxed text-[#ffb764]">
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              {w}
            </li>
          ))}
        </ul>
      ) : null}

      {report.coverage.length > 0 ? (
        <div>
          <div className="mb-1 flex items-baseline justify-between">
            <h3 className="text-[12.5px] font-medium text-white">What this process measures</h3>
            {gaps > 0 ? (
              <span className="text-[11.5px] text-[#ffb764]">{gaps} not covered</span>
            ) : (
              <span className="text-[11.5px] text-[#27c93f]">all covered</span>
            )}
          </div>
          <ul className="flex list-none flex-col divide-y divide-white/[0.05]">
            {report.coverage.map((row) => (
              <CoverageBar key={row.competency_id} row={row} />
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
