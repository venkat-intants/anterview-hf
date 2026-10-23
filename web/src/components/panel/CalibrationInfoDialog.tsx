// CalibrationInfoDialog — "How is this calculated?" for the calibration tab
// (PH5-E4 criterion 16): the spec name/version, the governed thresholds from
// `rules`, the cohort basis, and the registry hash — the same ingredients
// MetricInfoDialog shows for a governed metric, for the calibration spec.

import { useState } from 'react';
import { Copy } from '@/design/components/icons';
import type { CalibrationResponse } from '@/api/scheduling';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';

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

/** A threshold as a person reads it: a share as a percentage (0.7 is "70%"),
 *  everything else as the server sent it. */
function ruleValue(key: string, value: unknown): string {
  if (key === 'min_same_direction_share' && typeof value === 'number') {
    return `${Math.round(value * 100)}%`;
  }
  return String(value);
}

export default function CalibrationInfoDialog({
  calibration,
  onClose,
}: {
  calibration: CalibrationResponse;
  onClose: () => void;
}): JSX.Element {
  const [copied, setCopied] = useState(false);

  async function copyHash() {
    try {
      await navigator.clipboard.writeText(calibration.registry_hash);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard may be unavailable; the full hash is shown below for a manual copy.
    }
  }

  // Mounted only while open (the tab renders it conditionally), so the Radix
  // dialog is always open and closing it hands control back to the parent.
  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent aria-describedby={undefined} className="block max-h-[85vh] max-w-[480px]">
        <DialogHeader className="pr-10 text-left">
          <DialogTitle className="text-[16px] font-semibold leading-snug text-foreground">
            How is this calculated?
          </DialogTitle>
        </DialogHeader>

        <p className="mt-1 text-[12px] text-muted-foreground">
          {calibration.spec.name}@{calibration.spec.version}
        </p>

        <p className="mt-3 text-[13px] leading-relaxed text-[var(--ui-soft)]">
          Paired panel mean: for every candidate a criterion had at least two interviewers score,
          each interviewer&apos;s score minus that judgement&apos;s panel mean. Averaged, that is
          how far above or below the panel someone lands on the SAME evidence - never a raw average,
          which would just reflect who happened to see the strongest candidates.
        </p>

        <div className="mt-3 rounded-[10px] border border-border bg-[var(--ui-inset-soft)] p-3">
          <p className="text-[12px] font-medium uppercase tracking-wide text-muted-foreground">
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
              <dt className="text-muted-foreground">{RULE_LABELS[key]}</dt>
              <dd className="text-right text-foreground">{ruleValue(key, calibration.rules[key])}</dd>
            </div>
          ))}
        </dl>

        <div className="mt-4 flex items-center gap-2 text-[12px] text-muted-foreground">
          {/* The whole hash, selectable: the copy button is a convenience,
              and a truncated hash cannot be checked against the registry. */}
          <span>
            registry{' '}
            <code className="select-all break-all font-mono text-[11.5px] text-foreground">
              {calibration.registry_hash}
            </code>
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void copyHash()}
            className="h-8 shrink-0 px-3 text-[12px]"
          >
            <Copy aria-hidden="true" /> {copied ? 'Copied' : 'Copy'}
          </Button>
        </div>

        <p className="mt-4 text-[12px] leading-relaxed text-muted-foreground">
          These are patterns in scoring, not assessments of any interviewer. Differences can have
          good reasons. Use them to start a calibration conversation. Nothing here changes a
          scorecard or a decision.
        </p>
      </DialogContent>
    </Dialog>
  );
}
