// DecisionQueue — the human end of the workflow engine (D-05, E2).
//
// Everything the runner automates ends here. A candidate who clears every round
// is NOT hired, and one who falls short is NOT rejected: both land in this list
// for a person to decide. That is the rule the whole engine is built around and
// this page is where it is honoured — or, if this page did not exist, where it
// would quietly stop being true.
//
// MASTER-DETAIL, ONE DECISION AT A TIME. At `lg+` a compact list of everyone
// waiting sits on the left and ONE focused panel on the right holds the
// selected candidate, as a short guided sequence:
//
//   1 Evidence          the round results, the score summary, what the round
//                       assesses, and the way into the full record
//   2 Decision          the legal actions for THIS candidate's state, all equal
//   3 Reason & confirm  the reason the action needs, what will happen, who is
//                       recorded as deciding, and Confirm
//
// After a confirm the panel moves to the next candidate and says so in a polite
// live region. Below `lg` the list is the page and the panel opens as a
// full-height sheet.
//
// Advanced and held candidates deliberately share one list rather than sitting
// behind separate tabs. Held candidates in their own tab are held candidates
// nobody opens, which would make "every candidate reaches a human decision"
// accurate on paper and false in practice. The server orders holds first for
// the same reason.
//
// Scores rank and explain; they do not decide. Nothing in the panel is
// pre-selected, recommended or weighted: the four outcomes are the same
// control, and the person chooses. There is no Undo, because the API has no way
// to revert a decision (a reject after a hire is a new, recorded decision, not
// an undo), and a control that pretended otherwise would be a lie.

import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Pause,
  Play,
  Users,
  XCircle,
} from '@/design/components/icons';
import { StatusTag } from '@/design/components/primitives';
import { Breadcrumbs, PageHeader, EmptyState, Page, Stepper } from '@/design/components/page';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet';
import CandidateDrawer from '@/components/CandidateDrawer';
import { codeEvidenceLabel, hasCodeEvidence } from '@/lib/codeEvidenceLabel';
import { toast } from '@/lib/toast';
import { errorMessage } from '@/lib/errorMessage';
import { formatDateTime } from '@/lib/formatters';
import { statusLabel } from '@/lib/statusLabel';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import { cn } from '@/lib/utils';
import { LIVE_POLL_MS } from '@/lib/polling';
import { useIsDesktop } from '@/hooks/useIsDesktop';
import { useSignedInStaff } from '@/hooks/useSignedInStaff';
import { getRequisition } from '@/api/requisitions';
import {
  getDecisionQueue,
  recordFinalDecision,
  recordRoundReview,
  releaseHold,
  type DecisionQueueRow,
} from '@/api/workflows';
import { DecisionReasonSelect } from '@/components/hr/DecisionReasonSelect';
import { minReasonLength, reasonsFor, useDecisionReasons } from '@/lib/decisionReasons';

/** The shared error copy (errorMessage), with this page's period-less fallbacks. */
function errText(e: unknown, fallback: string): string {
  return errorMessage(e, `${fallback}.`);
}

/**
 * PH4-O1 — informational only: an SLA badge never changes what a reviewer can
 * do here, it just says where to look first. Worded with the due time and
 * owner, never colour alone.
 */
function slaBadge(
  row: DecisionQueueRow,
): { tone: 'ember' | 'amber' | 'forest'; text: string } | null {
  const sla = row.sla;
  if (!sla) return null;
  // HR-L16: the console's one date-time format, zone included, no seconds.
  const due = formatDateTime(sla.due_at);
  const owner = row.stage_owner_name ? ` · ${row.stage_owner_name}` : '';
  if (sla.state === 'overdue') {
    return { tone: 'ember', text: `Overdue since ${due}${owner}` };
  }
  if (sla.state === 'due_soon') {
    return { tone: 'amber', text: `Due soon - ${due}${owner}` };
  }
  return { tone: 'forest', text: `On track - due ${due}${owner}` };
}

/** Where this candidate is, in words a reviewer reads at a glance. */
function stageLine(row: DecisionQueueRow): string {
  const version = row.workflow_version ? ` · workflow v${row.workflow_version}` : '';
  if (row.held) {
    const at = row.current_round_title ? ` on ${row.current_round_title}` : '';
    return `Held${at}${version}`;
  }
  if (row.awaiting_review) {
    return `Waiting for review on ${row.review_round_title ?? 'a review round'}${version}`;
  }
  return `Finished every round${version}`;
}

function waitingText(row: DecisionQueueRow): string {
  if (row.waiting_days == null) return '';
  const d = Math.max(0, Math.round(row.waiting_days));
  return `waiting ${d} day${d === 1 ? '' : 's'}`;
}

/* ── The master: a compact list ─────────────────────────────────────────── */

/** The state of a row in one word, for the list. Icon-free: the word is the signal. */
function StateTag({ row }: { row: DecisionQueueRow }): JSX.Element {
  if (row.held) return <StatusTag tone="amber" dot>held</StatusTag>;
  if (row.awaiting_review) {
    return (
      <StatusTag tone="amber" dot>
        {row.review_round_title ?? 'your review'}
      </StatusTag>
    );
  }
  return <StatusTag tone="forest">finished</StatusTag>;
}

function CandidateList({
  rows,
  selectedId,
  onSelect,
  onMove,
}: {
  rows: DecisionQueueRow[];
  selectedId: string;
  onSelect: (row: DecisionQueueRow) => void;
  /** Returns false when the move was held back (unconfirmed input on the panel). */
  onMove: (id: string) => boolean;
}): JSX.Element {
  const refs = useRef(new Map<string, HTMLButtonElement>());
  const index = Math.max(
    0,
    rows.findIndex((r) => r.enrolment_id === selectedId),
  );

  function onKeyDown(e: KeyboardEvent<HTMLUListElement>): void {
    let to = index;
    if (e.key === 'ArrowDown') to = Math.min(rows.length - 1, index + 1);
    else if (e.key === 'ArrowUp') to = Math.max(0, index - 1);
    else if (e.key === 'Home') to = 0;
    else if (e.key === 'End') to = rows.length - 1;
    else return;
    e.preventDefault();
    const target = rows[to];
    if (!target) return;
    if (onMove(target.enrolment_id)) refs.current.get(target.enrolment_id)?.focus();
  }

  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex items-baseline justify-between gap-3 border-b border-border px-4 py-2.5">
        <h2 className="text-[13px] font-semibold text-foreground">Waiting for you</h2>
        <span
          className="text-[12.5px] tabular-nums text-muted-foreground"
          data-testid="queue-position"
        >
          {index + 1} of {rows.length}
        </span>
      </div>
      <ul
        aria-label="Candidates waiting for a decision"
        onKeyDown={onKeyDown}
        className="min-h-0 flex-1 divide-y divide-border overflow-y-auto"
      >
        {rows.map((r, i) => {
          const current = r.enrolment_id === selectedId;
          const badge = r.sla?.state === 'overdue' ? 'Overdue' : r.sla?.state === 'due_soon' ? 'Due soon' : null;
          return (
            <li key={r.enrolment_id}>
              <button
                type="button"
                ref={(el) => {
                  if (el) refs.current.set(r.enrolment_id, el);
                  else refs.current.delete(r.enrolment_id);
                }}
                tabIndex={current || (selectedId === '' && i === 0) ? 0 : -1}
                aria-current={current ? 'true' : undefined}
                onClick={() => onSelect(r)}
                data-testid="queue-row"
                className={cn(
                  'flex min-h-[64px] w-full flex-col gap-0.5 px-4 py-2.5 text-left transition-colors motion-reduce:transition-none',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--accent)]',
                  current
                    ? 'bg-[var(--ui-highlight-wash)] shadow-[inset_3px_0_0_var(--accent)]'
                    : 'hover:bg-[var(--ui-inset-soft)]',
                )}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="min-w-0 truncate text-[14px] font-semibold text-foreground">
                    {r.full_name}
                  </span>
                  <span className="flex flex-none items-center gap-1.5">
                    {badge ? <StatusTag tone="amber">{badge}</StatusTag> : null}
                    <StateTag row={r} />
                  </span>
                </span>
                <span className="truncate text-[12.5px] text-muted-foreground">
                  {r.held
                    ? (r.current_round_title ?? 'Held')
                    : r.awaiting_review
                      ? (r.review_round_title ?? 'Review round')
                      : 'Every round finished'}
                  {waitingText(r) ? ` · ${waitingText(r)}` : ''}
                </span>
                <span className="flex gap-3 text-[12.5px] tabular-nums text-muted-foreground">
                  {r.ats_overall !== null ? <span>Resume {r.ats_overall}</span> : null}
                  {r.composite_percent != null ? (
                    <span>Avg {Math.round(r.composite_percent)}%</span>
                  ) : r.best_percent !== null ? (
                    <span>Best {Math.round(r.best_percent)}%</span>
                  ) : null}
                  <span>
                    {r.rounds_taken} round{r.rounds_taken === 1 ? '' : 's'}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      <p className="border-t border-border px-4 py-2 text-[12px] text-muted-foreground">
        Scores rank and explain. They never decide.
      </p>
    </div>
  );
}

/* ── The detail: one candidate, three steps ─────────────────────────────── */

type StepKey = 'evidence' | 'decision' | 'confirm';
type Action = 'pass' | 'hold' | 'release' | 'hired' | 'rejected';

const STEPS: { key: StepKey; label: string }[] = [
  { key: 'evidence', label: 'Evidence' },
  { key: 'decision', label: 'Decision' },
  { key: 'confirm', label: 'Reason & confirm' },
];

const OPTION =
  'flex min-h-[56px] w-full items-center gap-3 rounded-[12px] border border-input bg-card px-4 py-2.5 text-left transition-colors hover:bg-[var(--ui-inset-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] motion-reduce:transition-none';

/** Whether `action` is one the server would accept for this person right now. */
function actionIsLegal(action: Action, row: DecisionQueueRow): boolean {
  if (action === 'pass' || action === 'hold') return Boolean(row.awaiting_review);
  if (action === 'release') return row.held;
  return true;
}

function DecisionPanel({
  row,
  onOpenEvidence,
  onDone,
  focusOnMount,
  onDirtyChange,
  pendingSwitch,
  onKeepEditing,
  onDiscardAndSwitch,
}: {
  row: DecisionQueueRow;
  onOpenEvidence: (row: DecisionQueueRow) => void;
  /** Called once an action has been recorded; `outcome` is the sentence for the live region. */
  onDone: (row: DecisionQueueRow, outcome: string) => void;
  focusOnMount: boolean;
  /** Reports whether step 3 holds typed-but-unconfirmed input. */
  onDirtyChange: (dirty: boolean) => void;
  /** Set when the reviewer tried to move to someone else while input is unconfirmed. */
  pendingSwitch: string | null;
  onKeepEditing: () => void;
  onDiscardAndSwitch: () => void;
}): JSX.Element {
  const qc = useQueryClient();
  const staff = useSignedInStaff();
  const [step, setStep] = useState<StepKey>('evidence');
  const [notice, setNotice] = useState('');
  const [action, setAction] = useState<Action | null>(null);
  const [rationale, setRationale] = useState('');
  const [reasonCode, setReasonCode] = useState('');
  const body = useRef<HTMLDivElement>(null);
  const moved = useRef(false);

  const final = action === 'hired' || action === 'rejected' ? action : null;
  const reasonsQuery = useDecisionReasons();
  const reasonOptions = reasonsFor(reasonsQuery.data, final);
  const chosenReason = reasonOptions.find((r) => r.code === reasonCode) ?? null;
  const minReason = minReasonLength(chosenReason);
  const reasonReady = rationale.trim().length >= minReason;
  // Hire/reject additionally need a chosen reason code (O4); the other actions
  // do not go through this gate at all.
  const canConfirm = final ? reasonReady && Boolean(reasonCode) : true;

  const invalidate = () => {
    // HR-L12: by prefix, so the attention panel, stage SLAs, analytics and the
    // applicant list move with the decision, not only this opening's views.
    void invalidateAfterHrMutation(qc);
  };

  const decideMut = useMutation({
    mutationFn: (decision: 'hired' | 'rejected') =>
      recordFinalDecision(row.enrolment_id, {
        decision,
        reason: rationale,
        reason_code: reasonCode,
      }),
    onSuccess: (_r, decision) => {
      toast.success(decision === 'hired' ? `${row.full_name} hired` : `${row.full_name} rejected`);
      invalidate();
      onDone(row, decision === 'hired' ? `${row.full_name} hired.` : `${row.full_name} rejected.`);
    },
    onError: (e) => toast.error(errText(e, 'That decision did not save')),
  });

  const releaseMut = useMutation({
    mutationFn: () => releaseHold(row.enrolment_id, { reason: rationale }),
    onSuccess: (out) => {
      // Releasing a hold is no longer a flag change: it advances the candidate,
      // which mints the next round's exam link or interview invite and emails
      // them. A bare "continues" left HR unable to tell that from the case where
      // the round had been deleted under the candidate and nobody moved — and
      // the second one needs them to look.
      if (out.to_round) {
        toast.success(`${row.full_name} continues - moved to ${out.to_round}`);
      } else if (out.action === 'completed') {
        toast.success(`${row.full_name} continues - now awaiting your final decision`);
      } else if (out.reason?.includes('did not move')) {
        toast.warning(`Hold lifted, but ${row.full_name} did not move: ${out.reason}`);
      } else {
        toast.success(`${row.full_name} continues`);
      }
      invalidate();
      onDone(row, `${row.full_name} continues.`);
    },
    onError: (e) => toast.error(errText(e, 'Could not release this hold')),
  });

  // The verdict on a review round (C4). Passing advances them; not passing
  // holds them for the final decision — it never rejects.
  const reviewMut = useMutation({
    mutationFn: (passed: boolean) =>
      recordRoundReview(row.enrolment_id, { passed, note: rationale }),
    onSuccess: (_r, passed) => {
      toast.success(passed ? `${row.full_name} advances` : `${row.full_name} is held for you`);
      invalidate();
      onDone(row, passed ? `${row.full_name} advances.` : `${row.full_name} is held for you.`);
    },
    onError: (e) => toast.error(errText(e, 'That review did not save')),
  });

  const pending = decideMut.isPending || releaseMut.isPending || reviewMut.isPending;

  function confirm(): void {
    if (action === 'hired' || action === 'rejected') decideMut.mutate(action);
    else if (action === 'release') releaseMut.mutate();
    else if (action === 'pass') reviewMut.mutate(true);
    else if (action === 'hold') reviewMut.mutate(false);
  }

  function go(next: StepKey): void {
    moved.current = true;
    setStep(next);
  }

  function choose(a: Action): void {
    // A typed "why" belongs to the action it was written for. Carrying a hire
    // rationale over to a rejection would record it against the wrong outcome,
    // so a CHANGED action starts clean; re-picking the same one keeps its text.
    if (a !== action) {
      setRationale('');
      setReasonCode('');
    }
    setAction(a);
    setNotice('');
    go('confirm');
  }

  // A refetch can change what is legal for this person while a choice is open
  // (held -> not held, a review closed by someone else). Confirming would only
  // earn a server rejection, so go back to step 2 and say why.
  useEffect(() => {
    if (!action || actionIsLegal(action, row)) return;
    setAction(null);
    setRationale('');
    setReasonCode('');
    if (step === 'confirm') {
      setNotice(
        `${row.full_name}'s situation changed, so the action you chose is no longer available. Choose again.`,
      );
      setStep('decision');
    }
  }, [action, row, step]);

  // Typed-but-unconfirmed input: the parent asks before moving to someone else.
  const dirty = step === 'confirm' && (rationale.trim() !== '' || reasonCode !== '');
  useEffect(() => {
    onDirtyChange(dirty);
    return () => onDirtyChange(false);
  }, [dirty, onDirtyChange]);

  const keepEditingRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (pendingSwitch) keepEditingRef.current?.focus();
  }, [pendingSwitch]);

  // Land the reader where the work is: on the new candidate after an advance,
  // on the first field of the confirm step, otherwise on the panel itself. Never
  // on an outcome.
  useEffect(() => {
    if (!moved.current && !focusOnMount) return;
    if (step === 'confirm') {
      const id = final ? `reason-code-${row.enrolment_id}` : `why-${row.enrolment_id}`;
      document.getElementById(id)?.focus();
    } else {
      body.current?.focus();
    }
  }, [step, final, row.enrolment_id, focusOnMount]);

  const rounds = row.round_results ?? [];
  const badge = slaBadge(row);
  const who = staff.name ? `you (${staff.name})` : 'you';

  const summary: Record<Action, string> = {
    pass: `${row.full_name} passes ${row.review_round_title ?? 'this round'} and advances to the next round.`,
    hold: `${row.full_name} is held for a final decision. Holding does not reject them.`,
    release: `${row.full_name} continues. The hold is lifted and they move on to the next round, which may email them an exam link or an interview invite.`,
    hired: `${row.full_name} is marked hired. This ends their candidacy for this opening and is recorded.`,
    rejected: `${row.full_name} is rejected. This ends their candidacy for this opening and is recorded.`,
  };

  return (
    <section
      aria-label={`Decision for ${row.full_name}`}
      data-testid="decision-card"
      className="flex min-h-0 flex-1 flex-col"
    >
      {/* Who, and where they stand. Fixed: only the body below scrolls. */}
      <header className="flex flex-col gap-2 border-b border-border px-5 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-[17px] font-bold text-foreground">{row.full_name}</h2>
          {row.held ? (
            <StatusTag tone="amber" dot>
              {statusLabel('held', 'candidate')}
            </StatusTag>
          ) : row.awaiting_review ? (
            <StatusTag tone="amber" dot>
              {row.review_round_title ?? 'your review'}
            </StatusTag>
          ) : null}
          {/* PH4-D4 — a job_simulation/portfolio round's own state, so
              "not started" reads apart from "submitted, awaiting review". */}
          {row.awaiting_review && row.task_submission ? (
            <StatusTag tone={row.task_submission.status === 'submitted' ? 'forest' : 'neutral'} dot>
              {row.task_submission.status === 'submitted'
                ? 'Submission received'
                : `Submission ${row.task_submission.status.replace('_', ' ')}`}
            </StatusTag>
          ) : null}
          {!row.held && !row.awaiting_review ? (
            <StatusTag tone="forest">finished the workflow</StatusTag>
          ) : null}
          {badge ? (
            <StatusTag tone={badge.tone} dot>
              {badge.text}
            </StatusTag>
          ) : null}
          {(row.open_exceptions ?? 0) > 0 ? (
            <StatusTag tone="amber">
              {row.open_exceptions} open exception{row.open_exceptions === 1 ? '' : 's'}
            </StatusTag>
          ) : null}
        </div>
        <p className="text-[12.5px] text-muted-foreground">
          {row.email ? <span className="truncate">{row.email} · </span> : null}
          <span className="text-[var(--ui-soft)]">{stageLine(row)}</span>
          {waitingText(row) ? <span> · {waitingText(row)}</span> : null}
        </p>
        <Stepper
          steps={STEPS}
          current={step}
          label={`Decision steps for ${row.full_name}`}
          compactLabel={`Step ${STEPS.findIndex((s) => s.key === step) + 1} of 3 · ${STEPS.find((s) => s.key === step)?.label ?? ''}`}
          className="mt-1"
        />
      </header>

      <div ref={body} tabIndex={-1} className="min-h-0 flex-1 overflow-y-auto px-5 py-4 focus:outline-none">
        {pendingSwitch ? (
          <div
            role="alert"
            data-testid="switch-confirm"
            className="mb-4 flex flex-col gap-2 rounded-[12px] border border-[var(--ui-warn)]/40 bg-[var(--ui-warn-wash)] p-3 text-[13px] leading-relaxed text-foreground"
          >
            <p>
              You have an unconfirmed decision for {row.full_name}. Moving to {pendingSwitch} will
              discard what you typed.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                ref={keepEditingRef}
                type="button"
                size="sm"
                variant="outline"
                onClick={onKeepEditing}
              >
                Keep editing
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={onDiscardAndSwitch}>
                Discard and switch
              </Button>
            </div>
          </div>
        ) : null}
        {step === 'evidence' ? (
          <div className="flex flex-col gap-3" data-testid="step-evidence">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[13px] text-muted-foreground">
              <span>
                {row.rounds_taken} round{row.rounds_taken === 1 ? '' : 's'} taken
              </span>
              {row.composite_percent != null ? (
                <span>average across rounds {Math.round(row.composite_percent)}%</span>
              ) : row.best_percent !== null ? (
                <span>best {Math.round(row.best_percent)}%</span>
              ) : null}
              {row.ats_overall !== null ? (
                <span>
                  resume match {row.ats_overall}
                  {row.ats_recommendation ? ` (${row.ats_recommendation})` : ''}
                </span>
              ) : null}
              {row.scorecards ? (
                <span>
                  {row.scorecards.submitted}/{row.scorecards.assigned} scorecards in
                  {row.scorecards.late > 0 ? (
                    <span className="text-[var(--ui-warn)]"> ({row.scorecards.late} late)</span>
                  ) : null}
                </span>
              ) : null}
              {row.code_evidence && hasCodeEvidence(row.code_evidence) ? (
                // Counts only, and worded as what they are: a similarity SIGNAL
                // is automated and awaits review; a FINDING is a person's
                // judgement, named by its outcome.
                <span>{codeEvidenceLabel(row.code_evidence)}</span>
              ) : null}
            </div>

            {rounds.length > 0 ? (
              <ul
                className="flex flex-wrap gap-2"
                aria-label={`Completed rounds for ${row.full_name}`}
              >
                {rounds.map((r) => (
                  <li
                    key={`${r.round_id}-${r.position}`}
                    className="rounded-full border border-border bg-[var(--ui-inset-soft)] px-3 py-1 text-[12px] text-[var(--ui-soft)]"
                  >
                    {r.title}{' '}
                    <span className="text-foreground">
                      {r.percent === null
                        ? r.graded_by === 'human'
                          ? 'reviewed'
                          : '-'
                        : `${Math.round(r.percent)}%`}
                    </span>
                    {r.passed !== null ? (
                      <span className={r.passed ? 'text-[var(--ui-ok)]' : 'text-[var(--ui-warn)]'}>
                        {' '}
                        · {r.passed ? 'advanced' : 'held'}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}

            {row.ats_summary ? (
              <p className="max-w-[70ch] text-[13px] leading-relaxed text-muted-foreground">
                {row.ats_summary}
              </p>
            ) : null}

            {row.held_reason ? (
              <div className="flex items-start gap-2 rounded-[12px] border border-[var(--ui-warn)]/25 bg-[var(--ui-warn-wash)] p-3 text-[13px] leading-relaxed text-foreground">
                <Pause className="mt-0.5 h-4 w-4 shrink-0 text-[var(--ui-warn)]" aria-hidden="true" />
                <span>
                  {row.held_reason}
                  {/* Said explicitly, because a "held" badge reads as a rejection to
                      most people and it is not one. */}
                  <span className="mt-1 block text-[12px] text-muted-foreground">
                    Nothing was decided automatically. They are waiting on you.
                  </span>
                </span>
              </div>
            ) : null}

            {/* The round's own competencies, as the checklist to review against —
                rather than leaving the reviewer to remember what this round is for. */}
            {row.awaiting_review && (row.review_criteria?.length ?? 0) > 0 ? (
              <div className="rounded-[12px] bg-[var(--ui-inset-soft)] px-3.5 py-3">
                <p className="text-[12px] font-medium text-foreground">
                  What {row.review_round_title ?? 'this round'} assesses
                </p>
                <ul className="mt-1.5 space-y-1">
                  {row.review_criteria?.map((c) => (
                    <li key={c.competency_id} className="text-[13px] text-[var(--ui-soft)]">
                      {c.name}
                      <span className="text-muted-foreground"> · weight {c.weight.toFixed(2)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {row.applicant_id ? (
              <div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => onOpenEvidence(row)}
                  aria-label={`Open details for ${row.full_name}`}
                >
                  Scores, evidence &amp; history
                </Button>
              </div>
            ) : null}
          </div>
        ) : null}

        {step === 'decision' ? (
          <div className="flex flex-col gap-4" data-testid="step-decision">
            <p className="text-[13.5px] text-muted-foreground">
              Choose what happens to {row.full_name}. Nothing is selected for you.
            </p>
            {notice ? (
              <p
                role="status"
                data-testid="action-changed-notice"
                className="rounded-[12px] border border-[var(--ui-warn)]/40 bg-[var(--ui-warn-wash)] px-3 py-2 text-[13px] text-foreground"
              >
                {notice}
              </p>
            ) : null}

            {/* Two groups, so the controls read as a sequence: first move them
                through the process, then (separately) decide the candidacy. */}
            {row.awaiting_review || row.held ? (
              <div role="group" aria-label="Move them through the process" className="flex flex-col gap-2">
                <p className="text-[12px] font-semibold uppercase tracking-[0.04em] text-muted-foreground">
                  Move them through the process
                </p>
                {row.awaiting_review ? (
                  <>
                    <button type="button" className={OPTION} onClick={() => choose('pass')}>
                      <CheckCircle2 className="h-4 w-4 flex-none text-muted-foreground" aria-hidden="true" />
                      <span>
                        <span className="block text-[14px] font-semibold text-foreground">
                          Passes this round
                        </span>
                        <span className="block text-[12.5px] text-muted-foreground">
                          Advances them to the next round.
                        </span>
                      </span>
                    </button>
                    <button type="button" className={OPTION} onClick={() => choose('hold')}>
                      <Pause className="h-4 w-4 flex-none text-muted-foreground" aria-hidden="true" />
                      <span>
                        <span className="block text-[14px] font-semibold text-foreground">
                          Hold for a decision
                        </span>
                        <span className="block text-[12.5px] text-muted-foreground">
                          Keeps them here for a final decision. Does not reject.
                        </span>
                      </span>
                    </button>
                  </>
                ) : null}
                {row.held ? (
                  <button type="button" className={OPTION} onClick={() => choose('release')}>
                    <Play className="h-4 w-4 flex-none text-muted-foreground" aria-hidden="true" />
                    <span>
                      <span className="block text-[14px] font-semibold text-foreground">
                        Let them continue
                      </span>
                      <span className="block text-[12.5px] text-muted-foreground">
                        Lifts the hold and moves them to the next round.
                      </span>
                    </span>
                  </button>
                ) : null}
              </div>
            ) : null}

            <div role="group" aria-label="Decide the candidacy" className="flex flex-col gap-2">
              <p className="text-[12px] font-semibold uppercase tracking-[0.04em] text-muted-foreground">
                Decide the candidacy
              </p>
              <button type="button" className={OPTION} onClick={() => choose('hired')}>
                <CheckCircle2 className="h-4 w-4 flex-none text-muted-foreground" aria-hidden="true" />
                <span>
                  <span className="block text-[14px] font-semibold text-foreground">Hire</span>
                  <span className="block text-[12.5px] text-muted-foreground">
                    Ends their candidacy for this opening. Needs a reason.
                  </span>
                </span>
              </button>
              <button type="button" className={OPTION} onClick={() => choose('rejected')}>
                <XCircle className="h-4 w-4 flex-none text-muted-foreground" aria-hidden="true" />
                <span>
                  <span className="block text-[14px] font-semibold text-foreground">Reject</span>
                  <span className="block text-[12.5px] text-muted-foreground">
                    Ends their candidacy for this opening. Needs a reason.
                  </span>
                </span>
              </button>
            </div>
          </div>
        ) : null}

        {step === 'confirm' && action ? (
          <div className="flex flex-col gap-4" data-testid="step-confirm">
            {final ? (
              <DecisionReasonSelect
                id={`reason-code-${row.enrolment_id}`}
                reasons={reasonOptions}
                value={reasonCode}
                onChange={setReasonCode}
              />
            ) : null}

            <div>
              <label
                htmlFor={`why-${row.enrolment_id}`}
                className="block text-[13px] font-semibold text-foreground"
              >
                Why (recorded against your name)
              </label>
              <input
                id={`why-${row.enrolment_id}`}
                value={rationale}
                onChange={(e) => setRationale(e.target.value)}
                placeholder={final ? 'Required to hire or reject' : 'Optional'}
                className="mt-1.5 h-10 w-full rounded-[12px] border border-input bg-card px-3.5 text-[14px] text-foreground placeholder:text-[var(--ui-faint)] focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              />
              {final && !canConfirm ? (
                <p className="mt-1.5 text-[12.5px] text-[var(--ui-warn)]">
                  {!reasonCode
                    ? 'Choose a reason above first.'
                    : `Write at least ${minReason} characters above first.`}
                </p>
              ) : null}
            </div>

            <div
              className="rounded-[12px] bg-[var(--ui-inset-soft)] px-3.5 py-3 text-[13.5px] leading-relaxed text-foreground"
              data-testid="confirm-summary"
            >
              <p className="text-[12px] font-semibold uppercase tracking-[0.04em] text-muted-foreground">
                If you confirm
              </p>
              <p className="mt-1">{summary[action]}</p>
              <p className="mt-1 text-muted-foreground">
                Recorded as decided by {who}, not by the system.
                {final ? ' There is no undo for this step.' : ''}
              </p>
            </div>
          </div>
        ) : null}
      </div>

      <footer className="flex items-center justify-between gap-3 border-t border-border px-5 py-3">
        {step === 'evidence' ? (
          <>
            <span className="text-[12.5px] text-muted-foreground">Step 1 of 3</span>
            <Button type="button" onClick={() => go('decision')} data-testid="to-decision">
              Continue to decision
            </Button>
          </>
        ) : null}
        {step === 'decision' ? (
          <>
            <Button type="button" variant="outline" onClick={() => go('evidence')}>
              Back to evidence
            </Button>
            <span className="text-[12.5px] text-muted-foreground">Step 2 of 3</span>
          </>
        ) : null}
        {step === 'confirm' ? (
          <>
            <Button type="button" variant="outline" onClick={() => go('decision')} disabled={pending}>
              Back
            </Button>
            <Button
              type="button"
              variant={action === 'rejected' ? 'destructive' : 'default'}
              onClick={confirm}
              disabled={pending || !canConfirm}
              data-testid="confirm-decision"
            >
              {pending ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
              {pending ? 'Saving…' : 'Confirm'}
            </Button>
          </>
        ) : null}
      </footer>
    </section>
  );
}

/**
 * Whether this opening is settled — said on the queue itself.
 *
 * Closing an opening does not decide anyone (D-05), so "closed" and "resolved"
 * are different things: a closed opening can still have people waiting here,
 * and an open one can have nobody waiting while candidates are mid-round.
 */
function resolutionNote(
  status: string | undefined,
  waiting: number,
  unresolved: number | undefined,
): { tone: 'warn' | 'ok' | 'info'; text: string } | null {
  if (status === 'closed' && waiting > 0) {
    return {
      tone: 'warn',
      text: `This opening is closed, and ${waiting} candidate${waiting === 1 ? '' : 's'} below still need${waiting === 1 ? 's' : ''} a final decision. Closing it did not reject anyone.`,
    };
  }
  if (unresolved === undefined) return null;
  if (unresolved === 0 && status === 'closed') {
    return { tone: 'ok', text: 'Resolved - every candidate in this opening has a final decision.' };
  }
  const inProgress = unresolved - waiting;
  if (waiting === 0 && inProgress > 0) {
    return {
      tone: 'info',
      text: `${inProgress} candidate${inProgress === 1 ? ' is' : 's are'} still in progress and will appear here when they reach a decision.`,
    };
  }
  return null;
}

export default function DecisionQueue(): JSX.Element {
  const { requisitionId = '' } = useParams();
  const desktop = useIsDesktop();
  const [open, setOpen] = useState<DecisionQueueRow | null>(null);
  const [selectedId, setSelectedId] = useState('');
  // Below lg the panel is a sheet, opened by choosing a row.
  const [sheetOpen, setSheetOpen] = useState(false);
  const [announcement, setAnnouncement] = useState('');
  const [advanced, setAdvanced] = useState(false);
  // PH4-O1 — informational filter only: it narrows what is shown, never what
  // is in the queue or what a reviewer can do with a row.
  const [slaOnly, setSlaOnly] = useState(false);
  // Whether the open panel holds typed-but-unconfirmed input, and a move that
  // was held back because of it.
  const dirtyRef = useRef(false);
  const [pending, setPending] = useState<{ id: string; openSheet: boolean } | null>(null);
  const onDirtyChange = useCallback((dirty: boolean) => {
    dirtyRef.current = dirty;
    if (!dirty) setPending(null);
  }, []);

  const req = useQuery({
    queryKey: ['hr', 'requisition', requisitionId],
    queryFn: () => getRequisition(requisitionId),
    enabled: Boolean(requisitionId),
    refetchInterval: LIVE_POLL_MS,
  });

  const queue = useQuery({
    queryKey: ['hr', 'decision-queue', requisitionId],
    queryFn: () => getDecisionQueue(requisitionId),
    enabled: Boolean(requisitionId),
    refetchInterval: LIVE_POLL_MS,
  });

  const rows = queue.data ?? [];
  const held = rows.filter((r) => r.held).length;
  const atRisk = rows.filter(
    (r) => r.sla?.state === 'overdue' || r.sla?.state === 'due_soon',
  ).length;
  const visibleRows = slaOnly
    ? rows.filter((r) => r.sla?.state === 'overdue' || r.sla?.state === 'due_soon')
    : rows;
  const note = queue.isSuccess
    ? resolutionNote(req.data?.status, rows.length, req.data?.unresolved)
    : null;

  // The selection survives a refetch; if the person is no longer in the queue
  // (someone else decided them) the first in line takes their place.
  const selected = visibleRows.find((r) => r.enrolment_id === selectedId) ?? visibleRows[0];

  /**
   * Move the panel to someone else. If step 3 holds typed-but-unconfirmed input
   * the move is held back and the panel asks first; returns whether it happened.
   */
  function moveTo(id: string, openSheet: boolean): boolean {
    if (id === selected?.enrolment_id) {
      if (openSheet && !desktop) setSheetOpen(true);
      return true;
    }
    if (dirtyRef.current) {
      setPending({ id, openSheet });
      return false;
    }
    setSelectedId(id);
    setAdvanced(false);
    if (openSheet && !desktop) setSheetOpen(true);
    return true;
  }

  function select(row: DecisionQueueRow): void {
    moveTo(row.enrolment_id, true);
  }

  const pendingRow = pending ? visibleRows.find((r) => r.enrolment_id === pending.id) : undefined;
  function discardAndSwitch(): void {
    if (pending) {
      dirtyRef.current = false;
      setSelectedId(pending.id);
      setAdvanced(false);
      if (pending.openSheet && !desktop) setSheetOpen(true);
    }
    setPending(null);
  }

  /** After a recorded action: on to the next person, and say so. */
  function afterDecision(done: DecisionQueueRow, outcome: string): void {
    const at = visibleRows.findIndex((r) => r.enrolment_id === done.enrolment_id);
    const next = visibleRows[at + 1] ?? visibleRows[at - 1];
    if (next && next.enrolment_id !== done.enrolment_id) {
      setSelectedId(next.enrolment_id);
      setAdvanced(true);
      setAnnouncement(`${outcome} Now showing ${next.full_name}.`);
    } else {
      setSelectedId('');
      setAnnouncement(`${outcome} Nobody else is waiting.`);
    }
    setSheetOpen(false);
  }

  const panel = selected ? (
    <DecisionPanel
      key={selected.enrolment_id}
      row={selected}
      onOpenEvidence={setOpen}
      onDone={afterDecision}
      focusOnMount={advanced}
      onDirtyChange={onDirtyChange}
      pendingSwitch={pendingRow?.full_name ?? null}
      onKeepEditing={() => setPending(null)}
      onDiscardAndSwitch={discardAndSwitch}
    />
  ) : null;

  return (
    <Page width="default">
      <PageHeader
        className="mb-2"
        breadcrumbs={
          <Breadcrumbs
            items={[
              { label: 'Openings', to: '/hr/requisitions' },
              { label: req.data?.title ?? 'Opening', to: `/hr/requisitions/${requisitionId}` },
              { label: 'Decisions' },
            ]}
          />
        }
        actions={
          <Button asChild variant="outline">
            <Link to={`/hr/requisitions/${requisitionId}/workflow`}>Workflow</Link>
          </Button>
        }
        title={`Decisions - ${req.data?.title ?? 'this opening'}`}
        description="Everyone the workflow has taken as far as it can. Scores rank and explain; nothing here was decided automatically, and nothing here moves until you move it."
      />

      {/* Said to assistive tech after every recorded action. */}
      <p role="status" aria-live="polite" className="sr-only" data-testid="decision-announcer">
        {announcement}
      </p>

      {held > 0 || note || rows.some((r) => r.sla) ? (
        <div className="flex flex-col gap-2">
          {note ? (
            <p
              role="status"
              data-testid="resolution-note"
              className={cn(
                'max-w-[70ch] rounded-[12px] border px-3.5 py-2.5 text-[13px] leading-relaxed',
                note.tone === 'warn' &&
                  'border-[var(--ui-warn)]/40 bg-[var(--ui-warn-wash)] text-foreground',
                note.tone === 'ok' &&
                  'border-[var(--ui-ok)]/40 bg-[var(--ui-ok-wash)] text-foreground',
                note.tone === 'info' &&
                  'border-border bg-[var(--ui-inset-soft)] text-muted-foreground',
              )}
            >
              {note.text}
            </p>
          ) : null}
          {held > 0 || rows.some((r) => r.sla) ? (
            <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
              {held > 0 ? (
                <div className="inline-flex items-center gap-1.5 text-[13px] font-semibold text-[var(--ui-warn)]">
                  <AlertTriangle className="h-4 w-4" aria-hidden="true" />
                  {held} held below a round threshold
                </div>
              ) : null}
              {rows.some((r) => r.sla) ? (
                <label className="flex min-h-10 w-fit items-center gap-2 text-[13px] text-foreground">
                  <input
                    type="checkbox"
                    checked={slaOnly}
                    onChange={(e) => setSlaOnly(e.target.checked)}
                    className="h-4 w-4 accent-[var(--accent)]"
                  />
                  Overdue or due soon only
                  {atRisk > 0 ? <span className="text-muted-foreground"> ({atRisk})</span> : null}
                </label>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {queue.isLoading ? (
        <div
          className="grid gap-4 lg:grid-cols-[340px_minmax(0,1fr)]"
          role="status"
          aria-label="Loading queue"
        >
          <div className="flex flex-col gap-2">
            <Skeleton className="h-16 rounded-[12px]" />
            <Skeleton className="h-16 rounded-[12px]" />
            <Skeleton className="h-16 rounded-[12px]" />
          </div>
          <Skeleton className="hidden h-64 rounded-card lg:block" />
          <span className="sr-only">Loading queue…</span>
        </div>
      ) : queue.isError ? (
        <p
          role="alert"
          className="rounded-[12px] border border-[var(--ui-danger)]/40 bg-[var(--ui-danger-wash)] px-4 py-3 text-[14px] text-[var(--ui-danger)]"
        >
          {errText(queue.error, 'Could not load the decision queue')}
        </p>
      ) : rows.length === 0 ? (
        <EmptyState
          size="row"
          bordered
          icon={Users}
          title="Nobody is waiting"
          description="Candidates appear here when they finish the workflow, reach a review round, or land below a round’s threshold."
        />
      ) : visibleRows.length === 0 ? (
        <EmptyState
          size="row"
          title="Nobody is overdue or due soon."
          action={
            <Button type="button" variant="outline" size="sm" onClick={() => setSlaOnly(false)}>
              Show everyone waiting
            </Button>
          }
        />
      ) : (
        <div
          className={cn(
            'overflow-hidden rounded-card border border-border bg-card shadow-raised',
            'lg:grid lg:h-[calc(100dvh-19rem)] lg:min-h-[500px] lg:grid-cols-[340px_minmax(0,1fr)]',
          )}
          data-testid="decision-workspace"
        >
          <div className="min-h-0 lg:flex lg:flex-col lg:border-r lg:border-border">
            <CandidateList
              rows={visibleRows}
              selectedId={selected?.enrolment_id ?? ''}
              onSelect={select}
              onMove={(id) => moveTo(id, false)}
            />
          </div>
          {desktop ? <div className="flex min-h-0 flex-col">{panel}</div> : null}
        </div>
      )}

      {/* Below lg the panel is a full-height sheet over the list. */}
      {!desktop && panel ? (
        <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
          <SheetContent side="bottom" className="flex h-[100dvh] flex-col gap-0 p-0">
            <SheetTitle className="sr-only">Decision for {selected?.full_name}</SheetTitle>
            {panel}
          </SheetContent>
        </Sheet>
      ) : null}

      <CandidateDrawer
        applicantId={open?.applicant_id ?? null}
        enrolmentId={open?.enrolment_id ?? null}
        onClose={() => setOpen(null)}
      />
    </Page>
  );
}
