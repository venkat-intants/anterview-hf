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

import { useAttention } from '@/hooks/useHrHomeQueries';
import { Link } from 'react-router-dom';
import type { AttentionItem, AttentionSeverity } from '@/api/attention';
import { SectionCard } from '@/design/components/page';
import { Skeleton } from '@/components/ui/skeleton';
import { AlertTriangle, CheckCircle2, ChevronRight, Info } from '@/design/components/icons';
import { cn } from '@/lib/utils';
import CitationChips from './agent/CitationChips';
import EvidenceBanner from './agent/EvidenceBanner';
import { appPath } from '@/lib/safeUrl';

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
  critical: { label: 'Critical', tint: 'text-[var(--ui-danger)]', icon: AlertTriangle },
  warning: { label: 'Needs attention', tint: 'text-[var(--ui-warn)]', icon: AlertTriangle },
  info: { label: 'For information', tint: 'text-[var(--ui-info)]', icon: Info },
};

function Finding({ item, flat = false }: { item: AttentionItem; flat?: boolean }) {
  const tone = SEVERITY[item.severity] ?? SEVERITY.info;
  const Icon = tone.icon;

  const header = (
    <div className="flex items-start gap-2.5">
      <Icon className={cn('mt-0.5 h-4 w-4 shrink-0', tone.tint)} aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="text-[14px] font-medium text-foreground">
          <span className="sr-only">{tone.label}: </span>
          {item.title}
        </p>
        <p className="mt-0.5 text-[13px] leading-relaxed text-muted-foreground">{item.body}</p>
      </div>
      {item.link ? (
        <ChevronRight
          className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground"
          aria-hidden="true"
        />
      ) : null}
    </div>
  );

  // The card is a plain container, never itself an <a> — a citation chip
  // below can be its own link to its own record (E1's whole point), and an
  // <a> nested inside an <a> is invalid HTML and breaks that link for a
  // keyboard or screen-reader user. Only the header is the finding's link;
  // `hover:`/`focus-within:` on the card react to that descendant, since
  // hover state bubbles up the DOM without any JS.
  return (
    <div
      className={cn(
        'text-left transition-colors',
        // `flat`: a row of a divided list inside a card, not a box of its own.
        flat
          ? 'py-3 first:pt-0 last:pb-0'
          : 'rounded-[12px] border border-border bg-[var(--ui-inset-soft)] p-3.5',
        !flat &&
          item.link &&
          'hover:border-[var(--ui-line-strong)] focus-within:border-[var(--accent)]',
      )}
    >
      {/* A finding with nowhere to go is rendered as text rather than a dead link. */}
      {item.link ? (
        <Link to={appPath(item.link, '/')} className="block rounded-[8px] outline-none">
          {header}
        </Link>
      ) : (
        header
      )}

      {/* The specific records, when a finding is about more than one thing.
          Capped: "7 applicants stalled" wants a few names to make it real, not
          a list that buries the next finding. Links now, deliberately — the
          href was always there, this panel just used to discard it. */}
      <CitationChips citations={item.citations} variant="strip" className="mt-2 pl-[26px]" />

      {/* PH5-E1 criteria 4/5's banner, on this surface too: a finding with
          records named beside it says so plainly, and one with none is
          honest about being a general rule rather than about anyone
          specific. 'unattributed' never fires here — every rule is
          deterministic SQL plus arithmetic, not a model's free prose — but
          the same component is used for the same reason CitationChips is:
          one renderer, everywhere the answer is the same question. */}
      <EvidenceBanner citationState={item.citation_state} className="mt-2 pl-[26px]" />
    </div>
  );
}

export default function AttentionPanel({
  className,
  bare = false,
}: {
  className?: string;
  /**
   * Only the findings — no card, title or count badge, and each finding a row
   * rather than a box — for a page that supplies its own frame and heading
   * (the HR home's "Needs you today").
   */
  bare?: boolean;
}) {
  const attention = useAttention();

  const items = attention.data?.items ?? [];

  const body = (
    <>
      {attention.isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-[58px] rounded-[12px]" />
          <Skeleton className="h-[58px] rounded-[12px]" />
        </div>
      ) : null}

      {/* A failure must not read as "nothing needs attention" — that is the one
          wrong answer this panel can give, because it is also the good news. */}
      {attention.isError ? (
        <p role="alert" className="text-[13px] text-muted-foreground">
          Could not check for issues just now. Refresh to try again.
        </p>
      ) : null}

      {!attention.isLoading && !attention.isError && items.length === 0 ? (
        <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
          <CheckCircle2 className="h-4 w-4 text-[var(--ui-ok)]" aria-hidden="true" />
          Nothing needs your attention right now.
        </p>
      ) : null}

      {items.length > 0 ? (
        <div className={bare ? 'flex flex-col divide-y divide-border' : 'flex flex-col gap-2'}>
          {items.map((item) => (
            <Finding
              key={item.dedupe_key || `${item.watcher}-${item.title}`}
              item={item}
              flat={bare}
            />
          ))}
        </div>
      ) : null}
    </>
  );

  if (bare) return <div className={className}>{body}</div>;

  return (
    <SectionCard
      title="Attention required"
      icon={AlertTriangle}
      className={className}
      action={
        attention.data && attention.data.total > 0 ? (
          <span className="rounded-full bg-[var(--ui-warn-wash)] px-2.5 py-1 text-[12px] font-semibold text-[var(--ui-warn)]">
            {attention.data.total} {attention.data.total === 1 ? 'item' : 'items'}
          </span>
        ) : null
      }
    >
      {body}
    </SectionCard>
  );
}
