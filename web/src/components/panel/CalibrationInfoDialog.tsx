// CalibrationInfoDialog — "How is this calculated?" for the calibration tab
// (PH5-E4 criterion 16): the spec name/version, the governed thresholds from
// `rules`, the cohort basis, and the registry hash — the same ingredients
// MetricInfoDialog shows for a governed metric, for the calibration spec.

import { useState } from 'react';
import { Copy, X } from '@/design/components/icons';
import type { CalibrationResponse } from '@/api/scheduling';
import { useDialogFocus } from '@/hooks/useDialogFocus';

const RULE_LABELS: Record<keyof CalibrationResponse['rules'], string> = {
  min_candidates: 'Minimum distinct candidates before a figure is shown',
  min_pairs: 'Minimum shared judgements before a figure is shown',
  meaningful_delta: 'Gap that counts as meaningful',
  min_same_direction_share: 'Share of shared judgements that must point the same way',
  min_interviewers_for_baseline: 'Minimum interviewers for a panel baseline',
  wide_disagreement_range: '"Wide disagreement" range threshold',
  min_span_days: 'Shortest period allowed',
  max_span_days: 'Longest period allowed',
  scale: 'Scoring scale',
};

const RULE_ORDER: (keyof CalibrationResponse['rules'])[] = [
  'scale',
  'min_candidates',
  'min_pairs',
  'meaningful_delta',
  'min_same_direction_share',
  'min_interviewers_for_baseline',
  'wide_disagreement_range',
  'min_span_days',
  'max_span_days',
];

export default function CalibrationInfoDialog({
  calibration,
  onClose,
}: {
  calibration: CalibrationResponse;
  onClose: () => void;
}): JSX.Element {
  const [copied, setCopied] = useState(false);
  const panelRef = useDialogFocus<HTMLDivElement>(onClose);

  async function copyHash() {
    try {
      await navigator.clipboard.writeText(calibration.registry_hash);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard may be unavailable — the hash is still shown for a manual copy.
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-black/50"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="calibration-info-title"
        tabIndex={-1}
        className="relative max-h-[85vh] w-full max-w-[480px] overflow-y-auto rounded-[16px] border border-border bg-card p-6 outline-none"
      >
        <div className="flex items-start justify-between gap-3">
          <h2 id="calibration-info-title" className="text-[16px] font-semibold text-foreground">
            How is this calculated?
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 rounded-[8px] p-1 text-muted-foreground hover:text-foreground focus:outline-none focus-visible:text-foreground"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        <p className="mt-1 text-[12px] text-[var(--ui-faint)]">
          {calibration.spec.name}@{calibration.spec.version}
        </p>

        <p className="mt-3 text-[13px] leading-relaxed text-[var(--ui-soft)]">
          Paired panel mean: for every candidate a criterion had at least two interviewers score,
          each interviewer&apos;s score minus that judgement&apos;s panel mean. Averaged, that is
          how far above or below the panel someone lands on the SAME evidence — never a raw average,
          which would just reflect who happened to see the strongest candidates.
        </p>

        <div className="mt-3 rounded-[10px] border border-border bg-[var(--ui-inset-soft)] p-3">
          <p className="text-[11px] font-medium uppercase tracking-wide text-[var(--ui-faint)]">
            Cohort
          </p>
          <p className="mt-1 text-[12.5px] text-foreground">
            {calibration.cohort.basis === 'scorecard_submitted'
              ? 'Scorecards submitted in the chosen period'
              : calibration.cohort.basis}
          </p>
        </div>

        <dl className="mt-3 flex flex-col gap-1.5 text-[12.5px]">
          {RULE_ORDER.map((key) => (
            <div key={key} className="flex items-baseline justify-between gap-3">
              <dt className="text-[var(--ui-faint)]">{RULE_LABELS[key]}</dt>
              <dd className="text-right text-foreground">{String(calibration.rules[key])}</dd>
            </div>
          ))}
        </dl>

        <div className="mt-4 flex items-center gap-2 text-[11.5px] text-[var(--ui-faint)]">
          <span>registry {calibration.registry_hash.slice(0, 10)}…</span>
          <button
            type="button"
            onClick={() => void copyHash()}
            className="inline-flex items-center gap-1 rounded-[8px] border border-border px-2 py-1 text-[11px] text-foreground hover:border-[var(--ui-line-strong)] focus:outline-none focus-visible:border-[var(--accent)]"
          >
            <Copy size={11} aria-hidden="true" /> {copied ? 'Copied' : 'Copy'}
          </button>
        </div>

        <p className="mt-4 text-[11.5px] leading-relaxed text-[var(--ui-faint)]">
          These are patterns in scoring, not assessments of any interviewer. Differences can have
          good reasons. Use them to start a calibration conversation. Nothing here changes a
          scorecard or a decision.
        </p>
      </div>
    </div>
  );
}
