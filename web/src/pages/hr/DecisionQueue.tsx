// DecisionQueue — the human end of the workflow engine (D-05, E2).
//
// Everything the runner automates ends here. A candidate who clears every round
// is NOT hired, and one who falls short is NOT rejected: both land in this list
// for a person to decide. That is the rule the whole engine is built around and
// this page is where it is honoured — or, if this page did not exist, where it
// would quietly stop being true.
//
// Advanced and held candidates deliberately share one list rather than sitting
// behind separate tabs. Held candidates in their own tab are held candidates
// nobody opens, which would make "every candidate reaches a human decision"
// accurate on paper and false in practice. The server orders holds first for
// the same reason.
//
// Scores rank and explain; they do not decide. The card shows the resume match,
// each completed round and the mean of those rounds, labelled as a summary.
// Criterion scores, the evidence behind them and the full history are one click
// away in the candidate drawer, scoped to THIS application.
//
// Three outcomes are offered, and the asymmetry is intentional: continuing is
// one click because it is reversible, while hire and reject need a reason
// because they end a candidacy and are recorded against the person deciding.

import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Loader2,
  Pause,
  Play,
  Users,
  XCircle,
} from '@/design/components/icons';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import CandidateDrawer from '@/components/CandidateDrawer';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { LIVE_POLL_MS } from '@/lib/polling';
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

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
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

function QueueCard({
  row,
  requisitionId,
  onOpen,
}: {
  row: DecisionQueueRow;
  requisitionId: string;
  onOpen: (row: DecisionQueueRow) => void;
}) {
  const qc = useQueryClient();
  const [rationale, setRationale] = useState('');
  const [reasonCode, setReasonCode] = useState('');
  const [pending, setPending] = useState<'hired' | 'rejected' | null>(null);

  const reasonsQuery = useDecisionReasons();
  const reasonOptions = reasonsFor(reasonsQuery.data, pending);
  const chosenReason = reasonOptions.find((r) => r.code === reasonCode) ?? null;
  const minReason = minReasonLength(chosenReason);
  const reasonReady = rationale.trim().length >= minReason;
  // Hire/reject additionally need a chosen reason code (O4); releasing a hold
  // does not go through this gate at all.
  const canConfirm = reasonReady && Boolean(reasonCode);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['hr', 'decision-queue', requisitionId] });
    void qc.invalidateQueries({ queryKey: ['hr', 'requisition', requisitionId] });
    void qc.invalidateQueries({ queryKey: ['hr', 'requisitions'] });
    void qc.invalidateQueries({ queryKey: ['hr', 'requisition-dashboard'] });
  };

  const decideMut = useMutation({
    mutationFn: (decision: 'hired' | 'rejected') =>
      recordFinalDecision(row.enrolment_id, { decision, reason: rationale, reason_code: reasonCode }),
    onSuccess: (_r, decision) => {
      toast.success(decision === 'hired' ? `${row.full_name} hired` : `${row.full_name} rejected`);
      setPending(null);
      invalidate();
    },
    onError: (e) => toast.error(errText(e, 'That decision did not save')),
  });

  const releaseMut = useMutation({
    mutationFn: () => releaseHold(row.enrolment_id, { reason: rationale }),
    onSuccess: () => {
      toast.success(`${row.full_name} continues`);
      invalidate();
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
    },
    onError: (e) => toast.error(errText(e, 'That review did not save')),
  });

  const rounds = row.round_results ?? [];

  return (
    <GlassCard className={cn('p-5', row.held && 'border-[var(--ui-warn)]/25')}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[15px] font-medium text-foreground">{row.full_name}</span>
            {row.held ? (
              <StatusTag tone="amber" dot>
                held
              </StatusTag>
            ) : row.awaiting_review ? (
              <StatusTag tone="amber" dot>
                {row.review_round_title ?? 'your review'}
              </StatusTag>
            ) : (
              <StatusTag tone="forest">finished the workflow</StatusTag>
            )}
          </div>
          {row.email ? (
            <div className="mt-0.5 truncate text-[12.5px] text-muted-foreground">{row.email}</div>
          ) : null}
          <div className="mt-1 text-[12px] text-[var(--ui-soft)]">
            {stageLine(row)}
            {row.waiting_days != null ? (
              <span className="text-muted-foreground">
                {' '}
                · waiting {Math.max(0, Math.round(row.waiting_days))}{' '}
                day{Math.round(row.waiting_days) === 1 ? '' : 's'}
              </span>
            ) : null}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[12px] text-muted-foreground">
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
          </div>
        </div>
        {row.applicant_id ? (
          <button
            type="button"
            onClick={() => onOpen(row)}
            aria-label={`Open details for ${row.full_name}`}
            className="shrink-0 rounded-[10px] border border-border px-3 py-1.5 text-[12px] text-muted-foreground hover:text-foreground focus:outline-none focus-visible:border-[var(--accent)]"
          >
            Scores, evidence &amp; history
          </button>
        ) : null}
      </div>

      {rounds.length > 0 ? (
        <ul className="mt-3 flex flex-wrap gap-2" aria-label={`Completed rounds for ${row.full_name}`}>
          {rounds.map((r) => (
            <li
              key={`${r.round_id}-${r.position}`}
              className="rounded-[10px] border border-border px-2.5 py-1 text-[12px] text-[var(--ui-soft)]"
            >
              {r.title}{' '}
              <span className="text-foreground">
                {r.percent === null ? (r.graded_by === 'human' ? 'reviewed' : '—') : `${Math.round(r.percent)}%`}
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
        <p className="mt-2 max-w-[70ch] text-[12.5px] leading-relaxed text-muted-foreground">
          {row.ats_summary}
        </p>
      ) : null}

      {row.held_reason ? (
        <div className="mt-3 flex items-start gap-2 rounded-[12px] border border-[var(--ui-warn)]/25 bg-[var(--ui-warn)]/[0.06] p-3 text-[12.5px] leading-relaxed text-[var(--ui-soft)]">
          <Pause className="mt-0.5 h-4 w-4 shrink-0 text-[var(--ui-warn)]" aria-hidden="true" />
          <span>
            {row.held_reason}
            {/* Said explicitly, because a "held" badge reads as a rejection to
                most people and it is not one. */}
            <span className="mt-1 block text-[11.5px] text-muted-foreground">
              Nothing was decided automatically. They are waiting on you.
            </span>
          </span>
        </div>
      ) : null}

      {/* The round's own competencies, as the checklist to review against —
          rather than leaving the reviewer to remember what this round is for. */}
      {row.awaiting_review && (row.review_criteria?.length ?? 0) > 0 ? (
        <div className="mt-3 rounded-[12px] border border-border p-3">
          <p className="text-[12px] font-medium text-foreground">
            What {row.review_round_title ?? 'this round'} assesses
          </p>
          <ul className="mt-1.5 space-y-1">
            {row.review_criteria?.map((c) => (
              <li key={c.competency_id} className="text-[12.5px] text-[var(--ui-soft)]">
                {c.name}
                <span className="text-[var(--ui-faint)]"> · weight {c.weight.toFixed(2)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <label htmlFor={`why-${row.enrolment_id}`} className="mt-4 block text-[12px] text-[var(--ui-soft)]">
        Why (recorded against your name)
      </label>
      <input
        id={`why-${row.enrolment_id}`}
        value={rationale}
        onChange={(e) => setRationale(e.target.value)}
        placeholder="Optional to let them continue; required to hire or reject"
        className="mt-1.5 w-full rounded-[12px] border border-border bg-secondary px-3.5 py-2.5 text-[13px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none"
      />

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {row.awaiting_review ? (
          <>
            <button
              type="button"
              onClick={() => reviewMut.mutate(true)}
              disabled={reviewMut.isPending}
              className="inline-flex items-center gap-1.5 rounded-[12px] bg-primary px-4 py-2 text-[13px] font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40"
            >
              Passes this round
            </button>
            <button
              type="button"
              onClick={() => reviewMut.mutate(false)}
              disabled={reviewMut.isPending}
              className="rounded-[12px] border border-[var(--ui-line-strong)] px-4 py-2 text-[13px] text-[var(--ui-soft)] hover:text-foreground disabled:opacity-40"
            >
              Hold for a decision
            </button>
          </>
        ) : null}
        {row.held ? (
          <button
            type="button"
            onClick={() => releaseMut.mutate()}
            disabled={releaseMut.isPending}
            className="inline-flex items-center gap-1.5 rounded-[12px] bg-primary px-4 py-2 text-[13px] font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40"
          >
            {releaseMut.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Play className="h-4 w-4" aria-hidden="true" />
            )}
            Let them continue
          </button>
        ) : null}

        {pending ? (
          <div className="flex w-full flex-wrap items-center gap-2 rounded-[12px] border border-border bg-black/25 p-3">
            <div className="w-full">
              <DecisionReasonSelect
                id={`reason-code-${row.enrolment_id}`}
                reasons={reasonOptions}
                value={reasonCode}
                onChange={setReasonCode}
              />
            </div>
            <span className="flex-1 text-[12.5px] text-[var(--ui-soft)]">
              {pending === 'hired' ? 'Mark hired' : 'Reject'} — {row.full_name}. This ends their
              candidacy for this opening and is recorded.
              {!canConfirm ? (
                <span className="mt-1 block text-[11.5px] text-[var(--ui-warn)]">
                  {!reasonCode
                    ? 'Choose a reason above first.'
                    : `Write at least ${minReason} characters above first.`}
                </span>
              ) : null}
            </span>
            <button
              type="button"
              onClick={() => decideMut.mutate(pending)}
              disabled={decideMut.isPending || !canConfirm}
              className={cn(
                'rounded-[10px] px-4 py-2 text-[12.5px] font-medium text-foreground disabled:opacity-40',
                pending === 'hired' ? 'bg-[var(--ui-ok)]' : 'bg-[var(--ui-danger)]',
              )}
            >
              {decideMut.isPending ? 'Saving…' : 'Confirm'}
            </button>
            <button
              type="button"
              onClick={() => { setPending(null); setReasonCode(''); }}
              className="rounded-[10px] border border-[var(--ui-line-strong)] px-4 py-2 text-[12.5px] text-[var(--ui-soft)] hover:text-foreground"
            >
              Cancel
            </button>
          </div>
        ) : (
          <>
            <button
              type="button"
              onClick={() => { setPending('hired'); setReasonCode(''); }}
              className="inline-flex items-center gap-1.5 rounded-[12px] border border-[var(--ui-ok)]/35 px-4 py-2 text-[13px] font-medium text-[var(--ui-ok)] hover:bg-[var(--ui-ok)]/10"
            >
              <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
              Hire
            </button>
            <button
              type="button"
              onClick={() => { setPending('rejected'); setReasonCode(''); }}
              className="inline-flex items-center gap-1.5 rounded-[12px] border border-[var(--ui-line-strong)] px-4 py-2 text-[13px] text-muted-foreground hover:border-[var(--ui-danger)]/40 hover:text-[var(--ui-danger)]"
            >
              <XCircle className="h-4 w-4" aria-hidden="true" />
              Reject
            </button>
          </>
        )}
      </div>
    </GlassCard>
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
    return { tone: 'ok', text: 'Resolved — every candidate in this opening has a final decision.' };
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
  const [open, setOpen] = useState<DecisionQueueRow | null>(null);

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
  const note = queue.isSuccess
    ? resolutionNote(req.data?.status, rows.length, req.data?.unresolved)
    : null;

  return (
    <div className="mx-auto w-full max-w-[900px] px-4 py-8">
      <Reveal>
        <header className="mb-6">
          <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1">
            <Link
              to={`/hr/requisitions/${requisitionId}`}
              className="inline-flex items-center gap-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
              Opening
            </Link>
            <Link
              to={`/hr/requisitions/${requisitionId}/workflow`}
              className="text-[12.5px] text-muted-foreground hover:text-foreground"
            >
              Workflow
            </Link>
          </div>
          <h1 className="text-[26px] font-semibold tracking-[-0.8px] text-foreground">
            Decisions — {req.data?.title ?? 'this opening'}
          </h1>
          <p className="mt-1.5 max-w-[70ch] text-[13.5px] leading-relaxed text-muted-foreground">
            Everyone the workflow has taken as far as it can. Scores rank and explain; nothing
            here was decided automatically, and nothing here moves until you move it.
          </p>
          {held > 0 ? (
            <div className="mt-3 inline-flex items-center gap-1.5 text-[12.5px] text-[var(--ui-warn)]">
              <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
              {held} held below a round threshold
            </div>
          ) : null}
          {note ? (
            <p
              role="status"
              data-testid="resolution-note"
              className={cn(
                'mt-3 max-w-[70ch] rounded-[12px] border px-3 py-2 text-[12.5px] leading-relaxed',
                note.tone === 'warn' &&
                  'border-[var(--ui-warn)]/30 bg-[var(--ui-warn)]/[0.06] text-[var(--ui-soft)]',
                note.tone === 'ok' && 'border-[var(--ui-ok)]/30 text-[var(--ui-soft)]',
                note.tone === 'info' && 'border-border text-muted-foreground',
              )}
            >
              {note.text}
            </p>
          ) : null}
        </header>
      </Reveal>

      {queue.isLoading ? (
        <div className="flex items-center gap-2 py-16 text-[13px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading queue…
        </div>
      ) : queue.isError ? (
        <GlassCard className="p-6 text-[13.5px] text-[var(--ui-danger)]">
          {errText(queue.error, 'Could not load the decision queue')}
        </GlassCard>
      ) : rows.length === 0 ? (
        <GlassCard className="p-10 text-center">
          <Users className="mx-auto h-8 w-8 text-[var(--ui-faint)]" aria-hidden="true" />
          <div className="mt-3 text-[15px] font-medium text-foreground">Nobody is waiting</div>
          <p className="mx-auto mt-1.5 max-w-[46ch] text-[13px] text-muted-foreground">
            Candidates appear here when they finish the workflow, reach a review round, or land
            below a round&rsquo;s threshold.
          </p>
        </GlassCard>
      ) : (
        <div className="flex flex-col gap-3">
          {rows.map((r) => (
            <QueueCard key={r.enrolment_id} row={r} requisitionId={requisitionId} onOpen={setOpen} />
          ))}
        </div>
      )}

      <CandidateDrawer
        applicantId={open?.applicant_id ?? null}
        enrolmentId={open?.enrolment_id ?? null}
        onClose={() => setOpen(null)}
      />
    </div>
  );
}
