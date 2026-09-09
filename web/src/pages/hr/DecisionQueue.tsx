// DecisionQueue — the human end of the workflow engine (D-05).
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
// Three outcomes are offered, and the asymmetry is intentional: continuing is
// one click because it is reversible, while hire and reject take a rationale
// because they end a candidacy and are audit-logged server-side.

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
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { LIVE_POLL_MS } from '@/lib/polling';
import { getRequisition, setEnrolmentStatus } from '@/api/requisitions';
import { getDecisionQueue, releaseHold, type DecisionQueueRow } from '@/api/workflows';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function QueueCard({ row, requisitionId }: { row: DecisionQueueRow; requisitionId: string }) {
  const qc = useQueryClient();
  const [rationale, setRationale] = useState('');
  const [pending, setPending] = useState<'hired' | 'rejected' | null>(null);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['hr', 'decision-queue', requisitionId] });
    void qc.invalidateQueries({ queryKey: ['hr', 'requisition', requisitionId] });
    void qc.invalidateQueries({ queryKey: ['hr', 'requisitions'] });
  };

  const decideMut = useMutation({
    mutationFn: (decision: 'hired' | 'rejected') =>
      setEnrolmentStatus(row.enrolment_id, decision, rationale),
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
            ) : (
              <StatusTag tone="forest">finished the workflow</StatusTag>
            )}
          </div>
          {row.email ? (
            <div className="mt-0.5 truncate text-[12.5px] text-muted-foreground">{row.email}</div>
          ) : null}
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[12px] text-muted-foreground">
            <span>
              {row.rounds_taken} round{row.rounds_taken === 1 ? '' : 's'} taken
            </span>
            {row.best_percent !== null ? <span>best {Math.round(row.best_percent)}%</span> : null}
            {row.ats_overall !== null ? <span>ATS {row.ats_overall}</span> : null}
          </div>
        </div>
      </div>

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

      <label htmlFor={`why-${row.enrolment_id}`} className="mt-4 block text-[12px] text-[var(--ui-soft)]">
        Why (recorded against your name)
      </label>
      <input
        id={`why-${row.enrolment_id}`}
        value={rationale}
        onChange={(e) => setRationale(e.target.value)}
        placeholder="Optional for continuing; worth writing for a hire or reject"
        className="mt-1.5 w-full rounded-[12px] border border-border bg-secondary px-3.5 py-2.5 text-[13px] text-foreground placeholder:text-[var(--ui-faint)] focus:border-[var(--accent)] focus:outline-none"
      />

      <div className="mt-3 flex flex-wrap items-center gap-2">
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
            <span className="flex-1 text-[12.5px] text-[var(--ui-soft)]">
              {pending === 'hired' ? 'Mark hired' : 'Reject'} — {row.full_name}. This ends their
              candidacy for this opening and is recorded.
            </span>
            <button
              type="button"
              onClick={() => decideMut.mutate(pending)}
              disabled={decideMut.isPending}
              className={cn(
                'rounded-[10px] px-4 py-2 text-[12.5px] font-medium text-foreground disabled:opacity-40',
                pending === 'hired' ? 'bg-[var(--ui-ok)]' : 'bg-[var(--ui-danger)]',
              )}
            >
              {decideMut.isPending ? 'Saving…' : 'Confirm'}
            </button>
            <button
              type="button"
              onClick={() => setPending(null)}
              className="rounded-[10px] border border-[var(--ui-line-strong)] px-4 py-2 text-[12.5px] text-[var(--ui-soft)] hover:text-foreground"
            >
              Cancel
            </button>
          </div>
        ) : (
          <>
            <button
              type="button"
              onClick={() => setPending('hired')}
              className="inline-flex items-center gap-1.5 rounded-[12px] border border-[var(--ui-ok)]/35 px-4 py-2 text-[13px] font-medium text-[var(--ui-ok)] hover:bg-[var(--ui-ok)]/10"
            >
              <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
              Hire
            </button>
            <button
              type="button"
              onClick={() => setPending('rejected')}
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

export default function DecisionQueue(): JSX.Element {
  const { requisitionId = '' } = useParams();

  const req = useQuery({
    queryKey: ['hr', 'requisition', requisitionId],
    queryFn: () => getRequisition(requisitionId),
    enabled: Boolean(requisitionId),
  });

  const queue = useQuery({
    queryKey: ['hr', 'decision-queue', requisitionId],
    queryFn: () => getDecisionQueue(requisitionId),
    enabled: Boolean(requisitionId),
    refetchInterval: LIVE_POLL_MS,
  });

  const rows = queue.data ?? [];
  const held = rows.filter((r) => r.held).length;

  return (
    <div className="mx-auto w-full max-w-[900px] px-4 py-8">
      <Reveal>
        <header className="mb-6">
          <Link
            to={`/hr/requisitions/${requisitionId}/workflow`}
            className="mb-2 inline-flex items-center gap-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
            Workflow
          </Link>
          <h1 className="text-[26px] font-semibold tracking-[-0.8px] text-foreground">
            Decisions — {req.data?.title ?? 'this opening'}
          </h1>
          <p className="mt-1.5 max-w-[70ch] text-[13.5px] leading-relaxed text-muted-foreground">
            Everyone the workflow has taken as far as it can. Nothing here was decided
            automatically, and nothing here moves until you move it.
          </p>
          {held > 0 ? (
            <div className="mt-3 inline-flex items-center gap-1.5 text-[12.5px] text-[var(--ui-warn)]">
              <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
              {held} held below a round threshold
            </div>
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
            Candidates appear here when they finish the workflow or land below a round&rsquo;s
            threshold.
          </p>
        </GlassCard>
      ) : (
        <div className="flex flex-col gap-3">
          {rows.map((r) => (
            <QueueCard key={r.enrolment_id} row={r} requisitionId={requisitionId} />
          ))}
        </div>
      )}
    </div>
  );
}
