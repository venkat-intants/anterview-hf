// YourInterviews — PH4-A2. A candidate's own interview loops: job title, each
// session's time in the loop's own timezone (named), duration, location and
// interviewer names, a calendar download, and — for a session waiting on the
// candidate — the times they may pick from.
//
// A23: once a time is fixed there is NO reschedule or cancel control here —
// only HR can move a booked session, and that is audited. This component
// therefore never renders one, whatever the session's status.
//
// Candidate-facing: every string goes through i18n (EN/HI/TE per CLAUDE.md).

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  bookMySlot,
  downloadMyLoopIcs,
  getMySessionSlots,
  listMyInterviewLoops,
  type CandidateLoop,
  type CandidateSession,
} from '@/api/scheduling';
import { toast } from '@/lib/toast';
import {
  browserTimezone,
  dayHeading,
  dayKey,
  formatDayTime,
  timeInZone,
  zoneAbbrev,
} from '@/lib/timezone';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { SectionCard, EmptyState } from '@/design/components/page';
import { StatusTag, type TagTone } from '@/design/components/primitives';
import { CalendarDays, Download } from '@/design/components/icons';

function localeFor(lang: string): string {
  if (lang.startsWith('hi')) return 'hi-IN';
  if (lang.startsWith('te')) return 'te-IN';
  return 'en-IN';
}

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

const STATUS_TONE: Record<CandidateSession['status'], TagTone> = {
  awaiting_slot: 'amber',
  scheduled: 'electric',
  completed: 'forest',
  no_show: 'ember',
  cancelled: 'neutral',
};

function ChooseTimeSection({
  loop,
  session,
  locale,
}: {
  loop: CandidateLoop;
  session: CandidateSession;
  locale: string;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const slotsKey = ['me', 'interview-loop', loop.id, 'session', session.id, 'slots'];

  const slots = useQuery({
    queryKey: slotsKey,
    queryFn: () => getMySessionSlots(loop.id, session.id),
    enabled: open,
  });

  const bookMut = useMutation({
    mutationFn: (startsAt: string) =>
      bookMySlot(loop.id, {
        session_id: session.id,
        starts_at: startsAt,
        timezone: browserTimezone(),
      }),
    onSuccess: () => {
      toast.success(t('myInterviews.booked'));
      setOpen(false);
      void qc.invalidateQueries({ queryKey: ['me', 'interview-loops'] });
    },
    onError: (e: unknown) => {
      // Covers both known 409s the server can answer here: the exact slot was
      // just taken, or the schedule closed under the candidate (a decision or
      // an HR cancellation) — reusing the same path, since both are "refresh
      // and tell them why" rather than needing their own branch.
      toast.error(errText(e, t('myInterviews.bookError')));
      void qc.invalidateQueries({ queryKey: slotsKey });
      void qc.invalidateQueries({ queryKey: ['me', 'interview-loops'] });
    },
  });

  if (!open) {
    return (
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => setOpen(true)}
        className="mt-3"
      >
        {t('myInterviews.chooseTime')}
      </Button>
    );
  }

  // Grouped and shown in the candidate's OWN browser timezone, not the
  // loop's stored one — the loop's may be stale (set by HR, or from a
  // previous device), and this is what gets sent back on booking below.
  const myTz = browserTimezone();
  const groups = new Map<string, string[]>();
  for (const iso of slots.data ?? []) {
    const key = dayKey(iso, myTz);
    const arr = groups.get(key) ?? [];
    arr.push(iso);
    groups.set(key, arr);
  }

  return (
    <div className="mt-3 rounded-[14px] bg-card p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[14px] font-semibold text-foreground">{t('myInterviews.chooseTime')}</p>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="min-h-[40px] rounded px-1 text-[13px] font-semibold text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          {t('myInterviews.hideTimes')}
        </button>
      </div>

      {slots.isLoading ? (
        <p className="mt-2 text-[13px] text-muted-foreground">{t('myInterviews.slotsLoading')}</p>
      ) : slots.isError ? (
        <p role="alert" className="mt-2 text-[13px] text-[var(--ui-danger)]">
          {errText(slots.error, t('myInterviews.slotsError'))}
        </p>
      ) : (slots.data ?? []).length === 0 ? (
        <p className="mt-2 text-[13px] text-muted-foreground">{t('myInterviews.slotsEmpty')}</p>
      ) : (
        <div className="mt-2 flex flex-col gap-3">
          {Array.from(groups.entries()).map(([day, isos]) => (
            <div key={day}>
              <p className="text-[12px] font-semibold text-[var(--ui-soft)]">
                {dayHeading(isos[0], myTz, locale)}
              </p>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {isos.map((iso) => (
                  <button
                    key={iso}
                    type="button"
                    disabled={bookMut.isPending}
                    onClick={() => bookMut.mutate(iso)}
                    className="min-h-[40px] rounded-full border border-input bg-card px-3.5 text-[13px] font-medium text-foreground hover:border-[var(--ui-line-strong)] hover:bg-[var(--ui-inset-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-40"
                  >
                    {timeInZone(iso, myTz, locale)}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SessionRow({
  loop,
  session,
  locale,
}: {
  loop: CandidateLoop;
  session: CandidateSession;
  locale: string;
}) {
  const { t } = useTranslation();
  return (
    <li className="rounded-[14px] bg-[var(--ui-inset-soft)] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[14px] font-semibold text-foreground">{session.title}</p>
        <StatusTag tone={STATUS_TONE[session.status]} dot>
          {t(`myInterviews.status.${session.status}`)}
        </StatusTag>
      </div>
      <p className="mt-1 text-[13px] text-muted-foreground">
        {session.starts_at
          ? `${formatDayTime(session.starts_at, loop.timezone, locale)} ${zoneAbbrev(session.starts_at, loop.timezone)} · ${t(
              'myInterviews.duration',
              { count: session.duration_minutes },
            )}`
          : t('myInterviews.status.awaiting_slot')}
        {session.location ? ` · ${session.location}` : ''}
      </p>
      {session.interviewers.length > 0 ? (
        <p className="mt-1 text-[13px] text-[var(--ui-soft)]">
          {t('myInterviews.withInterviewers', { names: session.interviewers.join(', ') })}
        </p>
      ) : null}

      {session.status === 'awaiting_slot' && loop.self_schedule ? (
        <ChooseTimeSection loop={loop} session={session} locale={locale} />
      ) : null}

      {/* A23 — fixed once set. No reschedule or cancel control here, ever. */}
      {session.status === 'scheduled' ? (
        <p className="mt-2 text-[12px] text-muted-foreground">{t('myInterviews.fixedNotice')}</p>
      ) : null}
    </li>
  );
}

/**
 * One interview loop. `embedded` is for a loop shown ON its application card,
 * where the card already names the role: the heading is then the loop's own
 * title ("Panel interview"), not the job title again. The wrapper is a focus
 * target so the card's "See available times" action can scroll to it.
 */
function LoopCard({
  loop,
  locale,
  embedded = false,
}: {
  loop: CandidateLoop;
  locale: string;
  embedded?: boolean;
}) {
  const { t } = useTranslation();

  const icsMut = useMutation({
    mutationFn: () => downloadMyLoopIcs(loop.id),
    onError: (e: unknown) => toast.error(errText(e, t('myInterviews.calendarError'))),
  });

  return (
    <div id={`loop-${loop.id}`} tabIndex={-1} className="outline-none">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-[15px] font-bold text-foreground">
            {embedded ? loop.title : loop.job_title}
          </h3>
          <p className="mt-0.5 text-[13px] text-muted-foreground">{loop.timezone}</p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => icsMut.mutate()}
          disabled={icsMut.isPending}
        >
          <Download aria-hidden="true" />
          {icsMut.isPending
            ? t('myInterviews.downloadingCalendar')
            : t('myInterviews.addToCalendar')}
        </Button>
      </div>

      {loop.sessions.length > 0 ? (
        <ul className="mt-3 flex flex-col gap-2.5">
          {loop.sessions.map((s) => (
            <SessionRow key={s.id} loop={loop} session={s} locale={locale} />
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** Loops shown on their application card (the server sent an `application_id`). */
export function EmbeddedLoops({ loops }: { loops: CandidateLoop[] }): JSX.Element {
  const { i18n } = useTranslation();
  const locale = localeFor(i18n.language);
  return (
    <div className="flex flex-col gap-4">
      {loops.map((loop) => (
        <LoopCard key={loop.id} loop={loop} locale={locale} embedded />
      ))}
    </div>
  );
}

interface YourInterviewsProps {
  /** Loop ids already shown on an application card; they are left out here. */
  exclude?: ReadonlySet<string>;
  /** Render nothing (rather than an empty card) when there is nothing to show. */
  hideWhenEmpty?: boolean;
}

export default function YourInterviews({
  exclude,
  hideWhenEmpty = false,
}: YourInterviewsProps = {}): JSX.Element | null {
  const { t, i18n } = useTranslation();
  const locale = localeFor(i18n.language);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['me', 'interview-loops'],
    queryFn: listMyInterviewLoops,
    staleTime: 30 * 1000,
    retry: false,
    throwOnError: false,
  });

  const rows = (data ?? []).filter((l) => !exclude?.has(l.id));

  if (hideWhenEmpty && !isLoading && !isError && rows.length === 0) return null;

  return (
    <SectionCard title={t('myInterviews.heading')} icon={CalendarDays}>
      {isLoading ? (
        <div aria-busy="true" className="space-y-2">
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-4 w-1/3" />
          <span className="sr-only">{t('myInterviews.loading')}</span>
        </div>
      ) : isError ? (
        <p role="alert" className="text-[14px] text-[var(--ui-danger)]">
          {errText(error, t('myInterviews.loadError'))}
        </p>
      ) : rows.length === 0 ? (
        <EmptyState title={t('myInterviews.empty')} />
      ) : (
        <div className="flex flex-col gap-5">
          {rows.map((loop) => (
            <LoopCard key={loop.id} loop={loop} locale={locale} />
          ))}
        </div>
      )}
    </SectionCard>
  );
}
