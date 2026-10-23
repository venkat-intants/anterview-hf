// InterviewerScorecard (/interviewer/scorecards/:id) — D4-1.
//
// The doc's flow: Kit → Conduct interview → Notes → Scorecard → Submit. Two
// tabs cover it: the kit (read-only guidance, plus a private notes textarea)
// and the scorecard itself (per-criterion score, evidence, an overall
// summary).
//
// Scorecards never decide a hiring outcome (CLAUDE.md D-05) — submitting one
// only records an assessment. A submitted scorecard can't be edited, only
// corrected with a reason, and the original is kept (superseded, not gone).
//
// English-only by design (CLAUDE.md — staff consoles are not translated).

import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Check, CheckCircle2, Info, Lock, Loader2 } from '@/design/components/icons';
import { GlassCard, SegTabs, StatusTag, ToggleSwitch } from '@/design/components/primitives';
import { Breadcrumbs, PageHeader, Page } from '@/design/components/page';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { errorMessage } from '@/lib/errorMessage';
import { ApiError } from '@/api/client';
import { useUnsavedGuard } from '@/components/bank/useUnsavedGuard';
import { isUuid } from '@/api/pathId';
import {
  getPrivateNotes,
  getScorecard,
  getScorecardKit,
  requestCorrection,
  saveScorecard,
  savePrivateNotes,
  submitScorecard,
  type ScorecardDetail,
  type ScoreInput,
} from '@/api/interviewer';
import SubmissionPanel from '@/components/interviewer/SubmissionPanel';
import { formatDateTime } from '@/lib/formatters';

/** PH4-D4 — only these two round kinds carry a submission to review. */
const TASK_ROUND_KINDS = new Set(['job_simulation', 'portfolio']);

/** A withdrawn assignment now 404s on every interviewer-facing endpoint for
 *  it (kit, notes, the scorecard itself) — read as "gone", not a generic error. */
const WITHDRAWN_MESSAGE = 'This assignment is no longer available.';

function errOrWithdrawn(e: unknown, fallback: string): string {
  if (e instanceof ApiError && e.status === 404) return WITHDRAWN_MESSAGE;
  return errorMessage(e, fallback);
}

const MIN_CORRECTION_REASON = 10;
const MAX_CORRECTION_REASON = 1000;
const MAX_EVIDENCE = 4000;
/** The server's limit on the overall summary (routers/interviewer.py). */
const MAX_SUMMARY = 4000;
const MAX_NOTES = 20000;

type ScoreState = Record<string, { score: number | null; not_assessed: boolean; evidence: string }>;

function initScores(detail: ScorecardDetail | undefined): ScoreState {
  const out: ScoreState = {};
  for (const c of detail?.criteria ?? []) {
    out[c.competency_id] = {
      score: c.score,
      not_assessed: c.not_assessed,
      evidence: c.evidence ?? '',
    };
  }
  return out;
}

/** A comparable form of the draft, to tell saved from unsaved. */
function snapshot(scores: ScoreState, summary: string): string {
  return JSON.stringify([toScoreInputs(scores), summary.trim()]);
}

function toScoreInputs(scores: ScoreState): ScoreInput[] {
  return Object.entries(scores).map(([competency_id, v]) => ({
    competency_id,
    score: v.not_assessed ? null : v.score,
    not_assessed: v.not_assessed,
    evidence: v.evidence.trim() || null,
  }));
}

/* ── Kit panel (read-only guidance) + private notes ─────────────────────── */

function KitPanel({ scorecardId }: { scorecardId: string }) {
  const qc = useQueryClient();
  const kit = useQuery({
    queryKey: ['interviewer', 'kit', scorecardId],
    queryFn: () => getScorecardKit(scorecardId),
  });

  const notesQuery = useQuery({
    queryKey: ['interviewer', 'notes', scorecardId],
    queryFn: () => getPrivateNotes(scorecardId),
  });
  const [notes, setNotes] = useState('');
  const [notesHydrated, setNotesHydrated] = useState(false);
  useEffect(() => {
    if (notesQuery.data && !notesHydrated) {
      setNotes(notesQuery.data.notes);
      setNotesHydrated(true);
    }
  }, [notesQuery.data, notesHydrated]);

  const saveNotes = useMutation({
    mutationFn: () => savePrivateNotes(scorecardId, notes),
    onSuccess: (res, _vars) => {
      // Written into the cache rather than refetched: the "Saved …" line must
      // follow this save, and a refetch would race the text still being typed.
      qc.setQueryData(['interviewer', 'notes', scorecardId], { notes, updated_at: res.updated_at });
      toast.success('Private notes saved');
    },
    onError: (e) => toast.error(errOrWithdrawn(e, 'Could not save your notes.')),
  });

  if (kit.isLoading) {
    return (
      <div className="flex flex-col gap-3 py-2" role="status" aria-label="Loading the interview kit">
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-20 w-full rounded-card" />
        <Skeleton className="h-20 w-full rounded-card" />
      </div>
    );
  }
  if (kit.isError || !kit.data) {
    return (
      <p className="py-6 text-[13px] text-[var(--ui-danger)]">
        {errOrWithdrawn(kit.error, 'Could not load the interview kit')}
      </p>
    );
  }
  const data = kit.data;

  return (
    <div className="flex flex-col gap-5">
      {data.instructions ? (
        <section>
          <h3 className="text-[12px] font-semibold uppercase tracking-wide text-[var(--ui-faint)]">
            Instructions
          </h3>
          <p className="mt-1.5 whitespace-pre-wrap text-[13.5px] leading-relaxed text-[var(--ui-soft)]">
            {data.instructions}
          </p>
        </section>
      ) : null}

      {data.interviewer_notes_from_hr ? (
        <section>
          <h3 className="text-[12px] font-semibold uppercase tracking-wide text-[var(--ui-faint)]">
            HR&rsquo;s notes for interviewers
          </h3>
          <p className="mt-1.5 whitespace-pre-wrap text-[13.5px] leading-relaxed text-[var(--ui-soft)]">
            {data.interviewer_notes_from_hr}
          </p>
        </section>
      ) : null}

      {!data.has_custom_kit ? (
        <p className="flex items-start gap-1.5 text-[12px] leading-relaxed text-muted-foreground">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          HR has not customised this round&rsquo;s kit yet - here is what the rubric assesses.
        </p>
      ) : null}

      <section className="flex flex-col gap-3">
        {data.criteria.map((c) => (
          <div key={c.competency_id} className="rounded-[14px] border border-border p-3.5">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-[13.5px] font-medium text-foreground">{c.competency_name}</span>
              <span className="text-[12px] text-[var(--ui-faint)]">
                weight {c.weight.toFixed(2)}
              </span>
            </div>

            {c.what_to_evaluate.length > 0 ? (
              <div className="mt-2">
                <p className="text-[12px] font-medium text-[var(--ui-faint)]">What to evaluate</p>
                <ul className="mt-1 list-disc pl-4 text-[12.5px] leading-relaxed text-[var(--ui-soft)]">
                  {c.what_to_evaluate.map((x, i) => (
                    <li key={i}>{x}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {c.look_for.length > 0 ? (
              <div className="mt-2">
                <p className="text-[12px] font-medium text-[var(--ui-faint)]">Look for</p>
                <ul className="mt-1 list-disc pl-4 text-[12.5px] leading-relaxed text-[var(--ui-soft)]">
                  {c.look_for.map((x, i) => (
                    <li key={i}>{x}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {c.frozen_probes.length > 0 || c.probes.length > 0 ? (
              <div className="mt-2">
                <p className="text-[12px] font-medium text-[var(--ui-faint)]">
                  Suggested probes (guidance, not mandatory questions)
                </p>
                <ul className="mt-1 list-disc pl-4 text-[12.5px] leading-relaxed text-[var(--ui-soft)]">
                  {[...c.frozen_probes, ...c.probes].map((x, i) => (
                    <li key={i}>{x}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {c.anchors ? (
              <dl className="mt-2.5 grid grid-cols-1 gap-1.5 border-t border-border pt-2 sm:grid-cols-3">
                {(['low', 'mid', 'high'] as const).map((band) => (
                  <div key={band}>
                    <dt className="text-[12px] font-medium uppercase tracking-wide text-[var(--ui-faint)]">
                      {band === 'low' ? 'Weak' : band === 'mid' ? 'Adequate' : 'Strong'}
                    </dt>
                    <dd className="mt-0.5 text-[12px] leading-relaxed text-[var(--ui-soft)]">
                      {c.anchors ? c.anchors[band] : ''}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : null}
          </div>
        ))}
      </section>

      <section className="rounded-[14px] border border-[var(--ui-warn)]/25 bg-[var(--ui-warn)]/[0.05] p-3.5">
        <div className="flex items-center gap-1.5">
          <Lock className="h-3.5 w-3.5 text-[var(--ui-warn)]" aria-hidden="true" />
          <label htmlFor="private-notes" className="text-[12.5px] font-medium text-foreground">
            Private notes - not shared with HR, not part of your scorecard
          </label>
        </div>
        {notesQuery.isError ? (
          <p className="mt-2 text-[12.5px] text-[var(--ui-danger)]">
            {errOrWithdrawn(notesQuery.error, 'Could not load your notes')}
          </p>
        ) : (
          <>
            <textarea
              id="private-notes"
              value={notes}
              maxLength={MAX_NOTES}
              onChange={(e) => setNotes(e.target.value)}
              rows={6}
              placeholder="Jot anything down while you interview - only you can see this."
              className="mt-2 w-full resize-y rounded-[10px] border border-input bg-card px-3.5 py-2.5 text-[14px] text-foreground placeholder:text-[var(--ui-faint)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            />
            <div className="mt-2 flex items-center justify-between">
              <span className="text-[12px] text-[var(--ui-faint)]">
                {notesQuery.data?.updated_at
                  ? `Saved ${formatDateTime(notesQuery.data.updated_at)}`
                  : 'Not saved yet'}
              </span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => saveNotes.mutate()}
                disabled={saveNotes.isPending}
              >
                {saveNotes.isPending ? 'Saving…' : 'Save notes'}
              </Button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}

/* ── Scorecard panel ─────────────────────────────────────────────────────── */

function ScoreControl({
  competencyName,
  value,
  disabled,
  onChange,
}: {
  /** Folded into both accessible names, so two criteria never share one. */
  competencyName: string;
  value: { score: number | null; not_assessed: boolean };
  disabled: boolean;
  onChange: (next: { score: number | null; not_assessed: boolean }) => void;
}) {
  // The ARIA radio-group pattern, because role="radiogroup" promises it: one
  // Tab stop (the checked score, or 1), arrow keys move AND select, Home/End
  // jump to the ends. Five separate Tab stops that ignore arrows would tell a
  // screen-reader user one thing and do another.
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const selected = !value.not_assessed && value.score ? value.score : null;
  const tabStop = selected ?? 1;

  function onKeyDown(e: KeyboardEvent<HTMLButtonElement>, n: number) {
    let next: number | null = null;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = n === 5 ? 1 : n + 1;
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = n === 1 ? 5 : n - 1;
    else if (e.key === 'Home') next = 1;
    else if (e.key === 'End') next = 5;
    if (next === null) return;
    e.preventDefault();
    onChange({ score: next, not_assessed: false });
    buttons.current[next - 1]?.focus();
  }

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
      <div>
        <div
          role="radiogroup"
          aria-label={`Score - ${competencyName}`}
          aria-required={!value.not_assessed}
          className="flex items-center gap-2"
        >
          {[1, 2, 3, 4, 5].map((n) => {
            const checked = !value.not_assessed && value.score === n;
            return (
              <button
                key={n}
                ref={(el) => {
                  buttons.current[n - 1] = el;
                }}
                type="button"
                role="radio"
                aria-checked={checked}
                aria-label={`Score ${n}`}
                tabIndex={n === tabStop ? 0 : -1}
                disabled={disabled || value.not_assessed}
                onKeyDown={(e) => onKeyDown(e, n)}
                onClick={() => onChange({ score: n, not_assessed: false })}
                className={cn(
                  'relative flex h-11 w-11 items-center justify-center rounded-[10px] border text-[15px] font-semibold tabular-nums transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-background',
                  // Selected is a fill, a heavier ring AND a check mark, so it
                  // does not depend on telling two blues apart.
                  checked
                    ? 'border-primary bg-primary text-primary-foreground ring-2 ring-primary/30'
                    : 'border-input bg-card text-foreground hover:border-[var(--ui-line-strong)] hover:bg-[var(--ui-inset-soft)]',
                  (disabled || value.not_assessed) && 'cursor-not-allowed opacity-40',
                )}
              >
                {n}
                {checked ? (
                  <span
                    aria-hidden="true"
                    className="absolute -right-1.5 -top-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-card text-primary ring-1 ring-primary"
                  >
                    <Check size={10} strokeWidth={3} />
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
        <p className="mt-1.5 text-[12px] text-muted-foreground" aria-hidden="true">
          1 = lowest · 5 = highest
        </p>
      </div>
      <label className="flex min-h-10 items-center gap-2 text-[13px] text-foreground">
        {/* Locked with the score buttons (audit AD-F15): on a submitted or
            superseded scorecard it used to clear the score on screen. */}
        <ToggleSwitch
          checked={value.not_assessed}
          disabled={disabled}
          onChange={(next) => onChange({ score: next ? null : value.score, not_assessed: next })}
          label={`Mark ${competencyName} as not assessed`}
        />
        Not assessed
      </label>
    </div>
  );
}

const TEXTAREA =
  'mt-1.5 w-full resize-y rounded-[10px] border border-input bg-card px-3.5 py-2.5 text-[14px] text-foreground placeholder:text-[var(--ui-faint)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:cursor-not-allowed disabled:bg-[var(--ui-inset-soft)] disabled:opacity-70';

/** How many criteria are settled — scored, or explicitly marked not assessed. */
function settledCount(detail: ScorecardDetail, scores: ScoreState): number {
  return detail.criteria.filter((c) => {
    const v = scores[c.competency_id];
    return v ? v.not_assessed || v.score != null : false;
  }).length;
}

function ScorecardPanel({
  detail,
  scores,
  setScores,
  summary,
  setSummary,
  readOnly,
}: {
  detail: ScorecardDetail;
  scores: ScoreState;
  setScores: (next: ScoreState) => void;
  summary: string;
  setSummary: (next: string) => void;
  readOnly: boolean;
}) {
  return (
    <div className="flex flex-col gap-6">
      {readOnly ? null : (
        <p className="flex items-start gap-2 text-[13px] leading-relaxed text-muted-foreground">
          <Info className="mt-0.5 h-4 w-4 flex-none" aria-hidden="true" />
          <span>
            <span aria-hidden="true" className="font-semibold text-[var(--ui-danger)]">
              *
            </span>{' '}
            Required to submit: give every criterion a score, or mark it Not assessed. At least one
            criterion needs a score. Evidence and the summary are optional.
          </span>
        </p>
      )}

      {detail.criteria.map((c) => {
        const v = scores[c.competency_id] ?? { score: null, not_assessed: false, evidence: '' };
        return (
          <section
            key={c.competency_id}
            aria-labelledby={`crit-${c.competency_id}`}
            className="rounded-[14px] border border-border bg-card p-5"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
              <h3 id={`crit-${c.competency_id}`} className="text-[15px] font-bold text-foreground">
                {c.competency_name}
              </h3>
              <span className="text-[12.5px] tabular-nums text-muted-foreground">
                weight {c.weight.toFixed(2)}
              </span>
            </div>
            {c.anchors ? (
              <dl className="mt-3 grid gap-2 rounded-[10px] bg-[var(--ui-inset-soft)] p-3 sm:grid-cols-3">
                {(['low', 'mid', 'high'] as const).map((band) => (
                  <div key={band}>
                    <dt className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {band === 'low' ? 'Weak' : band === 'mid' ? 'Adequate' : 'Strong'}
                    </dt>
                    <dd className="mt-0.5 text-[12.5px] leading-relaxed text-foreground">
                      {c.anchors ? c.anchors[band] : ''}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : null}

            <div className="mt-4">
              <p className="mb-2 text-[13px] font-medium text-foreground">
                Score
                {readOnly ? null : (
                  <span aria-hidden="true" className="ml-0.5 text-[var(--ui-danger)]">
                    *
                  </span>
                )}
              </p>
              <ScoreControl
                competencyName={c.competency_name}
                value={v}
                disabled={readOnly}
                onChange={(next) => setScores({ ...scores, [c.competency_id]: { ...v, ...next } })}
              />
            </div>

            <div className="mt-4">
              <label
                htmlFor={`evidence-${c.competency_id}`}
                className="text-[13px] font-medium text-foreground"
              >
                Evidence
              </label>
              <textarea
                id={`evidence-${c.competency_id}`}
                value={v.evidence}
                disabled={readOnly}
                maxLength={MAX_EVIDENCE}
                onChange={(e) =>
                  setScores({ ...scores, [c.competency_id]: { ...v, evidence: e.target.value } })
                }
                rows={2}
                className={TEXTAREA}
              />
            </div>
          </section>
        );
      })}

      <section className="rounded-[14px] border border-border bg-card p-5">
        <label htmlFor="scorecard-summary" className="text-[15px] font-bold text-foreground">
          Overall summary
        </label>
        <p className="mt-0.5 text-[12.5px] text-muted-foreground">Optional.</p>
        <textarea
          id="scorecard-summary"
          value={summary}
          disabled={readOnly}
          maxLength={MAX_SUMMARY}
          onChange={(e) => setSummary(e.target.value)}
          rows={4}
          className={TEXTAREA}
        />
      </section>
    </div>
  );
}

/* ── Page ───────────────────────────────────────────────────────────────── */

// A scorecard id is a UUID, and nothing else is used. React Router decodes %2F
// inside a route parameter, so a crafted link like
// /interviewer/scorecards/..%2F..%2Fhr%2F... would otherwise become an id of
// "../../hr/..." and steer this page's authenticated requests at another API
// path. The API client validates the id too; this refuses it before any request.
export default function InterviewerScorecard(): JSX.Element {
  const { scorecardId: rawId = '' } = useParams();
  if (!isUuid(rawId)) {
    return (
      <div className="p-8 text-[13px] text-muted-foreground">
        {rawId ? 'Scorecard not found.' : 'No scorecard selected.'}
      </div>
    );
  }
  // Keyed by id. Opening a correction navigates to a new id on the SAME route,
  // and React Router keeps the element mounted for a param-only change — so
  // without the key every piece of local state (the drafted scores, an error
  // banner, "already hydrated") would carry over to a different scorecard.
  return <ScorecardView key={rawId} scorecardId={rawId} />;
}

function ScorecardView({ scorecardId }: { scorecardId: string }): JSX.Element {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [tab, setTab] = useState<'kit' | 'scorecard' | 'submission'>('kit');
  const [scores, setScores] = useState<ScoreState>({});
  const [summary, setSummary] = useState('');
  const [hydrated, setHydrated] = useState(false);
  const [confirmSubmit, setConfirmSubmit] = useState(false);
  const [correcting, setCorrecting] = useState(false);
  const [correctionReason, setCorrectionReason] = useState('');
  const [formError, setFormError] = useState<string | null>(null);
  // What the server last held, to tell unsaved edits from saved ones. Drafts
  // live only in this page until "Save draft"; leaving by a breadcrumb, the
  // sidebar, Back or a reload used to discard them silently (audit AD-F14).
  const [saved, setSaved] = useState<string | null>(null);
  const guard = useUnsavedGuard();

  const detailQuery = useQuery({
    queryKey: ['interviewer', 'scorecard', scorecardId],
    queryFn: () => getScorecard(scorecardId),
  });
  const detail = detailQuery.data;

  useEffect(() => {
    if (detail && !hydrated) {
      const initial = initScores(detail);
      setScores(initial);
      setSummary(detail.summary ?? '');
      setSaved(snapshot(initial, detail.summary ?? ''));
      setHydrated(true);
    }
  }, [detail, hydrated]);

  const current = snapshot(scores, summary);
  const editable = detail?.can_edit === true;
  const { setDirty } = guard;
  useEffect(() => {
    setDirty(hydrated && editable && saved !== null && current !== saved);
  }, [current, saved, hydrated, editable, setDirty]);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['interviewer', 'scorecard', scorecardId] });
    void qc.invalidateQueries({ queryKey: ['interviewer', 'assignments'] });
  };

  const saveMut = useMutation({
    mutationFn: () =>
      saveScorecard(scorecardId, {
        scores: toScoreInputs(scores),
        summary: summary.trim() || null,
      }),
    onSuccess: () => {
      toast.success('Draft saved');
      setFormError(null);
      setSaved(current);
      invalidate();
    },
    onError: (e) => {
      // A withdrawn assignment reads as gone, as it does on load (AD-F30).
      const msg = errOrWithdrawn(e, 'Could not save this draft.');
      setFormError(msg);
      toast.error(msg);
    },
  });

  const submitMut = useMutation({
    mutationFn: () =>
      submitScorecard(scorecardId, {
        scores: toScoreInputs(scores),
        summary: summary.trim() || null,
      }),
    onSuccess: (res) => {
      setConfirmSubmit(false);
      setFormError(null);
      setSaved(current);
      toast.success(res.late ? 'Submitted - after the due date' : 'Scorecard submitted');
      invalidate();
    },
    onError: (e) => {
      setConfirmSubmit(false);
      const msg = errOrWithdrawn(e, 'Could not submit this scorecard.');
      setFormError(msg);
      toast.error(msg);
    },
  });

  const correctionMut = useMutation({
    mutationFn: () => requestCorrection(scorecardId, correctionReason),
    onSuccess: (res) => {
      toast.success('Correction started - the original is kept');
      void navigate(`/interviewer/scorecards/${res.scorecard_id}`);
    },
    onError: (e) => toast.error(errOrWithdrawn(e, 'Could not start a correction.')),
  });

  if (detailQuery.isLoading) {
    return (
      <Page width="form">
        <div className="flex flex-col gap-4 py-4" role="status" aria-label="Loading scorecard">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-32 w-full rounded-card" />
          <Skeleton className="h-32 w-full rounded-card" />
          <span className="sr-only">Loading scorecard…</span>
        </div>
      </Page>
    );
  }
  if (detailQuery.isError || !detail) {
    return (
      <Page width="form">
        <GlassCard className="p-6 text-[13.5px] text-[var(--ui-danger)]">
          {errOrWithdrawn(detailQuery.error, 'Could not load this scorecard')}
        </GlassCard>
      </Page>
    );
  }

  const readOnly = !detail.can_edit;
  const correctionReasonReady =
    correctionReason.trim().length >= MIN_CORRECTION_REASON &&
    correctionReason.trim().length <= MAX_CORRECTION_REASON;

  return (
    <Page width="form">
      <PageHeader
        breadcrumbs={
          <Breadcrumbs
            items={[{ label: 'My interviews', to: '/interviewer' }, { label: 'Scorecard' }]}
          />
        }
        title={detail.candidate_name}
        description={
          <>
            {detail.job_title} &middot; {detail.round_title}
          </>
        }
        actions={
          // Same tones as the assignments list (AD-F30): late is ember there.
          <StatusTag
            tone={
              detail.status === 'submitted' ? 'forest' : detail.state === 'late' ? 'ember' : 'amber'
            }
            dot
          >
            {detail.status === 'submitted'
              ? 'Submitted'
              : detail.state === 'late'
                ? 'Late'
                : 'In progress'}
          </StatusTag>
        }
      />

      {detail.superseded ? (
        <div
          role="status"
          className="flex items-start gap-2 rounded-[12px] border border-border bg-[var(--ui-inset)] p-3 text-[12.5px] leading-relaxed text-[var(--ui-soft)]"
        >
          <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          This scorecard was corrected. It is kept for the record and is read-only.
        </div>
      ) : null}

      {detail.is_correction && detail.correction_reason ? (
        <div className="flex items-start gap-2 rounded-[12px] border border-[var(--ui-lavender)]/30 bg-[var(--ui-lavender-wash)] p-3 text-[12.5px] leading-relaxed text-[var(--ui-soft)]">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-[var(--ui-lavender)]" aria-hidden="true" />
          Correcting the earlier scorecard: {detail.correction_reason}
        </div>
      ) : null}

      {/* PH4-D2 — the ONE thing this page ever says about a candidate's
          accommodations: the interviewer note, when one is effective for this
          round. Nothing else about accommodations reaches this payload — no
          basis, no internal note, no who recorded it — so there is nothing
          else to render here. */}
      {detail.adjustments_note ? (
        <div
          role="note"
          aria-label="Adjustments"
          className="flex items-start gap-2 rounded-[12px] border border-border bg-[var(--ui-inset)] p-3 text-[12.5px] leading-relaxed text-[var(--ui-soft)]"
        >
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-[var(--ui-info)]" aria-hidden="true" />
          <span>
            <span className="font-medium text-foreground">Adjustments: </span>
            {detail.adjustments_note}
          </span>
        </div>
      ) : null}

      {formError ? (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-[12px] border border-[var(--ui-danger)]/30 bg-[var(--ui-danger-wash)] p-3 text-[12.5px] leading-relaxed text-[var(--ui-soft)]"
        >
          <AlertTriangle
            className="mt-0.5 h-4 w-4 shrink-0 text-[var(--ui-danger)]"
            aria-hidden="true"
          />
          {formError}
        </div>
      ) : null}

      <GlassCard className="p-5">
        <SegTabs
          tabs={[
            { key: 'kit', label: 'Interview kit' },
            { key: 'scorecard', label: 'Scorecard' },
            ...(detail.round_kind && TASK_ROUND_KINDS.has(detail.round_kind)
              ? [{ key: 'submission', label: 'Submission' }]
              : []),
          ]}
          active={tab}
          onChange={(k) => setTab(k as 'kit' | 'scorecard' | 'submission')}
          className="mb-5"
        />

        {tab === 'kit' ? (
          <KitPanel scorecardId={scorecardId} />
        ) : tab === 'submission' ? (
          <SubmissionPanel scorecardId={scorecardId} />
        ) : (
          <>
            <ScorecardPanel
              detail={detail}
              scores={scores}
              setScores={setScores}
              summary={summary}
              setSummary={setSummary}
              readOnly={readOnly}
            />

            {readOnly ? (
              detail.can_correct ? (
                <div className="mt-6 border-t border-border pt-4">
                  {correcting ? (
                    <div className="flex flex-col gap-2">
                      <label
                        htmlFor="correction-reason"
                        className="text-[13px] font-medium text-foreground"
                      >
                        Why does this need correcting? (10 to 1000 characters)
                      </label>
                      <textarea
                        id="correction-reason"
                        value={correctionReason}
                        onChange={(e) => setCorrectionReason(e.target.value)}
                        maxLength={MAX_CORRECTION_REASON}
                        rows={3}
                        className={TEXTAREA}
                      />
                      <div className="flex flex-wrap items-center justify-end gap-2">
                        <Button
                          type="button"
                          variant="ghost"
                          onClick={() => {
                            setCorrecting(false);
                            setCorrectionReason('');
                          }}
                        >
                          Cancel
                        </Button>
                        <Button
                          type="button"
                          onClick={() => correctionMut.mutate()}
                          disabled={!correctionReasonReady || correctionMut.isPending}
                        >
                          {correctionMut.isPending ? 'Starting…' : 'Start correction'}
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <Button type="button" variant="outline" onClick={() => setCorrecting(true)}>
                      Request correction
                    </Button>
                  )}
                </div>
              ) : null
            ) : (
              // Pinned clear of the floating "Ask assistant" button, which
              // sits in the bottom-right corner (audit AD-F29).
              <div
                data-testid="scorecard-actions"
                className="sticky bottom-20 z-20 -mx-5 -mb-5 mt-6 rounded-b-card border-t border-border bg-card/95 px-5 py-3 backdrop-blur"
              >
                {confirmSubmit ? (
                  <div
                    role="group"
                    aria-label="Confirm submission"
                    className="mb-3 flex flex-wrap items-center gap-3 rounded-[12px] border border-border bg-[var(--ui-inset-soft)] p-3"
                  >
                    <span className="min-w-0 flex-1 text-[13px] text-foreground">
                      Submitted scorecards can&rsquo;t be edited - only corrected, with a reason,
                      and the original is kept.
                    </span>
                    <Button type="button" variant="ghost" onClick={() => setConfirmSubmit(false)}>
                      Cancel
                    </Button>
                    <Button
                      type="button"
                      onClick={() => submitMut.mutate()}
                      disabled={submitMut.isPending}
                    >
                      {submitMut.isPending ? 'Submitting…' : 'Confirm submit'}
                    </Button>
                  </div>
                ) : null}
                <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                  <p className="text-[13px] tabular-nums text-muted-foreground" role="status">
                    {settledCount(detail, scores)} of {detail.criteria.length} criteria settled
                  </p>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => saveMut.mutate()}
                      disabled={saveMut.isPending || submitMut.isPending}
                    >
                      {saveMut.isPending ? (
                        <Loader2 className="animate-spin" aria-hidden="true" />
                      ) : null}
                      Save draft
                    </Button>
                    {confirmSubmit ? null : (
                      <Button type="button" onClick={() => setConfirmSubmit(true)}>
                        <CheckCircle2 aria-hidden="true" />
                        Submit scorecard
                      </Button>
                    )}
                  </div>
                </div>
              </div>
            )}
          </>
        )}
      </GlassCard>
    </Page>
  );
}
