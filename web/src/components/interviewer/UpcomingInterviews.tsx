// UpcomingInterviews — PH4-A2/O5. An interviewer's own sessions, scoped
// entirely server-side (GET /interviewer/sessions never returns anyone
// else's — O5 #6). English-only by design, same as the console it lives on
// (CLAUDE.md — staff consoles are not translated).

import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Calendar, Loader2, MapPin } from '@/design/components/icons';
import { GlassCard, StatusTag, type TagTone } from '@/design/components/primitives';
import { getMySessions, type SessionStatus } from '@/api/scheduling';
import { formatDayTime, browserTimezone } from '@/lib/timezone';

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

const STATUS_TONE: Record<SessionStatus, TagTone> = {
  awaiting_slot: 'amber',
  scheduled: 'electric',
  completed: 'forest',
  no_show: 'ember',
  cancelled: 'neutral',
};

export default function UpcomingInterviews(): JSX.Element {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['interviewer', 'sessions'],
    queryFn: () => getMySessions(),
  });

  const rows = data ?? [];

  return (
    <section className="mt-8">
      <h2 className="text-[16px] font-semibold text-foreground">Upcoming interviews</h2>
      <p className="mt-1 text-[12.5px] text-muted-foreground">
        Sessions you are on — in your own local time ({browserTimezone()}).
      </p>

      {isLoading ? (
        <div className="mt-3 flex items-center gap-2 text-[13px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading…
        </div>
      ) : isError ? (
        <GlassCard className="mt-3 p-4 text-[13px] text-[var(--ui-danger)]">
          {errText(error, 'Could not load your interviews')}
        </GlassCard>
      ) : rows.length === 0 ? (
        <GlassCard className="mt-3 p-6 text-center text-[13px] text-muted-foreground">
          Nothing scheduled right now.
        </GlassCard>
      ) : (
        <ul className="mt-3 flex flex-col gap-2.5">
          {rows.map((s) => (
            <li key={s.id}>
              <GlassCard className="p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-[14px] font-medium text-foreground">{s.candidate}</p>
                    <p className="mt-0.5 text-[12.5px] text-muted-foreground">
                      {s.job_title} &middot; {s.title}
                    </p>
                    <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-[var(--ui-soft)]">
                      <span className="inline-flex items-center gap-1">
                        <Calendar size={12} aria-hidden="true" />
                        {s.starts_at
                          ? `${formatDayTime(s.starts_at, browserTimezone())} · ${s.duration_minutes} min`
                          : 'Time not set yet'}
                      </span>
                      {s.location ? (
                        <span className="inline-flex items-center gap-1">
                          <MapPin size={12} aria-hidden="true" />
                          {s.location}
                        </span>
                      ) : null}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-2">
                    <StatusTag tone={STATUS_TONE[s.status]} dot>
                      {s.status.replace('_', ' ')}
                    </StatusTag>
                    {s.scorecard_id ? (
                      <Link
                        to={`/interviewer/scorecards/${s.scorecard_id}`}
                        className="text-[12px] text-[var(--ui-info)] hover:underline"
                      >
                        Open scorecard
                      </Link>
                    ) : null}
                  </div>
                </div>
              </GlassCard>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
