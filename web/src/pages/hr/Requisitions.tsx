// Requisitions — the openings this company is hiring for.
//
// The list exists because Phase 2 gave the product a first-class job. Before
// it, an applicant carried a free-text `target_job_title` and there was nothing
// to open: no per-job funnel, no per-job workflow, nothing to count. Everything
// on the workflow side hangs off a row on this page.
//
// A DENSE LIST, NOT TALL CARDS. One row per opening, scannable down a column:
// the role, five dots of setup progress, the approval, whether it is taking
// applications, how many people are in it, and the one next action as a link
// into the right place. The default sort puts the openings that need a person
// first, because a queue nobody can see is a queue nobody works (D-05: every
// point where a person must decide is one the process stops at).
//
// THE DOTS ARE HONEST. The list payload always proves approval and "accepting
// applications". A newer backend also sends has_published_jd /
// has_published_workflow, which make the first two dots truthful; when those are
// absent (an older backend) they read "open to check" rather than a guess
// (lib/openingList.ts).

import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  Briefcase,
  Check,
  ChevronRight,
  Clock,
  Plus,
  Search,
  Users,
} from '@/design/components/icons';
import { StatusTag, SegTabs } from '@/design/components/primitives';
import { Page, PageHeader, EmptyState, Toolbar } from '@/design/components/page';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { LIVE_POLL_MS } from '@/lib/polling';
import { APPROVAL_LABEL } from '@/lib/openingSetup';
import {
  isAcceptingApplications,
  notLiveReason,
  matchesQuery,
  nextAction,
  provenCount,
  setupDots,
  sortOpenings,
  type DotState,
  type OpeningSort,
} from '@/lib/openingList';
import {
  listRequisitions,
  getBackfillReview,
  type ApprovalStatus,
  type Requisition,
  type RequisitionStatus,
} from '@/api/requisitions';
import NewOpeningDialog from '@/components/hr/opening/NewOpeningDialog';
import { cn } from '@/lib/utils';

const TABS = [
  { key: 'open', label: 'Open' },
  { key: 'paused', label: 'Paused' },
  { key: 'closed', label: 'Closed' },
  { key: 'all', label: 'All' },
];

const SORTS: { key: OpeningSort; label: string }[] = [
  { key: 'needs-you', label: 'Needs you first' },
  { key: 'newest', label: 'Newest first' },
  { key: 'title', label: 'Title A to Z' },
];

const APPROVAL_TONE: Record<ApprovalStatus, 'neutral' | 'amber' | 'forest' | 'ember'> = {
  draft: 'neutral',
  pending_approval: 'amber',
  approved: 'forest',
  rejected: 'ember',
};

// One grid for the header and every row, so the columns line up.
const GRID =
  'md:grid md:grid-cols-[minmax(0,2fr)_104px_minmax(0,1.15fr)_minmax(0,1.4fr)_minmax(0,1.1fr)_minmax(0,1.8fr)] md:items-center md:gap-x-4';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

/* ── The five setup dots ─────────────────────────────────────────────────── */

function Dot({ state }: { state: DotState }): JSX.Element {
  const base = 'flex h-4 w-4 flex-none items-center justify-center rounded-full';
  switch (state) {
    case 'done':
      return (
        <span className={cn(base, 'bg-primary text-primary-foreground')}>
          <Check size={10} strokeWidth={3} />
        </span>
      );
    case 'waiting':
      return (
        <span className={cn(base, 'border border-[var(--ui-warn)] bg-[var(--ui-warn-wash)] text-[var(--ui-warn)]')}>
          <Clock size={9} strokeWidth={3} />
        </span>
      );
    case 'open':
      return <span className={cn(base, 'border-2 border-muted-foreground')} />;
    case 'unknown':
      return <span className={cn(base, 'border border-dashed border-muted-foreground')} />;
    default:
      return (
        <span className={base}>
          <span className="h-1.5 w-1.5 rounded-full bg-[var(--ui-faint)]" />
        </span>
      );
  }
}

function SetupDots({ req }: { req: Requisition }): JSX.Element {
  const dots = setupDots(req);
  const proven = provenCount(dots);
  return (
    <ol
      aria-label={`Setup progress, ${proven} of 4 stages confirmed by the list`}
      className="flex items-center gap-1.5"
      data-testid="setup-dots"
    >
      {dots.map((d) => (
        <li key={d.key} title={d.text} data-state={d.state} className="flex items-center">
          <Dot state={d.state} />
          <span className="sr-only">{d.text}</span>
        </li>
      ))}
    </ol>
  );
}

function DotLegend(): JSX.Element {
  const items: { state: DotState; text: string }[] = [
    { state: 'done', text: 'confirmed' },
    { state: 'waiting', text: 'waiting on someone else' },
    { state: 'open', text: 'not done' },
    { state: 'unknown', text: 'open to check - the list cannot confirm it (the server did not say)' },
    { state: 'optional', text: 'optional' },
  ];
  return (
    <p className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 pb-3 text-[12px] text-muted-foreground">
      <span className="font-semibold text-foreground">Setup dots, in order:</span>
      <span>
        role and posting, hiring process, approval, go live, documents. Approval can be submitted
        while the first two are still open.
      </span>
      {items.map((i) => (
        <span key={i.state} className="inline-flex items-center gap-1.5">
          <span aria-hidden="true">
            <Dot state={i.state} />
          </span>
          {i.text}
        </span>
      ))}
    </p>
  );
}

/* ── A row ───────────────────────────────────────────────────────────────── */

function Live({ req }: { req: Requisition }): JSX.Element | null {
  if (isAcceptingApplications(req)) return <StatusTag tone="electric" className="whitespace-nowrap">accepting applications</StatusTag>;
  if (req.status === 'open') {
    // Say WHY: "open" in the tab and "not live" here otherwise read as a
    // contradiction (QA ANTHIRE-002/003).
    const why = notLiveReason(req);
    return (
      <StatusTag tone={why === 'closing date passed' ? 'amber' : 'neutral'} className="whitespace-nowrap">
        {why ? `not live · ${why}` : 'not live'}
      </StatusTag>
    );
  }
  return (
    <StatusTag tone={req.status === 'paused' ? 'amber' : 'neutral'}>{req.status}</StatusTag>
  );
}

function RequisitionRow({ req }: { req: Requisition }): JSX.Element {
  const status = req.approval_status ?? 'draft';
  const next = nextAction(req);
  const place = [req.department, req.location].filter(Boolean).join(' · ');
  return (
    <li
      role="row"
      data-testid="opening-row"
      className={cn(
        // Below md a row is two compact lines: the opening with its status
        // pills, then the setup dots, who is in it, and what to do next. The
        // DOM order is the desktop column order (so focus order is unchanged);
        // `max-md:order-*` only re-lays it out on a phone, with a zero-height
        // full-width spacer forcing the break between the two lines.
        'relative flex flex-wrap items-center gap-x-3 gap-y-0 px-4 py-2.5 transition-colors hover:bg-[var(--ui-inset-soft)] md:py-3',
        GRID,
      )}
    >
      {/* The opening: the whole row opens it (stretched link); the next
          action sits above the stretch so it stays its own target. */}
      <div role="cell" className="min-w-0 max-md:order-1 max-md:flex-1 max-md:basis-0">
        <Link
          to={`/hr/requisitions/${req.id}`}
          className="block truncate text-[14.5px] font-semibold text-foreground after:absolute after:inset-0 focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-inset focus-visible:after:ring-[var(--accent)]"
        >
          {req.title}
        </Link>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[12.5px] text-muted-foreground">
          <span>{req.level}</span>
          {place ? <span>{place}</span> : null}
          {req.from_backfill ? <span>imported</span> : null}
          {/* Only the two bands worth acting on; a green tick on every healthy
              opening is noise nobody scans. */}
          {req.status === 'open' && req.delivery_risk === 'at_risk' ? (
            <StatusTag tone="amber">behind target</StatusTag>
          ) : null}
          {req.status === 'open' && req.delivery_risk === 'off_track' ? (
            <StatusTag tone="ember">will miss target</StatusTag>
          ) : null}
        </p>
      </div>

      <div role="cell" className="max-md:order-4">
        <SetupDots req={req} />
      </div>

      <div role="cell" className="max-md:order-2">
        <span data-testid="opening-approval">
          <StatusTag tone={APPROVAL_TONE[status]} className="whitespace-nowrap">
            {APPROVAL_LABEL[status]}
          </StatusTag>
        </span>
      </div>

      <div
        role="cell"
        // "not live" repeats the go-live dot; on a phone that line is dropped.
        className={cn(
          'max-md:order-5',
          req.status === 'open' && !isAcceptingApplications(req) && 'max-sm:hidden',
        )}
      >
        <Live req={req} />
      </div>

      <span aria-hidden="true" className="hidden basis-full max-md:order-3 max-md:block" />

      <div role="cell" className="flex flex-wrap items-baseline gap-x-2 text-[13px] max-md:order-6 md:block">
        <p>
          <span className="font-semibold tabular-nums text-foreground">{req.total_enrolments}</span>{' '}
          <span className="text-foreground">
            candidate{req.total_enrolments === 1 ? '' : 's'}
          </span>
        </p>
        {req.hired > 0 || req.target_hires ? (
          <p className="text-[12.5px] text-muted-foreground">
            {[req.hired > 0 ? `${req.hired} hired` : null, req.target_hires ? `target ${req.target_hires}` : null]
              .filter(Boolean)
              .join(' · ')}
          </p>
        ) : null}
        {req.awaiting_decision > 0 ? (
          <span className="mt-0.5 flex items-center gap-1 font-semibold text-[var(--ui-warn)]">
            <Users className="h-3.5 w-3.5" aria-hidden="true" />
            {req.awaiting_decision} awaiting you
          </span>
        ) : null}
      </div>

      <div role="cell" className="min-w-0 text-[13px] max-md:order-7 max-md:ml-auto">
        {next ? (
          <Link
            to={next.to}
            className={cn(
              'relative z-10 inline-flex min-h-10 items-center gap-1 rounded-[8px] font-semibold hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
              next.passive ? 'text-muted-foreground' : 'text-[var(--ui-info)]',
            )}
          >
            <span>
              {next.text} <span aria-hidden="true">→</span>
            </span>
          </Link>
        ) : (
          <span className="text-muted-foreground">Nothing to do</span>
        )}
      </div>
    </li>
  );
}

function ListHeader(): JSX.Element {
  const h = 'text-[12px] font-semibold uppercase tracking-[0.04em] text-muted-foreground';
  return (
    <div
      role="row"
      className={cn(
        // top-16 clears the shell's sticky 64px top bar.
        'sticky top-16 z-20 hidden rounded-t-card border-b border-border bg-[var(--ui-inset-soft)] px-4 py-2.5 backdrop-blur',
        GRID,
      )}
    >
      <span role="columnheader" className={h}>
        Opening
      </span>
      <span role="columnheader" className={h}>
        Setup
      </span>
      <span role="columnheader" className={h}>
        Approval
      </span>
      <span role="columnheader" className={h}>
        Status
      </span>
      <span role="columnheader" className={h}>
        Candidates
      </span>
      <span role="columnheader" className={h}>
        Next
      </span>
    </div>
  );
}

/** The roll-up across every opening in view, in one quiet line. */
function RollUp({ rows }: { rows: Requisition[] }): JSX.Element | null {
  if (rows.length === 0) return null;
  const waiting = rows.reduce((n, r) => n + r.awaiting_decision, 0);
  const candidates = rows.reduce((n, r) => n + r.total_enrolments, 0);
  const hired = rows.reduce((n, r) => n + r.hired, 0);
  const live = rows.filter((r) => isAcceptingApplications(r)).length;
  return (
    <span
      data-testid="openings-rollup"
      className="px-4 pt-3 text-[13px] tabular-nums text-muted-foreground"
    >
      Across these openings: {waiting} awaiting you · {candidates} candidates · {hired} hired · {live}{' '}
      accepting applications
    </span>
  );
}

export default function Requisitions(): JSX.Element {
  const navigate = useNavigate();
  const [tab, setTab] = useState('open');
  const [creating, setCreating] = useState(false);
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<OpeningSort>('needs-you');

  const list = useQuery({
    queryKey: ['hr', 'requisitions', tab],
    queryFn: () => listRequisitions(tab === 'all' ? {} : { status: tab as RequisitionStatus }),
    refetchInterval: LIVE_POLL_MS,
  });

  // Only to decide whether the review banner is worth showing. Cheap, cached,
  // and the alternative — a permanent link to a page that says "nothing to
  // review" — is the kind of dead nav that trains people to ignore banners.
  const review = useQuery({
    queryKey: ['hr', 'requisitions', 'review'],
    queryFn: getBackfillReview,
  });
  const pending =
    (review.data?.backfilled_requisitions.length ?? 0) +
    (review.data?.merge_candidates.length ?? 0);

  const all = list.data;
  const rows = useMemo(
    () => sortOpenings((all ?? []).filter((r) => matchesQuery(r, query)), sort),
    [all, query, sort],
  );

  const field =
    'h-10 rounded-[10px] border border-input bg-card px-3 text-[13.5px] text-foreground placeholder:text-[var(--ui-faint)] focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]/30';

  return (
    <Page>
      <PageHeader
        title="Openings"
        description="Each opening carries its own hiring workflow and its own funnel."
        actions={
          <Button type="button" onClick={() => setCreating(true)} data-testid="new-opening">
            <Plus aria-hidden="true" />
            New opening
          </Button>
        }
      />

      {pending > 0 ? (
        <Link
          to="/hr/requisitions/review"
          className="flex items-center gap-2.5 rounded-[16px] border border-[var(--ui-warn)]/40 bg-[var(--ui-warn-wash)] px-4 py-3 text-[14px] text-foreground hover:border-[var(--ui-warn)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          <AlertTriangle className="h-4 w-4 shrink-0 text-[var(--ui-warn)]" aria-hidden="true" />
          <span className="flex-1">
            {pending} thing{pending === 1 ? '' : 's'} to confirm from your imported data - openings
            that were grouped by job title, and people who may be duplicated.
          </span>
          <ChevronRight className="h-4 w-4 shrink-0" aria-hidden="true" />
        </Link>
      ) : null}

      <NewOpeningDialog
        open={creating}
        onOpenChange={setCreating}
        onCreated={(id) => void navigate(`/hr/requisitions/${id}?tab=setup`)}
      />

      <div className="-mt-2">
        <Toolbar className="!mb-0">
          <div className="relative min-w-[200px] max-w-[320px] flex-1 max-sm:max-w-none max-sm:basis-full">
            <Search
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden="true"
            />
            <input
              type="search"
              aria-label="Search openings"
              placeholder="Search title, department, location"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className={cn(field, 'w-full pl-9')}
            />
          </div>
          <SegTabs tabs={TABS} active={tab} onChange={setTab} />
          <label className="flex items-center gap-2 text-[13px] text-muted-foreground">
            Sort
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as OpeningSort)}
              className={field}
            >
              {SORTS.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          {list.data ? (
            <span
              className="ml-auto text-[13px] tabular-nums text-muted-foreground"
              aria-live="polite"
            >
              {rows.length} opening{rows.length === 1 ? '' : 's'}
            </span>
          ) : null}
        </Toolbar>
      </div>

      {list.isLoading ? (
        <div className="flex flex-col gap-2" role="status">
          <Skeleton className="h-14 rounded-card" />
          <Skeleton className="h-14 rounded-card" />
          <Skeleton className="h-14 rounded-card" />
          <span className="sr-only">Loading openings…</span>
        </div>
      ) : list.isError ? (
        <div
          role="alert"
          className="rounded-card border border-[var(--ui-danger)]/40 bg-[var(--ui-danger-wash)] p-4 text-[14px] text-[var(--ui-danger)]"
        >
          {errText(list.error, 'Could not load openings')}
        </div>
      ) : (all ?? []).length === 0 ? (
        <EmptyState
          size="page"
          icon={Briefcase}
          title={tab === 'open' ? 'No open roles' : `Nothing ${tab}`}
          description="Create an opening to give a role its own workflow, funnel and decision queue."
          action={
            <Button type="button" variant="outline" onClick={() => setCreating(true)}>
              <Plus aria-hidden="true" />
              New opening
            </Button>
          }
        />
      ) : rows.length === 0 ? (
        <EmptyState
          size="row"
          title={`No opening matches “${query.trim()}”.`}
          action={
            <Button type="button" variant="outline" size="sm" onClick={() => setQuery('')}>
              Clear the search
            </Button>
          }
        />
      ) : (
        <div role="table" aria-label="Openings" className="rounded-card border border-border bg-card shadow-card">
          <ListHeader />
          <ul role="rowgroup" className="divide-y divide-border">
            {rows.map((r) => (
              <RequisitionRow key={r.id} req={r} />
            ))}
          </ul>
          <div className="flex flex-col gap-1 border-t border-border">
            <RollUp rows={rows} />
            <DotLegend />
          </div>
        </div>
      )}
    </Page>
  );
}
