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
import { Loader2, Users } from '@/design/components/icons';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import { Reveal, Stagger, StaggerItem } from '@/design/components/Reveal';
import { LIVE_POLL_MS } from '@/lib/polling';
import {
  listAssignments,
  type InterviewerAssignment,
  type ScorecardState,
} from '@/api/interviewer';
import UpcomingInterviews from '@/components/interviewer/UpcomingInterviews';
import MyAvailability from '@/components/interviewer/MyAvailability';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

const STATE_META: Record<ScorecardState, { label: string; tone: 'amber' | 'electric' | 'ember' | 'forest' }> = {
  assigned: { label: 'Assigned', tone: 'electric' },
  in_progress: { label: 'In progress', tone: 'amber' },
  late: { label: 'Late', tone: 'ember' },
  submitted: { label: 'Submitted', tone: 'forest' },
};

function AssignmentRow({ a }: { a: InterviewerAssignment }) {
  const meta = STATE_META[a.state];
  return (
    <Link
      to={`/interviewer/scorecards/${a.scorecard_id}`}
      className="block rounded-[24px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
      aria-label={`Open scorecard for ${a.candidate_name} — ${a.round_title}`}
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
            </p>
          </div>
          <div className="shrink-0 text-right text-[12px] text-muted-foreground">
            {a.submitted_at ? (
              <span>Submitted {new Date(a.submitted_at).toLocaleString()}</span>
            ) : a.due_at ? (
              <span className={a.state === 'late' ? 'text-[var(--ui-danger)]' : undefined}>
                Due {new Date(a.due_at).toLocaleString()}
              </span>
            ) : null}
          </div>
        </div>
      </GlassCard>
    </Link>
  );
}

export default function InterviewerConsole(): JSX.Element {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['interviewer', 'assignments'],
    queryFn: listAssignments,
    refetchInterval: LIVE_POLL_MS,
  });

  const rows = data ?? [];

  return (
    <div className="mx-auto w-full max-w-[900px] px-4 py-8">
      <Reveal>
        <header className="mb-6">
          <h1 data-testid="page-title" className="text-[26px] font-semibold tracking-[-0.8px] text-foreground">
            My interviews
          </h1>
          <p className="mt-1.5 max-w-[70ch] text-[13.5px] leading-relaxed text-muted-foreground">
            Interviews assigned to you. Your scorecard is yours alone — nobody else&rsquo;s
            assessment is visible here, and nothing here decides an outcome.
          </p>
        </header>
      </Reveal>

      {isLoading ? (
        <div className="flex items-center gap-2 py-16 text-[13px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading your interviews…
        </div>
      ) : isError ? (
        <GlassCard className="p-6 text-[13.5px] text-[var(--ui-danger)]">
          {errText(error, 'Could not load your interviews')}
        </GlassCard>
      ) : rows.length === 0 ? (
        <GlassCard className="p-10 text-center">
          <Users className="mx-auto h-8 w-8 text-[var(--ui-faint)]" aria-hidden="true" />
          <div className="mt-3 text-[15px] font-medium text-foreground">Nothing assigned yet</div>
          <p className="mx-auto mt-1.5 max-w-[46ch] text-[13px] text-muted-foreground">
            Interviews HR assigns to you will show up here.
          </p>
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
    </div>
  );
}
