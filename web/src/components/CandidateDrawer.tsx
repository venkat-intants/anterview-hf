// CandidateDrawer — one applicant, everything about them, without leaving the board.
//
// §6.5. The content all existed already; what did not exist was a place to
// read it together. A recruiter deciding on somebody was opening three pages
// and holding the fourth in their head.
//
// TWO THINGS THIS SURFACES THAT NOTHING ELSE DOES
//
// The name the candidate typed, when the CV disagrees. The reconciler no
// longer overwrites a typed name with the parsed one, so the two can differ —
// and if the drawer showed only one of them, the discrepancy would be
// invisible to the only person who could resolve it.
//
// Their answers to the opening's questions, which are otherwise write-only:
// candidates fill them in and nobody could read them back.
//
// AND ONE THING IT DELIBERATELY DOES NOT DO
// There is no Advance button here. Moving a candidate goes through the
// enrolment endpoint, which records who moved them and why; a shortcut on a
// read surface is how that ledger acquires gaps.

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getApplicant, listApplications } from '@/api/applicants';
import { listAnswers, type ApplicationAnswer } from '@/api/questions';
import { listRoundResults, type RoundResult } from '@/api/applicants';
import { getEnrolmentHistory } from '@/api/requisitions';
import { getWorkflow, HUMAN_EVALUATED_KINDS, listWorkflows } from '@/api/workflows';
import {
  assignInterviewers,
  getEnrolmentScorecards,
  listInterviewers,
  withdrawScorecard,
  type EnrolmentScorecard,
} from '@/api/scorecards';
import { formatDate } from '@/lib/formatters';
import { describeMove } from '@/lib/stageHistory';
import { toast } from '@/lib/toast';
import { StatusTag, type TagTone } from '@/design/components/primitives';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import AccommodationsSection from '@/components/hr/AccommodationsSection';
import TaskSubmissionSection from '@/components/hr/TaskSubmissionSection';
import CodeEvidenceCounts from '@/components/hr/CodeEvidenceCounts';
import ExceptionsSection from '@/components/ExceptionsSection';
import InterviewLoopsSection from '@/components/InterviewLoopsSection';
import OfferSection from '@/components/OfferSection';
import CheckinSection from '@/components/hr/CheckinSection';
import { getMetricDefinitions, sourceLabel } from '@/api/metrics';
import { AlertTriangle, ExternalLink, Info } from '@/design/components/icons';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { DataList } from '@/design/components/page';
import { Collapsible, ProgressiveList } from '@/design/components/collapsible';
import { useScopedPersistKey } from '@/hooks/useScopedPersistKey';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import ViewCvButton from '@/components/hr/ViewCvButton';

// Each block of the drawer is a titled section divided from the last by a
// hairline — the drawer is a long read, so the rhythm does the grouping.
const SECTION = 'mt-6 border-t border-border pt-5 outline-none';
const SECTION_TITLE = 'text-[15px] font-bold text-foreground';
const CONTROL =
  'w-full rounded-[12px] border border-input bg-card px-3.5 py-2.5 text-[14px] text-foreground focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]/30';

export interface DrawerCandidate {
  applicant_id: string;
  /** A CV file is stored (offer "View CV"). */
  has_cv?: boolean;
  full_name: string;
  email?: string | null;
  target_job_title?: string;
  status?: string;
  ats_overall?: number | null;
  ats_recommendation?: string | null;
  ats_strengths?: string[] | null;
  ats_concerns?: string[] | null;
  ats_summary?: string | null;
  /** Present only when the CV disagrees with the name on file. */
  parsed_full_name?: string | null;
  full_name_source?: string | null;
  phone?: string | null;
  years_experience?: number | null;
  current_company?: string | null;
  current_title?: string | null;
  linkedin_url?: string | null;
  github_url?: string | null;
  current_round_title?: string | null;
  /** Where THIS application came from (PH5-C1/wave-1 follow-up B) — a code
   *  from the closed source vocabulary, e.g. "referral" or "job_board". */
  source?: string | null;
}

/* ── Why a score is what it is (§7, C8) ─────────────────────────────────── */

/** Colour by band, but never colour ALONE — the number is always present. */
function scoreTone(pct: number | null): string {
  if (pct === null) return 'text-muted-foreground';
  if (pct >= 75) return 'text-[var(--ui-ok)]';
  if (pct >= 50) return 'text-[var(--ui-warn)]';
  return 'text-[var(--ui-danger)]';
}

/**
 * Per-round scores, criterion by criterion.
 *
 * The specification's §7 is explicit that a score must not be presented as an
 * unexplained truth, and the data to explain it has existed since C8 — per
 * criterion, each with the evidence the model cited. Nothing read it back, so
 * the console showed a composite and no way to ask what produced it.
 *
 * Two layers, shown in that order and labelled differently on purpose. The
 * criteria decided whether this candidate advanced; the axes are the frozen
 * comparison that makes composites mean the same thing across roles. Reading
 * the axes as the reason for a decision would be wrong, so they are visually
 * secondary.
 *
 * `passed` is rendered as "advanced / held", never as pass/fail: under D-05 a
 * candidate below a threshold is held and nothing here has ended their
 * candidacy.
 */
function RoundScores({
  applicantId,
  enrolmentId,
}: {
  applicantId: string;
  /** Scopes the results to one application; a person may have several. */
  enrolmentId?: string | null;
}) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['hr', 'applicant', applicantId, 'round-results', enrolmentId ?? null],
    queryFn: () => listRoundResults(applicantId, enrolmentId),
  });

  if (isLoading) {
    return (
      <div id="round-results" tabIndex={-1} className={SECTION}>
        <h3 className={SECTION_TITLE}>Assessment</h3>
        <Skeleton className="mt-2 h-16 rounded-[12px]" />
      </div>
    );
  }
  // A failed read must not imply "not assessed" — those are different facts and
  // one of them would mislead a decision.
  if (isError) {
    return (
      <div id="round-results" tabIndex={-1} className={SECTION}>
        <h3 className={SECTION_TITLE}>Assessment</h3>
        <p role="alert" className="mt-1.5 text-[13px] text-muted-foreground">
          Scores could not be loaded. Reopen to retry.
        </p>
      </div>
    );
  }
  if (!data?.length) return null;

  return (
    <div id="round-results" tabIndex={-1} className={SECTION}>
      <h3 className={SECTION_TITLE}>Assessment</h3>
      <ul className="mt-2 flex flex-col gap-3">
        {data.map((r: RoundResult) => (
          <li key={r.round_id} className="rounded-[16px] border border-border p-3.5">
            <div className="flex items-baseline justify-between gap-3">
              <span className="min-w-0 truncate text-[12.5px] text-foreground">
                {r.position + 1}. {r.round_title}
              </span>
              <span className={cn('text-[15px] font-semibold', scoreTone(r.percent))}>
                {r.percent === null ? '-' : `${Math.round(r.percent)}%`}
              </span>
            </div>

            <div className="mt-1 flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
              <span>
                {r.graded_by === 'ai'
                  ? 'AI-graded'
                  : r.graded_by === 'human'
                    ? 'Reviewed by a person'
                    : 'Auto-graded'}
              </span>
              {r.passed !== null ? (
                <span className={r.passed ? 'text-[var(--ui-ok)]' : 'text-[var(--ui-warn)]'}>
                  · {r.passed ? 'advanced' : 'held for your decision'}
                </span>
              ) : null}
            </div>

            {r.criteria.length ? (
              <ul className="mt-2.5 flex flex-col gap-1.5">
                {r.criteria.map((c) => (
                  <li key={c.competency_id}>
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="min-w-0 truncate text-[12px] text-[var(--ui-soft)]">
                        {c.name}
                      </span>
                      <span className={cn('text-[12px]', scoreTone(c.score))}>
                        {c.score === null ? '-' : Math.round(c.score)}
                      </span>
                    </div>
                    {c.evidence ? (
                      <p className="mt-0.5 text-[12px] leading-relaxed text-muted-foreground">
                        {c.evidence}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}

            {Object.keys(r.axes).length ? (
              <div className="mt-2.5 border-t border-border pt-2">
                <p className="text-[12px] uppercase tracking-wide text-muted-foreground">
                  Comparable axes
                </p>
                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
                  {Object.entries(r.axes).map(([axis, v]) => (
                    <span key={axis} className="text-[12px] text-muted-foreground">
                      {axis} <span className="text-[var(--ui-soft)]">{Math.round(v)}</span>
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ── Human interview (D4-1) ──────────────────────────────────────────────
 *
 * Everything about the human-review round(s) on this application: who is
 * assigned, their state, and — once submitted — their scores. Scorecards
 * never decide anything (D-05); this is a read plus an assignment control,
 * never a hire/reject shortcut.
 */

const SCORECARD_TONE: Record<EnrolmentScorecard['state'], TagTone> = {
  assigned: 'electric',
  in_progress: 'amber',
  late: 'ember',
  submitted: 'forest',
  withdrawn: 'neutral',
};

function AssignInterviewersForm({
  enrolmentId,
  rounds,
  onAssigned,
}: {
  enrolmentId: string;
  rounds: { id: string; title: string }[];
  onAssigned: () => void;
}) {
  const [roundId, setRoundId] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [dueAt, setDueAt] = useState('');

  const interviewers = useQuery({
    queryKey: ['hr', 'interviewers'],
    queryFn: listInterviewers,
  });

  const assignMut = useMutation({
    mutationFn: () =>
      assignInterviewers(enrolmentId, {
        round_id: roundId,
        interviewer_user_ids: selected,
        ...(dueAt ? { due_at: new Date(dueAt).toISOString() } : {}),
      }),
    onSuccess: (res) => {
      // already_assigned is information, not an error — say so rather than
      // silently dropping it.
      toast.success(
        res.already_assigned.length > 0
          ? `Assigned ${res.created.length} - ${res.already_assigned.length} already had this round`
          : `Assigned ${res.created.length} interviewer${res.created.length === 1 ? '' : 's'}`,
      );
      setSelected([]);
      onAssigned();
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : 'Could not assign interviewers'),
  });

  return (
    <div className="mt-3 flex flex-col gap-3 rounded-[16px] border border-border bg-[var(--ui-inset-soft)] p-4">
      <div>
        <label htmlFor="assign-round" className="text-[13px] font-semibold text-foreground">
          Round
        </label>
        <select
          id="assign-round"
          value={roundId}
          onChange={(e) => setRoundId(e.target.value)}
          className={cn(CONTROL, 'mt-1')}
        >
          <option value="">Choose a round…</option>
          {rounds.map((r) => (
            <option key={r.id} value={r.id}>
              {r.title}
            </option>
          ))}
        </select>
      </div>

      <fieldset className="flex flex-col gap-1.5">
        <legend className="text-[13px] font-semibold text-foreground">Interviewers</legend>
        {interviewers.isLoading ? (
          <span className="text-[12px] text-muted-foreground">Loading…</span>
        ) : (interviewers.data ?? []).length === 0 ? (
          <span className="text-[12px] text-muted-foreground">No interviewers set up yet.</span>
        ) : (
          (interviewers.data ?? []).map((iv) => (
            <label
              key={iv.user_id}
              className="flex items-center gap-2 text-[12.5px] text-foreground"
            >
              <input
                type="checkbox"
                checked={selected.includes(iv.user_id)}
                onChange={(e) =>
                  setSelected((prev) =>
                    e.target.checked
                      ? [...prev, iv.user_id]
                      : prev.filter((id) => id !== iv.user_id),
                  )
                }
                className="h-4 w-4 accent-[var(--accent)]"
              />
              {iv.full_name} <span className="text-muted-foreground">({iv.email})</span>
            </label>
          ))
        )}
      </fieldset>

      <div>
        <label htmlFor="assign-due" className="text-[13px] font-semibold text-foreground">
          Due date (optional)
        </label>
        <input
          id="assign-due"
          type="datetime-local"
          value={dueAt}
          onChange={(e) => setDueAt(e.target.value)}
          className={cn(CONTROL, 'mt-1')}
        />
      </div>

      <Button
        type="button"
        size="sm"
        disabled={!roundId || selected.length === 0 || assignMut.isPending}
        onClick={() => assignMut.mutate()}
        className="self-start"
      >
        {assignMut.isPending ? 'Assigning…' : 'Assign'}
      </Button>
    </div>
  );
}

function HumanInterviewSection({
  enrolmentId,
  requisitionId,
}: {
  enrolmentId: string;
  requisitionId: string | null;
}) {
  const qc = useQueryClient();
  const [assigning, setAssigning] = useState(false);
  const [withdrawReasons, setWithdrawReasons] = useState<Record<string, string>>({});

  const scorecards = useQuery({
    queryKey: ['hr', 'enrolment', enrolmentId, 'scorecards'],
    queryFn: () => getEnrolmentScorecards(enrolmentId),
    retry: false,
  });

  // The round PICKER needs each round's `kind`, which the scorecards endpoint
  // does not carry — only the published workflow does. Fetched the same way
  // WorkflowBuilder does: list versions, then the published one in full.
  const workflows = useQuery({
    queryKey: ['hr', 'workflows', requisitionId],
    queryFn: () => listWorkflows(requisitionId as string),
    enabled: Boolean(requisitionId),
  });
  const publishedId = workflows.data?.find((w) => w.status === 'published')?.id ?? null;
  const workflow = useQuery({
    queryKey: ['hr', 'workflow', publishedId],
    queryFn: () => getWorkflow(publishedId as string),
    enabled: Boolean(publishedId),
  });
  // PH4-D4: reviewers are assigned the same way for a job_simulation or
  // portfolio round as for a human_review one — through the SAME scorecard
  // machinery (interviewer_scorecards, widened server-side to these kinds).
  // The name `humanReviewRounds` is kept: this section's own heading and
  // copy ("Human interview" / "has no human interview round") are unchanged
  // on purpose, and a `human_review` round is still the common case.
  const humanReviewRounds = (workflow.data?.rounds ?? [])
    .filter((r) => (HUMAN_EVALUATED_KINDS as readonly string[]).includes(r.kind))
    .map((r) => ({ id: r.id, title: r.title }));
  // Why assigning is not possible right now, or null when it is. Kept apart
  // from "no rounds": a failed fetch and a workflow with no interview round
  // used to render the same empty dropdown.
  const roundsLoading = workflows.isLoading || (Boolean(publishedId) && workflow.isLoading);
  const assignBlocked: string | null = !requisitionId
    ? 'This application is not on an opening with a workflow, so there is no interview round to assign.'
    : workflows.isError || workflow.isError
      ? "Could not load this opening's interview rounds. Try again in a moment."
      : roundsLoading
        ? null
        : humanReviewRounds.length === 0
          ? "This opening's published workflow has no human interview round. Add one in the workflow builder to assign interviewers."
          : null;

  const invalidate = () =>
    void qc.invalidateQueries({ queryKey: ['hr', 'enrolment', enrolmentId, 'scorecards'] });

  const withdrawMut = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) => withdrawScorecard(id, reason),
    onSuccess: () => {
      toast.success('Assignment withdrawn');
      invalidate();
    },
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : 'Could not withdraw this assignment'),
  });

  const rounds = scorecards.data?.rounds ?? [];

  return (
    <div id="human-interview" tabIndex={-1} className={SECTION}>
      <div className="flex items-center justify-between gap-2">
        <h3 className={SECTION_TITLE}>Human interview</h3>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setAssigning((v) => !v)}
          disabled={!assigning && (roundsLoading || assignBlocked !== null)}
        >
          {assigning ? 'Close' : 'Assign interviewers'}
        </Button>
      </div>
      {assignBlocked ? (
        <p className="mt-1 text-[12px] text-muted-foreground">{assignBlocked}</p>
      ) : null}

      {assigning && assignBlocked === null ? (
        <AssignInterviewersForm
          enrolmentId={enrolmentId}
          rounds={humanReviewRounds}
          onAssigned={() => {
            setAssigning(false);
            invalidate();
          }}
        />
      ) : null}

      {scorecards.isLoading ? (
        <p className="mt-2 text-[12.5px] text-muted-foreground">Loading…</p>
      ) : scorecards.isError ? (
        <p className="mt-2 text-[12.5px] text-muted-foreground">
          Could not load interview scorecards.
        </p>
      ) : rounds.length === 0 ? (
        <p className="mt-2 text-[12.5px] text-muted-foreground">
          No human interview round for this application yet.
        </p>
      ) : (
        <div className="mt-2 flex flex-col gap-3">
          {rounds.map((round) => {
            const live = round.scorecards.filter((s) => !s.superseded);
            const superseded = round.scorecards.filter((s) => s.superseded);
            return (
              <div key={round.round_id} className="rounded-[16px] border border-border p-3.5">
                <p className="text-[12.5px] font-medium text-foreground">{round.round_title}</p>
                {round.hidden_until_you_submit ? (
                  <p className="mt-1 text-[12px] text-muted-foreground">
                    Hidden until you submit your own scorecard.
                  </p>
                ) : null}
                <ul className="mt-2 flex flex-col gap-2">
                  {live.map((sc) => (
                    <li
                      key={sc.scorecard_id}
                      className="rounded-[12px] border border-border bg-[var(--ui-inset-soft)] p-3"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-[12.5px] text-foreground">{sc.interviewer_name}</span>
                        <StatusTag tone={SCORECARD_TONE[sc.state]}>{sc.state}</StatusTag>
                      </div>
                      {sc.submitted_at ? (
                        <p className="mt-0.5 text-[12px] text-muted-foreground">
                          Submitted {formatDate(sc.submitted_at)}
                        </p>
                      ) : sc.due_at && sc.state !== 'withdrawn' ? (
                        <p className="mt-0.5 text-[12px] text-muted-foreground">
                          Due {formatDate(sc.due_at)}
                        </p>
                      ) : null}
                      {sc.is_correction && sc.correction_reason ? (
                        <p className="mt-1 text-[12px] text-[var(--ui-lavender)]">
                          Corrected: {sc.correction_reason}
                        </p>
                      ) : null}
                      {sc.is_correction && sc.corrected_after_peers_visible ? (
                        <p className="mt-1 flex items-center gap-1 text-[12px] text-muted-foreground">
                          <Info size={11} aria-hidden="true" />
                          Corrected after other scorecards were visible
                        </p>
                      ) : null}
                      {sc.withdrawn_reason ? (
                        <p className="mt-1 text-[12px] text-muted-foreground">
                          Withdrawn: {sc.withdrawn_reason}
                        </p>
                      ) : null}
                      {sc.redacted ? (
                        <p className="mt-1 text-[12px] text-muted-foreground">
                          Written evidence removed after a data-erasure request. The scores are
                          kept.
                        </p>
                      ) : null}
                      {sc.scores ? (
                        <div className="mt-1.5 flex flex-col gap-1">
                          {round.criteria.map((c) => {
                            const s = sc.scores?.[c.competency_id];
                            if (!s) return null;
                            return (
                              <div
                                key={c.competency_id}
                                className="flex items-baseline justify-between gap-2 text-[12px]"
                              >
                                <span className="text-[var(--ui-soft)]">{c.competency_name}</span>
                                <span className="text-foreground">
                                  {s.not_assessed ? 'not assessed' : (s.score ?? '-')}
                                </span>
                              </div>
                            );
                          })}
                          {round.criteria.some((c) => sc.scores?.[c.competency_id]?.evidence) ? (
                            <ul className="mt-1 flex flex-col gap-1">
                              {round.criteria
                                .filter((c) => sc.scores?.[c.competency_id]?.evidence)
                                .map((c) => (
                                  <li
                                    key={c.competency_id}
                                    className="text-[12px] text-muted-foreground"
                                  >
                                    {c.competency_name}: {sc.scores?.[c.competency_id]?.evidence}
                                  </li>
                                ))}
                            </ul>
                          ) : null}
                          {sc.summary ? (
                            <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
                              {sc.summary}
                            </p>
                          ) : null}
                        </div>
                      ) : sc.state === 'submitted' ? (
                        // Submitted, but not readable by this viewer yet: they owe
                        // their own scorecard for the round. Saying "not submitted"
                        // here would be false.
                        <p className="mt-1 text-[12px] text-muted-foreground">
                          {round.hidden_until_you_submit
                            ? 'Hidden until you submit your own scorecard.'
                            : 'Submitted.'}
                        </p>
                      ) : sc.state !== 'withdrawn' ? (
                        <p className="mt-1 text-[12px] text-muted-foreground">Not submitted yet.</p>
                      ) : null}
                      {/* Not on an open correction: the server refuses, because it would
                          leave that interviewer with no current scorecard. */}
                      {sc.state !== 'submitted' && sc.state !== 'withdrawn' && !sc.is_correction ? (
                        <div className="mt-2 flex items-center gap-2">
                          <input
                            type="text"
                            value={withdrawReasons[sc.scorecard_id] ?? ''}
                            onChange={(e) =>
                              setWithdrawReasons((prev) => ({
                                ...prev,
                                [sc.scorecard_id]: e.target.value,
                              }))
                            }
                            placeholder="Reason (optional)"
                            aria-label={`Withdraw reason for ${sc.interviewer_name}`}
                            className="h-9 w-full max-w-[220px] rounded-[10px] border border-input bg-card px-3 text-[13px] text-foreground placeholder:text-[var(--ui-faint)] focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]/30"
                          />
                          <ConfirmDeleteButton
                            label="Withdraw"
                            pending={
                              withdrawMut.isPending && withdrawMut.variables?.id === sc.scorecard_id
                            }
                            onConfirm={() =>
                              withdrawMut.mutate({
                                id: sc.scorecard_id,
                                reason: withdrawReasons[sc.scorecard_id],
                              })
                            }
                          />
                        </div>
                      ) : null}
                    </li>
                  ))}
                </ul>
                {superseded.length > 0 ? (
                  <details className="mt-2">
                    <summary className="cursor-pointer text-[12px] text-muted-foreground">
                      {superseded.length} corrected{' '}
                      {superseded.length === 1 ? 'scorecard' : 'scorecards'}
                    </summary>
                    <ul className="mt-1.5 flex flex-col gap-1.5 pl-2">
                      {superseded.map((sc) => (
                        <li key={sc.scorecard_id} className="text-[12px] text-muted-foreground">
                          {sc.interviewer_name} - corrected
                          {sc.correction_reason ? `: ${sc.correction_reason}` : ''}
                        </li>
                      ))}
                    </ul>
                  </details>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const STATUS_TONE: Record<string, TagTone> = {
  new: 'neutral',
  shortlisted: 'electric',
  held: 'amber',
  interviewed: 'lavender',
  hired: 'forest',
  rejected: 'ember',
};

function answerText(a: ApplicationAnswer): string {
  if (Array.isArray(a.answer)) return a.answer.join(', ');
  if (typeof a.answer === 'boolean') return a.answer ? 'Yes' : 'No';
  return String(a.answer);
}

export default function CandidateDrawer({
  applicantId,
  enrolmentId,
  onClose,
  focusSection,
}: {
  /** Null closes the drawer. */
  applicantId: string | null;
  /**
   * Needed to load their answers, and absent on surfaces that only know the
   * person — the applicant board, for one. Without it the answers section is
   * not shown rather than shown empty, because "no answers" and "we cannot
   * look" are different statements.
   */
  enrolmentId?: string | null;
  onClose: () => void;
  /**
   * PH5 wave-1 follow-up (A2) — opened from the analytics page's "Check-ins
   * due" card, which wants the drawer to land on the 90-day check-in section
   * rather than the top of the drawer. Scrolled and focused once the section
   * has mounted (`#checkin-section`, rendered by CheckinSection itself); a
   * no-op if the application turns out not to be hired after all.
   */
  focusSection?: 'checkin';
}) {
  const detail = useQuery({
    queryKey: ['applicant', applicantId],
    queryFn: () => getApplicant(applicantId as string),
    enabled: Boolean(applicantId),
    retry: false,
    throwOnError: false,
  });
  // The applicant row's ats_* describe their LATEST application. Opened from
  // an application (the pipeline), the resume match shown is that
  // application's own — the same CV scores differently against another role.
  const apps = useQuery({
    queryKey: ['hr', 'applicant', applicantId, 'applications'],
    queryFn: () => listApplications(applicantId as string),
    enabled: Boolean(applicantId && enrolmentId),
    retry: false,
    throwOnError: false,
  });
  const app = apps.data?.find((x) => x.enrolment_id === enrolmentId);
  const person = detail.data as DrawerCandidate | undefined;
  const candidate: DrawerCandidate | undefined =
    person && app
      ? {
          ...person,
          target_job_title: app.opening_title ?? person.target_job_title,
          status: app.status,
          ats_overall: app.ats_overall,
          ats_recommendation: app.ats_recommendation,
          ats_strengths: app.ats_strengths,
          ats_concerns: app.ats_concerns,
          ats_summary: app.ats_summary,
          source: app.source,
        }
      : person;

  // PH5 wave-1 follow-up (A2) — land on the check-in section instead of the
  // top of the drawer, once it has actually mounted (status hired). Runs
  // after the sheet's own focus placement, so it wins over the close button. A no-op — the section simply never mounts — if
  // this application turns out not to be hired.
  useEffect(() => {
    if (focusSection !== 'checkin' || !enrolmentId || candidate?.status !== 'hired') return;
    const el = document.getElementById('checkin-section');
    // Optional chained on the method itself, not just the element: jsdom (and
    // some embedded webviews) have no scrollIntoView at all.
    el?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    el?.focus();
  }, [focusSection, enrolmentId, candidate?.status]);

  const answers = useQuery({
    queryKey: ['answers', enrolmentId],
    queryFn: () => listAnswers(enrolmentId as string),
    enabled: Boolean(enrolmentId),
    retry: false,
    throwOnError: false,
  });

  // How they got here, and who moved them (B2). Under the ['hr', ...] prefix
  // so a new notification refreshes it with the rest of the candidate's views.
  const history = useQuery({
    queryKey: ['hr', 'enrolment', enrolmentId, 'history'],
    queryFn: () => getEnrolmentHistory(enrolmentId as string),
    enabled: Boolean(enrolmentId),
    retry: false,
    throwOnError: false,
  });

  const historyKey = useScopedPersistKey('candidate-history');
  // The newest move, by time, for the one-line summary under the closed header.
  const newestMove = (history.data ?? []).reduce<
    NonNullable<typeof history.data>[number] | undefined
  >((n, h) => (n === undefined || h.occurred_at > n.occurred_at ? h : n), undefined);

  // Same query key the analytics page uses for the source vocabulary — one
  // shared cache entry, not a second fetch, wherever both mount.
  const definitions = useQuery({
    queryKey: ['hr', 'metrics', 'definitions'],
    queryFn: getMetricDefinitions,
    enabled: Boolean(applicantId),
    staleTime: 5 * 60_000,
  });

  if (!applicantId) return null;

  const nameDiffers =
    Boolean(candidate?.parsed_full_name) && candidate?.parsed_full_name !== candidate?.full_name;

  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      {/* Radix supplies the focus trap, Escape, the overlay click and the Close
          button; focus lands on that button on open, as the old close ref did. */}
      <SheetContent
        side="right"
        aria-describedby={undefined}
        className="flex w-full flex-col gap-0 overflow-hidden bg-card p-0 shadow-elevated sm:max-w-[520px]"
      >
        {/* Pinned header: who this is and where they stand, always in view. */}
        <SheetHeader className="shrink-0 space-y-0 border-b border-border px-6 py-5 pr-14 text-left">
          <SheetTitle className="truncate text-[20px] font-bold tracking-[-0.4px]">
            {candidate?.full_name ?? 'Loading…'}
          </SheetTitle>
          <p className="mt-0.5 truncate text-[13px] text-muted-foreground">
            {[candidate?.target_job_title, candidate?.email].filter(Boolean).join(' · ')}
          </p>
          {candidate && (candidate.status || candidate.current_round_title) ? (
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              {candidate.status ? (
                <StatusTag tone={STATUS_TONE[candidate.status] ?? 'neutral'} dot>
                  {candidate.status}
                </StatusTag>
              ) : null}
              {candidate.current_round_title ? (
                <span className="text-[13px] text-muted-foreground">
                  on {candidate.current_round_title}
                </span>
              ) : null}
            </div>
          ) : null}
        </SheetHeader>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 pt-1">
          {detail.isError ? (
            <p role="alert" className="mt-4 text-[13px] text-muted-foreground">
              Could not load this candidate. Close and try again.
            </p>
          ) : null}

          {candidate ? (
            <>
              {/* The discrepancy nobody else can see. Stated plainly rather than as a
            warning: neither name is wrong, they simply disagree, and only a
            person can decide which the candidate meant. */}
              {nameDiffers ? (
                <p className="mt-5 flex items-start gap-2 rounded-[16px] border border-border bg-[var(--ui-inset-soft)] p-3.5 text-[13px] leading-relaxed text-[var(--ui-soft)]">
                  <Info
                    size={13}
                    className="mt-0.5 shrink-0 text-[var(--ui-info)]"
                    aria-hidden="true"
                  />
                  <span>
                    Their CV reads{' '}
                    <span className="text-foreground">{candidate.parsed_full_name}</span>. The name
                    above is what
                    {candidate.full_name_source === 'candidate' ? ' they typed' : ' is on file'}.
                  </span>
                </p>
              ) : null}

              {candidate.ats_overall != null ? (
                <div id="screening" tabIndex={-1} className={cn(SECTION, 'mt-5 border-t-0 pt-0')}>
                  <div className="flex items-baseline justify-between">
                    <h3 className={SECTION_TITLE}>Resume match</h3>
                    <span className="font-display text-[22px] font-bold text-foreground">
                      {candidate.ats_overall}
                      <span className="text-[13px] text-muted-foreground">/100</span>
                    </span>
                  </div>
                  {candidate.has_cv && applicantId ? (
                    <div className="mt-2">
                      {/* The CV behind this score: the application's own when
                          opened from one, otherwise their latest. */}
                      <ViewCvButton applicantId={applicantId} enrolmentId={enrolmentId} />
                    </div>
                  ) : null}
                  {candidate.ats_summary ? (
                    <p className="mt-1.5 text-[13px] leading-relaxed text-muted-foreground">
                      {candidate.ats_summary}
                    </p>
                  ) : null}
                  {candidate.ats_strengths?.length ? (
                    <ul className="mt-2 flex flex-col gap-1">
                      {candidate.ats_strengths.map((s) => (
                        <li key={s} className="text-[12.5px] text-[var(--ui-soft)]">
                          ✓ {s}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {candidate.ats_concerns?.length ? (
                    <ul className="mt-1.5 flex flex-col gap-1">
                      {candidate.ats_concerns.map((c) => (
                        <li
                          key={c}
                          className="flex items-start gap-1.5 text-[12.5px] text-[var(--ui-soft)]"
                        >
                          <AlertTriangle
                            size={12}
                            className="mt-0.5 shrink-0 text-[var(--ui-warn)]"
                            aria-hidden="true"
                          />
                          {c}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ) : null}

              {/* The prop, not candidate.applicant_id: GET /hr/applicants/{id} returns
            the id as `id`, so that field was always undefined and the round
            scores never loaded. */}
              <RoundScores applicantId={applicantId} enrolmentId={enrolmentId} />

              {/* PH4-D2 — recorded adjustments to how this applicant is assessed.
            Scoped to the applicant, so shown regardless of whether the drawer
            was opened from a specific application. */}
              <AccommodationsSection
                applicantId={applicantId}
                enrolmentId={enrolmentId}
                requisitionId={app?.requisition_id ?? null}
              />

              {enrolmentId ? (
                <HumanInterviewSection
                  enrolmentId={enrolmentId}
                  requisitionId={app?.requisition_id ?? null}
                />
              ) : null}

              {/* PH4-D4 — a job simulation or portfolio round's own submission(s):
            lifecycle, re-issue and withdraw. Reviewers for these rounds are
            assigned in "Human interview" above, through the same scorecard
            machinery; the pass/hold verdict is recorded on the decision
            queue, through the existing round-review action — nothing here
            does either. */}
              {enrolmentId ? (
                <div id="tasks" tabIndex={-1} className="outline-none">
                  <TaskSubmissionSection enrolmentId={enrolmentId} />
                </div>
              ) : null}

              {/* PH4-D3 -- counts only; the evidence is on the exam attempt. */}
              {enrolmentId ? <CodeEvidenceCounts enrolmentId={enrolmentId} /> : null}

              {/* PH4-A2 — scheduling the human interview(s) above: the loop(s), each
            session's time in both zones, and the controls to create, send,
            move and close them out. */}
              {enrolmentId ? (
                <InterviewLoopsSection
                  enrolmentId={enrolmentId}
                  requisitionId={app?.requisition_id ?? null}
                />
              ) : null}

              {/* PH4-O1 — what is blocking this application, for a person to see and
            act on. Scoped to this enrolment/application, same as the rest of
            the drawer. */}
              {enrolmentId ? <ExceptionsSection enrolmentId={enrolmentId} /> : null}

              {/* PH4-A3 — the offer, once this application is a hire. */}
              {enrolmentId ? (
                <div id="offers" tabIndex={-1} className="outline-none">
                  <OfferSection
                    enrolmentId={enrolmentId}
                    jobTitle={candidate.target_job_title}
                    applicationStatus={candidate.status}
                  />
                </div>
              ) : null}

              {/* PH5 wave-1 follow-up A1 — the 90-day check-in, once this
            application has actually resulted in a hire. Nothing here can
            move it back off "hired" or otherwise change a decision (D-05). */}
              {enrolmentId && candidate.status === 'hired' ? (
                <CheckinSection enrolmentId={enrolmentId} />
              ) : null}

              <div className={SECTION}>
                <h3 className={cn(SECTION_TITLE, 'mb-3')}>Details</h3>
                {/* Only the facts that exist: a blank row is noise in a drawer. */}
                <DataList
                  columns={1}
                  items={[
                    {
                      label: 'Source',
                      value: candidate.source
                        ? sourceLabel(candidate.source, definitions.data)
                        : null,
                    },
                    { label: 'Phone', value: candidate.phone },
                    {
                      label: 'Experience',
                      value:
                        candidate.years_experience != null
                          ? `${candidate.years_experience} years`
                          : null,
                    },
                    { label: 'Current company', value: candidate.current_company },
                    { label: 'Current role', value: candidate.current_title },
                    { label: 'LinkedIn', value: candidate.linkedin_url },
                    { label: 'GitHub', value: candidate.github_url },
                  ].filter((i) => Boolean(i.value))}
                />
              </div>

              {/* Otherwise write-only: candidates fill these in and nobody reads them. */}
              {enrolmentId ? (
                <div id="answers" tabIndex={-1} className={SECTION}>
                  <h3 className={cn(SECTION_TITLE, 'mb-2')}>Application answers</h3>
                  {answers.isLoading ? (
                    <p className="text-[12.5px] text-muted-foreground">Loading…</p>
                  ) : null}
                  {!answers.isLoading && (answers.data?.length ?? 0) === 0 ? (
                    <p className="text-[12.5px] text-muted-foreground">
                      This opening did not ask any questions.
                    </p>
                  ) : null}
                  <div className="flex flex-col gap-3">
                    {answers.data?.map((a) => (
                      <div key={a.question_id}>
                        <p
                          className={cn(
                            'text-[12px]',
                            a.retired ? 'text-muted-foreground' : 'text-muted-foreground',
                          )}
                        >
                          {a.prompt}
                          {/* An answer to a question no longer asked still counts —
                        hiding it would leave a decision partly based on
                        something nobody can see. */}
                          {a.retired ? (
                            <span className="ml-1.5 text-[12px] italic">no longer asked</span>
                          ) : null}
                        </p>
                        <p className="mt-0.5 whitespace-pre-wrap text-[13px] text-[var(--ui-soft)]">
                          {answerText(a)}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {enrolmentId && (history.data?.length ?? 0) > 0 ? (
                <div id="history" tabIndex={-1} className={SECTION}>
                  <Collapsible
                    title="History"
                    headingLevel="h3"
                    count={history.data?.length ?? 0}
                    noun="move"
                    latest={
                      newestMove
                        ? `${describeMove(newestMove)}, ${new Date(newestMove.occurred_at).toLocaleDateString()}`
                        : undefined
                    }
                    persistKey={historyKey}
                    data-testid="candidate-history"
                    // PH5-E5 — the full evidence trail behind this application:
                    // what existed when a decision was recorded, and what came
                    // after. Opens without preselecting a decision; the trail
                    // page's own picker handles more than one.
                    actions={
                      <Link
                        to={`/hr/enrolments/${enrolmentId}/evidence`}
                        className="inline-flex min-h-10 items-center gap-1 text-[12px] text-[var(--ui-info)] hover:underline focus:outline-none focus-visible:underline focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                      >
                        <ExternalLink size={11} aria-hidden="true" />
                        Evidence trail
                      </Link>
                    }
                  >
                    <ProgressiveList
                      as="ol"
                      items={history.data ?? []}
                      initial={5}
                      noun="moves"
                      getKey={(h, i) => `${h.occurred_at}-${i}`}
                      className="mt-1 flex flex-col gap-3.5 border-l border-border pl-4"
                      renderItem={(h) => {
                        // A decision row (the ledger's single writer for a hire/
                        // reject, never automated) also gets its own "Why?" —
                        // `h.id` (the stage_transitions row's own id, PH5-E5) IS
                        // the decision_id GET /hr/decisions/{id}/trace takes, so
                        // this deep-links straight to that decision rather than
                        // opening the trail's picker.
                        const isDecision =
                          !h.automated && (h.to_status === 'hired' || h.to_status === 'rejected');
                        return (
                          <li className="relative text-[12.5px]">
                            <span
                              className="absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full border-2 border-card bg-[var(--ui-info)]"
                              aria-hidden="true"
                            />
                            <p className="flex items-center gap-2 text-[var(--ui-soft)]">
                              {describeMove(h)}
                              {isDecision ? (
                                <Link
                                  to={`/hr/enrolments/${enrolmentId}/evidence?decision=${h.id}`}
                                  className="text-[12px] text-[var(--ui-info)] hover:underline focus:outline-none focus-visible:underline focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                                >
                                  Why?
                                </Link>
                              ) : null}
                            </p>
                            <p className="text-[12px] text-muted-foreground">
                              {new Date(h.occurred_at).toLocaleString()}
                              {/* O4: the structured reason label, when this move recorded
                                  one, alongside the free-text reason. Historical rows
                                  have neither and render exactly as before. */}
                              {h.reason_label ? ` - ${h.reason_label}` : ''}
                              {h.reason ? `${h.reason_label ? ': ' : ' - '}${h.reason}` : ''}
                            </p>
                          </li>
                        );
                      }}
                    />
                  </Collapsible>
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}
