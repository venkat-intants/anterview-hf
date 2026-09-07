// AttentionPanel — the exceptions, so nobody has to go looking for them.
//
// The point of this panel is that HR should not have to inspect every
// candidate to discover that seven of them went cold last week. The detection
// already existed and ran nightly into the notification bell; this is the
// same rules asked directly, which matters because the bell is deduplicated —
// it tells you once and then goes quiet whether or not anyone acted.
//
// Two rules the spec is right about and this follows.
//
// EVERY ALERT IS ACTIONABLE. Each finding renders as a link to the thing that
// needs attention, and the citations under it link to the specific records. A
// panel of statements you cannot act on is just a second inbox.
//
// NO NOISE FOR NORMAL ACTIVITY. When nothing is wrong the panel says so in one
// quiet line rather than inventing something to report. A panel that always has
// content is one people stop reading.

import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getAttention, type AttentionItem, type AttentionSeverity } from '@/api/attention';
import { GlassCard } from '@/design/components/primitives';
import { AlertTriangle, CheckCircle2, ChevronRight, Info } from '@/design/components/icons';
import { cn } from '@/lib/utils';

/**
 * Severity is carried by an icon and a word as well as a colour.
 *
 * Colour alone fails for a colourblind reader and disappears in a screenshot
 * pasted into a chat — and this is the surface people screenshot when they are
 * asking someone else to look at something.
 */
const SEVERITY: Record<
  AttentionSeverity,
  { label: string; tint: string; icon: typeof AlertTriangle }
> = {
  critical: { label: 'Critical', tint: 'text-[#e6714f]', icon: AlertTriangle },
  warning: { label: 'Needs attention', tint: 'text-[#ffb764]', icon: AlertTriangle },
  info: { label: 'For information', tint: 'text-[#60a5fa]', icon: Info },
};

function Finding({ item }: { item: AttentionItem }) {
  const tone = SEVERITY[item.severity] ?? SEVERITY.info;
  const Icon = tone.icon;

  const inner = (
    <>
      <div className="flex items-start gap-2.5">
        <Icon className={cn('mt-0.5 h-4 w-4 shrink-0', tone.tint)} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="text-[14px] font-medium text-white">
            <span className="sr-only">{tone.label}: </span>
            {item.title}
          </p>
          <p className="mt-0.5 text-[13px] leading-relaxed text-[#888b91]">{item.body}</p>
        </div>
        {item.link ? (
          <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-[#5a5f66]" aria-hidden="true" />
        ) : null}
      </div>

      {/* The specific records, when a finding is about more than one thing.
          Capped: "7 applicants stalled" wants a few names to make it real, not
          a list that buries the next finding. */}
      {item.citations.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1.5 pl-[26px]">
          {item.citations.slice(0, 4).map((c) => (
            <span
              key={`${c.kind}-${c.id}`}
              className="rounded-full border border-white/[0.08] bg-white/[0.03] px-2 py-0.5 text-[11.5px] text-[#b8babf]"
            >
              {c.label}
            </span>
          ))}
          {item.citations.length > 4 ? (
            <span className="px-1 py-0.5 text-[11.5px] text-[#70757c]">
              +{item.citations.length - 4} more
            </span>
          ) : null}
        </div>
      ) : null}
    </>
  );

  const shell =
    'block rounded-[12px] border border-white/[0.07] bg-white/[0.02] p-3.5 text-left';

  // A finding with nowhere to go is rendered as text rather than a dead link.
  return item.link ? (
    <Link
      to={item.link}
      className={cn(
        shell,
        'transition-colors hover:border-white/[0.16] focus:outline-none focus-visible:border-[var(--accent)]',
      )}
    >
      {inner}
    </Link>
  ) : (
    <div className={shell}>{inner}</div>
  );
}

export default function AttentionPanel({ className }: { className?: string }) {
  const attention = useQuery({
    queryKey: ['hr-attention'],
    queryFn: getAttention,
    // Fresh enough to be trusted, not so fresh that leaving the dashboard open
    // re-runs the aggregate queries every minute.
    staleTime: 5 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  const items = attention.data?.items ?? [];

  return (
    <GlassCard className={cn('p-5', className)}>
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="text-[15px] font-semibold text-white">Attention required</h2>
        {attention.data && attention.data.total > 0 ? (
          <span className="text-[12px] text-[#888b91]">
            {attention.data.total} {attention.data.total === 1 ? 'item' : 'items'}
          </span>
        ) : null}
      </div>

      {attention.isLoading ? (
        <div className="flex flex-col gap-2">
          <div className="h-[58px] animate-pulse rounded-[12px] bg-white/[0.03]" />
          <div className="h-[58px] animate-pulse rounded-[12px] bg-white/[0.03]" />
        </div>
      ) : null}

      {/* A failure must not read as "nothing needs attention" — that is the one
          wrong answer this panel can give, because it is also the good news. */}
      {attention.isError ? (
        <p className="text-[13px] text-[#888b91]">
          Could not check for issues just now. Refresh to try again.
        </p>
      ) : null}

      {!attention.isLoading && !attention.isError && items.length === 0 ? (
        <p className="flex items-center gap-2 text-[13px] text-[#888b91]">
          <CheckCircle2 className="h-4 w-4 text-[#27c93f]" aria-hidden="true" />
          Nothing needs your attention right now.
        </p>
      ) : null}

      {items.length > 0 ? (
        <div className="flex flex-col gap-2">
          {items.map((item) => (
            <Finding key={item.dedupe_key || `${item.watcher}-${item.title}`} item={item} />
          ))}
        </div>
      ) : null}
    </GlassCard>
  );
}
