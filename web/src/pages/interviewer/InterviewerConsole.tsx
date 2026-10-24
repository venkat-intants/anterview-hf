// InterviewerConsole (/interviewer) — D4-1. An interviewer's own queue.
//
// Scoped entirely server-side: /interviewer/assignments returns only what is
// assigned to the caller, sorted late-first then due-soonest then submitted
// last. Nothing here decides a hiring outcome — that boundary lives on the
// decision queue, not this console.
//
// English-only by design (CLAUDE.md — staff consoles are not translated).

import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Users } from '@/design/components/icons';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { Reveal, Stagger, StaggerItem } from '@/design/components/Reveal';
import { EmptyState, PageHeader, Page } from '@/design/components/page';
import { LIVE_POLL_MS } from '@/lib/polling';
import {
  listAssignments,
  type InterviewerAssignment,
  type ScorecardState,
} from '@/api/interviewer';
import UpcomingInterviews from '@/components/interviewer/UpcomingInterviews';
import MyAvailability from '@/components/interviewer/MyAvailability';
import { QueryErrorPanel } from '@/components/QueryState';
import { Skeleton } from '@/components/ui/skeleton';
import { formatDateTime } from '@/lib/formatters';

const STATE_META: Record<
  ScorecardState,
  { label: string; tone: 'amber' | 'electric' | 'ember' | 'forest' }
> = {
  assigned: { label: 'Assigned', tone: 'electric' },
  in_progress: { label: 'In progress', tone: 'amber' },
  late: { label: 'Late', tone: 'ember' },
  submitted: { label: 'Submitted', tone: 'forest' },
};

/** PH4-D4 — a job_simulation/portfolio round reads evidence, not an
 *  interview; the label says which kind of review this is. */
const ROUND_KIND_LABEL: Record<string, string> = {
  job_simulation: 'Simulation review',
  portfolio: 'Portfolio review',
};

function AssignmentRow({ a }: { a: InterviewerAssignment }) {
  const meta = STATE_META[a.state];
  return (
    <Link
      to={`/interviewer/scorecards/${a.scorecard_id}`}
      className="block rounded-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
      aria-label={`Open scorecard for ${a.candidate_name} - ${a.round_title}`}
    >
      <GlassCard hover className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[15px] font-medium text-foreground">{a.candidate_name}</span>
              <StatusTag tone={meta.tone} dot>
                {meta.label}
              </StatusTag>
              {a.is_correction ? <StatusTag tone="lavender">Correction</StatusTag> : null}
            </div>
            <p className="mt-1 text-[12.5px] text-muted-foreground">
              {a.job_title} &middot; {a.round_title}
              {a.round_kind && ROUND_KIND_LABEL[a.round_kind] ? (
                <span className="ml-1.5 text-[var(--ui-faint)]">
                  &middot; {ROUND_KIND_LABEL[a.round_kind]}
                </span>
              ) : null}
            </p>
          </div>
          <div className="shrink-0 text-right text-[12px] text-muted-foreground">
            {a.submitted_at ? (
              <span>Submitted {formatDateTime(a.submitted_at)}</span>
            ) : a.due_at ? (
              <span className={a.state === 'late' ? 'text-[var(--ui-danger)]' : undefined}>
                Due {formatDateTime(a.due_at)}
              </span>
            ) : null}
          </div>
        </div>
      </GlassCard>
    </Link>
  );
}

export default function InterviewerConsole(): JSX.Element {
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['interviewer', 'assignments'],
    queryFn: listAssignments,
    refetchInterval: LIVE_POLL_MS,
  });

  const rows = data ?? [];

  return (
    <Page width="form">
      <Reveal>
        <PageHeader
          className="mb-0"
          title={<span data-testid="page-title">My interviews</span>}
          description="Interviews assigned to you. Your scorecard is yours alone - nobody else’s assessment is visible here, and nothing here decides an outcome."
        />
      </Reveal>

      {/* The assignments list gets a heading of its own (AD-F30), like the two
          sections below it. */}
      <h2 className="-mb-3 text-[16px] font-semibold text-foreground">Scorecards to write</h2>
      {isLoading ? (
        <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading your interviews">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : isError ? (
        <QueryErrorPanel
          title="Could not load your interviews."
          error={error}
          onRetry={() => void refetch()}
          retrying={isFetching}
        />
      ) : rows.length === 0 ? (
        <GlassCard className="p-4">
          <EmptyState
            icon={Users}
            title="Nothing assigned yet"
            description="Interviews HR assigns to you will show up here."
          />
        </GlassCard>
      ) : (
        <Stagger className="flex flex-col gap-3">
          {rows.map((a) => (
            <StaggerItem key={a.scorecard_id}>
              <AssignmentRow a={a} />
            </StaggerItem>
          ))}
        </Stagger>
      )}

      <UpcomingInterviews />
      <MyAvailability />
    </Page>
  );
}
