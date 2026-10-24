// MetricInfoDialog — "How is this calculated?": label, formula, cohorts,
// version, hash. A Radix Dialog: focus trap, Escape, the overlay click, the
// Close button and focus return to the "How is this calculated?" trigger.

import { useRef, useState } from 'react';
import { Copy } from '@/design/components/icons';
import { findDefinition, metricLabel, type MetricDefinitionsResponse } from '@/api/metrics';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { formatDate } from '@/lib/formatters';

export default function MetricInfoDialog({
  metricName,
  definitions,
  onClose,
}: {
  metricName: string;
  definitions: MetricDefinitionsResponse | undefined;
  onClose: () => void;
}): JSX.Element | null {
  const [copied, setCopied] = useState(false);
  const returnFocusTo = useRef<HTMLElement | null>(null);
  // Opened by name alone (no version in hand) — the CURRENT definition, never
  // whichever version the registry happens to list first.
  const def = findDefinition(definitions, metricName);

  async function copyHash(hash: string) {
    try {
      await navigator.clipboard.writeText(hash);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard may be unavailable (older browser, no HTTPS) — the hash is
      // still shown on screen for a manual copy.
    }
  }

  if (!def || !definitions) return null;

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        className="block max-h-[85vh] max-w-[480px] gap-0"
        // Opened by a plain button, not a Radix trigger, so Radix has nothing to
        // return focus to: hand it back to whatever opened this.
        onOpenAutoFocus={() => {
          returnFocusTo.current =
            document.activeElement instanceof HTMLElement ? document.activeElement : null;
        }}
        onCloseAutoFocus={(e) => {
          e.preventDefault();
          returnFocusTo.current?.focus();
        }}
      >
        <DialogTitle className="pr-10 text-[17px] font-bold leading-snug text-foreground">
          {metricLabel(def.name, def)}
        </DialogTitle>
        <DialogDescription className="mt-1 text-[13px]">
          {def.name}@{def.version} · effective {formatDate(def.effective_from)}
          {def.current ? '' : ' · superseded'}
        </DialogDescription>

        <p className="mt-3 text-[14px] leading-relaxed text-foreground">{def.description}</p>

        <div className="mt-3 rounded-[12px] border border-border bg-[var(--ui-inset-soft)] p-3">
          <p className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
            Formula
          </p>
          <p className="mt-1 break-words font-mono text-[12.5px] text-foreground">{def.formula}</p>
        </div>

        <dl className="mt-3 flex flex-col gap-2 text-[13px]">
          <div>
            <dt className="font-semibold text-muted-foreground">Cohort bases</dt>
            <dd className="text-foreground">{def.cohort_bases.join(', ')}</dd>
          </div>
          {def.dimensions.length > 0 ? (
            <div>
              <dt className="font-semibold text-muted-foreground">Dimensions</dt>
              <dd className="flex flex-col gap-1 text-foreground">
                {def.dimensions.map((dimName) => {
                  const dim = definitions.dimensions?.find((d) => d.name === dimName);
                  return (
                    <span key={dimName}>
                      {dim ? dim.label : dimName}
                      {dim?.description ? (
                        <span className="text-muted-foreground"> - {dim.description}</span>
                      ) : null}
                    </span>
                  );
                })}
              </dd>
            </div>
          ) : null}
          {def.change_note ? (
            <div>
              <dt className="font-semibold text-muted-foreground">What changed</dt>
              <dd className="text-foreground">{def.change_note}</dd>
            </div>
          ) : null}
        </dl>

        <div className="mt-4 flex items-center gap-2 text-[12px] text-muted-foreground">
          <span>registry {definitions.registry_hash.slice(0, 10)}…</span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void copyHash(definitions.registry_hash)}
            className="gap-1 px-3 text-[12px] text-foreground [&_svg]:size-[11px]"
          >
            <Copy size={11} aria-hidden="true" /> {copied ? 'Copied' : 'Copy'}
          </Button>
        </div>

        <p className="mt-4 text-[12px] leading-relaxed text-muted-foreground">
          Definitions are versioned and frozen. The numbers are recalculated from the underlying
          records each time you look, so they can change if a record changes (for example, an offer
          is withdrawn).
        </p>
      </DialogContent>
    </Dialog>
  );
}
