// RequisitionDashboard — the opening hub. One opening, end to end.
//
// Two tabs, carried in `?tab=`:
//
//   SETUP     The ordered journey to a live opening: role and posting, hiring
//             process, budget and approval, go live, preboarding documents. Every
//             tick is a fact the server holds (see lib/openingSetup.ts). The next
//             stage's action is the page's single primary button.
//
//   PIPELINE  What is happening inside the opening once people are in it: who is
//             waiting on a person, what needs attention, where candidates are per
//             round of the PUBLISHED workflow, who is held (by name — held is not
//             rejected), and what the automation did versus a person.
//
// The default is Pipeline whenever anyone has applied (a recruiter with forty
// live candidates should not land on setup the day someone pauses applications)
// and Setup for an opening nobody has entered yet.
//
// REQUEST DISCIPLINE. Only the dashboard query polls. Everything Setup needs is
// fetched while the Setup tab is open and not otherwise (useOpeningSetup).

import { useEffect, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Globe,
  Info,
  Loader2,
  Pause,
  Users,
} from '@/design/components/icons';
import { StatusTag, SegTabs } from '@/design/components/primitives';
import { Breadcrumbs, Page, PageBody, PageHeader } from '@/design/components/page';
import { Card } from '@/design/components/card';
import { Section } from '@/design/components/rows';
import { Collapsible, ProgressiveList } from '@/design/components/collapsible';
import { useIsDesktop } from '@/hooks/useIsDesktop';
import { useScopedPersistKey } from '@/hooks/useScopedPersistKey';
import { Stat, StatGroup } from '@/design/components/stat-group';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { LIVE_POLL_MS } from '@/lib/polling';
import { queryKeys } from '@/lib/queryKeys';
import { defaultStageKey, primaryAction, type StageKey } from '@/lib/openingSetup';
import {
  getRequisitionDashboard,
  setRequisitionStatus,
  UnresolvedCandidatesError,
  type ActivityEntry,
  type AttentionSeverity,
  type DashboardAttention,
  type RequisitionDashboard as Dashboard,
  type RequisitionStatus,
  type RoundProgress,
} from '@/api/requisitions';
import { getCompanyRequisitionDashboard } from '@/api/companyBoard';
import { appPath } from '@/lib/safeUrl';
import ApprovalPill from '@/components/hr/opening/ApprovalPill';
import SetupTab, { type RoleStepRequest } from '@/components/hr/opening/SetupTab';
import { useOpeningSetup } from '@/components/hr/opening/useOpeningSetup';

type Tab = 'setup' | 'pipeline';

const TABS: { key: Tab; label: string }[] = [
  { key: 'setup', label: 'Setup' },
  { key: 'pipeline', label: 'Pipeline' },
];

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
 * Status change for one opening, split in two so the header can lay them out
 * separately: the buttons sit with the page actions, and the close-anyway
 * question renders as its own panel under the header (it is a full-width
 * decision, not a button).
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
function useStatusChange(requisitionId: string) {
  const qc = useQueryClient();
  const [pendingClose, setPendingClose] = useState<number | null>(null);

  const mut = useMutation({
    mutationFn: (v: { next: RequisitionStatus; title: string; ack?: boolean }) =>
      setRequisitionStatus(requisitionId, v.next, { acknowledgeUnresolved: v.ack }),
    onSuccess: (_r, v) => {
      setPendingClose(null);
      toast.success(
        v.next === 'closed'
          ? `Closed “${v.title}”`
          : v.next === 'paused'
            ? `Paused “${v.title}”`
            : `Reopened “${v.title}”`,
      );
      void qc.invalidateQueries({ queryKey: queryKeys.requisition(requisitionId) });
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
  return { mut, pendingClose, setPendingClose };
}

type StatusChange = ReturnType<typeof useStatusChange>;

/** Pause / Reopen / Close — quiet ghost buttons: they are rare, not the next step. */
function StatusButtons({
  status,
  title,
  change,
}: {
  status: string;
  title: string;
  change: StatusChange;
}) {
  const { mut } = change;
  const btn = (label: string, next: RequisitionStatus) => (
    <Button
      type="button"
      variant="ghost"
      disabled={mut.isPending}
      onClick={() => mut.mutate({ next, title })}
    >
      {label}
    </Button>
  );
  return (
    <>
      {status === 'open' ? (
        <>
          {btn('Pause', 'paused')}
          {btn('Close', 'closed')}
        </>
      ) : null}
      {status === 'paused' ? (
        <>
          {btn('Reopen', 'open')}
          {btn('Close', 'closed')}
        </>
      ) : null}
      {status === 'closed' ? btn('Reopen', 'open') : null}
    </>
  );
}

function CloseUnresolvedPanel({
  requisitionId,
  title,
  change,
}: {
  requisitionId: string;
  title: string;
  change: StatusChange;
}) {
  const { mut, pendingClose, setPendingClose } = change;
  if (pendingClose === null) return null;
  return (
    <div
      role="alertdialog"
      aria-labelledby="close-unresolved-title"
      className="w-full rounded-[16px] border border-[var(--ui-warn)]/40 bg-[var(--ui-warn-wash)] p-4"
    >
      <h3
        id="close-unresolved-title"
        className="flex items-center gap-1.5 text-[15px] font-bold text-foreground"
      >
        <AlertTriangle className="h-4 w-4 text-[var(--ui-warn)]" aria-hidden="true" />
        {pendingClose} candidate{pendingClose === 1 ? '' : 's'} still undecided
      </h3>
      <p className="mt-1.5 text-[13px] leading-relaxed text-foreground">
        Closing does not reject them. They stay in the decision queue exactly as they are — nothing
        here ends a candidacy without you. Decide on them now, or close the opening and come back to
        them.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button asChild>
          <Link to={`/hr/requisitions/${requisitionId}/decisions`}>
            <Users aria-hidden="true" />
            Review {pendingClose}
          </Link>
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={mut.isPending}
          onClick={() => mut.mutate({ next: 'closed', ack: true, title })}
        >
          {mut.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
          Close anyway
        </Button>
        <Button type="button" variant="outline" onClick={() => setPendingClose(null)}>
          Cancel
        </Button>
      </div>
    </div>
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
        <p className="text-[13.5px] font-medium leading-snug text-foreground">
          <span className="sr-only">{tone.word}: </span>
          {item.title}
        </p>
        <p className="mt-0.5 line-clamp-2 text-[12.5px] leading-snug text-muted-foreground">
          {item.body}
        </p>
      </div>
      {item.link ? (
        <ChevronRight
          className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground"
          aria-hidden="true"
        />
      ) : null}
    </div>
  );
  // An inset fill, not a box inside the card.
  const shell =
    'block min-h-10 rounded-[12px] bg-[var(--ui-inset-soft)] px-3 py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]';
  return (
    <li>
      {item.link ? (
        <Link to={appPath(item.link, '/')} className={cn(shell, 'hover:bg-[var(--ui-inset)]')}>
          {inner}
        </Link>
      ) : (
        <div className={shell}>{inner}</div>
      )}
    </li>
  );
}

function NeedsAttention({ items }: { items: DashboardAttention[] }) {
  return (
    <Section
      title="Needs attention"
      data-testid="needs-attention"
      actions={
        items.length ? (
          <span className="rounded-full bg-[var(--ui-warn-wash)] px-2.5 py-1 text-[12px] font-semibold text-[var(--ui-warn)]">
            {items.length} {items.length === 1 ? 'item' : 'items'}
          </span>
        ) : null
      }
    >
      {items.length === 0 ? (
        <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
          <CheckCircle2 className="h-4 w-4 text-[var(--ui-ok)]" aria-hidden="true" />
          Nothing in this opening needs attention right now.
        </p>
      ) : (
        <ProgressiveList
          items={items}
          initial={3}
          noun="items"
          getKey={(i) => i.key}
          renderItem={(i) => <AttentionRow item={i} />}
          className="flex flex-col gap-1.5"
        />
      )}
    </Section>
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
        <span className="shrink-0 text-[12px] text-muted-foreground">
          {round.at_this_round > 0 ? `${round.at_this_round} here now · ` : ''}
          {round.attempted} sat
        </span>
      </div>
      <div className="mt-1.5 flex items-center gap-2">
        <span className="h-2 flex-1 overflow-hidden rounded-full bg-[var(--ui-inset-strong)]">
          <span
            className="block h-full rounded-full bg-[var(--accent)]"
            style={{ width: `${pct}%` }}
          />
        </span>
        <span
          className={cn(
            'w-[86px] shrink-0 text-right text-[12px] tabular-nums',
            round.pass_rate === null
              ? 'text-muted-foreground'
              : suspicious
                ? 'text-[var(--ui-warn)]'
                : 'text-[var(--ui-ok)]',
          )}
        >
          {round.pass_rate === null ? 'not sat yet' : `${round.pass_rate}% pass`}
        </span>
      </div>
      {suspicious ? (
        <p className="mt-1 text-[12px] text-[var(--ui-warn)]">
          Almost nobody clears this. Worth checking the threshold before the questions.
        </p>
      ) : null}
    </li>
  );
}

/* ── Held pool, manual steps, timing, activity ──────────────────────────── */

function HeldPool({
  data,
  requisitionId,
  readOnly = false,
  className,
}: {
  data: Dashboard;
  requisitionId: string;
  readOnly?: boolean;
  className?: string;
}) {
  const { held_pool: pool, progress } = data;
  return (
    <Section
      title="Held for your decision"
      className={className}
      data-testid="held-pool"
      description="Below a round’s threshold. Held, not rejected — they wait for a person."
      actions={
        <span className="rounded-full bg-[var(--ui-inset-strong)] px-2.5 py-1 text-[12px] font-semibold tabular-nums text-[var(--ui-soft)]">
          <Pause className="mr-1 inline h-3 w-3" aria-hidden="true" />
          {progress.held}
        </span>
      }
    >
      {pool.length === 0 ? (
        <p className="text-[13px] text-muted-foreground">Nobody is held.</p>
      ) : (
        <ProgressiveList
          items={pool}
          initial={3}
          noun="held"
          getKey={(h) => h.enrolment_id}
          className="flex flex-col divide-y divide-border"
          renderItem={(h) => (
            <li className="py-2">
              <div className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 truncate text-[13px] text-foreground">{h.full_name}</span>
                <span className="shrink-0 text-[12px] text-muted-foreground">
                  {h.round_title ? `${h.round_title} · ` : ''}
                  {h.held_days !== null ? `${days(h.held_days)} held` : ''}
                </span>
              </div>
              {h.held_reason ? (
                <p className="mt-0.5 line-clamp-2 text-[12.5px] text-[var(--ui-soft)]">
                  {h.held_reason}
                </p>
              ) : null}
            </li>
          )}
        />
      )}
      {progress.held > pool.length ? (
        <p className="mt-2 text-[13px] text-muted-foreground">
          and {progress.held - pool.length} more
        </p>
      ) : null}
      {progress.held > 0 && !readOnly ? (
        <Link
          to={`/hr/requisitions/${requisitionId}/decisions`}
          className="mt-3 inline-flex min-h-10 items-center text-[13px] font-semibold text-[var(--ui-info)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          Review them in the decision queue →
        </Link>
      ) : null}
    </Section>
  );
}

function ManualSteps({ data }: { data: Dashboard }) {
  return (
    <Section
      title="Waiting on you, by design"
      titleAs="h2"
      data-testid="manual-steps"
      description="The steps a person takes. Nothing here is stuck for want of automation."
    >
      {data.manual_steps.length === 0 ? (
        <p className="text-[13px] text-muted-foreground">Nothing waiting on you.</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {data.manual_steps.map((s) => (
            <li key={s.key}>
              {s.link ? (
                <Link
                  to={appPath(s.link, '/')}
                  className="flex min-h-9 items-center justify-between gap-3 rounded-[8px] px-1 py-0.5 text-[13px] hover:bg-[var(--ui-inset)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                >
                  <span className="text-[var(--ui-soft)]">{s.label}</span>
                  <span className="tabular-nums text-foreground">{s.count}</span>
                </Link>
              ) : (
                <div className="flex items-baseline justify-between gap-3 px-1 py-0.5 text-[13px]">
                  <span className="text-[var(--ui-soft)]">{s.label}</span>
                  <span className="tabular-nums text-foreground">{s.count}</span>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function TimingAndScores({ data }: { data: Dashboard }) {
  const { stage_timing: timing, scores } = data;
  return (
    <Section
      title="How long it takes"
      className="pt-6"
      data-testid="timing"
      description="Median time from applying, from the stage history."
    >
      <ul className="flex flex-col gap-1.5">
        {timing.map((t) => (
          <li key={t.key} className="flex items-baseline justify-between gap-3 text-[13px]">
            <span className="min-w-0 truncate text-[var(--ui-soft)]">{t.label}</span>
            <span className="shrink-0 tabular-nums text-foreground">
              {t.median_days === null ? 'nobody yet' : days(t.median_days)}
              {t.count > 0 ? <span className="text-muted-foreground"> · {t.count}</span> : null}
            </span>
          </li>
        ))}
      </ul>
      <div className="mt-4 border-t border-border pt-3">
        <h3 className="text-[15px] font-bold text-foreground">Scores, as summaries</h3>
        <div className="mt-1.5 flex flex-col gap-1 text-[13px]">
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
        <p className="mt-1.5 text-[12px] text-muted-foreground">
          Scores rank and explain. Every decision is a person&rsquo;s.
        </p>
      </div>
    </Section>
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
  const persistKey = useScopedPersistKey('hub-activity');
  const newest = activity.reduce<ActivityEntry | null>(
    (n, a) => (n === null || a.occurred_at > n.occurred_at ? a : n),
    null,
  );
  return (
    <Collapsible
      variant="card"
      title="Activity"
      count={activity.length}
      noun="event"
      latest={newest ? `${describe(newest)}, ${ago(newest.occurred_at)}` : undefined}
      persistKey={persistKey}
      data-testid="activity"
    >
      <p className="mb-2 text-[12.5px] text-muted-foreground">
        last 7 days: {summary.automated_7d} automated · {summary.manual_7d} by people
        {summary.last_automated_at ? ` · automation last ran ${ago(summary.last_automated_at)}` : ''}
      </p>
      {activity.length === 0 ? (
        <p className="text-[13px] text-muted-foreground">Nothing has happened yet.</p>
      ) : (
        <ProgressiveList
          items={activity}
          initial={5}
          noun="events"
          getKey={(a) => `${a.enrolment_id}-${a.occurred_at}`}
          className="flex flex-col divide-y divide-border"
          renderItem={(a) => (
            <li className="py-2">
              <div className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 text-[13px] text-foreground">{describe(a)}</span>
                <span className="shrink-0 text-[12px] text-muted-foreground">
                  {ago(a.occurred_at)}
                </span>
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[12px]">
                <StatusTag tone={a.automated ? 'electric' : 'lavender'}>
                  {a.automated ? 'Automated' : (a.actor ?? 'A person')}
                </StatusTag>
                {a.reason ? <span className="text-muted-foreground">{a.reason}</span> : null}
              </div>
            </li>
          )}
        />
      )}
    </Collapsible>
  );
}

/* ── Pipeline tab ───────────────────────────────────────────────────────── */

function PipelineTab({
  data,
  requisitionId,
  readOnly,
  onOpenSetup,
}: {
  data: Dashboard;
  requisitionId: string;
  readOnly: boolean;
  onOpenSetup: () => void;
}) {
  const { requisition: req, rounds, still_being_read, progress, workflow_state: wf } = data;
  const widest = Math.max(1, ...rounds.map((r) => r.attempted));
  const target = progress.target_hires;
  const hirePct = target ? Math.min(100, Math.round((progress.hired / target) * 100)) : null;
  const decisions = readOnly ? undefined : `/hr/requisitions/${requisitionId}/decisions`;
  const desktop = useIsDesktop();
  const asideKey = useScopedPersistKey('hub-pipeline-aside');

  const asideBody = (
    <div className="flex flex-col gap-6">
      <ManualSteps data={data} />
      <TimingAndScores data={data} />
      <Section
        title={`Public applications ${req.public_apply_enabled ? 'on' : 'off'}`}
        className="pt-6"
      >
        <p className="flex items-start gap-1.5 text-[13px] text-muted-foreground">
          <Globe className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {readOnly
            ? 'Read-only — HR controls whether this opening takes applications.'
            : 'Switch this on or off, and schedule it, from the Setup tab.'}
        </p>
        {readOnly ? null : (
          <Button type="button" variant="outline" className="mt-3" onClick={onOpenSetup}>
            Open Setup
          </Button>
        )}
      </Section>
    </div>
  );
  // At lg+ the process, timing and applications sit in the aside, always in
  // view. Below it they would be a second screen of scrolling after everything
  // else, so they fold into one closed section.
  const aside = desktop ? (
    <Card density="compact">{asideBody}</Card>
  ) : (
    <Collapsible
      variant="card"
      headingLevel="h2"
      title="Process, timing and applications"
      summary="Steps waiting on you, how long it takes, public applications"
      persistKey={asideKey}
      data-testid="pipeline-aside"
    >
      {asideBody}
    </Collapsible>
  );

  return (
    <div className="flex flex-col gap-6">
      {/* Awaiting-you leads, because it is the only number here that nothing
          else in the system will clear. */}
      <div>
        <StatGroup aria-label="Pipeline summary" data-testid="pipeline-stats">
          <Stat label="Awaiting your decision" value={progress.awaiting_decision} to={decisions} />
          <Stat label="Held, not rejected" value={progress.held} />
          <Stat label="In a round now" value={progress.in_progress} />
          <Stat label="Applications" value={progress.applications} />
          <Stat
            label="Hired"
            value={target ? `${progress.hired} of ${target}` : String(progress.hired)}
          />
        </StatGroup>
        {hirePct !== null ? (
          <div className="mt-3" aria-label={`Hiring progress: ${progress.hired} of ${target}`}>
            <span className="block h-1.5 overflow-hidden rounded-full bg-[var(--ui-inset-strong)]">
              <span
                className="block h-full rounded-full bg-[var(--ui-ok)]"
                style={{ width: `${hirePct}%` }}
              />
            </span>
          </div>
        ) : null}
      </div>

      <PageBody aside={aside} asideLabel="Process and timing">
        {/* The one raised region: where everybody is. */}
        <Card
          as="section"
          emphasis="raised"
          density="compact"
          title="Where candidates are"
          data-testid="funnel"
        >
          {rounds.length === 0 ? (
            <div>
              <p className="text-[13px] leading-relaxed text-muted-foreground">
                No published workflow yet, so nobody is being moved through anything automatically.
              </p>
              {readOnly ? null : (
                <Link
                  to={`/hr/requisitions/${requisitionId}/workflow`}
                  className="mt-3 inline-flex min-h-10 items-center text-[14px] font-semibold text-[var(--ui-info)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                >
                  Build the hiring process →
                </Link>
              )}
            </div>
          ) : (
            <>
              <p className="text-[13px] text-muted-foreground">
                The rounds of workflow v{wf.published_version ?? '?'}, in order.
                {progress.not_started > 0
                  ? ` ${progress.not_started} applied and not yet shortlisted.`
                  : ''}
                {progress.on_older_version > 0
                  ? ` ${progress.on_older_version} still finishing an earlier version.`
                  : ''}
              </p>
              <ul className="mt-2 flex list-none flex-col divide-y divide-border">
                {rounds.map((r) => (
                  <RoundBar key={r.round_id} round={r} widest={widest} />
                ))}
              </ul>
            </>
          )}
          {still_being_read > 0 && !data.attention.some((a) => /being read/i.test(a.title)) ? (
            <p className="mt-4 flex items-center gap-2 border-t border-border pt-3 text-[13px] text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" aria-hidden="true" />
              {still_being_read} CV{still_being_read === 1 ? '' : 's'} still being read and scored.
              Their names and fit scores fill in shortly.
            </p>
          ) : null}
        </Card>

        {/* What needs a person, grouped: the open items and the people held. */}
        <Card density="compact">
          <div className="grid gap-6 md:grid-cols-2 md:gap-0 md:divide-x md:divide-border">
            <div className="md:pr-6">
              <NeedsAttention items={data.attention} />
            </div>
            <div className="border-t border-border pt-6 md:border-t-0 md:pl-6 md:pt-0">
              <HeldPool
                data={data}
                requisitionId={requisitionId}
                readOnly={readOnly}
                className="!border-t-0 !pt-0"
              />
            </div>
          </div>
        </Card>

        <Activity data={data} />
      </PageBody>
    </div>
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
  const [params, setParams] = useSearchParams();
  const [pickedStage, setPickedStage] = useState<StageKey | null>(null);
  const [roleStepRequest, setRoleStepRequest] = useState<RoleStepRequest | undefined>(undefined);
  const statusChange = useStatusChange(requisitionId);

  // The ONLY polling query on this page.
  const dash = useQuery({
    queryKey: [readOnly ? 'superadmin' : 'hr', 'requisition-dashboard', requisitionId],
    queryFn: () =>
      readOnly
        ? getCompanyRequisitionDashboard(requisitionId)
        : getRequisitionDashboard(requisitionId),
    enabled: Boolean(requisitionId),
    refetchInterval: LIVE_POLL_MS,
  });

  // The default tab is decided once, from the first payload: if it were
  // recomputed on every poll, the first application to arrive would pull a
  // recruiter out of Setup in the middle of editing.
  const defaultTab = useRef<Tab | null>(null);
  if (defaultTab.current === null && dash.data) {
    defaultTab.current = dash.data.requisition.total_enrolments > 0 ? 'pipeline' : 'setup';
  }
  const asked = params.get('tab');
  const tab: Tab =
    asked === 'setup' || asked === 'pipeline' ? asked : (defaultTab.current ?? 'pipeline');

  const [setupOpened, setSetupOpened] = useState(false);
  if (tab === 'setup' && !setupOpened) setSetupOpened(true);

  const setup = useOpeningSetup({
    requisitionId,
    dashboard: dash.data,
    dashboardUpdatedAt: dash.dataUpdatedAt,
    enabled: tab === 'setup' && Boolean(dash.data),
    readOnly,
  });

  // The open stage is PINNED once it is shown. It used to follow
  // defaultStageKey on every render, so the 15s dashboard poll, or finishing a
  // stage, swapped the panel out from under someone working in it. The default
  // is resolved once, only after the facts that decide it have loaded; a stage
  // the user picks wins and stays until they pick another.
  const [resolvedStage, setResolvedStage] = useState<StageKey | null>(null);
  const defaultStage =
    setup.ready && setup.stages.length > 0 ? defaultStageKey(setup.stages) : null;
  useEffect(() => {
    if (resolvedStage === null && defaultStage !== null) setResolvedStage(defaultStage);
  }, [resolvedStage, defaultStage]);

  function chooseTab(next: string): void {
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        p.set('tab', next);
        return p;
      },
      { replace: true },
    );
  }

  if (dash.isLoading) {
    return (
      <Page>
        <div className="flex flex-col gap-4" role="status">
          <Skeleton className="h-10 w-72 rounded-[12px]" />
          <Skeleton className="h-24 rounded-card" />
          <Skeleton className="h-48 rounded-card" />
          <span className="sr-only">Loading…</span>
        </div>
      </Page>
    );
  }
  if (dash.isError || !dash.data) {
    return (
      <Page>
        <Card>
          <p role="alert" className="text-[14px] text-[var(--ui-danger)]">
            {errText(dash.error, 'Could not load this opening')}
          </p>
        </Card>
      </Page>
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
  // The freshest copy: after a panel saves, the record query is newer than the poll.
  const req = setup.requisition ?? data.requisition;
  const wf = data.workflow_state;

  const stageKey = pickedStage ?? resolvedStage ?? defaultStage;
  const next = tab === 'setup' && !readOnly ? primaryAction(setup.stages) : null;

  return (
    <Page>
      <PageHeader
        compact
        breadcrumbs={
          <Breadcrumbs
            items={[
              {
                label: readOnly ? 'Hiring board' : 'Openings',
                to: readOnly ? '/superadmin/board' : '/hr/requisitions',
              },
              { label: 'Opening dashboard' },
            ]}
          />
        }
        title={req.title}
        badges={
          <>
            <StatusTag
              tone={
                req.status === 'open' ? 'forest' : req.status === 'paused' ? 'amber' : 'neutral'
              }
              dot={req.status === 'open'}
            >
              {req.status}
            </StatusTag>
            <ApprovalPill status={req.approval_status} />
          </>
        }
        meta={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-muted-foreground">
            <span>{req.level}</span>
            {req.department ? <span>{req.department}</span> : null}
            {req.location ? <span>{req.location}</span> : null}
            {req.closes_at ? (
              <span>closes {new Date(req.closes_at).toLocaleDateString()}</span>
            ) : null}
            {req.owner_name ? <span>owner {req.owner_name}</span> : null}
            <span data-testid="workflow-state">
              {wf.published_version ? `workflow v${wf.published_version} live` : 'no live workflow'}
              {wf.draft_version ? ` · v${wf.draft_version} draft` : ''}
            </span>
          </span>
        }
        actions={
          readOnly ? (
            <span className="rounded-full border border-border px-3 py-1 text-[13px] font-semibold text-muted-foreground">
              Read-only
            </span>
          ) : (
            <>
              <StatusButtons status={req.status} title={req.title} change={statusChange} />
              <Button asChild variant="outline">
                <Link to={`/hr/requisitions/${requisitionId}/workflow`}>Workflow</Link>
              </Button>
              {next ? (
                <Button asChild variant="outline">
                  <Link to={`/hr/requisitions/${requisitionId}/decisions`}>
                    <Users aria-hidden="true" />
                    Decisions
                  </Link>
                </Button>
              ) : null}
              {next ? (
                next.to ? (
                  <Button asChild>
                    <Link to={next.to}>{next.label}</Link>
                  </Button>
                ) : (
                  <Button
                    type="button"
                    onClick={() => {
                      setPickedStage(next.stageKey);
                      // Stage 1's next action is about the job description, so
                      // open on that sub-step rather than on the first.
                      if (next.stageKey === 'role') {
                        setRoleStepRequest((r) => ({ step: 'jd', n: (r?.n ?? 0) + 1 }));
                      }
                    }}
                  >
                    {next.label}
                  </Button>
                )
              ) : (
                <Button asChild>
                  <Link to={`/hr/requisitions/${requisitionId}/decisions`}>
                    <Users aria-hidden="true" />
                    Decisions
                  </Link>
                </Button>
              )}
            </>
          )
        }
      />

      {readOnly ? null : (
        <CloseUnresolvedPanel
          requisitionId={requisitionId}
          title={req.title}
          change={statusChange}
        />
      )}

      {/* Sticky, so the tabs are always one click away however far the page is
          scrolled. top-16 clears the shell's own sticky top bar. */}
      <div
        data-testid="hub-subheader"
        className="sticky top-16 z-20 -mx-4 -my-2 flex flex-wrap items-center justify-between gap-x-6 gap-y-2 bg-background/90 px-4 py-2 backdrop-blur sm:-mx-6 sm:px-6 lg:-mx-10 lg:px-10"
      >
        <SegTabs tabs={TABS} active={tab} onChange={chooseTab} />
        {/* Demoted: a quiet freshness note, not a header line of its own. */}
        <p className="text-[12px] text-muted-foreground">
          Live — refreshes on its own
          {dash.dataUpdatedAt
            ? ` · updated ${ago(new Date(dash.dataUpdatedAt).toISOString())}`
            : ''}
        </p>
      </div>

      {/* Setup stays mounted (hidden) once it has been opened, so a trip to
          Pipeline does not throw away what was typed and not yet saved. */}
      {tab === 'setup' || setupOpened ? (
        <div role="tabpanel" aria-label="Setup" hidden={tab !== 'setup'}>
          {setup.stages.length > 0 && stageKey === null ? (
            // Not mounting any stage until the default is known: stage 1's four
            // panels would fire their requests just to be replaced.
            <div className="flex flex-col gap-4" role="status" data-testid="setup-loading">
              <Skeleton className="h-24 rounded-card" />
              <Skeleton className="h-48 rounded-card" />
              <span className="sr-only">Loading setup…</span>
            </div>
          ) : setup.stages.length > 0 && stageKey !== null ? (
            <SetupTab
              stages={setup.stages}
              activeKey={stageKey}
              onSelect={setPickedStage}
              requisition={req}
              hasPublishedWorkflow={data.has_published_workflow}
              readOnly={readOnly}
              roleStepRequest={roleStepRequest}
            />
          ) : null}
        </div>
      ) : null}

      {tab === 'setup' ? null : (
        <div role="tabpanel" aria-label="Pipeline">
          <PipelineTab
            data={data}
            requisitionId={requisitionId}
            readOnly={readOnly}
            onOpenSetup={() => {
              setPickedStage('golive');
              chooseTab('setup');
            }}
          />
        </div>
      )}
    </Page>
  );
}
