// RequisitionDashboard — one opening, end to end. Group E, E1.
//
// The Openings list answers "which of these needs me?". This answers "what is
// actually happening inside this one?", which is a different question and
// needs a different shape.
//
// What this page is for, in the order it is laid out:
//
//   WHO IS WAITING ON A PERSON. Leading the page, because under D-05 nothing
//   advances those candidates without a human and a queue nobody opens is the
//   one backlog that cannot clear itself.
//
//   WHAT NEEDS ATTENTION HERE. The problems in this opening — held candidates,
//   links about to lapse, scoring that gave up, no live workflow — rather than
//   the company-wide list, where this opening's problems would be one line
//   among many.
//
//   WHERE PEOPLE ARE, per round of the PUBLISHED workflow rather than per
//   status, so two openings with different workflows show different rounds.
//   "Shortlisted" is the same word whether someone is waiting for round one or
//   sitting between rounds three and four.
//
//   WHO IS HELD, by name. A held candidate is paused below a threshold and
//   waiting on you; the page says so rather than letting "held" read as
//   "rejected".
//
//   WHAT THE AUTOMATION DID, and when — told apart from what a person did — and
//   WHAT WAITS FOR YOU ON PURPOSE, so nobody mistakes a human gate for a stall.
//
// It refreshes itself on the shared live interval.

import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ChevronRight,
  Copy,
  ExternalLink,
  Globe,
  Info,
  Loader2,
  Pause,
  Users,
} from '@/design/components/icons';
import { GlassCard, StatCard, StatusTag, ToggleSwitch } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { LIVE_POLL_MS } from '@/lib/polling';
import {
  getRequisitionDashboard,
  setRequisitionStatus,
  updateRequisition,
  UnresolvedCandidatesError,
  type ActivityEntry,
  type AttentionSeverity,
  type DashboardAttention,
  type RequisitionDashboard as Dashboard,
  type RequisitionStatus,
  type RoundProgress,
} from '@/api/requisitions';
import OpeningDetails from '@/components/OpeningDetails';
import { getCompanyRequisitionDashboard } from '@/api/companyBoard';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function days(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  const r = Math.round(n * 10) / 10;
  return `${r} day${r === 1 ? '' : 's'}`;
}

function ago(iso: string): string {
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} h ago`;
  const d = Math.round(hours / 24);
  return `${d} day${d === 1 ? '' : 's'} ago`;
}

/* ── Open / pause / close, with the AC-12 prompt ────────────────────────── */

/**
 * Status control for one opening.
 *
 * Closing is the only transition that can strand anybody, so it is the only
 * one that asks. The server refuses a close while candidates are mid-process
 * (409) and returns the count; that refusal is not an error to report but a
 * question to put to the manager, so it renders as a panel offering the
 * decision queue first and closing anyway second.
 *
 * Under D-05 neither answer rejects anyone: candidates in a closed opening
 * stay exactly where they are and remain in the decision queue. The panel says
 * so, because "close anyway" otherwise reads as "discard these people".
 */
function StatusControl({
  requisitionId,
  status,
  title,
}: {
  requisitionId: string;
  status: string;
  title: string;
}) {
  const qc = useQueryClient();
  const [pendingClose, setPendingClose] = useState<number | null>(null);

  const mut = useMutation({
    mutationFn: (v: { next: RequisitionStatus; ack?: boolean }) =>
      setRequisitionStatus(requisitionId, v.next, { acknowledgeUnresolved: v.ack }),
    onSuccess: (_r, v) => {
      setPendingClose(null);
      toast.success(
        v.next === 'closed'
          ? `Closed “${title}”`
          : v.next === 'paused'
            ? `Paused “${title}”`
            : `Reopened “${title}”`,
      );
      void qc.invalidateQueries({ queryKey: ['hr', 'requisition', requisitionId] });
      void qc.invalidateQueries({ queryKey: ['hr', 'requisitions'] });
      void qc.invalidateQueries({ queryKey: ['hr', 'requisition-dashboard', requisitionId] });
    },
    onError: (e) => {
      if (e instanceof UnresolvedCandidatesError) {
        setPendingClose(e.unresolved);
        return;
      }
      toast.error(errText(e, 'Could not change the status'));
    },
  });

  const btn =
    'rounded-[10px] border border-[var(--ui-line-strong)] px-3.5 py-2 text-[12.5px] text-[var(--ui-soft)] hover:border-[var(--ui-line-strong)] hover:text-foreground disabled:opacity-40';

  return (
    <>
      {status === 'open' ? (
        <>
          <button
            type="button"
            className={btn}
            disabled={mut.isPending}
            onClick={() => mut.mutate({ next: 'paused' })}
          >
            Pause
          </button>
          <button
            type="button"
            className={btn}
            disabled={mut.isPending}
            onClick={() => mut.mutate({ next: 'closed' })}
          >
            Close
          </button>
        </>
      ) : null}
      {status === 'paused' ? (
        <>
          <button
            type="button"
            className={btn}
            disabled={mut.isPending}
            onClick={() => mut.mutate({ next: 'open' })}
          >
            Reopen
          </button>
          <button
            type="button"
            className={btn}
            disabled={mut.isPending}
            onClick={() => mut.mutate({ next: 'closed' })}
          >
            Close
          </button>
        </>
      ) : null}
      {status === 'closed' ? (
        <button
          type="button"
          className={btn}
          disabled={mut.isPending}
          onClick={() => mut.mutate({ next: 'open' })}
        >
          Reopen
        </button>
      ) : null}

      {pendingClose !== null ? (
        <div
          role="alertdialog"
          aria-labelledby="close-unresolved-title"
          className="mt-3 w-full rounded-[14px] border border-[var(--ui-warn)]/30 bg-[rgba(240,180,41,0.06)] p-4"
        >
          <h3
            id="close-unresolved-title"
            className="flex items-center gap-1.5 text-[13.5px] font-semibold text-foreground"
          >
            <AlertTriangle className="h-4 w-4 text-[var(--ui-warn)]" aria-hidden="true" />
            {pendingClose} candidate{pendingClose === 1 ? '' : 's'} still undecided
          </h3>
          <p className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--ui-soft)]">
            Closing does not reject them. They stay in the decision queue exactly as
            they are — nothing here ends a candidacy without you. Decide on them now,
            or close the opening and come back to them.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Link
              to={`/hr/requisitions/${requisitionId}/decisions`}
              className="inline-flex items-center gap-1.5 rounded-[10px] bg-primary px-3.5 py-2 text-[12.5px] font-medium text-primary-foreground hover:opacity-90"
            >
              <Users className="h-3.5 w-3.5" aria-hidden="true" />
              Review {pendingClose}
            </Link>
            <button
              type="button"
              className={btn}
              disabled={mut.isPending}
              onClick={() => mut.mutate({ next: 'closed', ack: true })}
            >
              {mut.isPending ? (
                <Loader2 className="mr-1.5 inline h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : null}
              Close anyway
            </button>
            <button type="button" className={btn} onClick={() => setPendingClose(null)}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </>
  );
}

/* ── Needs attention ────────────────────────────────────────────────────── */

/** Severity carried by an icon and a word as well as colour. */
const SEVERITY: Record<AttentionSeverity, { word: string; tint: string; Icon: typeof Info }> = {
  critical: { word: 'Critical', tint: 'text-[var(--ui-danger)]', Icon: AlertTriangle },
  warning: { word: 'Needs attention', tint: 'text-[var(--ui-warn)]', Icon: AlertTriangle },
  info: { word: 'For information', tint: 'text-[var(--ui-info)]', Icon: Info },
};

function AttentionRow({ item }: { item: DashboardAttention }) {
  const tone = SEVERITY[item.severity] ?? SEVERITY.info;
  const inner = (
    <div className="flex items-start gap-2.5">
      <tone.Icon className={cn('mt-0.5 h-4 w-4 shrink-0', tone.tint)} aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="text-[13.5px] font-medium text-foreground">
          <span className="sr-only">{tone.word}: </span>
          {item.title}
        </p>
        <p className="mt-0.5 text-[12.5px] leading-relaxed text-muted-foreground">{item.body}</p>
      </div>
      {item.link ? (
        <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-[var(--ui-faint)]" aria-hidden="true" />
      ) : null}
    </div>
  );
  const shell = 'block rounded-[12px] border border-border bg-[var(--ui-inset-soft)] p-3';
  return item.link ? (
    <Link to={item.link} className={cn(shell, 'hover:border-[var(--ui-line-strong)]')}>
      {inner}
    </Link>
  ) : (
    <div className={shell}>{inner}</div>
  );
}

function NeedsAttention({ items }: { items: DashboardAttention[] }) {
  return (
    <GlassCard className="mb-5 p-5" data-testid="needs-attention">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="text-[15px] font-semibold text-foreground">Needs attention</h2>
        {items.length ? (
          <span className="text-[12px] text-muted-foreground">
            {items.length} {items.length === 1 ? 'item' : 'items'}
          </span>
        ) : null}
      </div>
      {items.length === 0 ? (
        <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
          <CheckCircle2 className="h-4 w-4 text-[var(--ui-ok)]" aria-hidden="true" />
          Nothing in this opening needs attention right now.
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {items.map((i) => (
            <AttentionRow key={i.key} item={i} />
          ))}
        </div>
      )}
    </GlassCard>
  );
}

/* ── Per-round funnel ───────────────────────────────────────────────────── */

function RoundBar({ round, widest }: { round: RoundProgress; widest: number }) {
  const pct = widest > 0 ? Math.max(2, Math.round((round.attempted / widest) * 100)) : 0;
  // A pass rate this low usually means the threshold, not the candidates —
  // flagged rather than asserted, because sometimes the round is simply hard.
  const suspicious = round.pass_rate !== null && round.attempted >= 5 && round.pass_rate < 20;

  return (
    <li className="py-2.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="min-w-0 truncate text-[13px] text-foreground">
          {round.position + 1}. {round.title}
        </span>
        <span className="shrink-0 text-[11.5px] text-muted-foreground">
          {round.at_this_round > 0 ? `${round.at_this_round} here now · ` : ''}
          {round.attempted} sat
        </span>
      </div>
      <div className="mt-1.5 flex items-center gap-2">
        <span className="h-2 flex-1 overflow-hidden rounded-full bg-[var(--ui-inset)]">
          <span
            className="block h-full rounded-full bg-[var(--accent)]/70"
            style={{ width: `${pct}%` }}
          />
        </span>
        <span
          className={cn(
            'w-[86px] shrink-0 text-right text-[11.5px] tabular-nums',
            round.pass_rate === null
              ? 'text-[var(--ui-faint)]'
              : suspicious
                ? 'text-[var(--ui-warn)]'
                : 'text-[var(--ui-ok)]',
          )}
        >
          {round.pass_rate === null ? 'not sat yet' : `${round.pass_rate}% pass`}
        </span>
      </div>
      {suspicious ? (
        <p className="mt-1 text-[11.5px] text-[var(--ui-warn)]">
          Almost nobody clears this. Worth checking the threshold before the questions.
        </p>
      ) : null}
    </li>
  );
}

/* ── Public apply ───────────────────────────────────────────────────────── */

function PublicApplyCard({
  requisitionId,
  enabled,
  status,
  hasWorkflow,
}: {
  requisitionId: string;
  enabled: boolean;
  status: string;
  /** No published workflow: the link refuses applications until there is one. */
  hasWorkflow: boolean;
}) {
  const qc = useQueryClient();
  const link = `${window.location.origin}/apply/${requisitionId}`;

  const mut = useMutation({
    mutationFn: (next: boolean) =>
      updateRequisition(requisitionId, { public_apply_enabled: next }),
    onSuccess: (_r, next) => {
      toast.success(next ? 'Applications are open' : 'Applications closed');
      void qc.invalidateQueries({ queryKey: ['hr', 'requisition-dashboard', requisitionId] });
      void qc.invalidateQueries({ queryKey: ['hr', 'requisitions'] });
    },
    onError: (e) => toast.error(errText(e, 'Could not change that')),
  });

  return (
    <GlassCard className="p-5">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Globe className="h-4 w-4 text-[var(--ui-faint)]" aria-hidden="true" />
            <span className="text-[14px] font-medium text-foreground">Accept applications</span>
          </div>
          <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
            Publishes a page anyone with the link can apply through. Off by default — a
            link that leaks should not open a channel you did not choose to open.
          </p>
        </div>
        <ToggleSwitch
          checked={enabled}
          onChange={(next) => mut.mutate(next)}
          label="Accept public applications"
        />
      </div>

      {enabled ? (
        status === 'open' && !hasWorkflow ? (
          // Said here so HR does not share a link that turns everyone away.
          <p
            className="mt-3 flex items-start gap-1.5 text-[12px] text-[var(--ui-warn)]"
            data-testid="apply-needs-workflow"
          >
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            The link will not accept applications until a workflow is published, so nobody is
            taken into a process that does not exist yet.
          </p>
        ) : status === 'open' ? (
          <div className="mt-3 flex items-center gap-2 rounded-[10px] border border-border bg-black/25 px-3 py-2">
            <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-[var(--ui-soft)]">
              {link}
            </span>
            <button
              type="button"
              onClick={() => {
                void navigator.clipboard?.writeText(link);
                toast.success('Link copied');
              }}
              className="shrink-0 rounded p-1 text-muted-foreground hover:text-foreground"
              aria-label="Copy the application link"
            >
              <Copy className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
            <a
              href={link}
              target="_blank"
              rel="noreferrer"
              className="shrink-0 rounded p-1 text-muted-foreground hover:text-foreground"
              aria-label="Open the application page"
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            </a>
          </div>
        ) : (
          // The toggle is on but the opening is paused or closed, so the page
          // 404s. Saying so beats letting someone share a dead link.
          <p className="mt-3 flex items-start gap-1.5 text-[12px] text-[var(--ui-warn)]">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            This opening is {status}, so the link will not accept applications until you
            reopen it.
          </p>
        )
      ) : null}
    </GlassCard>
  );
}

/* ── Held pool, manual steps, timing, activity ──────────────────────────── */

function HeldPool({
  data,
  requisitionId,
  readOnly = false,
}: {
  data: Dashboard;
  requisitionId: string;
  readOnly?: boolean;
}) {
  const { held_pool: pool, progress } = data;
  return (
    <GlassCard className="p-5" data-testid="held-pool">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="flex items-center gap-1.5 text-[15px] font-semibold text-foreground">
          <Pause className="h-4 w-4 text-[var(--ui-warn)]" aria-hidden="true" />
          Held for your decision
        </h2>
        <span className="text-[12px] tabular-nums text-muted-foreground">{progress.held}</span>
      </div>
      <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
        Below a round&rsquo;s threshold. Held, not rejected — they wait here until a person
        lets them continue or decides.
      </p>
      {pool.length === 0 ? (
        <p className="mt-3 text-[12.5px] text-[var(--ui-faint)]">Nobody is held.</p>
      ) : (
        <ul className="mt-3 flex flex-col divide-y divide-white/[0.05]">
          {pool.map((h) => (
            <li key={h.enrolment_id} className="py-2">
              <div className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 truncate text-[13px] text-foreground">{h.full_name}</span>
                <span className="shrink-0 text-[11.5px] text-muted-foreground">
                  {h.round_title ? `${h.round_title} · ` : ''}
                  {h.held_days !== null ? `${days(h.held_days)} held` : ''}
                </span>
              </div>
              {h.held_reason ? (
                <p className="mt-0.5 text-[12px] text-[var(--ui-soft)]">{h.held_reason}</p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {progress.held > pool.length ? (
        <p className="mt-2 text-[12px] text-muted-foreground">
          and {progress.held - pool.length} more
        </p>
      ) : null}
      {progress.held > 0 && !readOnly ? (
        <Link
          to={`/hr/requisitions/${requisitionId}/decisions`}
          className="mt-3 inline-block text-[12.5px] text-[var(--accent)] hover:underline"
        >
          Review them in the decision queue →
        </Link>
      ) : null}
    </GlassCard>
  );
}

function ManualSteps({ data }: { data: Dashboard }) {
  return (
    <GlassCard className="p-5" data-testid="manual-steps">
      <h2 className="text-[15px] font-semibold text-foreground">Waiting on you, by design</h2>
      <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
        The steps a person takes. Nothing here is stuck for want of automation.
      </p>
      {data.manual_steps.length === 0 ? (
        <p className="mt-3 text-[12.5px] text-[var(--ui-faint)]">Nothing waiting on you.</p>
      ) : (
        <ul className="mt-3 flex flex-col gap-1.5">
          {data.manual_steps.map((s) => (
            <li key={s.key}>
              {s.link ? (
                <Link
                  to={s.link}
                  className="flex items-baseline justify-between gap-3 rounded-[8px] px-1 py-0.5 text-[12.5px] hover:bg-[var(--ui-inset-soft)]"
                >
                  <span className="text-[var(--ui-soft)]">{s.label}</span>
                  <span className="tabular-nums text-foreground">{s.count}</span>
                </Link>
              ) : (
                <div className="flex items-baseline justify-between gap-3 px-1 py-0.5 text-[12.5px]">
                  <span className="text-[var(--ui-soft)]">{s.label}</span>
                  <span className="tabular-nums text-foreground">{s.count}</span>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </GlassCard>
  );
}

function TimingAndScores({ data }: { data: Dashboard }) {
  const { stage_timing: timing, scores } = data;
  return (
    <GlassCard className="p-5" data-testid="timing">
      <h2 className="text-[15px] font-semibold text-foreground">How long it takes</h2>
      <p className="mt-1 text-[12px] text-muted-foreground">
        Median time from applying, from the stage history.
      </p>
      <ul className="mt-3 flex flex-col gap-1.5">
        {timing.map((t) => (
          <li key={t.key} className="flex items-baseline justify-between gap-3 text-[12.5px]">
            <span className="min-w-0 truncate text-[var(--ui-soft)]">{t.label}</span>
            <span className="shrink-0 tabular-nums text-foreground">
              {t.median_days === null ? 'nobody yet' : days(t.median_days)}
              {t.count > 0 ? (
                <span className="text-[var(--ui-faint)]"> · {t.count}</span>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
      <div className="mt-4 border-t border-border pt-3">
        <h3 className="text-[13px] font-medium text-foreground">Scores, as summaries</h3>
        <div className="mt-1.5 flex flex-col gap-1 text-[12.5px]">
          <div className="flex justify-between gap-3">
            <span className="text-[var(--ui-soft)]">Average resume match</span>
            <span className="tabular-nums text-foreground">
              {scores.avg_ats === null ? '—' : `${Math.round(scores.avg_ats)}/100`}
            </span>
          </div>
          <div className="flex justify-between gap-3">
            <span className="text-[var(--ui-soft)]">Average across assessed rounds</span>
            <span className="tabular-nums text-foreground">
              {scores.avg_composite === null ? '—' : `${Math.round(scores.avg_composite)}%`}
            </span>
          </div>
        </div>
        <p className="mt-1.5 text-[11.5px] text-[var(--ui-faint)]">
          Scores rank and explain. Every decision is a person&rsquo;s.
        </p>
      </div>
    </GlassCard>
  );
}

function describe(a: ActivityEntry): string {
  const who = a.candidate ?? 'A candidate';
  if (a.to_round && a.to_round !== a.from_round) return `${who} moved to ${a.to_round}`;
  if (!a.to_round && a.from_round && a.from_status === a.to_status) {
    return `${who} left ${a.from_round}`;
  }
  if (a.from_status === null) return `${who} applied`;
  return `${who}: ${a.from_status} → ${a.to_status}`;
}

function Activity({ data }: { data: Dashboard }) {
  const { activity, activity_summary: summary } = data;
  return (
    <GlassCard className="p-5" data-testid="activity">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[15px] font-semibold text-foreground">Activity</h2>
        <span className="text-[12px] text-muted-foreground">
          last 7 days: {summary.automated_7d} automated · {summary.manual_7d} by people
          {summary.last_automated_at ? ` · automation last ran ${ago(summary.last_automated_at)}` : ''}
        </span>
      </div>
      {activity.length === 0 ? (
        <p className="mt-3 text-[12.5px] text-[var(--ui-faint)]">Nothing has happened yet.</p>
      ) : (
        <ul className="mt-3 flex flex-col divide-y divide-white/[0.05]">
          {activity.map((a, i) => (
            <li key={`${a.enrolment_id}-${a.occurred_at}-${i}`} className="py-2">
              <div className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 text-[12.5px] text-foreground">{describe(a)}</span>
                <span className="shrink-0 text-[11.5px] text-muted-foreground">{ago(a.occurred_at)}</span>
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[11.5px]">
                <StatusTag tone={a.automated ? 'electric' : 'lavender'}>
                  {a.automated ? 'Automated' : a.actor ?? 'A person'}
                </StatusTag>
                {a.reason ? <span className="text-[var(--ui-faint)]">{a.reason}</span> : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </GlassCard>
  );
}

/* ── Page ───────────────────────────────────────────────────────────────── */

/**
 * `readOnly` is the company super admin's view (E3): the same dashboard, from a
 * read-only endpoint, with every control and every link into HR's screens
 * removed. Nothing a super admin sees here can move a candidate or change the
 * opening — that stays with HR.
 */
export default function RequisitionDashboard({
  readOnly = false,
}: {
  readOnly?: boolean;
}): JSX.Element {
  const { requisitionId = '' } = useParams();

  const dash = useQuery({
    queryKey: [readOnly ? 'superadmin' : 'hr', 'requisition-dashboard', requisitionId],
    queryFn: () =>
      readOnly
        ? getCompanyRequisitionDashboard(requisitionId)
        : getRequisitionDashboard(requisitionId),
    enabled: Boolean(requisitionId),
    refetchInterval: LIVE_POLL_MS,
  });

  if (dash.isLoading) {
    return (
      <div className="flex items-center gap-2 p-8 text-[13px] text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        Loading…
      </div>
    );
  }
  if (dash.isError || !dash.data) {
    return (
      <div className="mx-auto max-w-[1100px] p-8">
        <GlassCard className="p-6 text-[13.5px] text-[var(--ui-danger)]">
          {errText(dash.error, 'Could not load this opening')}
        </GlassCard>
      </div>
    );
  }

  // Read-only: the links point into HR's screens, which this viewer cannot use.
  const data: Dashboard = readOnly
    ? {
        ...dash.data,
        attention: dash.data.attention.map((a) => ({ ...a, link: null })),
        manual_steps: dash.data.manual_steps.map((s) => ({ ...s, link: null })),
      }
    : dash.data;
  const { requisition: req, rounds, still_being_read, progress, workflow_state: wf } = data;
  const widest = Math.max(1, ...rounds.map((r) => r.attempted));
  const target = progress.target_hires;
  const hirePct = target ? Math.min(100, Math.round((progress.hired / target) * 100)) : null;

  return (
    <div className="mx-auto w-full max-w-[1100px] px-4 py-8">
      <Reveal>
        <header className="mb-6">
          <Link
            to={readOnly ? '/superadmin/board' : '/hr/requisitions'}
            className="mb-2 inline-flex items-center gap-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
            {readOnly ? 'Hiring board' : 'All openings'}
          </Link>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <h1 className="truncate text-[26px] font-semibold tracking-[-0.8px] text-foreground">
                {req.title}
              </h1>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-[12.5px] text-muted-foreground">
                <StatusTag
                  tone={req.status === 'open' ? 'forest' : req.status === 'paused' ? 'amber' : 'neutral'}
                  dot={req.status === 'open'}
                >
                  {req.status}
                </StatusTag>
                <span>{req.level}</span>
                {req.department ? <span>{req.department}</span> : null}
                {req.location ? <span>{req.location}</span> : null}
                {req.closes_at ? (
                  <span>closes {new Date(req.closes_at).toLocaleDateString()}</span>
                ) : null}
                {req.owner_name ? <span>owner {req.owner_name}</span> : null}
                <span data-testid="workflow-state">
                  {wf.published_version
                    ? `workflow v${wf.published_version} live`
                    : 'no live workflow'}
                  {wf.draft_version ? ` · v${wf.draft_version} draft` : ''}
                </span>
              </div>
            </div>
            {readOnly ? (
              <span className="rounded-full border border-border px-3 py-1 text-[12px] text-muted-foreground">
                Read-only
              </span>
            ) : (
            <div className="flex flex-wrap gap-2">
              <Link
                to={`/hr/requisitions/${requisitionId}/workflow`}
                className="rounded-[10px] border border-[var(--ui-line-strong)] px-3.5 py-2 text-[12.5px] text-[var(--ui-soft)] hover:border-[var(--ui-line-strong)] hover:text-foreground"
              >
                Workflow
              </Link>
              <Link
                to={`/hr/requisitions/${requisitionId}/decisions`}
                className="inline-flex items-center gap-1.5 rounded-[10px] bg-primary px-3.5 py-2 text-[12.5px] font-medium text-primary-foreground hover:opacity-90"
              >
                <Users className="h-3.5 w-3.5" aria-hidden="true" />
                Decisions
              </Link>
              <StatusControl
                requisitionId={requisitionId}
                status={req.status}
                title={req.title}
              />
            </div>
            )}
          </div>
          <p className="mt-2 text-[11.5px] text-[var(--ui-faint)]">
            Live — refreshes on its own
            {dash.dataUpdatedAt ? ` · updated ${ago(new Date(dash.dataUpdatedAt).toISOString())}` : ''}
          </p>
        </header>
      </Reveal>

      {/* Awaiting-you leads, because it is the only number here that nothing
          else in the system will clear. */}
      <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <StatCard
          label="Awaiting your decision"
          value={String(progress.awaiting_decision)}
          feature={progress.awaiting_decision > 0}
        />
        <StatCard label="Held, not rejected" value={String(progress.held)} />
        <StatCard label="In a round now" value={String(progress.in_progress)} />
        <StatCard label="Applications" value={String(progress.applications)} />
        <StatCard
          label="Hired"
          value={target ? `${progress.hired} of ${target}` : String(progress.hired)}
        />
      </div>
      {hirePct !== null ? (
        <div className="-mt-3 mb-5" aria-label={`Hiring progress: ${progress.hired} of ${target}`}>
          <span className="block h-1.5 overflow-hidden rounded-full bg-[var(--ui-inset)]">
            <span
              className="block h-full rounded-full bg-[var(--ui-ok)]/70"
              style={{ width: `${hirePct}%` }}
            />
          </span>
        </div>
      ) : null}

      <NeedsAttention items={data.attention} />

      {readOnly ? null : <OpeningDetails requisition={req} />}

      {still_being_read > 0 ? (
        <div className="mb-5 flex items-center gap-2 rounded-[14px] border border-border bg-black/25 px-4 py-3 text-[12.5px] text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          {still_being_read} CV{still_being_read === 1 ? '' : 's'} still being read and
          scored. Their names and fit scores fill in shortly.
        </div>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex flex-col gap-5">
          <GlassCard className="p-5" data-testid="funnel">
            <h2 className="text-[15px] font-semibold text-foreground">Where candidates are</h2>
            {rounds.length === 0 ? (
              <div className="mt-3">
                <p className="text-[13px] leading-relaxed text-muted-foreground">
                  No published workflow yet, so nobody is being moved through anything
                  automatically.
                </p>
                {readOnly ? null : (
                  <Link
                    to={`/hr/requisitions/${requisitionId}/workflow`}
                    className="mt-3 inline-block text-[13px] text-[var(--accent)] hover:underline"
                  >
                    Build the hiring process →
                  </Link>
                )}
              </div>
            ) : (
              <>
                <p className="mt-1 text-[12px] text-muted-foreground">
                  The rounds of workflow v{wf.published_version ?? '?'}, in order.
                  {progress.not_started > 0
                    ? ` ${progress.not_started} applied and not yet shortlisted.`
                    : ''}
                  {progress.on_older_version > 0
                    ? ` ${progress.on_older_version} still finishing an earlier version.`
                    : ''}
                </p>
                <ul className="mt-2 flex list-none flex-col divide-y divide-white/[0.05]">
                  {rounds.map((r) => (
                    <RoundBar key={r.round_id} round={r} widest={widest} />
                  ))}
                </ul>
              </>
            )}
          </GlassCard>

          <HeldPool data={data} requisitionId={requisitionId} readOnly={readOnly} />
          <Activity data={data} />
        </div>

        <div className="flex flex-col gap-5">
          <ManualSteps data={data} />
          <TimingAndScores data={data} />
          {readOnly ? (
            <GlassCard className="p-5">
              <div className="flex items-center gap-2">
                <Globe className="h-4 w-4 text-[var(--ui-faint)]" aria-hidden="true" />
                <span className="text-[14px] font-medium text-foreground">
                  Public applications {req.public_apply_enabled ? 'on' : 'off'}
                </span>
              </div>
            </GlassCard>
          ) : (
            <PublicApplyCard
              requisitionId={requisitionId}
              enabled={req.public_apply_enabled}
              status={req.status}
              hasWorkflow={data.has_published_workflow}
            />
          )}
        </div>
      </div>
    </div>
  );
}
