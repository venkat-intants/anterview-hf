// CodeSimilarityCompare — PH4-D3. A side-by-side dialog for one similarity
// SIGNAL, with the matched regions highlighted, and the ONLY place a
// "Record a finding" form lives on this surface.
//
// This dialog fetches the comparison — audited server-side every time
// (D3 #24) — only once HR opens it for this specific signal, never ahead of
// that. Source and excerpts are candidate content: rendered as plain text,
// line by line, never through `dangerouslySetInnerHTML`.
//
// The signal itself is never the finding. The rationale is mandatory and
// mirrors the server's own bounds (20–2000 characters) so a save never
// round-trips into a 422.

import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  FINDING_RATIONALE_MAX,
  FINDING_RATIONALE_MIN,
  getSimilarityCompare,
  recordCodeIntegrityFinding,
  type AttemptSimilaritySignal,
  type FindingOutcome,
  type MatchedRegion,
  type SimilarityExcerpt,
} from '@/api/codeEvidence';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { Loader2, X } from '@/design/components/icons';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function pct(n: number): string {
  return `${Math.round(n * 100)}%`;
}

function inRange(line: number, ranges: Array<[number, number]>): boolean {
  return ranges.some(([start, end]) => line >= start && line <= end);
}

/** Plain-text excerpt, one line per row, with matched lines highlighted.
 *  Never HTML — each line is rendered as text content only. */
function ExcerptPane({
  title,
  excerpt,
  highlightRanges,
}: {
  title: string;
  excerpt: SimilarityExcerpt;
  highlightRanges: Array<[number, number]>;
}) {
  const lines = (excerpt.excerpt ?? '').split('\n');
  return (
    <div className="min-w-0 overflow-hidden rounded-[10px] border border-border bg-[#0b0c0e]">
      <div className="border-b border-[var(--ui-line-strong)] px-2.5 py-1.5 text-[11px] uppercase tracking-[0.4px] text-[var(--ui-faint)]">
        {title}
        {excerpt.language ? ` · ${excerpt.language}` : ''}
      </div>
      <pre className="max-h-[380px] overflow-auto p-0 text-[12px] leading-[1.55]">
        {lines.map((line, i) => {
          const ln = i + 1;
          const highlighted = inRange(ln, highlightRanges);
          return (
            <div
              key={ln}
              className={cn(
                'whitespace-pre px-2.5',
                highlighted ? 'bg-[var(--ui-warn)]/20 text-[#f5e6c8]' : 'text-[#d8dadd]',
              )}
            >
              <span className="mr-2 inline-block w-7 shrink-0 select-none text-right text-[var(--ui-faint)]">
                {ln}
              </span>
              {line.length === 0 ? ' ' : line}
            </div>
          );
        })}
      </pre>
    </div>
  );
}

const OUTCOMES: Array<{ value: FindingOutcome; label: string }> = [
  { value: 'no_concern', label: 'No concern' },
  { value: 'follow_up', label: 'Follow up' },
  { value: 'confirmed', label: 'Confirmed' },
];

export interface CodeSimilarityCompareProps {
  signal: AttemptSimilaritySignal;
  /** The attempt whose evidence tab this was opened from — the finding is
   *  always recorded against it, whichever side of the pair it is. */
  attemptId: string;
  onClose: () => void;
  /** Fired once a finding is saved, so the caller can refetch the evidence
   *  that now includes it. */
  onRecorded: () => void;
}

export default function CodeSimilarityCompare({
  signal,
  attemptId,
  onClose,
  onRecorded,
}: CodeSimilarityCompareProps): JSX.Element {
  const [outcome, setOutcome] = useState<FindingOutcome>('no_concern');
  const [rationale, setRationale] = useState('');
  const [error, setError] = useState<string | null>(null);

  const compare = useQuery({
    queryKey: ['hr', 'code-similarity', signal.id],
    queryFn: () => getSimilarityCompare(signal.id),
    retry: false,
  });

  const recordMut = useMutation({
    mutationFn: () =>
      recordCodeIntegrityFinding({
        attempt_id: attemptId,
        coding_question_id: signal.coding_question_id,
        outcome,
        rationale: rationale.trim(),
        signal_id: signal.id,
      }),
    onSuccess: () => {
      toast.success('Finding recorded');
      onRecorded();
      onClose();
    },
    onError: (e) => {
      const msg = errText(e, 'Could not record this finding');
      setError(msg);
      toast.error(msg);
    },
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = rationale.trim();
    if (trimmed.length < FINDING_RATIONALE_MIN) {
      setError(`Give a rationale of at least ${FINDING_RATIONALE_MIN} characters.`);
      return;
    }
    setError(null);
    recordMut.mutate();
  }

  const matchedRegions: MatchedRegion[] = compare.data?.matched_regions ?? [];
  const lowRanges: Array<[number, number]> = matchedRegions.map((r) => [r.low_start, r.low_end]);
  const highRanges: Array<[number, number]> = matchedRegions.map((r) => [r.high_start, r.high_end]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="code-compare-heading"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onKeyDown={(e) => {
        if (e.key === 'Escape') onClose();
      }}
    >
      <div className="flex max-h-[90vh] w-full max-w-[980px] flex-col overflow-hidden rounded-[18px] border border-border bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <h2 id="code-compare-heading" className="text-[15px] font-semibold text-foreground">
            Compare submissions
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-[8px] p-1 text-muted-foreground hover:text-foreground"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {/* Fixed caption, server-provided verbatim: this is a signal, never
              a finding on its own. */}
          {compare.data ? (
            <p className="mb-3 rounded-[10px] border border-[var(--ui-info)]/25 bg-[var(--ui-info)]/[0.06] px-3 py-2 text-[12px] leading-relaxed text-[var(--ui-soft)]">
              {compare.data.caption}
            </p>
          ) : null}

          <p className="mb-3 font-mono text-[12px] text-[var(--ui-faint)]">
            containment {pct(signal.containment_low)}
            {signal.containment_high != null ? ` / ${pct(signal.containment_high)}` : ''} · jaccard{' '}
            {pct(signal.jaccard)} · {signal.shared_fingerprints} shared fingerprints
          </p>

          {compare.isLoading ? (
            <p className="flex items-center gap-2 py-8 text-[13px] text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Loading…
            </p>
          ) : compare.isError ? (
            <p className="py-8 text-[13px] text-[var(--ui-danger)]">
              {errText(compare.error, 'Could not load this comparison.')}
            </p>
          ) : compare.data ? (
            <>
              <div className="grid gap-3 sm:grid-cols-2">
                <ExcerptPane title="This submission" excerpt={compare.data.low} highlightRanges={lowRanges} />
                <ExcerptPane
                  title={signal.reference_kind === 'reference_solution' ? 'Reference solution' : 'Other submission'}
                  excerpt={compare.data.high}
                  highlightRanges={highRanges}
                />
              </div>
              {matchedRegions.length === 0 ? (
                <p className="mt-2 text-[11.5px] text-[var(--ui-faint)]">
                  {signal.reference_kind === 'reference_solution'
                    ? 'Matched line ranges are not available for a reference-solution comparison — see the raw counts above.'
                    : 'No matched line ranges could be located for this pair.'}
                </p>
              ) : null}
            </>
          ) : null}

          <form onSubmit={submit} className="mt-5 flex flex-col gap-2.5 rounded-[12px] border border-border p-3.5">
            <h3 className="text-[12.5px] font-medium text-foreground">Record a finding</h3>
            <fieldset className="flex flex-wrap gap-3" aria-label="Outcome">
              {OUTCOMES.map((o) => (
                <label key={o.value} className="flex items-center gap-1.5 text-[12.5px] text-foreground">
                  <input
                    type="radio"
                    name="finding-outcome"
                    checked={outcome === o.value}
                    onChange={() => setOutcome(o.value)}
                  />
                  {o.label}
                </label>
              ))}
            </fieldset>
            <label className="block">
              <span className="text-[12px] text-[var(--ui-soft)]">
                Rationale (required, {FINDING_RATIONALE_MIN}–{FINDING_RATIONALE_MAX} characters)
              </span>
              <textarea
                value={rationale}
                onChange={(e) => setRationale(e.target.value)}
                maxLength={FINDING_RATIONALE_MAX}
                rows={3}
                aria-required="true"
                placeholder="What you reviewed and why you reached this outcome"
                className="mt-1 w-full rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none"
              />
              <span className="mt-0.5 block text-[11px] text-[var(--ui-faint)]">
                {rationale.trim().length}/{FINDING_RATIONALE_MIN} minimum characters
              </span>
            </label>
            {error ? <p className="text-[12px] text-[var(--ui-danger)]">{error}</p> : null}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                className="rounded-[10px] border border-border px-3.5 py-2 text-[12.5px] text-foreground"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={recordMut.isPending}
                className="inline-flex items-center gap-1.5 rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-50"
              >
                {recordMut.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                ) : null}
                Record finding
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
