// MetricInfoDialog — "How is this calculated?": label, formula, cohorts,
// version, hash.

import { useState } from 'react';
import { Copy, X } from '@/design/components/icons';
import { findDefinition, metricLabel, type MetricDefinitionsResponse } from '@/api/metrics';
import { formatDate } from '@/lib/formatters';
import { useDialogFocus } from '@/hooks/useDialogFocus';

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
  // Opened by name alone (no version in hand) — the CURRENT definition, never
  // whichever version the registry happens to list first.
  const def = findDefinition(definitions, metricName);
  // Called unconditionally — hooks cannot follow the `!def` early return
  // below — but harmlessly, since the dialog unmounts (closes) whenever
  // `def` is absent anyway.
  const panelRef = useDialogFocus<HTMLDivElement>(onClose);

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
        aria-labelledby="metric-info-title"
        tabIndex={-1}
        className="relative max-h-[85vh] w-full max-w-[480px] overflow-y-auto rounded-card border border-border bg-card p-6 shadow-elevated outline-none"
      >
        <div className="flex items-start justify-between gap-3">
          <h2 id="metric-info-title" className="text-[17px] font-bold text-foreground">
            {metricLabel(def.name, def)}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-[var(--ui-inset)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>
        <p className="mt-1 text-[13px] text-muted-foreground">
          {def.name}@{def.version} · effective {formatDate(def.effective_from)}
          {def.current ? '' : ' · superseded'}
        </p>

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
          <button
            type="button"
            onClick={() => void copyHash(definitions.registry_hash)}
            className="inline-flex min-h-9 items-center gap-1 rounded-full border border-input px-3 text-[12px] font-semibold text-foreground hover:border-[var(--ui-line-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            <Copy size={11} aria-hidden="true" /> {copied ? 'Copied' : 'Copy'}
          </button>
        </div>

        <p className="mt-4 text-[12px] leading-relaxed text-muted-foreground">
          Definitions are versioned and frozen. The numbers are recalculated from the underlying
          records each time you look, so they can change if a record changes (for example, an offer
          is withdrawn).
        </p>
      </div>
    </div>
  );
}
