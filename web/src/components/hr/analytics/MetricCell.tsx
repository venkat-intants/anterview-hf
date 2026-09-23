// MetricCell — one governed value, rendered exactly as the server sent it.
// Count: the value, clickable (opens the members drill-down). Rate: a
// percentage plus its OWN numerator/denominator, each independently
// clickable — never derived from a neighbouring step. Median/mean/
// distribution: the value plus its sample size; no drill-down (the contract
// defines one only for a count or a rate's numerator/denominator).

import { Info } from '@/design/components/icons';
import {
  isDrillable,
  metricLabel,
  type MetricDefinition,
  type MetricPart,
  type MetricResult,
} from '@/api/metrics';

export default function MetricCell({
  name,
  result,
  definition,
  onInfo,
  onDrillDown,
}: {
  name: string;
  result: MetricResult | undefined;
  definition: MetricDefinition | undefined;
  onInfo: (name: string) => void;
  onDrillDown: (name: string, part: MetricPart) => void;
}): JSX.Element {
  const infoButton = definition ? (
    <button
      type="button"
      onClick={() => onInfo(name)}
      aria-label={`How is ${metricLabel(name, definition)} calculated?`}
      className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[var(--ui-faint)] hover:text-[var(--ui-info)] focus:outline-none focus-visible:text-[var(--ui-info)]"
    >
      <Info size={12} aria-hidden="true" />
    </button>
  ) : null;

  if (!result) {
    return <span className="text-[12.5px] text-[var(--ui-faint)]">—</span>;
  }

  if (result.kind === 'count') {
    const clickable = isDrillable(definition, 'numerator');
    const value = result.value.toLocaleString('en-IN');
    return (
      <span className="inline-flex items-center gap-1.5">
        {clickable ? (
          <button
            type="button"
            onClick={() => onDrillDown(name, 'numerator')}
            className="text-[15px] font-semibold text-foreground underline decoration-dotted underline-offset-2 hover:text-[var(--ui-info)] focus:outline-none focus-visible:text-[var(--ui-info)]"
          >
            {value}
          </button>
        ) : (
          <span className="text-[15px] font-semibold text-foreground">{value}</span>
        )}
        {infoButton}
      </span>
    );
  }

  if (result.kind === 'rate') {
    // Check-in outcomes are aggregate-only (retention_90d's numerator — who
    // "retained" — is never a named list); checkin_coverage stays fully
    // clickable. Read straight off the definition's server-authoritative
    // `drillable` flag — never re-derived from the metric's name.
    const numeratorClickable = result.numerator !== null && isDrillable(definition, 'numerator');
    const denominatorClickable = isDrillable(definition, 'denominator');
    // A suppressed PIPELINE (non-check-in) rate now comes back WITH a real
    // value/numerator — "too few to compare" is always driven by `suppressed`
    // alone, never by `value === null`, which is a check-in-only signal.
    // Surfacing the withheld figure is optional and only for a non-null
    // value; a null one (a suppressed check-in metric) is never shown
    // anywhere, tooltip included.
    const suppressedTooltip =
      result.suppressed && result.value !== null
        ? `${result.value.toFixed(1)}% (${result.numerator ?? '—'} of ${result.denominator}), too few to compare reliably`
        : undefined;
    return (
      <span
        className="inline-flex flex-wrap items-center gap-1 text-[12.5px]"
        title={suppressedTooltip}
      >
        {result.suppressed ? (
          <span className="text-[var(--ui-faint)]">too few to compare</span>
        ) : (
          <span className="font-semibold text-foreground">
            {result.value === null ? '—' : `${result.value.toFixed(1)}%`}
          </span>
        )}
        <span className="text-[var(--ui-faint)]">(</span>
        {result.numerator === null ? (
          <span className="text-[var(--ui-faint)]">—</span>
        ) : numeratorClickable ? (
          <button
            type="button"
            onClick={() => onDrillDown(name, 'numerator')}
            className="text-[var(--ui-info)] underline decoration-dotted underline-offset-2 hover:opacity-80"
          >
            {result.numerator}
          </button>
        ) : (
          <span className="text-foreground">{result.numerator}</span>
        )}
        <span className="text-[var(--ui-faint)]">of</span>
        {denominatorClickable ? (
          <button
            type="button"
            onClick={() => onDrillDown(name, 'denominator')}
            className="text-[var(--ui-info)] underline decoration-dotted underline-offset-2 hover:opacity-80"
          >
            {result.denominator}
          </button>
        ) : (
          <span className="text-foreground">{result.denominator}</span>
        )}
        <span className="text-[var(--ui-faint)]">)</span>
        {result.suppressed ? (
          <span className="text-[11px] text-[var(--ui-faint)]" title={`n=${result.denominator}`}>
            n={result.denominator}
          </span>
        ) : null}
        {infoButton}
      </span>
    );
  }

  // Distribution — never clickable (no numerator/denominator, and
  // performance_90d's buckets are aggregate-only by contract; isDrillable
  // returns false for every non-rate, non-count kind). `value` is null
  // exactly when suppressed. Checked ahead of median/mean (a single-literal
  // discriminant narrows cleanly; 'median' | 'mean' sharing one interface
  // does not, for TS's purposes) so the final branch below is unambiguously
  // MedianMeanMetricResult.
  if (result.kind === 'distribution') {
    if (result.suppressed || result.value === null) {
      return (
        <span className="inline-flex items-center gap-1.5 text-[11px] text-[var(--ui-faint)]">
          too few to compare <span title={`n=${result.n}`}>(n={result.n})</span>
          {infoButton}
        </span>
      );
    }
    const value = result.value;
    const buckets = definition?.buckets ?? Object.keys(value);
    const total = Object.values(value).reduce((a, b) => a + b, 0) || 1;
    return (
      <span className="inline-flex flex-col gap-1">
        <span className="flex items-center gap-1.5 text-[11px] text-[var(--ui-faint)]">
          <span title={`n=${result.n}`}>n={result.n}</span>
          {infoButton}
        </span>
        <span className="flex flex-col gap-1">
          {buckets.map((b) => {
            const v = value[b] ?? 0;
            const pct = Math.round((v / total) * 100);
            return (
              <span key={b} className="flex items-center gap-2 text-[11px]">
                <span className="w-14 shrink-0 capitalize text-[var(--ui-soft)]">{b}</span>
                <span className="h-2 w-16 shrink-0 overflow-hidden rounded-full bg-[var(--ui-inset)]">
                  <span
                    className="block h-full rounded-full bg-[var(--accent)]"
                    style={{ width: `${pct}%` }}
                  />
                </span>
                <span className="text-foreground">{v}</span>
              </span>
            );
          })}
        </span>
      </span>
    );
  }

  // Only 'median' | 'mean' remains. A suppressed non-check-in metric (time to
  // hire, human interviewer scorecards) still carries its real value — the
  // server withholds only check-in OUTCOME metrics (which come back null,
  // same as a rate's null is never shown). Mirror the rate branch above: the
  // text stands, the real figure surfaces on hover only when there is one.
  const suppressedTooltip =
    result.suppressed && result.value !== null
      ? `${result.value.toLocaleString('en-IN')} (n=${result.n}), too few to compare reliably`
      : undefined;
  return (
    <span className="inline-flex items-center gap-1.5 text-[12.5px]" title={suppressedTooltip}>
      {result.suppressed ? (
        <span className="text-[var(--ui-faint)]">too few to compare</span>
      ) : (
        <span className="font-semibold text-foreground">
          {result.value === null ? '—' : result.value.toLocaleString('en-IN')}
        </span>
      )}
      <span className="text-[11px] text-[var(--ui-faint)]" title={`n=${result.n}`}>
        (n={result.n})
      </span>
      {infoButton}
    </span>
  );
}
