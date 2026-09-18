// WorkflowReviews — PH4-O6, decision D4-2. The company super admin's queue of
// workflow versions waiting for approval, and one version in full.
//
// Two views, one file: `/superadmin/workflow-reviews` (the queue) and
// `/superadmin/workflow-reviews/:workflowId` (one version, with everything a
// reviewer needs to decide — rounds, branches, criteria, the validation
// report, the dry run, stage owners/SLAs, and the history). The notification
// the backend sends when a version is submitted links straight to the detail
// route.
//
// Same account boundary as ApprovalQueue: only a super admin can reach these
// endpoints, and a version cannot be approved by the person who wrote it —
// enforced server-side, surfaced here as a 403 if it is ever reached anyway.

import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  ClipboardCheck,
  Loader2,
  XCircle,
} from '@/design/components/icons';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { cn } from '@/lib/utils';
import { toast } from '@/lib/toast';
import {
  approveWorkflowReview,
  getWorkflowReviewDetail,
  listPendingWorkflowReviews,
  requestWorkflowChanges,
  type ReviewAction,
  type SimulationResult,
  type SimulationRound,
  type WorkflowReviewDetail,
} from '@/api/workflowReview';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

const CHANGES_NOTE_MIN = 10;

function waiting(iso: string | null): string {
  if (!iso) return '';
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (Number.isNaN(days)) return '';
  if (days <= 0) return 'Submitted today';
  return days === 1 ? 'Waiting 1 day' : `Waiting ${days} days`;
}

/* ── The queue ──────────────────────────────────────────────────────────── */

function ReviewQueue(): JSX.Element {
  const queue = useQuery({
    queryKey: ['admin', 'workflow-reviews'],
    queryFn: listPendingWorkflowReviews,
  });

  return (
    <div className="mx-auto w-full max-w-[860px] px-4 py-8">
      <Reveal>
        <h1 className="text-[22px] font-semibold text-foreground">Workflow reviews</h1>
        <p className="mt-1 text-[13px] text-muted-foreground">
          Hiring workflows your HR managers have submitted for approval before they can go
          live. Oldest request first.
        </p>
      </Reveal>

      {queue.isLoading ? (
        <p className="mt-6 flex items-center gap-2 text-[13px] text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          Loading…
        </p>
      ) : queue.isError ? (
        <p className="mt-6 text-[13px] text-[var(--ui-danger)]">
          Could not load the workflow review queue.
        </p>
      ) : (queue.data ?? []).length === 0 ? (
        <GlassCard className="mt-6 p-6 text-center">
          <p className="text-[13.5px] text-foreground">Nothing is waiting on you.</p>
          <p className="mt-1 text-[12.5px] text-muted-foreground">
            A workflow your HR managers submit for review appears here.
          </p>
        </GlassCard>
      ) : (
        <div className="mt-6 flex flex-col gap-3">
          {(queue.data ?? []).map((row) => (
            <GlassCard key={row.workflow_id} className="p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <Link
                    to={`/superadmin/workflow-reviews/${row.workflow_id}`}
                    className="text-[15px] font-semibold text-foreground hover:underline"
                  >
                    {row.opening_title} · v{row.version}
                  </Link>
                  <p className="mt-0.5 text-[12.5px] text-muted-foreground">
                    {row.rounds} round{row.rounds === 1 ? '' : 's'}
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-[12px] text-muted-foreground">{waiting(row.submitted_at)}</p>
                  {row.submitted_by_name ? (
                    <p className="text-[11.5px] text-[var(--ui-faint)]">
                      by {row.submitted_by_name}
                    </p>
                  ) : null}
                </div>
              </div>
              {row.note ? (
                <p className="mt-2 rounded-[10px] border border-border bg-[var(--ui-inset-soft)] px-3 py-2 text-[12.5px] text-[var(--ui-soft)]">
                  {row.note}
                </p>
              ) : null}
              <Link
                to={`/superadmin/workflow-reviews/${row.workflow_id}`}
                className="mt-3 inline-flex items-center gap-1.5 text-[12.5px] text-[var(--accent)] hover:underline"
              >
                Review this version
              </Link>
            </GlassCard>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── The dry run, read-only (the version's own submitted result) ─────────── */

const STATE_WORD: Record<SimulationRound['state'], string> = { ok: 'OK', warning: 'Warning', error: 'Error' };

function SimulationReadout({ simulation }: { simulation: SimulationResult | null }): JSX.Element {
  const [scenariosOpen, setScenariosOpen] = useState(false);
  if (!simulation) {
    return <p className="text-[12.5px] text-muted-foreground">No dry run has been recorded for this version.</p>;
  }
  return (
    <div className="flex flex-col gap-3">
      {simulation.stale ? (
        <p
          role="status"
          className="flex items-center gap-1.5 rounded-[10px] border border-[var(--ui-warn)]/30 bg-[var(--ui-warn)]/[0.06] px-3 py-2 text-[12px] text-[var(--ui-soft)]"
        >
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-[var(--ui-warn)]" aria-hidden="true" />
          Stale — the workflow changed since this run.
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-muted-foreground">
        <StatusTag
          tone={simulation.status === 'passed' ? 'forest' : simulation.status === 'warnings' ? 'amber' : 'ember'}
          dot
        >
          {simulation.status === 'passed' ? 'Passed' : simulation.status === 'warnings' ? 'Needs attention' : 'Failed'}
        </StatusTag>
        <span>
          {simulation.errors} error{simulation.errors === 1 ? '' : 's'} &middot; {simulation.warnings} warning
          {simulation.warnings === 1 ? '' : 's'}
        </span>
        {simulation.run_by_name ? <span>by {simulation.run_by_name}</span> : null}
      </div>
      <ul className="flex flex-col gap-2">
        {simulation.rounds.map((r) => (
          <li key={r.round_id} className="rounded-[10px] border border-border p-3">
            <div className="flex items-center gap-2 text-[12.5px]">
              <span className="font-medium text-foreground">
                {r.position + 1}. {r.title}
              </span>
              <span className="text-muted-foreground">{STATE_WORD[r.state]}</span>
            </div>
            {r.findings.length > 0 ? (
              <ul className="mt-1.5 flex flex-col gap-1 pl-2">
                {r.findings.map((f, i) => (
                  <li
                    key={i}
                    className={cn(
                      'text-[12px]',
                      f.severity === 'error' ? 'text-[var(--ui-danger)]' : 'text-[var(--ui-warn)]',
                    )}
                  >
                    <span className="mr-1 text-[10px] font-semibold uppercase">{f.severity}</span>
                    {f.message}
                  </li>
                ))}
              </ul>
            ) : null}
          </li>
        ))}
      </ul>
      <div>
        <button
          type="button"
          onClick={() => setScenariosOpen((o) => !o)}
          aria-expanded={scenariosOpen}
          className="flex items-center gap-1.5 text-[12.5px] text-[var(--accent)] hover:underline"
        >
          <ChevronDown
            className={cn('h-3.5 w-3.5 transition-transform', scenariosOpen && 'rotate-180')}
            aria-hidden="true"
          />
          {scenariosOpen ? 'Hide' : 'Show'} {simulation.scenarios.length} scenario
          {simulation.scenarios.length === 1 ? '' : 's'}
        </button>
        {scenariosOpen ? (
          <ol className="mt-2 flex flex-col gap-1.5">
            {simulation.scenarios.map((s) => (
              <li key={s.id} className="rounded-[10px] border border-border p-2.5 text-[12px] text-[var(--ui-soft)]">
                <span className="font-medium text-foreground">{s.id}</span> — {s.description} —{' '}
                {s.end === 'decision' ? 'reaches a decision' : s.end === 'held' ? 'is held' : 'could not finish'}
              </li>
            ))}
          </ol>
        ) : null}
      </div>
    </div>
  );
}

/* ── One version, in full ──────────────────────────────────────────────── */

const ACTION_WORDS: Record<ReviewAction, string> = {
  submitted: 'Submitted for review',
  withdrawn: 'Withdrawn from review',
  approved: 'Approved',
  changes_requested: 'Changes requested',
  reopened: 'Reopened for edits',
};

function ReviewDetail({ workflowId }: { workflowId: string }): JSX.Element {
  const qc = useQueryClient();
  const [note, setNote] = useState('');
  const [mode, setMode] = useState<'approve' | 'changes' | null>(null);

  const detail = useQuery({
    queryKey: ['admin', 'workflow-reviews', workflowId],
    queryFn: () => getWorkflowReviewDetail(workflowId),
  });

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['admin', 'workflow-reviews', workflowId] });
    void qc.invalidateQueries({ queryKey: ['admin', 'workflow-reviews'] });
  };

  const approveMut = useMutation({
    mutationFn: () => approveWorkflowReview(workflowId, note),
    onSuccess: () => {
      setMode(null);
      setNote('');
      invalidate();
      toast.success('Approved — HR can publish it now');
    },
    onError: (e) => toast.error(errText(e, 'Could not approve this version')),
  });

  const changesMut = useMutation({
    mutationFn: () => requestWorkflowChanges(workflowId, note),
    onSuccess: () => {
      setMode(null);
      setNote('');
      invalidate();
      toast.success('Sent back with your note');
    },
    onError: (e) => toast.error(errText(e, 'Could not send this back')),
  });

  if (detail.isLoading) {
    return (
      <div className="mx-auto w-full max-w-[900px] px-4 py-16">
        <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading…
        </p>
      </div>
    );
  }
  if (detail.isError || !detail.data) {
    return (
      <div className="mx-auto w-full max-w-[900px] px-4 py-16">
        <p className="text-[13px] text-[var(--ui-danger)]">Could not load this workflow version.</p>
      </div>
    );
  }

  const wf: WorkflowReviewDetail = detail.data;
  const canDecide = wf.review.review_status === 'in_review';
  const changesReady = note.trim().length >= CHANGES_NOTE_MIN;

  return (
    <div className="mx-auto w-full max-w-[900px] px-4 py-8">
      <Reveal>
        <Link
          to="/superadmin/workflow-reviews"
          className="mb-2 inline-flex items-center gap-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
          Workflow reviews
        </Link>
        <h1 className="text-[24px] font-semibold tracking-[-0.6px] text-foreground">
          {wf.opening_title} · v{wf.version}
        </h1>
        <p className="mt-1.5 text-[13px] text-muted-foreground">
          {wf.rounds.length} round{wf.rounds.length === 1 ? '' : 's'} &middot; submitted by{' '}
          {wf.review.submitted_by_name ?? 'someone no longer with the company'}
          {wf.review.note ? ` — “${wf.review.note}”` : ''}
        </p>
      </Reveal>

      <div className="mt-6 grid gap-5">
        <GlassCard className="p-5">
          <h2 className="mb-3 text-[14px] font-semibold text-foreground">Rounds &amp; routing</h2>
          <ol className="flex flex-col gap-3">
            {wf.rounds.map((r) => (
              <li key={r.round_id} className="rounded-[12px] border border-border p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[13.5px] font-medium text-foreground">
                    {r.position + 1}. {r.title}
                  </span>
                  <StatusTag tone="neutral">{r.kind}</StatusTag>
                  {r.pass_threshold !== null ? (
                    <span className="text-[12px] text-muted-foreground">
                      advance at {r.pass_threshold}%
                    </span>
                  ) : null}
                </div>
                {r.criteria.length > 0 ? (
                  <p className="mt-1.5 text-[12px] text-[var(--ui-soft)]">
                    Assesses: {r.criteria.map((c) => c.name).join(', ')}
                  </p>
                ) : null}
                <div className="mt-1.5 flex flex-col gap-0.5 text-[12px] text-muted-foreground">
                  <span>
                    If they pass &rarr; <span className="text-foreground">{r.on_pass ?? 'Final decision'}</span>
                  </span>
                  <span>
                    If below the threshold &rarr;{' '}
                    <span className="text-foreground">{r.on_fail ?? 'Hold for a person'}</span>
                  </span>
                  {r.on_fast_track ? (
                    <span>
                      Fast-track at {r.fast_track_min_percent}% &rarr;{' '}
                      <span className="text-foreground">{r.on_fast_track}</span>
                    </span>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
        </GlassCard>

        <GlassCard className="p-5">
          <h2 className="mb-3 text-[14px] font-semibold text-foreground">Validation</h2>
          {wf.validation.errors.length === 0 && wf.validation.warnings.length === 0 ? (
            <p className="flex items-center gap-1.5 text-[12.5px] text-[var(--ui-ok)]">
              <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
              Nothing to fix.
            </p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {wf.validation.errors.map((e) => (
                <li key={e} className="flex items-start gap-1.5 text-[12.5px] text-[var(--ui-danger)]">
                  <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  {e}
                </li>
              ))}
              {wf.validation.warnings.map((w) => (
                <li key={w} className="flex items-start gap-1.5 text-[12.5px] text-[var(--ui-warn)]">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  {w}
                </li>
              ))}
            </ul>
          )}
        </GlassCard>

        <GlassCard className="p-5">
          <h2 className="mb-3 text-[14px] font-semibold text-foreground">Dry run</h2>
          <SimulationReadout simulation={wf.simulation} />
        </GlassCard>

        <GlassCard className="p-5">
          <h2 className="mb-3 text-[14px] font-semibold text-foreground">Stage owners &amp; SLAs</h2>
          {wf.stages.length === 0 ? (
            <p className="text-[12.5px] text-muted-foreground">No stages recorded.</p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {wf.stages.map((s) => (
                <li
                  key={s.round_id ?? '__decision__'}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-[10px] border border-border px-3 py-2 text-[12.5px]"
                >
                  <span className="text-foreground">{s.stage}</span>
                  <span className="text-muted-foreground">
                    {s.owner_name ?? 'Unassigned'}
                    {s.sla_hours ? ` · ${s.sla_hours}h SLA` : ''}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </GlassCard>

        <GlassCard className="p-5">
          <h2 className="mb-3 flex items-center gap-2 text-[14px] font-semibold text-foreground">
            <ClipboardCheck className="h-4 w-4" aria-hidden="true" />
            History
          </h2>
          {wf.review.history.length === 0 ? (
            <p className="text-[12.5px] text-muted-foreground">No activity recorded yet.</p>
          ) : (
            <ol className="flex flex-col gap-1.5 border-l border-border pl-3">
              {wf.review.history.map((h, i) => (
                <li key={i} className="text-[12.5px] text-[var(--ui-soft)]">
                  {ACTION_WORDS[h.action]}
                  {h.actor_name ? ` — ${h.actor_name}` : ''}
                  <span className="ml-1.5 text-[11px] text-[var(--ui-faint)]">
                    {new Date(h.at).toLocaleString()}
                  </span>
                  {h.note ? <span className="block text-[11.5px] text-muted-foreground">{h.note}</span> : null}
                </li>
              ))}
            </ol>
          )}
        </GlassCard>

        {canDecide ? (
          <GlassCard className="p-5">
            <h2 className="mb-3 text-[14px] font-semibold text-foreground">Your decision</h2>
            {mode === null ? (
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => setMode('approve')}
                  className="inline-flex items-center gap-1.5 rounded-[10px] border border-[var(--ui-ok)]/35 px-4 py-2 text-[13px] font-medium text-[var(--ui-ok)] hover:bg-[var(--ui-ok)]/10"
                >
                  <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                  Approve
                </button>
                <button
                  type="button"
                  onClick={() => setMode('changes')}
                  className="inline-flex items-center gap-1.5 rounded-[10px] border border-[var(--ui-line-strong)] px-4 py-2 text-[13px] text-muted-foreground hover:border-[var(--ui-warn)]/40 hover:text-[var(--ui-warn)]"
                >
                  <XCircle className="h-4 w-4" aria-hidden="true" />
                  Request changes
                </button>
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                <label htmlFor="review-decision-note" className="text-[12px] font-medium text-[var(--ui-soft)]">
                  {mode === 'approve' ? 'Note (optional)' : 'What needs to change (required)'}
                </label>
                <input
                  id="review-decision-note"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder={mode === 'changes' ? `At least ${CHANGES_NOTE_MIN} characters` : undefined}
                  className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
                />
                {mode === 'changes' && !changesReady ? (
                  <p className="text-[11.5px] text-[var(--ui-warn)]">
                    Write at least {CHANGES_NOTE_MIN} characters.
                  </p>
                ) : null}
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => (mode === 'approve' ? approveMut.mutate() : changesMut.mutate())}
                    disabled={
                      (mode === 'approve' ? approveMut.isPending : changesMut.isPending) ||
                      (mode === 'changes' && !changesReady)
                    }
                    className="rounded-[10px] bg-primary px-4 py-2 text-[12.5px] font-medium text-primary-foreground disabled:opacity-40"
                  >
                    {mode === 'approve'
                      ? approveMut.isPending
                        ? 'Approving…'
                        : 'Confirm approval'
                      : changesMut.isPending
                        ? 'Sending…'
                        : 'Send back'}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setMode(null);
                      setNote('');
                    }}
                    className="rounded-[10px] border border-[var(--ui-line-strong)] px-4 py-2 text-[12.5px] text-[var(--ui-soft)] hover:text-foreground"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </GlassCard>
        ) : (
          <GlassCard className="p-5">
            <p className="text-[12.5px] text-muted-foreground">
              This version is {wf.review.review_status.replace('_', ' ')} — nothing for you to
              decide right now.
            </p>
          </GlassCard>
        )}
      </div>
    </div>
  );
}

/* ── Page ───────────────────────────────────────────────────────────────── */

export default function WorkflowReviews(): JSX.Element {
  const { workflowId } = useParams();
  return workflowId ? <ReviewDetail workflowId={workflowId} /> : <ReviewQueue />;
}
