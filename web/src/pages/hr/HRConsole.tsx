// HRConsole — HR manager landing page.
// Layout: ONE raised region, "Needs you today" (attention findings and stages at
//   risk, each with its real count); then the openings still in setup, each
//   linking to its setup tab; then the funnel as one stat group whose cells
//   link to their lists; then recent activity. Shortcuts and the careers link
//   sit in the aside.
// Behavior: live getMe query for greeting; the stat group maps the live
//   analytics query; the activity feed is empty-safe.
// No AppShell wrapper — the app shell is provided by the route.

import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getMe } from '@/api/auth';
import { getHrAnalytics } from '@/api/hr';
import { listNotifications, type NotificationItem } from '@/api/notifications';
import { listRequisitions } from '@/api/requisitions';
import AttentionPanel from '@/components/AttentionPanel';
import CareersLinkCard from '@/components/hr/CareersLinkCard';
import StagesAtRiskWidget from '@/components/StagesAtRiskWidget';
import { useAttention, useStagesAtRisk } from '@/hooks/useHrHomeQueries';
import { useAuth } from '@/context/AuthContext';
import { Collapsible, ProgressiveList } from '@/design/components/collapsible';
import { useScopedPersistKey } from '@/hooks/useScopedPersistKey';
import { isInSetup, nextSetupStage, setupBadge } from '@/lib/hrHome';
import { LIVE_POLL_MS } from '@/lib/polling';
import { cn } from '@/lib/utils';
import { EmptyState, Page, PageBody, PageHeader } from '@/design/components/page';
import { StatusTag, type TagTone } from '@/design/components/primitives';
import { Card } from '@/design/components/card';
import { Row, RowList, Section } from '@/design/components/rows';
import { Stat, StatGroup } from '@/design/components/stat-group';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { Plus } from '@/design/components/icons';

// Activity tone → tinted square + dot, all on status tokens so both modes hold.
const ACTIVITY_TONE: Record<string, { bg: string; dot: string }> = {
  forest: { bg: 'bg-[var(--ui-ok-wash)]', dot: 'text-[var(--ui-ok)]' },
  electric: { bg: 'bg-[var(--ui-info-wash)]', dot: 'text-[var(--ui-info)]' },
  amber: { bg: 'bg-[var(--ui-warn-wash)]', dot: 'text-[var(--ui-warn)]' },
  lavender: { bg: 'bg-[var(--ui-lavender-wash)]', dot: 'text-[var(--ui-lavender)]' },
};

/** How many openings "Openings in setup" lists before pointing at the full list. */
const SETUP_VISIBLE = 5;

const SETUP_TONE: Record<string, TagTone> = {
  'Sent back': 'ember',
  'Awaiting approval': 'amber',
  'Not submitted': 'neutral',
  Approved: 'electric',
};

/** A small muted count after a heading — only rendered once the count is known. */
function Count({ n }: { n: number | undefined }): JSX.Element | null {
  if (n === undefined) return null;
  return (
    <span className="ml-2 rounded-full bg-[var(--ui-inset-strong)] px-2 py-0.5 text-[12px] font-semibold tabular-nums text-[var(--ui-soft)]">
      {n}
    </span>
  );
}

export default function HRConsole() {
  const { user } = useAuth();

  // Lightweight profile fetch so the greeting works even on a hard refresh.
  const { data: me, isLoading: meLoading } = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: () => getMe(),
    staleTime: 60_000,
  });

  // Live hiring funnel — drives the stat group. The dashboard is the page a
  // manager leaves open, so it polls like the other live views.
  const { data: analytics } = useQuery({
    queryKey: ['hr', 'analytics'],
    queryFn: getHrAnalytics,
    staleTime: 60_000,
    refetchInterval: LIVE_POLL_MS,
  });

  // Recent activity — reuse the notification feed (HR invites, scores, completions).
  // Its own key, under the bell's prefix. Sharing the bell's exact key while
  // asking for 8 rows instead of 30 meant whichever fetched last owned the
  // cache, and the bell could open showing only eight. The prefix keeps the
  // bell's mark-read invalidation reaching this list, and the interval matches
  // the bell's so the feed still updates on its own.
  const { data: notifs } = useQuery({
    queryKey: ['notifications', 'recent', 8],
    queryFn: () => listNotifications(8),
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: false,
  });

  // The two lists under "Needs you today" read the same cached queries as the
  // panels themselves, so their counts are the lists' own lengths.
  const attention = useAttention();
  const atRisk = useStagesAtRisk();
  const attentionCount = attention.data?.total;
  const atRiskCount = atRisk.data?.length;

  // Openings that are not yet approved and live. No polling: this changes when
  // someone acts on an opening, and the opening's own page is where that
  // happens.
  const openings = useQuery({
    queryKey: ['hr', 'requisitions', 'home'],
    queryFn: () => listRequisitions({ limit: 200 }),
    staleTime: 60_000,
    retry: false,
  });
  const inSetup = (openings.data ?? []).filter(isInSetup);

  const f = analytics?.funnel;
  const a = analytics?.averages;
  const open = analytics?.openings;
  const velocity = analytics?.velocity;

  /**
   * Week on week, as a signed count rather than a percentage.
   *
   * A percentage from a small base is noise dressed as a signal — going from
   * two applications to three is "+50%", which reads as a trend and is not
   * one. "+1 vs last week" cannot be misread that way.
   */
  const weekDelta = ((): string | null => {
    if (!velocity) return null;
    const diff = velocity.applications_last_7d - velocity.applications_prev_7d;
    if (velocity.applications_last_7d === 0 && velocity.applications_prev_7d === 0) {
      return null;
    }
    return `${diff >= 0 ? '+' : ''}${diff} vs last week`;
  })();

  // One line under a label, only when there is something true to say.
  const stats: { label: string; value: number | undefined; note?: string; to: string }[] = [
    {
      label: 'Open roles',
      value: open?.open,
      // Only mentioned when there is one. "0 paused" is not information.
      note: open?.paused ? `${open.paused} paused` : undefined,
      to: '/hr/requisitions',
    },
    {
      label: 'Applicants',
      value: f?.total_applicants,
      note: weekDelta ?? (a?.avg_ats != null ? `avg ATS ${Math.round(a.avg_ats)}` : undefined),
      to: '/hr/applicants',
    },
    { label: 'Shortlisted', value: f?.shortlisted, to: '/hr/applicants' },
    {
      label: 'Exam passed',
      value: f?.exam_passed,
      note: a?.avg_exam_percent != null ? `${Math.round(a.avg_exam_percent)}% avg` : undefined,
      to: '/hr/applicants',
    },
    {
      label: 'Interviewed',
      value: f?.interview_completed,
      note:
        a?.avg_interview_composite != null
          ? `${a.avg_interview_composite.toFixed(1)}/10 avg`
          : undefined,
      to: '/hr/interviews',
    },
    {
      label: 'Hired',
      value: f?.hired,
      // The median, and only once somebody has actually been hired. A
      // time-to-hire with no hires behind it is a number about nothing.
      note:
        velocity?.median_time_to_hire_days != null
          ? `${Math.round(velocity.median_time_to_hire_days)}d median`
          : undefined,
      to: '/hr/pipeline',
    },
  ];

  const name = me?.full_name ?? user?.full_name ?? null;
  const activityKey = useScopedPersistKey('hr-home-activity');

  return (
    <Page width="default">
      <PageHeader
        title={
          <span data-testid="page-title">
            {meLoading ? 'HR Console' : name ? `Welcome, ${name}` : 'HR Console'}
          </span>
        }
        description="Start with what needs you today, then check how the pipeline is moving."
        actions={
          <>
            <Button asChild variant="outline">
              <Link to="/hr/exams">
                <Plus aria-hidden="true" /> Create exam
              </Link>
            </Button>
            <Button asChild>
              <Link to="/hr/applicants">Invite applicant</Link>
            </Button>
          </>
        }
      />

      <PageBody
        asideLabel="Shortcuts"
        aside={
          <>
            <Card as="section" title="Shortcuts" density="compact">
              <RowList>
                <Row title="Open hiring pipeline" to="/hr/pipeline" />
                <Row title="Review applicants" to="/hr/applicants" />
                <Row title="View analytics" to="/hr/analytics" />
                <Row title="All openings" to="/hr/requisitions" />
                <Row title="Offers" to="/hr/offers" />
              </RowList>
            </Card>

            {/* The public careers board's address, which until now appeared
                nowhere in this console — see CareersLinkCard's header. Renders
                nothing when the account has no company. */}
            <CareersLinkCard slug={me?.company_slug} companyName={me?.company_name} />
          </>
        }
      >
        {/* ── Needs you today ──
            The one raised region. Above the feed on purpose: the feed further
            down is a record of what happened; these are the conditions that
            have not been dealt with, and burying them under the record would
            defeat the point of surfacing exceptions. Both lists show here —
            neither is hidden behind a tab. */}
        <Card
          emphasis="raised"
          as="section"
          title="Needs you today"
          data-testid="needs-you-today"
          className="flex flex-col"
        >
          <div className="flex flex-col gap-6">
            <Section
              title={
                <>
                  Attention required
                  <Count n={attentionCount} />
                </>
              }
            >
              <AttentionPanel bare />
            </Section>
            {/* PH4-O1 — overdue/due-soon stages across every opening, so nobody
                has to open each one's decision queue to find out. */}
            <Section
              title={
                <>
                  Stages at risk
                  <Count n={atRiskCount} />
                </>
              }
            >
              <StagesAtRiskWidget bare />
            </Section>
          </div>
        </Card>

        {/* ── Openings in setup ── */}
        <Card
          as="section"
          title={
            <>
              Openings in setup
              <Count n={openings.isSuccess ? inSetup.length : undefined} />
            </>
          }
          description="Not yet approved and accepting applications. Each opens its setup."
          data-testid="openings-in-setup"
        >
          {openings.isLoading ? (
            <Skeleton className="h-14 w-full rounded-card" />
          ) : openings.isError ? (
            <p role="alert" className="text-[13px] text-muted-foreground">
              Could not load your openings just now.
            </p>
          ) : inSetup.length === 0 ? (
            <EmptyState size="row" title="No opening is waiting on setup." />
          ) : (
            <>
              <RowList>
                {inSetup.slice(0, SETUP_VISIBLE).map((r) => {
                  const next = nextSetupStage(r);
                  const badge = setupBadge(r);
                  return (
                    <Row
                      key={r.id}
                      to={`/hr/requisitions/${r.id}?tab=setup`}
                      title={r.title}
                      meta={
                        <>
                          <span className="font-medium text-foreground">Next: {next.stage}</span> —{' '}
                          {next.detail}
                        </>
                      }
                      trailing={
                        <StatusTag tone={SETUP_TONE[badge] ?? 'neutral'} dot>
                          {badge}
                        </StatusTag>
                      }
                    />
                  );
                })}
              </RowList>
              {inSetup.length > SETUP_VISIBLE ? (
                <Link
                  to="/hr/requisitions"
                  className="mt-3 inline-flex min-h-10 items-center text-[13px] font-semibold text-[var(--ui-info)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                >
                  View all {inSetup.length} openings in setup
                </Link>
              ) : null}
            </>
          )}
        </Card>

        {/* ── Pipeline: the funnel as one stat group, each cell a link. ── */}
        <section aria-label="Pipeline at a glance">
          <StatGroup missingLabel="Not loaded yet">
            {stats.map((s) => (
              <Stat
                key={s.label}
                value={s.value}
                to={s.to}
                label={
                  <>
                    {s.label}
                    {s.note ? (
                      <span className="mt-0.5 block text-[12px] font-normal">{s.note}</span>
                    ) : null}
                  </>
                }
              />
            ))}
          </StatGroup>
        </section>

        <RecentActivity items={notifs?.items ?? []} persistKey={activityKey} />
      </PageBody>
    </Page>
  );
}

// ── Activity feed — driven by the notification feed ──────────────────────────
// Notifications ARE hiring activity (invite sent, applicant scored, interview
// completed), so we render the same feed here. Empty-state aware.

// Map a notification kind → a tone colour from the design palette.
const KIND_TONE: Record<string, keyof typeof ACTIVITY_TONE> = {
  applicant_scored: 'electric',
  interview_completed: 'forest',
  // Results arriving: something to read.
  exam_submitted: 'electric',
  bulk_upload: 'electric',
  auto_advance: 'forest',
  // Waiting on HR: a lapse to follow up, a review to do.
  link_expired: 'amber',
  interview_no_show: 'amber',
  review_due: 'amber',
  invite_sent: 'amber',
  decision: 'lavender',
  welcome: 'lavender',
  system: 'lavender',
};

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const day = Math.floor(diff / 86_400_000);
  if (day >= 1) return `${day}d ago`;
  const hr = Math.floor(diff / 3_600_000);
  if (hr >= 1) return `${hr}h ago`;
  const min = Math.floor(diff / 60_000);
  if (min >= 1) return `${min}m ago`;
  return 'just now';
}

/**
 * Kinds the Attention panel owns. Rendering them here repeats that panel.
 *
 * The feed is a record of EVENTS; the Attention panel above it lists
 * CONDITIONS. Measured on real data, 9 of 17 notifications were `system` and 8
 * of those repeated — word for word — a finding already shown a few hundred
 * pixels higher. `system` is written by exactly one place
 * (agents/watch_runner.py, NOTIFICATION_KIND = "system"), so excluding it
 * removes watcher findings and nothing else.
 *
 * A denylist rather than an allowlist: a new event kind should appear here
 * without anyone remembering to register it.
 */
const PANEL_OWNED_KINDS = new Set(['system']);

/** Recent activity: a record, so it sits closed under its newest line. */
function RecentActivity({
  items: all,
  persistKey,
}: {
  items: NotificationItem[];
  persistKey: string;
}) {
  const items = all.filter((n) => !PANEL_OWNED_KINDS.has(n.kind));
  const newest = items[0];
  return (
    <Collapsible
      variant="card"
      headingLevel="h2"
      title="Recent activity"
      count={items.length}
      noun="event"
      latest={newest ? `${newest.title}, ${timeAgo(newest.created_at)}` : undefined}
      persistKey={persistKey}
      data-testid="recent-activity"
    >
      <ActivityFeed items={items} />
    </Collapsible>
  );
}

function ActivityFeed({ items }: { items: NotificationItem[] }) {
  if (items.length === 0) {
    return (
      <EmptyState
        size="row"
        title="No recent activity yet."
        description="Invites, scores and completed interviews will show up here."
      />
    );
  }

  return (
    <ProgressiveList
      items={items}
      initial={5}
      noun="events"
      getKey={(n) => n.id}
      className="flex flex-col"
      renderItem={(n) => {
        const tone = KIND_TONE[n.kind] ?? 'lavender';
        return (
          <li className="flex items-center gap-3.5 border-b border-border py-3 last:border-0">
            <span
              className={cn(
                'flex h-9 w-9 flex-none items-center justify-center rounded-[10px]',
                ACTIVITY_TONE[tone].bg,
              )}
              aria-hidden="true"
            >
              <span className={cn('h-2 w-2 rounded-full bg-current', ACTIVITY_TONE[tone].dot)} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-[13.5px]">
                <span className="font-semibold">{n.title}</span>
                {n.body ? <span className="text-muted-foreground"> — {n.body}</span> : null}
              </div>
              <div className="text-[12px] text-muted-foreground">{timeAgo(n.created_at)}</div>
            </div>
          </li>
        );
      }}
    />
  );
}
