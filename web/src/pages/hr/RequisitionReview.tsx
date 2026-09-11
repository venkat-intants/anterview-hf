// RequisitionReview — confirm what the Group B backfill guessed.
//
// Before Phase 2 this product had no first-class job. Applicants carried a
// free-text `target_job_title`, so nothing could be counted or configured per
// opening. The backfill created one requisition per distinct normalised title
// and filed everyone into it, which is right almost always — and occasionally
// wrong in two specific, opposite ways:
//
//   • it FOLDED TOO MUCH: two genuinely different jobs spelled alike became one
//     opening. Splitting — by ticking the candidates who belong elsewhere —
//     takes them back apart.
//   • it FOLDED TOO LITTLE: one job spelled two unrelated ways became two
//     openings, or one person who applied twice stayed two people. Merging the
//     openings, or the people, joins them.
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
  confirmRequisition,
  getBackfillReview,
  listEnrolments,
  listRequisitions,
  mergeRequisition,
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
 * Three ways to settle an inferred opening, one per way the guess can be wrong:
 *   • right as it is  → "Looks right" (its own action, audited)
 *   • too much in it  → split: tick the CANDIDATES who belong elsewhere
 *   • one of two      → merge it into the opening it duplicates
 * Renaming also confirms: editing the title makes the grouping deliberate.
 *
 * Split picks candidates, not titles. The backfill grouped by normalised
 * title, so in an opening it made every candidate normalises the same — "pick
 * the titles that don't belong" selected everyone and was refused. Candidates
 * are the only unit that can actually separate them.
 */
function BackfilledCard({ req }: { req: BackfilledRequisition }) {
  const qc = useQueryClient();
  const [title, setTitle] = useState(req.title);
  const [splitting, setSplitting] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [newTitle, setNewTitle] = useState('');
  const [merging, setMerging] = useState(false);
  const [into, setInto] = useState('');
  const [mergeConfirming, setMergeConfirming] = useState(false);

  const folded = req.distinct_source_titles > 1;
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: REVIEW_KEY });
    void qc.invalidateQueries({ queryKey: ['hr', 'requisitions'] });
  };

  // Loaded only when a split is opened: most openings are confirmed as they are.
  const candidates = useQuery({
    queryKey: ['hr', 'requisition', req.id, 'enrolments', 'review'],
    queryFn: () => listEnrolments(req.id, { limit: 200 }),
    enabled: splitting,
  });
  const others = useQuery({
    queryKey: ['hr', 'requisitions', 'merge-targets'],
    queryFn: () => listRequisitions({ limit: 200 }),
    enabled: merging,
  });
  const targets = (others.data ?? []).filter((r) => r.id !== req.id);

  const confirmMut = useMutation({
    mutationFn: () =>
      title.trim() === req.title
        ? confirmRequisition(req.id)
        : updateRequisition(req.id, { title: title.trim() }),
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
      splitRequisition(req.id, { enrolment_ids: picked, new_title: newTitle.trim() }),
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

  const mergeMut = useMutation({
    mutationFn: () => mergeRequisition(req.id, into),
    onSuccess: (res) => {
      toast.success(`Moved ${res.moved} candidate(s) into “${res.title}”`);
      setMerging(false);
      setMergeConfirming(false);
      invalidate();
    },
    // Refusals name what to resolve (a workflow, someone who applied to both).
    onError: (e) => toast.error(errText(e, 'Could not merge these openings')),
  });

  const toggle = (id: string) =>
    setPicked((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  // Moving everyone out is a rename, and the server says so. Blocking the
  // button is friendlier than letting them fill the form in and be refused.
  const total = candidates.data?.length ?? 0;
  const wouldEmpty = picked.length > 0 && picked.length === total;
  const canSplit = picked.length > 0 && !wouldEmpty && newTitle.trim().length >= 2;
  const intoTitle = targets.find((r) => r.id === into)?.title ?? '';

  return (
    // Scoped by id: several openings can carry the same words (a folded
    // opening lists its source titles verbatim), so "the card containing this
    // text" is genuinely ambiguous here.
    <div data-testid={`opening-${req.id}`}>
    <GlassCard className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Briefcase className="h-4 w-4 shrink-0 text-[var(--ui-faint)]" aria-hidden="true" />
            <span className="truncate text-[15px] font-semibold text-foreground">{req.title}</span>
            {folded ? (
              <StatusTag tone="amber" dot>
                {req.distinct_source_titles} spellings folded
              </StatusTag>
            ) : (
              <StatusTag tone="neutral">1 spelling</StatusTag>
            )}
          </div>
          <div className="mt-1 text-[12.5px] text-muted-foreground">
            {req.enrolments} candidate{req.enrolments === 1 ? '' : 's'} · {req.status}
          </div>
        </div>
      </div>

      {folded ? (
        <div className="mt-4 rounded-[12px] border border-[var(--ui-warn)]/25 bg-[var(--ui-warn)]/[0.06] p-3">
          <div className="flex items-start gap-2">
            <AlertTriangle
              className="mt-0.5 h-4 w-4 shrink-0 text-[var(--ui-warn)]"
              aria-hidden="true"
            />
            <div className="text-[12.5px] leading-relaxed text-[var(--ui-soft)]">
              These titles were treated as one opening. If two of them are really
              different jobs, split them apart before building a workflow — a workflow
              is configured per opening, so everyone here would otherwise be assessed
              against the same rounds.
              <div className="mt-2 flex flex-wrap gap-1.5">
                {req.source_titles.map((t) => (
                  <span
                    key={t}
                    className="rounded-pill border border-border bg-[var(--ui-inset)] px-2.5 py-1 text-[11.5px] text-[var(--ui-soft)]"
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
            className="mb-1.5 block text-[12px] font-medium text-[var(--ui-soft)]"
          >
            Opening title
          </label>
          <input
            id={`title-${req.id}`}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="w-full rounded-[12px] border border-border bg-secondary px-3.5 py-2.5 text-[14px] text-foreground focus:border-[var(--accent)] focus:outline-none"
          />
        </div>
        <button
          type="button"
          onClick={() => confirmMut.mutate()}
          disabled={confirmMut.isPending || title.trim().length < 2}
          className="inline-flex items-center gap-1.5 rounded-[12px] bg-primary px-4 py-2.5 text-[13px] font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
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
        <button
          type="button"
          onClick={() => {
            setSplitting((s) => !s);
            setMerging(false);
          }}
          className="rounded-[12px] border border-[var(--ui-line-strong)] px-4 py-2.5 text-[13px] font-medium text-[var(--ui-soft)] hover:border-[var(--ui-line-strong)] hover:text-foreground"
        >
          {splitting ? 'Cancel split' : 'Split apart'}
        </button>
        <button
          type="button"
          onClick={() => {
            setMerging((m) => !m);
            setSplitting(false);
            setMergeConfirming(false);
          }}
          className="rounded-[12px] border border-[var(--ui-line-strong)] px-4 py-2.5 text-[13px] font-medium text-[var(--ui-soft)] hover:border-[var(--ui-line-strong)] hover:text-foreground"
        >
          {merging ? 'Cancel merge' : 'Merge into another opening…'}
        </button>
      </div>

      {/* Merge into another opening */}
      {merging ? (
        <div className="mt-4 rounded-[14px] border border-border bg-black/25 p-4">
          <div className="text-[13px] font-medium text-foreground">
            This is the same job as another opening
          </div>
          <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
            Everyone here moves into the opening you choose, keeping every assessment they
            have. This opening is then closed and retired.
          </p>
          <label
            htmlFor={`into-${req.id}`}
            className="mb-1.5 mt-3 block text-[12px] font-medium text-[var(--ui-soft)]"
          >
            Merge into
          </label>
          <select
            id={`into-${req.id}`}
            value={into}
            onChange={(e) => {
              setInto(e.target.value);
              setMergeConfirming(false);
            }}
            className="w-full rounded-[12px] border border-border bg-secondary px-3.5 py-2.5 text-[14px] text-foreground focus:border-[var(--accent)] focus:outline-none"
          >
            <option value="">Choose an opening…</option>
            {targets.map((r) => (
              <option key={r.id} value={r.id}>
                {r.title} · {r.status}
              </option>
            ))}
          </select>
          {mergeConfirming ? (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className="text-[12.5px] text-[var(--ui-soft)]">
                Move {req.enrolments} candidate{req.enrolments === 1 ? '' : 's'} into “
                {intoTitle}” and retire “{req.title}”?
              </span>
              <button
                type="button"
                onClick={() => mergeMut.mutate()}
                disabled={mergeMut.isPending}
                className="inline-flex items-center gap-1.5 rounded-[12px] bg-[var(--accent)] px-4 py-2 text-[13px] font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40"
              >
                {mergeMut.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : null}
                Yes, merge
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setMergeConfirming(true)}
              disabled={!into}
              className="mt-3 rounded-[12px] border border-[var(--ui-line-strong)] px-4 py-2 text-[13px] font-medium text-[var(--ui-soft)] hover:text-foreground disabled:opacity-40"
            >
              Merge…
            </button>
          )}
        </div>
      ) : null}

      {/* Split */}
      {splitting ? (
        <div className="mt-4 rounded-[14px] border border-border bg-black/25 p-4">
          <div className="text-[13px] font-medium text-foreground">
            Move some of these candidates into their own opening
          </div>
          <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
            Tick the candidates who do not belong here. They keep every assessment they
            have — only which opening they sit in changes.
          </p>

          {candidates.isLoading ? (
            <p className="mt-3 text-[12.5px] text-muted-foreground">Loading candidates…</p>
          ) : null}
          <div className="mt-3 flex max-h-[280px] flex-col gap-1.5 overflow-y-auto">
            {candidates.data?.map((c) => (
              <label
                key={c.id}
                className="flex cursor-pointer items-center gap-2.5 rounded-[10px] px-2 py-1.5 text-[13px] text-[var(--ui-soft)] hover:bg-[var(--ui-inset)]"
              >
                <input
                  type="checkbox"
                  aria-label={c.full_name}
                  checked={picked.includes(c.id)}
                  onChange={() => toggle(c.id)}
                  className="h-4 w-4 accent-[var(--accent)]"
                />
                <span className="text-foreground">{c.full_name}</span>
                {/* The spelling they applied under: the best clue to which
                    opening they meant. */}
                <span className="ml-auto truncate text-[11.5px] text-[var(--ui-faint)]">
                  applied as “{c.target_job_title}”
                </span>
              </label>
            ))}
          </div>

          <div className="mt-3">
            <label
              htmlFor={`new-title-${req.id}`}
              className="mb-1.5 block text-[12px] font-medium text-[var(--ui-soft)]"
            >
              New opening title
            </label>
            <input
              id={`new-title-${req.id}`}
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder="e.g. Senior Python Developer"
              className="w-full rounded-[12px] border border-border bg-secondary px-3.5 py-2.5 text-[14px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none"
            />
          </div>

          {wouldEmpty ? (
            <p className="mt-2 text-[12px] text-[var(--ui-warn)]">
              That moves every candidate out — rename the opening instead of splitting it.
            </p>
          ) : null}

          <button
            type="button"
            onClick={() => splitMut.mutate()}
            disabled={!canSplit || splitMut.isPending}
            className="mt-3 inline-flex items-center gap-1.5 rounded-[12px] bg-[var(--accent)] px-4 py-2.5 text-[13px] font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            {splitMut.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : null}
            Create opening & move {picked.length || 0} candidate
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
          <Users className="h-4 w-4 text-[var(--ui-faint)]" aria-hidden="true" />
          <span className="text-[15px] font-semibold text-foreground">{dup.email}</span>
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

      <div className="mt-1 text-[12.5px] text-muted-foreground">
        {dup.enrolment_count} enrolment{dup.enrolment_count === 1 ? '' : 's'} across these records
      </div>

      <fieldset className="mt-4">
        <legend className="mb-2 text-[12px] font-medium text-[var(--ui-soft)]">
          Keep which record?
        </legend>
        <div className="flex flex-col gap-1.5">
          {dup.applicant_ids.map((id, i) => (
            <label
              key={id}
              className={cn(
                'flex cursor-pointer items-center gap-2.5 rounded-[10px] border px-3 py-2 text-[13px]',
                id === survivor
                  ? 'border-[var(--accent)]/50 bg-[var(--accent)]/[0.08] text-foreground'
                  : 'border-border text-[var(--ui-soft)] hover:border-[var(--ui-line-strong)]',
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
              <span className="ml-auto font-mono text-[11px] text-[var(--ui-faint)]">
                {id.slice(0, 8)}
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      {confirming ? (
        <div className="mt-4 rounded-[12px] border border-[var(--ui-danger)]/30 bg-[var(--ui-danger)]/[0.07] p-3">
          <div className="flex items-start gap-2">
            <AlertTriangle
              className="mt-0.5 h-4 w-4 shrink-0 text-[var(--ui-danger)]"
              aria-hidden="true"
            />
            <div className="text-[12.5px] leading-relaxed text-[var(--ui-soft)]">
              This cannot be undone. Exam attempts, assignments and interview invites
              from the other {absorbed.length} record{absorbed.length === 1 ? '' : 's'}{' '}
              move onto <span className="font-medium text-foreground">{
                dup.names[dup.applicant_ids.indexOf(survivor)] ?? 'the kept record'
              }</span>, and those records are then retired.
            </div>
          </div>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={() => mergeMut.mutate()}
              disabled={mergeMut.isPending || absorbed.length === 0}
              className="inline-flex items-center gap-1.5 rounded-[12px] bg-[var(--ui-danger)] px-4 py-2 text-[13px] font-medium text-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              {mergeMut.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : null}
              Yes, merge permanently
            </button>
            <button
              type="button"
              onClick={() => setConfirming(false)}
              className="rounded-[12px] border border-[var(--ui-line-strong)] px-4 py-2 text-[13px] text-[var(--ui-soft)] hover:text-foreground"
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
          className="mt-4 rounded-[12px] border border-[var(--ui-line-strong)] px-4 py-2.5 text-[13px] font-medium text-[var(--ui-soft)] hover:border-[var(--ui-line-strong)] hover:text-foreground disabled:opacity-40"
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
          <h1 className="text-[26px] font-semibold tracking-[-0.8px] text-foreground">
            Review imported openings
          </h1>
          <p className="mt-1.5 max-w-[70ch] text-[13.5px] leading-relaxed text-muted-foreground">
            Your existing applicants were sorted into openings by job title. Confirm what
            that produced before you build workflows on top of it — a workflow belongs to
            one opening, so the grouping decides who gets assessed together.
          </p>
          {(data?.unfiled_applicants ?? 0) > 0 ? (
            // The backfill could not group anyone without a job title, and used
            // to drop them silently. Counted here so they are not lost from view.
            <p className="mt-3 flex items-start gap-1.5 text-[12.5px] text-[var(--ui-warn)]">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              {data?.unfiled_applicants} applicant
              {data?.unfiled_applicants === 1 ? ' is' : 's are'} not filed under any opening —
              they had no job title to group by. Re-upload them against an opening to include
              them.
            </p>
          ) : null}
        </header>
      </Reveal>

      {isLoading ? (
        <div className="flex items-center gap-2 py-16 text-[13px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading review…
        </div>
      ) : isError ? (
        <GlassCard className="p-6">
          <div className="flex items-center gap-2 text-[13.5px] text-[var(--ui-danger)]">
            <AlertTriangle className="h-4 w-4" aria-hidden="true" />
            {errText(error, 'Could not load the review')}
          </div>
        </GlassCard>
      ) : reqs.length === 0 && dupes.length === 0 ? (
        <GlassCard className="p-10 text-center">
          <CheckCircle2 className="mx-auto h-8 w-8 text-[var(--ui-ok)]" aria-hidden="true" />
          <div className="mt-3 text-[15px] font-medium text-foreground">Nothing left to review</div>
          <p className="mx-auto mt-1.5 max-w-[46ch] text-[13px] text-muted-foreground">
            Every imported opening has been confirmed and no duplicate applicant records
            were found.
          </p>
        </GlassCard>
      ) : (
        <div className="flex flex-col gap-8">
          {reqs.length > 0 ? (
            <section>
              <div className="mb-3 flex items-center gap-2">
                <h2 className="text-[15px] font-semibold text-foreground">
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
              <h2 className="mb-1 text-[15px] font-semibold text-foreground">
                People who look like duplicates ({dupes.length})
              </h2>
              <p className="mb-3 flex items-start gap-1.5 text-[12.5px] text-muted-foreground">
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
