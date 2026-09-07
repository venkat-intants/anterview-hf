// RequisitionReview — confirm what the Group B backfill guessed.
//
// Before Phase 2 this product had no first-class job. Applicants carried a
// free-text `target_job_title`, so nothing could be counted or configured per
// opening. The backfill created one requisition per distinct normalised title
// and filed everyone into it, which is right almost always — and occasionally
// wrong in two specific, opposite ways:
//
//   • it FOLDED TOO MUCH: two genuinely different jobs spelled alike became one
//     opening. Splitting takes them back apart.
//   • it FOLDED TOO LITTLE: one person who applied twice under two applicant
//     rows stayed two people. Merging joins them.
//
// Both fixes move somebody's application, so neither is done automatically. The
// backfill deliberately stopped at "here is what I inferred" and left this page
// to be the place a human confirms it.
//
// The asymmetry between the two actions is on purpose and is reflected in the
// UI. A split is reversible in practice — nothing is deleted, candidates keep
// every assessment. A merge is not: it repoints exam and interview history onto
// a survivor and soft-deletes the rest, so it asks for the survivor explicitly
// and takes a second confirmation.

import { useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  Loader2,
  Pencil,
  Users,
  Briefcase,
  Info,
} from '@/design/components/icons';
import {
  getBackfillReview,
  updateRequisition,
  mergeApplicants,
  splitRequisition,
  type BackfilledRequisition,
  type MergeCandidate,
} from '@/api/requisitions';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';

const REVIEW_KEY = ['hr', 'requisitions', 'review'] as const;

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/* ── Backfilled opening ─────────────────────────────────────────────────────
 *
 * Confirming is a PATCH of the title — even an unchanged one. That is not a
 * trick: the endpoint clears `from_backfill` whenever a title is written,
 * precisely because editing it makes the grouping deliberate rather than
 * inferred. So "confirm" and "rename" are the same operation with a different
 * argument, and the row leaves this queue either way.
 */
function BackfilledCard({ req }: { req: BackfilledRequisition }) {
  const qc = useQueryClient();
  const [title, setTitle] = useState(req.title);
  const [splitting, setSplitting] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [newTitle, setNewTitle] = useState('');

  const folded = req.distinct_source_titles > 1;
  const invalidate = () => void qc.invalidateQueries({ queryKey: REVIEW_KEY });

  const confirmMut = useMutation({
    mutationFn: () => updateRequisition(req.id, { title: title.trim() }),
    onSuccess: () => {
      toast.success(
        title.trim() === req.title ? 'Opening confirmed' : `Renamed to “${title.trim()}”`,
      );
      invalidate();
    },
    onError: (e) => toast.error(errText(e, 'Could not confirm this opening')),
  });

  const splitMut = useMutation({
    mutationFn: () =>
      splitRequisition(req.id, { source_titles: picked, new_title: newTitle.trim() }),
    onSuccess: (res) => {
      toast.success(`Moved ${res.moved} candidate(s) into “${res.title}”`);
      setSplitting(false);
      setPicked([]);
      setNewTitle('');
      invalidate();
    },
    // The server refuses a split whose candidates are mid-workflow, and says
    // who. That message is the whole value of the failure, so it is surfaced
    // verbatim rather than replaced with a generic one.
    onError: (e) => toast.error(errText(e, 'Could not split this opening')),
  });

  const toggle = (t: string) =>
    setPicked((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]));

  // Moving every title out is a rename, and the server says so. Blocking the
  // button is friendlier than letting them fill the form in and be refused.
  const wouldEmpty = picked.length > 0 && picked.length === req.source_titles.length;
  const canSplit = picked.length > 0 && !wouldEmpty && newTitle.trim().length >= 2;

  return (
    // Scoped by id: several openings can carry the same words (a folded
    // opening lists its source titles verbatim), so "the card containing this
    // text" is genuinely ambiguous here.
    <div data-testid={`opening-${req.id}`}>
    <GlassCard className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Briefcase className="h-4 w-4 shrink-0 text-[#70757c]" aria-hidden="true" />
            <span className="truncate text-[15px] font-semibold text-white">{req.title}</span>
            {folded ? (
              <StatusTag tone="amber" dot>
                {req.distinct_source_titles} spellings folded
              </StatusTag>
            ) : (
              <StatusTag tone="neutral">1 spelling</StatusTag>
            )}
          </div>
          <div className="mt-1 text-[12.5px] text-[#888b91]">
            {req.enrolments} candidate{req.enrolments === 1 ? '' : 's'} · {req.status}
          </div>
        </div>
      </div>

      {folded ? (
        <div className="mt-4 rounded-[12px] border border-[#ffb764]/25 bg-[#ffb764]/[0.06] p-3">
          <div className="flex items-start gap-2">
            <AlertTriangle
              className="mt-0.5 h-4 w-4 shrink-0 text-[#ffb764]"
              aria-hidden="true"
            />
            <div className="text-[12.5px] leading-relaxed text-[#d5d7da]">
              These titles were treated as one opening. If two of them are really
              different jobs, split them apart before building a workflow — a workflow
              is configured per opening, so everyone here would otherwise be assessed
              against the same rounds.
              <div className="mt-2 flex flex-wrap gap-1.5">
                {req.source_titles.map((t) => (
                  <span
                    key={t}
                    className="rounded-pill border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[11.5px] text-[#b8babf]"
                  >
                    {t}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {/* Confirm / rename */}
      <div className="mt-4 flex flex-wrap items-end gap-2">
        <div className="min-w-[220px] flex-1">
          <label
            htmlFor={`title-${req.id}`}
            className="mb-1.5 block text-[12px] font-medium text-[#b8babf]"
          >
            Opening title
          </label>
          <input
            id={`title-${req.id}`}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="w-full rounded-[12px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-3.5 py-2.5 text-[14px] text-white focus:border-[var(--accent)] focus:outline-none"
          />
        </div>
        <button
          type="button"
          onClick={() => confirmMut.mutate()}
          disabled={confirmMut.isPending || title.trim().length < 2}
          className="inline-flex items-center gap-1.5 rounded-[12px] bg-white px-4 py-2.5 text-[13px] font-medium text-black transition-opacity hover:opacity-90 disabled:opacity-40"
        >
          {confirmMut.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : title.trim() === req.title ? (
            <Check className="h-4 w-4" aria-hidden="true" />
          ) : (
            <Pencil className="h-4 w-4" aria-hidden="true" />
          )}
          {title.trim() === req.title ? 'Looks right' : 'Rename & confirm'}
        </button>
        {folded ? (
          <button
            type="button"
            onClick={() => setSplitting((s) => !s)}
            className="rounded-[12px] border border-white/[0.12] px-4 py-2.5 text-[13px] font-medium text-[#d5d7da] hover:border-white/25 hover:text-white"
          >
            {splitting ? 'Cancel split' : 'Split apart'}
          </button>
        ) : null}
      </div>

      {/* Split */}
      {splitting ? (
        <div className="mt-4 rounded-[14px] border border-white/[0.08] bg-black/25 p-4">
          <div className="text-[13px] font-medium text-white">
            Move some of these candidates into their own opening
          </div>
          <p className="mt-1 text-[12px] leading-relaxed text-[#888b91]">
            Pick the titles that do not belong here. Those candidates keep every
            assessment they have — only which opening they sit in changes.
          </p>

          <div className="mt-3 flex flex-col gap-1.5">
            {req.source_titles.map((t) => (
              <label
                key={t}
                className="flex cursor-pointer items-center gap-2.5 rounded-[10px] px-2 py-1.5 text-[13px] text-[#d5d7da] hover:bg-white/[0.04]"
              >
                <input
                  type="checkbox"
                  checked={picked.includes(t)}
                  onChange={() => toggle(t)}
                  className="h-4 w-4 accent-[var(--accent)]"
                />
                {t}
              </label>
            ))}
          </div>

          <div className="mt-3">
            <label
              htmlFor={`new-title-${req.id}`}
              className="mb-1.5 block text-[12px] font-medium text-[#b8babf]"
            >
              New opening title
            </label>
            <input
              id={`new-title-${req.id}`}
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder="e.g. Senior Python Developer"
              className="w-full rounded-[12px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-3.5 py-2.5 text-[14px] text-white placeholder:text-[#5a5f66] focus:border-[var(--accent)] focus:outline-none"
            />
          </div>

          {wouldEmpty ? (
            <p className="mt-2 text-[12px] text-[#ffb764]">
              That moves every candidate out — rename the opening instead of splitting it.
            </p>
          ) : null}

          <button
            type="button"
            onClick={() => splitMut.mutate()}
            disabled={!canSplit || splitMut.isPending}
            className="mt-3 inline-flex items-center gap-1.5 rounded-[12px] bg-[var(--accent)] px-4 py-2.5 text-[13px] font-medium text-black transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            {splitMut.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : null}
            Create opening & move {picked.length || 0} title
            {picked.length === 1 ? '' : 's'}
          </button>
        </div>
      ) : null}
    </GlassCard>
    </div>
  );
}

/* ── Duplicate people ───────────────────────────────────────────────────────
 *
 * The survivor is chosen explicitly and defaults to the OLDEST row (the API
 * returns ids oldest-first), because that is the one most likely to carry the
 * longest history. It is only a default — the choice is the point of the
 * screen, so it is a visible radio group rather than an implicit rule.
 */
function MergeCard({ dup }: { dup: MergeCandidate }) {
  const qc = useQueryClient();
  const [survivor, setSurvivor] = useState(dup.applicant_ids[0] ?? '');
  const [confirming, setConfirming] = useState(false);

  const absorbed = useMemo(
    () => dup.applicant_ids.filter((id) => id !== survivor),
    [dup.applicant_ids, survivor],
  );

  const mergeMut = useMutation({
    mutationFn: () => mergeApplicants(survivor, absorbed),
    onSuccess: (res) => {
      toast.success(`Merged ${res.absorbed + 1} records into one person`);
      setConfirming(false);
      void qc.invalidateQueries({ queryKey: REVIEW_KEY });
      // The applicant list and the pipeline both read these rows.
      void qc.invalidateQueries({ queryKey: ['hr', 'applicants'] });
      void qc.invalidateQueries({ queryKey: ['hr', 'pipeline'] });
    },
    // A 409 here means both rows are enrolled in the same opening, and the
    // server names it. That is the one case a human must resolve first, so the
    // message is shown as-is.
    onError: (e) => toast.error(errText(e, 'Could not merge these records')),
  });

  return (
    <div data-testid={`dupe-${dup.email}`}>
    <GlassCard className="p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Users className="h-4 w-4 text-[#70757c]" aria-hidden="true" />
          <span className="text-[15px] font-semibold text-white">{dup.email}</span>
        </div>
        <div className="flex items-center gap-2">
          <StatusTag tone="lavender">{dup.applicant_ids.length} records</StatusTag>
          {dup.has_history ? (
            <StatusTag tone="amber" dot>
              has assessment history
            </StatusTag>
          ) : null}
        </div>
      </div>

      <div className="mt-1 text-[12.5px] text-[#888b91]">
        {dup.enrolment_count} enrolment{dup.enrolment_count === 1 ? '' : 's'} across these records
      </div>

      <fieldset className="mt-4">
        <legend className="mb-2 text-[12px] font-medium text-[#b8babf]">
          Keep which record?
        </legend>
        <div className="flex flex-col gap-1.5">
          {dup.applicant_ids.map((id, i) => (
            <label
              key={id}
              className={cn(
                'flex cursor-pointer items-center gap-2.5 rounded-[10px] border px-3 py-2 text-[13px]',
                id === survivor
                  ? 'border-[var(--accent)]/50 bg-[var(--accent)]/[0.08] text-white'
                  : 'border-white/[0.08] text-[#d5d7da] hover:border-white/20',
              )}
            >
              <input
                type="radio"
                name={`survivor-${dup.email}`}
                value={id}
                checked={id === survivor}
                onChange={() => {
                  setSurvivor(id);
                  setConfirming(false);
                }}
                className="h-4 w-4 accent-[var(--accent)]"
              />
              <span className="font-medium">{dup.names[i] ?? 'Unnamed'}</span>
              <span className="ml-auto font-mono text-[11px] text-[#70757c]">
                {id.slice(0, 8)}
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      {confirming ? (
        <div className="mt-4 rounded-[12px] border border-[#e6714f]/30 bg-[#e6714f]/[0.07] p-3">
          <div className="flex items-start gap-2">
            <AlertTriangle
              className="mt-0.5 h-4 w-4 shrink-0 text-[#e6714f]"
              aria-hidden="true"
            />
            <div className="text-[12.5px] leading-relaxed text-[#d5d7da]">
              This cannot be undone. Exam attempts, assignments and interview invites
              from the other {absorbed.length} record{absorbed.length === 1 ? '' : 's'}{' '}
              move onto <span className="font-medium text-white">{
                dup.names[dup.applicant_ids.indexOf(survivor)] ?? 'the kept record'
              }</span>, and those records are then retired.
            </div>
          </div>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={() => mergeMut.mutate()}
              disabled={mergeMut.isPending || absorbed.length === 0}
              className="inline-flex items-center gap-1.5 rounded-[12px] bg-[#e6714f] px-4 py-2 text-[13px] font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              {mergeMut.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : null}
              Yes, merge permanently
            </button>
            <button
              type="button"
              onClick={() => setConfirming(false)}
              className="rounded-[12px] border border-white/[0.12] px-4 py-2 text-[13px] text-[#d5d7da] hover:text-white"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          disabled={absorbed.length === 0}
          className="mt-4 rounded-[12px] border border-white/[0.12] px-4 py-2.5 text-[13px] font-medium text-[#d5d7da] hover:border-white/25 hover:text-white disabled:opacity-40"
        >
          Merge into one person…
        </button>
      )}
    </GlassCard>
    </div>
  );
}

/* ── Page ───────────────────────────────────────────────────────────────── */

export default function RequisitionReview(): JSX.Element {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: REVIEW_KEY,
    queryFn: getBackfillReview,
  });

  const reqs = data?.backfilled_requisitions ?? [];
  const dupes = data?.merge_candidates ?? [];
  // Sorted server-side by how many spellings were folded, so the riskiest
  // guesses are already first. Count them for the header rather than re-sorting.
  const risky = reqs.filter((r) => r.distinct_source_titles > 1).length;

  return (
    <div className="mx-auto w-full max-w-[1100px] px-4 py-8">
      <Reveal>
        <header className="mb-6">
          <h1 className="text-[26px] font-semibold tracking-[-0.8px] text-white">
            Review imported openings
          </h1>
          <p className="mt-1.5 max-w-[70ch] text-[13.5px] leading-relaxed text-[#888b91]">
            Your existing applicants were sorted into openings by job title. Confirm what
            that produced before you build workflows on top of it — a workflow belongs to
            one opening, so the grouping decides who gets assessed together.
          </p>
        </header>
      </Reveal>

      {isLoading ? (
        <div className="flex items-center gap-2 py-16 text-[13px] text-[#888b91]">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading review…
        </div>
      ) : isError ? (
        <GlassCard className="p-6">
          <div className="flex items-center gap-2 text-[13.5px] text-[#e6714f]">
            <AlertTriangle className="h-4 w-4" aria-hidden="true" />
            {errText(error, 'Could not load the review')}
          </div>
        </GlassCard>
      ) : reqs.length === 0 && dupes.length === 0 ? (
        <GlassCard className="p-10 text-center">
          <CheckCircle2 className="mx-auto h-8 w-8 text-[#27c93f]" aria-hidden="true" />
          <div className="mt-3 text-[15px] font-medium text-white">Nothing left to review</div>
          <p className="mx-auto mt-1.5 max-w-[46ch] text-[13px] text-[#888b91]">
            Every imported opening has been confirmed and no duplicate applicant records
            were found.
          </p>
        </GlassCard>
      ) : (
        <div className="flex flex-col gap-8">
          {reqs.length > 0 ? (
            <section>
              <div className="mb-3 flex items-center gap-2">
                <h2 className="text-[15px] font-semibold text-white">
                  Openings to confirm ({reqs.length})
                </h2>
                {risky > 0 ? (
                  <StatusTag tone="amber" dot>
                    {risky} folded several titles
                  </StatusTag>
                ) : null}
              </div>
              <div className="flex flex-col gap-3">
                {reqs.map((r) => (
                  <BackfilledCard key={r.id} req={r} />
                ))}
              </div>
            </section>
          ) : null}

          {dupes.length > 0 ? (
            <section>
              <h2 className="mb-1 text-[15px] font-semibold text-white">
                People who look like duplicates ({dupes.length})
              </h2>
              <p className="mb-3 flex items-start gap-1.5 text-[12.5px] text-[#888b91]">
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                Matched on email only. Nothing was merged automatically, because merging
                moves someone&rsquo;s assessment history and cannot be reversed.
              </p>
              <div className="flex flex-col gap-3">
                {dupes.map((d) => (
                  <MergeCard key={d.email} dup={d} />
                ))}
              </div>
            </section>
          ) : null}
        </div>
      )}
    </div>
  );
}
