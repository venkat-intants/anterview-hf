// MetricInfoDialog — "How is this calculated?": label, formula, cohorts,
// version, hash.

import { useState } from 'react';
import { Copy, X } from '@/design/components/icons';
import { metricLabel, type MetricDefinitionsResponse } from '@/api/metrics';
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
  const def = definitions?.metrics.find((m) => m.name === metricName);
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
        className="relative max-h-[85vh] w-full max-w-[480px] overflow-y-auto rounded-[16px] border border-border bg-card p-6 outline-none"
      >
        <div className="flex items-start justify-between gap-3">
          <h2 id="metric-info-title" className="text-[16px] font-semibold text-foreground">
            {metricLabel(def.name, def)}
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
          {def.name}@{def.version} · effective {formatDate(def.effective_from)}
          {def.current ? '' : ' · superseded'}
        </p>

        <p className="mt-3 text-[13px] leading-relaxed text-[var(--ui-soft)]">{def.description}</p>

        <div className="mt-3 rounded-[10px] border border-border bg-[var(--ui-inset-soft)] p-3">
          <p className="text-[11px] font-medium uppercase tracking-wide text-[var(--ui-faint)]">
            Formula
          </p>
          <p className="mt-1 break-words font-mono text-[12.5px] text-foreground">{def.formula}</p>
        </div>

        <dl className="mt-3 flex flex-col gap-2 text-[12.5px]">
          <div>
            <dt className="text-[var(--ui-faint)]">Cohort bases</dt>
            <dd className="text-foreground">{def.cohort_bases.join(', ')}</dd>
          </div>
          {def.dimensions.length > 0 ? (
            <div>
              <dt className="text-[var(--ui-faint)]">Dimensions</dt>
              <dd className="flex flex-col gap-1 text-foreground">
                {def.dimensions.map((dimName) => {
                  const dim = definitions.dimensions?.find((d) => d.name === dimName);
                  return (
                    <span key={dimName}>
                      {dim ? dim.label : dimName}
                      {dim?.description ? (
                        <span className="text-[var(--ui-faint)]"> — {dim.description}</span>
                      ) : null}
                    </span>
                  );
                })}
              </dd>
            </div>
          ) : null}
          {def.change_note ? (
            <div>
              <dt className="text-[var(--ui-faint)]">What changed</dt>
              <dd className="text-foreground">{def.change_note}</dd>
            </div>
          ) : null}
        </dl>

        <div className="mt-4 flex items-center gap-2 text-[11.5px] text-[var(--ui-faint)]">
          <span>registry {definitions.registry_hash.slice(0, 10)}…</span>
          <button
            type="button"
            onClick={() => void copyHash(definitions.registry_hash)}
            className="inline-flex items-center gap-1 rounded-[8px] border border-border px-2 py-1 text-[11px] text-foreground hover:border-[var(--ui-line-strong)] focus:outline-none focus-visible:border-[var(--accent)]"
          >
            <Copy size={11} aria-hidden="true" /> {copied ? 'Copied' : 'Copy'}
          </button>
        </div>

        <p className="mt-4 text-[11.5px] leading-relaxed text-[var(--ui-faint)]">
          Definitions are versioned and frozen. The numbers are recalculated from the underlying
          records each time you look, so they can change if a record changes (for example, an offer
          is withdrawn).
        </p>
      </div>
    </div>
  );
}
