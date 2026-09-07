// HRConsole — HR manager landing page.
// Layout: faithfully reproduces anterview-pages/src/screens/hr/HRConsole.tsx
//   (stat strip, activity feed, quick-actions panel, cost card).
// Behavior: live getMe query for greeting; stat strip uses real HR_STATS shape
//   mapped from the live query; activity feed is empty-safe.
// No AppShell wrapper — the app shell is provided by the route.

import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getMe } from '@/api/auth';
import { getHrAnalytics } from '@/api/hr';
import { listNotifications, type NotificationItem } from '@/api/notifications';
import AttentionPanel from '@/components/AttentionPanel';
import { useAuth } from '@/context/AuthContext';
import { Reveal, Stagger, StaggerItem } from '@/design/components/Reveal';
import { GlassCard, StatCard, Pill } from '@/design/components/primitives';
import { PromoBanner, TrustStrip } from '@/design/components/banners';
import {
  Kanban, Users, BarChart3, Plus, Gauge, Target, ClipboardCheck, ShieldCheck,
} from '@/design/components/icons';

// ── Activity tone colours (design spec) ──────────────────────────────────────
const ACTIVITY_BG: Record<string, string> = {
  forest: 'rgba(39,201,63,0.16)',
  electric: 'rgba(var(--accent-rgb),0.16)',
  amber: 'rgba(255,183,100,0.16)',
  lavender: 'rgba(168,135,220,0.16)',
};

const ACTIVITY_COLOR: Record<string, string> = {
  forest: '#27c93f',
  electric: '#60a5fa',
  amber: '#ffb764',
  lavender: '#c89ce8',
};

// Trend helper — green up-arrow when a stage has anything in it, flat otherwise.
const tr = (n: number | undefined): 'up' | 'flat' => ((n ?? 0) > 0 ? 'up' : 'flat');
// Render a count, or an em-dash while the funnel is still loading.
const num = (n: number | undefined): string => (n === undefined ? '—' : String(n));

export default function HRConsole() {
  const { user } = useAuth();

  // Lightweight profile fetch so the greeting works even on a hard refresh.
  const { data: me, isLoading: meLoading } = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: () => getMe(),
    staleTime: 60_000,
  });

  // Live hiring funnel — drives the stat strip.
  const { data: analytics } = useQuery({
    queryKey: ['hr', 'analytics'],
    queryFn: getHrAnalytics,
    staleTime: 60_000,
  });

  // Recent activity — reuse the notification feed (HR invites, scores, completions).
  const { data: notifs } = useQuery({
    queryKey: ['notifications'],
    queryFn: () => listNotifications(8),
    staleTime: 30_000,
    retry: false,
  });

  const f = analytics?.funnel;
  const a = analytics?.averages;
  const openings = analytics?.openings;
  const velocity = analytics?.velocity;

  /**
   * Week on week, as a signed count rather than a percentage.
   *
   * A percentage from a small base is noise dressed as a signal — going from
   * two applications to three is "+50%", which reads as a trend and is not
   * one. "+1 vs last week" cannot be misread that way.
   */
  const weekDelta = ((): { text: string; trend: 'up' | 'down' | 'flat' } | null => {
    if (!velocity) return null;
    const diff = velocity.applications_last_7d - velocity.applications_prev_7d;
    if (velocity.applications_last_7d === 0 && velocity.applications_prev_7d === 0) {
      return null;
    }
    return {
      text: `${diff >= 0 ? '+' : ''}${diff} vs last week`,
      trend: diff > 0 ? 'up' : diff < 0 ? 'down' : 'flat',
    };
  })();

  const stats = [
    {
      label: 'Open roles',
      value: num(openings?.open),
      // Only mentioned when there is one. "0 paused" is not information.
      delta: openings?.paused ? `${openings.paused} paused` : undefined,
      trend: 'flat' as const,
    },
    {
      label: 'Applicants',
      value: num(f?.total_applicants),
      delta: weekDelta?.text ?? (a?.avg_ats != null ? `avg ATS ${Math.round(a.avg_ats)}` : undefined),
      trend: weekDelta?.trend ?? tr(f?.total_applicants),
    },
    { label: 'Shortlisted', value: num(f?.shortlisted), trend: tr(f?.shortlisted) },
    {
      label: 'Exam passed',
      value: num(f?.exam_passed),
      delta: a?.avg_exam_percent != null ? `${Math.round(a.avg_exam_percent)}% avg` : undefined,
      trend: tr(f?.exam_passed),
    },
    {
      label: 'Interviewed',
      value: num(f?.interview_completed),
      delta:
        a?.avg_interview_composite != null
          ? `${a.avg_interview_composite.toFixed(1)}/10 avg`
          : undefined,
      trend: tr(f?.interview_completed),
    },
    {
      label: 'Hired',
      value: num(f?.hired),
      // The median, and only once somebody has actually been hired. A
      // time-to-hire with no hires behind it is a number about nothing.
      delta:
        velocity?.median_time_to_hire_days != null
          ? `${Math.round(velocity.median_time_to_hire_days)}d median`
          : undefined,
      trend: tr(f?.hired),
    },
  ];

  const name = me?.full_name ?? user?.full_name ?? null;

  return (
    <div className="mx-auto max-w-[1280px] px-6 py-8 lg:px-8">
      {/* ── Brand promo banner + trust chips ── */}
      <Reveal>
        <PromoBanner
          tone="electric"
          badge="Hiring OS"
          eyebrow="Anterview for Teams"
          title="Screen smarter. Hire fairer. Move faster."
          subtitle="AI-ranked applicants, structured exams and avatar interviews — every candidate measured against the same fair rubric, end to end."
          cta={{ label: 'Review applicants', to: '/hr/applicants' }}
          icon={Gauge}
          dismissId="hr-hero-v1"
        />
      </Reveal>
      <TrustStrip
        className="mb-7 mt-4"
        items={[
          { icon: BarChart3, label: 'ATS-powered ranking' },
          { icon: Target, label: 'One fair rubric' },
          { icon: ClipboardCheck, label: 'Audit-ready' },
          { icon: ShieldCheck, label: 'DPDP-safe' },
        ]}
      />

      {/* ── Header ── */}
      <Reveal>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-[28px] font-semibold tracking-[-1px]">
              {meLoading
                ? 'HR Console'
                : name
                  ? `Welcome, ${name}`
                  : 'HR Console'}
            </h1>
            <p className="mt-1 text-[14px] text-[#888b91]">
              Your hiring at a glance
            </p>
          </div>
          <div className="flex items-center gap-2.5">
            <Link to="/hr/exams">
              <Pill variant="ghost" className="px-4 py-2.5">
                <Plus size={16} aria-hidden="true" /> Create exam
              </Pill>
            </Link>
            <Link to="/hr/applicants">
              <Pill className="px-5 py-2.5">Invite applicant</Pill>
            </Link>
          </div>
        </div>
      </Reveal>

      {/* ── Stat strip ── */}
      <Stagger className="mt-7 grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-5">
        {stats.map((s) => (
          <StaggerItem key={s.label}>
            <StatCard {...s} className="h-full" />
          </StaggerItem>
        ))}
      </Stagger>

      {/* ── Attention required ──
          Above the activity feed on purpose. The feed is a record of what
          happened; this is the short list of what has not, and burying it
          under the record would defeat the point of surfacing exceptions. */}
      <Reveal className="mt-5">
        <AttentionPanel />
      </Reveal>

      {/* ── Body ── */}
      <div className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-3">
        {/* Activity feed */}
        <Reveal dir="left" className="lg:col-span-2">
          <GlassCard className="p-5">
            <h3 className="mb-4 text-[16px] font-semibold">Activity feed</h3>
            <ActivityFeed items={notifs?.items ?? []} />
          </GlassCard>
        </Reveal>

        {/* Right panel */}
        <div className="flex flex-col gap-4">
          <Reveal dir="right">
            <GlassCard feature className="p-5">
              <h3 className="mb-3.5 text-[16px] font-semibold">Quick actions</h3>
              <div className="flex flex-col gap-2.5">
                <Link to="/hr/pipeline">
                  <Pill variant="ghost" className="w-full justify-start py-3">
                    <Kanban size={16} aria-hidden="true" /> Open hiring pipeline
                  </Pill>
                </Link>
                <Link to="/hr/applicants">
                  <Pill variant="ghost" className="w-full justify-start py-3">
                    <Users size={16} aria-hidden="true" /> Review applicants
                  </Pill>
                </Link>
                <Link to="/hr/analytics">
                  <Pill variant="ghost" className="w-full justify-start py-3">
                    <BarChart3 size={16} aria-hidden="true" /> View analytics
                  </Pill>
                </Link>
              </div>
            </GlassCard>
          </Reveal>

          <Reveal dir="right">
            <GlassCard className="p-5">
              <p className="text-[11px] font-medium uppercase tracking-wide text-[#888b91]">
                Getting started
              </p>
              <p className="mt-2 text-[13px] text-white/80">
                Upload resumes to begin ATS screening, then author an exam and send
                interview invites — all from the four stages in the pipeline.
              </p>
            </GlassCard>
          </Reveal>
        </div>
      </div>
    </div>
  );
}

// ── Activity feed — driven by the notification feed ──────────────────────────
// Notifications ARE hiring activity (invite sent, applicant scored, interview
// completed), so we render the same feed here. Empty-state aware.

// Map a notification kind → a tone colour from the design palette.
const KIND_TONE: Record<string, keyof typeof ACTIVITY_BG> = {
  applicant_scored: 'electric',
  interview_completed: 'forest',
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

function ActivityFeed({ items }: { items: NotificationItem[] }) {
  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
        <p className="text-[13px] text-[#888b91]">No recent activity yet.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      {items.map((n) => {
        const tone = KIND_TONE[n.kind] ?? 'lavender';
        return (
          <div
            key={n.id}
            className="flex items-center gap-3.5 border-b border-white/[0.05] py-3.5 last:border-0"
          >
            <span
              className="flex h-9 w-9 flex-none items-center justify-center rounded-[10px]"
              style={{ background: ACTIVITY_BG[tone] }}
              aria-hidden="true"
            >
              <span
                className="h-2 w-2 rounded-full bg-current"
                style={{ color: ACTIVITY_COLOR[tone] }}
              />
            </span>
            <div className="flex-1">
              <div className="text-[13.5px]">
                <span className="font-semibold">{n.title}</span>
                {n.body ? <span className="text-white/70"> — {n.body}</span> : null}
              </div>
              <div className="text-[11.5px] text-[#70757c]">{timeAgo(n.created_at)}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
