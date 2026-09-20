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
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Info,
  Lock,
  Loader2,
} from '@/design/components/icons';
import { GlassCard, SegTabs, StatusTag, ToggleSwitch } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { ApiError } from '@/api/client';
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

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/** A withdrawn assignment now 404s on every interviewer-facing endpoint for
 *  it (kit, notes, the scorecard itself) — read as "gone", not a generic error. */
const WITHDRAWN_MESSAGE = 'This assignment is no longer available.';

function errOrWithdrawn(e: unknown, fallback: string): string {
  if (e instanceof ApiError && e.status === 404) return WITHDRAWN_MESSAGE;
  return errText(e, fallback);
}

const MIN_CORRECTION_REASON = 10;
const MAX_CORRECTION_REASON = 1000;
const MAX_EVIDENCE = 4000;
const MAX_NOTES = 20000;

type ScoreState = Record<string, { score: number | null; not_assessed: boolean; evidence: string }>;

function initScores(detail: ScorecardDetail | undefined): ScoreState {
  const out: ScoreState = {};
  for (const c of detail?.criteria ?? []) {
    out[c.competency_id] = { score: c.score, not_assessed: c.not_assessed, evidence: c.evidence ?? '' };
  }
  return out;
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
    onError: (e) => toast.error(errText(e, 'Could not save your notes')),
  });

  if (kit.isLoading) {
    return (
      <div className="flex items-center gap-2 py-10 text-[13px] text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        Loading the interview kit…
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
          HR has not customised this round&rsquo;s kit yet — here is what the rubric assesses.
        </p>
      ) : null}

      <section className="flex flex-col gap-3">
        {data.criteria.map((c) => (
          <div key={c.competency_id} className="rounded-[14px] border border-border p-3.5">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-[13.5px] font-medium text-foreground">{c.competency_name}</span>
              <span className="text-[11px] text-[var(--ui-faint)]">weight {c.weight.toFixed(2)}</span>
            </div>

            {c.what_to_evaluate.length > 0 ? (
              <div className="mt-2">
                <p className="text-[11px] font-medium text-[var(--ui-faint)]">What to evaluate</p>
                <ul className="mt-1 list-disc pl-4 text-[12.5px] leading-relaxed text-[var(--ui-soft)]">
                  {c.what_to_evaluate.map((x, i) => (
                    <li key={i}>{x}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {c.look_for.length > 0 ? (
              <div className="mt-2">
                <p className="text-[11px] font-medium text-[var(--ui-faint)]">Look for</p>
                <ul className="mt-1 list-disc pl-4 text-[12.5px] leading-relaxed text-[var(--ui-soft)]">
                  {c.look_for.map((x, i) => (
                    <li key={i}>{x}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {c.frozen_probes.length > 0 || c.probes.length > 0 ? (
              <div className="mt-2">
                <p className="text-[11px] font-medium text-[var(--ui-faint)]">
                  Suggested probes &mdash; guidance, not mandatory questions
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
                    <dt className="text-[10.5px] font-medium uppercase tracking-wide text-[var(--ui-faint)]">
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
            Private notes — not shared with HR, not part of your scorecard
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
          placeholder="Jot anything down while you interview — only you can see this."
          className="mt-2 w-full resize-y rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none"
        />
        <div className="mt-2 flex items-center justify-between">
          <span className="text-[11px] text-[var(--ui-faint)]">
            {notesQuery.data?.updated_at
              ? `Saved ${new Date(notesQuery.data.updated_at).toLocaleString()}`
              : 'Not saved yet'}
          </span>
          <button
            type="button"
            onClick={() => saveNotes.mutate()}
            disabled={saveNotes.isPending}
            className="rounded-[10px] border border-[var(--ui-line-strong)] px-3 py-1.5 text-[12px] text-[var(--ui-soft)] hover:text-foreground disabled:opacity-50"
          >
            {saveNotes.isPending ? 'Saving…' : 'Save notes'}
          </button>
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
    <div className="flex flex-wrap items-center gap-3">
      <div role="radiogroup" aria-label={`Score — ${competencyName}`} className="flex items-center gap-1.5">
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
                'flex h-9 w-9 items-center justify-center rounded-[10px] border text-[13.5px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
                checked
                  ? 'border-[var(--accent)] bg-[rgba(var(--accent-rgb),0.16)] text-foreground'
                  : 'border-border text-[var(--ui-soft)] hover:border-[var(--ui-line-strong)]',
                (disabled || value.not_assessed) && 'cursor-not-allowed opacity-40',
              )}
            >
              {n}
            </button>
          );
        })}
      </div>
      <label className="flex items-center gap-2 text-[12.5px] text-[var(--ui-soft)]">
        <ToggleSwitch
          checked={value.not_assessed}
          onChange={(next) => onChange({ score: next ? null : value.score, not_assessed: next })}
          label={`Mark ${competencyName} as not assessed`}
        />
        Not assessed
      </label>
    </div>
  );
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
    <div className="flex flex-col gap-4">
      {detail.criteria.map((c) => {
        const v = scores[c.competency_id] ?? { score: null, not_assessed: false, evidence: '' };
        return (
          <div key={c.competency_id} className="rounded-[14px] border border-border p-4">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-[13.5px] font-medium text-foreground">{c.competency_name}</span>
              <span className="text-[11px] text-[var(--ui-faint)]">weight {c.weight.toFixed(2)}</span>
            </div>
            {c.anchors ? (
              <p className="mt-1 text-[11.5px] leading-relaxed text-muted-foreground">
                Weak: {c.anchors.low} &middot; Adequate: {c.anchors.mid} &middot; Strong: {c.anchors.high}
              </p>
            ) : null}

            <div className="mt-3">
              <ScoreControl
                competencyName={c.competency_name}
                value={v}
                disabled={readOnly}
                onChange={(next) => setScores({ ...scores, [c.competency_id]: { ...v, ...next } })}
              />
            </div>

            <div className="mt-3">
              <label htmlFor={`evidence-${c.competency_id}`} className="text-[12px] font-medium text-[var(--ui-soft)]">
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
                className="mt-1 w-full resize-y rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none disabled:opacity-60"
              />
            </div>
          </div>
        );
      })}

      <div>
        <label htmlFor="scorecard-summary" className="text-[12px] font-medium text-[var(--ui-soft)]">
          Overall summary
        </label>
        <textarea
          id="scorecard-summary"
          value={summary}
          disabled={readOnly}
          onChange={(e) => setSummary(e.target.value)}
          rows={4}
          className="mt-1 w-full resize-y rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none disabled:opacity-60"
        />
      </div>
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
  const [tab, setTab] = useState<'kit' | 'scorecard'>('kit');
  const [scores, setScores] = useState<ScoreState>({});
  const [summary, setSummary] = useState('');
  const [hydrated, setHydrated] = useState(false);
  const [confirmSubmit, setConfirmSubmit] = useState(false);
  const [correcting, setCorrecting] = useState(false);
  const [correctionReason, setCorrectionReason] = useState('');
  const [formError, setFormError] = useState<string | null>(null);

  const detailQuery = useQuery({
    queryKey: ['interviewer', 'scorecard', scorecardId],
    queryFn: () => getScorecard(scorecardId),
  });
  const detail = detailQuery.data;

  useEffect(() => {
    if (detail && !hydrated) {
      setScores(initScores(detail));
      setSummary(detail.summary ?? '');
      setHydrated(true);
    }
  }, [detail, hydrated]);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['interviewer', 'scorecard', scorecardId] });
    void qc.invalidateQueries({ queryKey: ['interviewer', 'assignments'] });
  };

  const saveMut = useMutation({
    mutationFn: () =>
      saveScorecard(scorecardId, { scores: toScoreInputs(scores), summary: summary.trim() || null }),
    onSuccess: () => {
      toast.success('Draft saved');
      setFormError(null);
      invalidate();
    },
    onError: (e) => {
      const msg = errText(e, 'Could not save this draft');
      setFormError(msg);
      toast.error(msg);
    },
  });

  const submitMut = useMutation({
    mutationFn: () =>
      submitScorecard(scorecardId, { scores: toScoreInputs(scores), summary: summary.trim() || null }),
    onSuccess: (res) => {
      setConfirmSubmit(false);
      setFormError(null);
      toast.success(res.late ? 'Submitted — after the due date' : 'Scorecard submitted');
      invalidate();
    },
    onError: (e) => {
      setConfirmSubmit(false);
      const msg = errText(e, 'Could not submit this scorecard');
      setFormError(msg);
      toast.error(msg);
    },
  });

  const correctionMut = useMutation({
    mutationFn: () => requestCorrection(scorecardId, correctionReason),
    onSuccess: (res) => {
      toast.success('Correction started — the original is kept');
      void navigate(`/interviewer/scorecards/${res.scorecard_id}`);
    },
    onError: (e) => toast.error(errText(e, 'Could not start a correction')),
  });

  if (detailQuery.isLoading) {
    return (
      <div className="mx-auto flex w-full max-w-[900px] items-center gap-2 px-4 py-16 text-[13px] text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        Loading scorecard…
      </div>
    );
  }
  if (detailQuery.isError || !detail) {
    return (
      <div className="mx-auto w-full max-w-[900px] px-4 py-8">
        <GlassCard className="p-6 text-[13.5px] text-[var(--ui-danger)]">
          {errOrWithdrawn(detailQuery.error, 'Could not load this scorecard')}
        </GlassCard>
      </div>
    );
  }

  const readOnly = !detail.can_edit;
  const correctionReasonReady =
    correctionReason.trim().length >= MIN_CORRECTION_REASON &&
    correctionReason.trim().length <= MAX_CORRECTION_REASON;

  return (
    <div className="mx-auto w-full max-w-[900px] px-4 py-8">
      <Reveal>
        <header className="mb-6">
          <Link
            to="/interviewer"
            className="mb-2 inline-flex items-center gap-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
            My interviews
          </Link>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-[24px] font-semibold tracking-[-0.8px] text-foreground">
              {detail.candidate_name}
            </h1>
            <StatusTag tone={detail.status === 'submitted' ? 'forest' : 'amber'} dot>
              {detail.status === 'submitted' ? 'Submitted' : detail.state === 'late' ? 'Late' : 'In progress'}
            </StatusTag>
          </div>
          <p className="mt-1 text-[13px] text-muted-foreground">
            {detail.job_title} &middot; {detail.round_title}
          </p>
        </header>
      </Reveal>

      {detail.superseded ? (
        <div
          role="status"
          className="mb-5 flex items-start gap-2 rounded-[12px] border border-border bg-[var(--ui-inset)] p-3 text-[12.5px] leading-relaxed text-[var(--ui-soft)]"
        >
          <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          This scorecard was corrected. It is kept for the record and is read-only.
        </div>
      ) : null}

      {detail.is_correction && detail.correction_reason ? (
        <div className="mb-5 flex items-start gap-2 rounded-[12px] border border-[var(--ui-lavender)]/30 bg-[var(--ui-lavender-wash)] p-3 text-[12.5px] leading-relaxed text-[var(--ui-soft)]">
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
          className="mb-5 flex items-start gap-2 rounded-[12px] border border-border bg-[var(--ui-inset)] p-3 text-[12.5px] leading-relaxed text-[var(--ui-soft)]"
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
          className="mb-5 flex items-start gap-2 rounded-[12px] border border-[var(--ui-danger)]/30 bg-[var(--ui-danger-wash)] p-3 text-[12.5px] leading-relaxed text-[var(--ui-soft)]"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--ui-danger)]" aria-hidden="true" />
          {formError}
        </div>
      ) : null}

      <GlassCard className="p-5">
        <SegTabs
          tabs={[
            { key: 'kit', label: 'Interview kit' },
            { key: 'scorecard', label: 'Scorecard' },
          ]}
          active={tab}
          onChange={(k) => setTab(k as 'kit' | 'scorecard')}
          className="mb-5"
        />

        {tab === 'kit' ? (
          <KitPanel scorecardId={scorecardId} />
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
                <div className="mt-5 border-t border-border pt-4">
                  {correcting ? (
                    <div className="flex flex-col gap-2">
                      <label htmlFor="correction-reason" className="text-[12px] font-medium text-[var(--ui-soft)]">
                        Why does this need correcting? (10&ndash;1000 characters)
                      </label>
                      <textarea
                        id="correction-reason"
                        value={correctionReason}
                        onChange={(e) => setCorrectionReason(e.target.value)}
                        maxLength={MAX_CORRECTION_REASON}
                        rows={3}
                        className="w-full resize-y rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
                      />
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => correctionMut.mutate()}
                          disabled={!correctionReasonReady || correctionMut.isPending}
                          className="rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
                        >
                          {correctionMut.isPending ? 'Starting…' : 'Start correction'}
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setCorrecting(false);
                            setCorrectionReason('');
                          }}
                          className="rounded-[10px] border border-[var(--ui-line-strong)] px-4 py-2 text-[12.5px] text-[var(--ui-soft)] hover:text-foreground"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setCorrecting(true)}
                      className="rounded-[10px] border border-[var(--ui-line-strong)] px-4 py-2 text-[12.5px] text-[var(--ui-soft)] hover:text-foreground"
                    >
                      Request correction
                    </button>
                  )}
                </div>
              ) : null
            ) : (
              <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-border pt-4">
                <button
                  type="button"
                  onClick={() => saveMut.mutate()}
                  disabled={saveMut.isPending || submitMut.isPending}
                  className="inline-flex items-center gap-1.5 rounded-[10px] border border-[var(--ui-line-strong)] px-4 py-2 text-[12.5px] text-[var(--ui-soft)] hover:text-foreground disabled:opacity-50"
                >
                  {saveMut.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : null}
                  Save draft
                </button>

                {confirmSubmit ? (
                  <div className="flex w-full flex-wrap items-center gap-2 rounded-[12px] border border-border bg-[var(--ui-inset)] p-3">
                    <span className="flex-1 text-[12.5px] text-[var(--ui-soft)]">
                      Submitted scorecards can&rsquo;t be edited — only corrected, with a reason, and
                      the original is kept.
                    </span>
                    <button
                      type="button"
                      onClick={() => submitMut.mutate()}
                      disabled={submitMut.isPending}
                      className="rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
                    >
                      {submitMut.isPending ? 'Submitting…' : 'Confirm submit'}
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmSubmit(false)}
                      className="rounded-[10px] border border-[var(--ui-line-strong)] px-4 py-2 text-[12.5px] text-[var(--ui-soft)] hover:text-foreground"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setConfirmSubmit(true)}
                    className="inline-flex items-center gap-1.5 rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground hover:opacity-90"
                  >
                    <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                    Submit scorecard
                  </button>
                )}
              </div>
            )}
          </>
        )}
      </GlassCard>
    </div>
  );
}
