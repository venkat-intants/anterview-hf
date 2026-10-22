// History — Interview history page. Renders inside AppShell (no shell here).
// Design: a Toolbar (completion count left, total right) over a scannable list
// of sessions (table from `md` up, stacked cards below). The list API returns
// no scores, so the only figure shown is how many sessions on this page ran
// to completion — a plain count, never a ring or an average.
// Behavior: listSessions({page, perPage:10}) + pagination + status/format
// helpers + conditional scorecard link + i18n.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import { listSessions } from '@/api/sessions';
import type { SessionListItem } from '@/api/sessions';
import { LoadErrorPanel } from '@/components/candidate/LoadErrorPanel';
import { cn } from '@/lib/utils';
import { formatDate, formatDuration, statusProps } from '@/lib/formatters';
import { Button, buttonVariants } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Page, PageHeader, EmptyState } from '@/design/components/page';
import { StatusTag } from '@/design/components/primitives';
import { initialsOf } from '@/design/data/shared';
import {
  ChevronLeft,
  ChevronRight,
  Clock,
  Globe,
  ArrowRight,
  Play,
} from '@/design/components/icons';

// ── Constants ─────────────────────────────────────────────────────────────────

const PER_PAGE = 10;

const LANGUAGE_LABEL_KEYS: Record<string, string> = {
  en: 'history.langEnglish',
  hi: 'history.langHindi',
  te: 'history.langTelugu',
};

// Map session status to a StatusTag tone
function statusTone(
  status: string,
): 'forest' | 'electric' | 'amber' | 'ember' | 'neutral' {
  switch (status) {
    case 'completed':
      return 'forest';
    case 'in_progress':
      return 'electric';
    case 'abandoned':
      return 'amber';
    case 'failed':
      return 'ember';
    default:
      return 'neutral';
  }
}

// ── Sub-components ────────────────────────────────────────────────────────────

function RoleTile({ title }: { title: string }) {
  return (
    <span
      className="flex h-10 w-10 flex-none items-center justify-center rounded-full border border-border bg-[var(--ui-inset)] text-[13px] font-bold text-[var(--ui-soft)]"
      aria-hidden="true"
    >
      {initialsOf(title)}
    </span>
  );
}

function LoadingSkeletons() {
  const { t } = useTranslation();
  return (
    <div className="space-y-3" aria-busy="true" aria-label={t('history.loadingAria')}>
      {Array.from({ length: 4 }).map((_, i) => (
        <Skeleton key={i} className="h-16 w-full rounded-[16px]" />
      ))}
    </div>
  );
}

function HistoryEmpty() {
  const { t } = useTranslation();
  return (
    <div data-testid="history-empty-state">
      <EmptyState
        size="page"
        icon={Clock}
        title={t('history.noInterviewsTitle')}
        description={t('history.noInterviewsDesc')}
        action={
          <Link to="/start" className={buttonVariants()}>
            <Play aria-hidden="true" />
            {t('history.startFirstInterview')}
          </Link>
        }
      />
    </div>
  );
}

const TH = 'px-4 py-3 text-left text-[12px] font-semibold uppercase tracking-[0.06em] text-[var(--ui-soft)]';

/** Desktop table row */
function SessionRow({ session }: { session: SessionListItem }) {
  const { t } = useTranslation();
  const { label } = statusProps(session.status);
  const tone = statusTone(session.status);
  const langKey = LANGUAGE_LABEL_KEYS[session.language];
  const langLabel = langKey ? t(langKey) : session.language.toUpperCase();

  return (
    <tr
      className="border-t border-border transition-colors hover:bg-[var(--ui-inset-soft)]"
      data-testid={`session-row-${session.session_id}`}
    >
      <td className="px-4 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <RoleTile title={session.job_title} />
          <div className="min-w-0">
            <div className="max-w-[240px] truncate text-[14px] font-semibold text-foreground">
              {session.job_title}
            </div>
            <div className="flex items-center gap-1 text-[12px] text-muted-foreground">
              <Globe className="h-3 w-3 shrink-0" aria-hidden="true" />
              {langLabel}
            </div>
          </div>
        </div>
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-[13px] tabular-nums text-[var(--ui-soft)]">
        {formatDate(session.created_at)}
      </td>
      <td className="px-4 py-3">
        <StatusTag tone={tone} dot>
          {label}
        </StatusTag>
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-right text-[13px] tabular-nums text-muted-foreground">
        {formatDuration(session.duration_seconds)}
      </td>
      <td className="px-4 py-3 text-right">
        {session.scorecard_id ? (
          <Link
            to={`/scorecard/${session.scorecard_id}`}
            className="inline-flex items-center gap-1 rounded text-[13px] font-semibold text-[var(--ui-info)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            aria-label={t('history.viewScorecardFor', { title: session.job_title })}
          >
            <span className="hidden lg:inline">{t('history.viewScorecard')}</span>
            <ArrowRight size={15} aria-hidden="true" />
          </Link>
        ) : (
          <span className="text-[12px] text-muted-foreground" aria-hidden="true">
            -
          </span>
        )}
      </td>
    </tr>
  );
}

/** Mobile card */
function SessionCard({ session }: { session: SessionListItem }) {
  const { t } = useTranslation();
  const { label } = statusProps(session.status);
  const tone = statusTone(session.status);
  const langKey = LANGUAGE_LABEL_KEYS[session.language];
  const langLabel = langKey ? t(langKey) : session.language.toUpperCase();

  return (
    <li
      data-testid={`session-card-${session.session_id}`}
      className="rounded-card border border-border bg-card p-4 shadow-card"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-3">
          <RoleTile title={session.job_title} />
          <div className="min-w-0">
            <p className="truncate text-[14px] font-semibold text-foreground">{session.job_title}</p>
            <p className="mt-0.5 text-[12.5px] tabular-nums text-muted-foreground">
              {formatDate(session.created_at)}
            </p>
          </div>
        </div>
        <StatusTag tone={tone} dot className="shrink-0">
          {label}
        </StatusTag>
      </div>

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-muted-foreground">
        <span className="flex items-center gap-1">
          <Globe className="h-3 w-3" aria-hidden="true" />
          {langLabel}
        </span>
        <span className="flex items-center gap-1 tabular-nums">
          <Clock className="h-3 w-3" aria-hidden="true" />
          {formatDuration(session.duration_seconds)}
        </span>
      </div>

      {session.scorecard_id && (
        <div className="mt-3">
          <Link
            to={`/scorecard/${session.scorecard_id}`}
            className={buttonVariants({ variant: 'outline', size: 'sm' })}
          >
            {t('history.viewScorecard')}
            <ArrowRight aria-hidden="true" />
          </Link>
        </div>
      )}
    </li>
  );
}

/** Pagination controls */
function PaginationBar({
  page,
  totalPages,
  onPrev,
  onNext,
}: {
  page: number;
  totalPages: number;
  onPrev: () => void;
  onNext: () => void;
}) {
  const { t } = useTranslation();
  return (
    <nav
      className="flex items-center justify-between gap-3 pt-4"
      aria-label={t('history.paginationAria')}
    >
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={onPrev}
        disabled={page <= 1}
        aria-label={t('history.prevPage')}
      >
        <ChevronLeft aria-hidden="true" />
        {t('history.prevPage')}
      </Button>
      <span className="text-[13px] tabular-nums text-muted-foreground">
        {t('history.pageOf', { page, total: totalPages })}
      </span>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={onNext}
        disabled={page >= totalPages}
        aria-label={t('history.nextPage')}
      >
        {t('history.nextPage')}
        <ChevronRight aria-hidden="true" />
      </Button>
    </nav>
  );
}

// ── History page ──────────────────────────────────────────────────────────────

export default function History() {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['sessions', { page, perPage: PER_PAGE }],
    queryFn: () => listSessions({ page, perPage: PER_PAGE }),
    staleTime: 2 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  const sessions = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PER_PAGE));

  // SessionListItem carries no composite_score, so this is a completion count
  // for the sessions on the current page only — not a score of any kind.
  const completedCount = sessions.filter((s) => s.status === 'completed').length;

  const goPrev = () => setPage((p) => Math.max(1, p - 1));
  const goNext = () => setPage((p) => Math.min(totalPages, p + 1));

  return (
    <Page width="default">
      <PageHeader
        title={<span id="history-heading">{t('history.pageTitle')}</span>}
        description={t('history.pageDesc')}
      />

      {isLoading ? (
        <LoadingSkeletons />
      ) : isError && !data ? (
        // A failed load is not "no interviews yet" (audit CA-M6).
        <LoadErrorPanel
          title={t('history.loadError')}
          onRetry={() => void refetch()}
          retrying={isFetching}
        />
      ) : sessions.length === 0 ? (
        <HistoryEmpty />
      ) : (
        <div>
          {/* The counts live in the head of the list they describe: on md+ the
              table's card header, below that a plain line over the cards. One
              element, so the words are in the page once. */}
          <div className="min-w-0 md:overflow-hidden md:rounded-card md:border md:border-border md:bg-card md:shadow-card">
            <div className="flex flex-wrap items-center justify-between gap-3 pb-3 md:border-b md:border-border md:px-4 md:py-3">
              <span className="text-[13.5px] font-medium tabular-nums text-foreground">
                {t('history.completedOfShown', { completed: completedCount, shown: sessions.length })}
              </span>
              <span className="text-[13px] tabular-nums text-muted-foreground">
                {t('history.sessionsTotal', { count: total })}
              </span>
            </div>

            {/* Desktop table */}
            <div className="hidden md:block">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] border-collapse">
                  <thead className="bg-[var(--ui-inset-soft)]">
                    <tr>
                      <th scope="col" className={TH}>{t('history.columnRole')}</th>
                      <th scope="col" className={TH}>{t('history.columnDate')}</th>
                      <th scope="col" className={TH}>{t('history.columnStatus')}</th>
                      <th scope="col" className={cn(TH, 'text-right')}>{t('history.columnDuration')}</th>
                      <th scope="col" className={TH}>
                        <span className="sr-only">{t('history.columnScorecard')}</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {sessions.map((session) => (
                      <SessionRow key={session.session_id} session={session} />
                    ))}
                  </tbody>
                </table>
              </div>
              {totalPages > 1 && (
                <div className="border-t border-border px-4 pb-4">
                  <PaginationBar page={page} totalPages={totalPages} onPrev={goPrev} onNext={goNext} />
                </div>
              )}
            </div>

            {/* Mobile card list */}
            <div className="md:hidden">
              <ul className="space-y-3">
                {sessions.map((session) => (
                  <SessionCard key={session.session_id} session={session} />
                ))}
              </ul>
              {totalPages > 1 && (
                <PaginationBar page={page} totalPages={totalPages} onPrev={goPrev} onNext={goNext} />
              )}
            </div>
          </div>
        </div>
      )}
    </Page>
  );
}
