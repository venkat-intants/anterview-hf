// History — Interview history page. Renders inside AppShell (no shell here).
// Design: GlassCard table + avg ScoreRing ring (computed from real data).
// Behavior: listSessions({page, perPage:10}) + pagination + responsive table/card
// split at `md` + status/format helpers + conditional scorecard link + i18n.

import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import { listSessions } from '@/api/sessions';
import type { SessionListItem } from '@/api/sessions';
import { toast } from '@/lib/toast';
import { formatDate, formatDuration, statusProps } from '@/lib/formatters';
import { cn } from '@/lib/utils';
import { GlassCard, ScoreRing, StatusTag, Avatar } from '@/design/components/primitives';
import { gradientFor, initialsOf } from '@/design/data/shared';
import { Reveal, Stagger, StaggerItem } from '@/design/components/Reveal';
import {
  ChevronLeft,
  ChevronRight,
  ExternalLink,
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

// ── Inline skeleton — avoids @/components/ui/* shadcn dep ────────────────────

function Sk({ className }: { className?: string }) {
  return (
    <div
      className={cn('animate-pulse rounded-md bg-white/[0.06]', className)}
      aria-hidden="true"
    />
  );
}

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

function LoadingSkeletons() {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="Loading interview history">
      {Array.from({ length: 4 }).map((_, i) => (
        <Sk key={i} className="h-16 w-full rounded-[16px]" />
      ))}
    </div>
  );
}

function EmptyState() {
  const { t } = useTranslation();
  return (
    <div data-testid="history-empty-state">
      <GlassCard className="flex flex-col items-center justify-center gap-4 py-20 text-center">
        <div className="inline-flex h-14 w-14 items-center justify-center rounded-full bg-[rgba(var(--accent-rgb),0.1)] ring-1 ring-[rgba(var(--accent-rgb),0.2)]">
          <Clock className="h-7 w-7 text-[#60a5fa]" aria-hidden="true" />
        </div>
        <div>
          <p className="text-[15px] font-semibold text-white">{t('history.noInterviewsTitle')}</p>
          <p className="mt-1 text-[13px] text-[#888b91]">{t('history.noInterviewsDesc')}</p>
        </div>
        <Link
          to="/start"
          className="inline-flex items-center gap-1.5 rounded-[9999px] bg-white px-4 py-2 text-[13px] font-semibold text-black hover:bg-[#eaeaea] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] mt-1"
        >
          <Play className="h-4 w-4" aria-hidden="true" />
          {t('history.startFirstInterview')}
        </Link>
      </GlassCard>
    </div>
  );
}

/** Desktop table row */
function SessionRow({ session }: { session: SessionListItem }) {
  const { t } = useTranslation();
  const { label } = statusProps(session.status);
  const tone = statusTone(session.status);
  const langKey = LANGUAGE_LABEL_KEYS[session.language];
  const langLabel = langKey ? t(langKey) : session.language.toUpperCase();
  // Seed the gradient from the session_id charCode so it's stable per session
  const seed = session.session_id.charCodeAt(0);

  return (
    <div
      className="grid grid-cols-[2fr_1fr_0.8fr_0.8fr_0.6fr_0.5fr] items-center gap-3 border-b border-white/[0.04] px-5 py-3.5 transition-colors last:border-0 hover:bg-white/[0.03]"
      data-testid={`session-row-${session.session_id}`}
    >
      {/* Role */}
      <div className="flex items-center gap-3 min-w-0">
        <Avatar
          initials={initialsOf(session.job_title)}
          gradient={gradientFor(seed)}
          size={34}
        />
        <div className="min-w-0">
          <div className="truncate text-[13.5px] font-medium text-white">{session.job_title}</div>
          <div className="flex items-center gap-1 text-[11.5px] text-[#70757c]">
            <Globe className="h-3 w-3 shrink-0" aria-hidden="true" />
            {langLabel}
          </div>
        </div>
      </div>

      {/* Date */}
      <div className="text-[13px] text-[#b8babf] tabular-nums">
        {formatDate(session.created_at)}
      </div>

      {/* Status */}
      <div>
        <StatusTag tone={tone}>{label}</StatusTag>
      </div>

      {/* Duration */}
      <div className="flex items-center gap-1.5 text-[13px] text-[#888b91] tabular-nums">
        <Clock className="h-3.5 w-3.5 shrink-0 text-[#70757c]" aria-hidden="true" />
        {formatDuration(session.duration_seconds)}
      </div>

      {/* Score placeholder — session list doesn't carry composite score */}
      <div className="text-[13px] text-[#70757c]">—</div>

      {/* Scorecard link */}
      <div className="flex items-center justify-end">
        {session.scorecard_id ? (
          <Link
            to={`/scorecard/${session.scorecard_id}`}
            className="flex items-center gap-1 text-[13px] text-[#60a5fa] transition-colors hover:text-white"
            aria-label={`View scorecard for ${session.job_title}`}
          >
            <span className="hidden lg:inline">{t('history.viewScorecard')}</span>
            <ArrowRight size={15} aria-hidden="true" />
          </Link>
        ) : (
          <span className="text-[12px] text-[#70757c]">—</span>
        )}
      </div>
    </div>
  );
}

/** Mobile card */
function SessionCard({ session }: { session: SessionListItem }) {
  const { t } = useTranslation();
  const { label } = statusProps(session.status);
  const tone = statusTone(session.status);
  const langKey = LANGUAGE_LABEL_KEYS[session.language];
  const langLabel = langKey ? t(langKey) : session.language.toUpperCase();
  const seed = session.session_id.charCodeAt(0);

  return (
    <StaggerItem>
      <div data-testid={`session-card-${session.session_id}`}>
      <GlassCard
        hover
        className="p-4"
      >
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2.5 min-w-0">
            <Avatar
              initials={initialsOf(session.job_title)}
              gradient={gradientFor(seed)}
              size={34}
            />
            <div className="min-w-0">
              <p className="truncate text-[14px] font-medium text-white">{session.job_title}</p>
              <p className="mt-0.5 text-[12px] text-[#888b91] tabular-nums">
                {formatDate(session.created_at)}
              </p>
            </div>
          </div>
          <StatusTag tone={tone} className="shrink-0">
            {label}
          </StatusTag>
        </div>

        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-[#888b91]">
          <span className="flex items-center gap-1">
            <Globe className="h-3 w-3 text-[#70757c]" aria-hidden="true" />
            {langLabel}
          </span>
          <span className="flex items-center gap-1 tabular-nums">
            <Clock className="h-3 w-3 text-[#70757c]" aria-hidden="true" />
            {formatDuration(session.duration_seconds)}
          </span>
        </div>

        {session.scorecard_id && (
          <div className="mt-3">
            <Link
              to={`/scorecard/${session.scorecard_id}`}
              className="inline-flex items-center gap-1.5 rounded-[9999px] border border-white/[0.1] bg-white/[0.04] px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-white/[0.08] hover:border-white/20 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              {t('history.viewScorecard')}
              <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            </Link>
          </div>
        )}
      </GlassCard>
      </div>
    </StaggerItem>
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
    <div className="flex items-center justify-between pt-4" aria-label="Pagination">
      <button
        type="button"
        onClick={onPrev}
        disabled={page <= 1}
        className={cn(
          'inline-flex items-center gap-1.5 rounded-[9999px] border border-white/[0.1] bg-transparent px-3.5 py-2 text-[13px] font-medium text-white transition-colors',
          'hover:bg-white/[0.06] hover:border-white/20',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
          'disabled:cursor-not-allowed disabled:opacity-40',
        )}
        aria-label={t('history.prevPage')}
      >
        <ChevronLeft className="h-4 w-4" aria-hidden="true" />
        {t('history.prevPage')}
      </button>
      <span className="text-[13px] text-[#888b91] tabular-nums">
        {t('history.pageOf', { page, total: totalPages })}
      </span>
      <button
        type="button"
        onClick={onNext}
        disabled={page >= totalPages}
        className={cn(
          'inline-flex items-center gap-1.5 rounded-[9999px] border border-white/[0.1] bg-transparent px-3.5 py-2 text-[13px] font-medium text-white transition-colors',
          'hover:bg-white/[0.06] hover:border-white/20',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
          'disabled:cursor-not-allowed disabled:opacity-40',
        )}
        aria-label={t('history.nextPage')}
      >
        {t('history.nextPage')}
        <ChevronRight className="h-4 w-4" aria-hidden="true" />
      </button>
    </div>
  );
}

// ── History page ──────────────────────────────────────────────────────────────

export default function History() {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['sessions', { page, perPage: PER_PAGE }],
    queryFn: () => listSessions({ page, perPage: PER_PAGE }),
    staleTime: 2 * 60 * 1000,
    retry: false,
    throwOnError: false,
  });

  // Fire error toast once per distinct error object — never on every render.
  useEffect(() => {
    if (isError) {
      toast.error(error instanceof Error ? error.message : 'Failed to load interview history.');
    }
  }, [isError, error]);

  const sessions = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PER_PAGE));

  // Compute avg ring from completed sessions as a proxy (SessionListItem has no composite_score).
  const completedCount = sessions.filter((s) => s.status === 'completed').length;
  const avgRingScore: number =
    sessions.length > 0 ? Math.round((completedCount / sessions.length) * 100) : 0;

  return (
    <div
      aria-labelledby="history-heading"
      className="mx-auto max-w-[1000px] px-6 py-8 lg:px-8"
    >
      {/* Page heading */}
      <Reveal>
        <h1
          id="history-heading"
          className="text-[28px] font-semibold tracking-[-1px] text-white"
        >
          {t('history.pageTitle')}
        </h1>
        <p className="mt-1 text-[14px] text-[#888b91]">{t('history.pageDesc')}</p>
      </Reveal>

      {/* Content */}
      <div className="mt-6">
        {isLoading ? (
          <LoadingSkeletons />
        ) : sessions.length === 0 ? (
          <EmptyState />
        ) : (
          <div className="grid grid-cols-1 gap-5 md:grid-cols-3">
            {/* Avg-score ring — left panel (design's "featured" GlassCard) */}
            <Reveal dir="left">
              <GlassCard
                feature
                className="flex h-full flex-col items-center justify-center gap-3 text-center"
              >
                <ScoreRing score={avgRingScore} size={120} label="avg" />
                <div className="text-[13px] text-[#9fb6d6]">
                  {t('history.sessionsTotal', { count: total })}
                </div>
                <div className="mt-1 text-[12px] text-[#70757c]">
                  {t('history.sessionsTitle')}
                </div>
              </GlassCard>
            </Reveal>

            {/* Table / card list — right panel */}
            <div className="md:col-span-2">
              <Reveal dir="right">
                {/* Desktop table */}
                <div className="hidden md:block">
                  <GlassCard className="overflow-hidden p-0">
                    {/* Table header */}
                    <div className="grid grid-cols-[2fr_1fr_0.8fr_0.8fr_0.6fr_0.5fr] gap-3 border-b border-white/[0.06] px-5 py-3.5 text-[11.5px] uppercase tracking-[0.5px] text-[#70757c]">
                      <div>{t('history.columnRole')}</div>
                      <div>{t('history.columnDate')}</div>
                      <div>{t('history.columnStatus')}</div>
                      <div>{t('history.columnDuration')}</div>
                      <div>{t('history.columnScorecard')}</div>
                      <div />
                    </div>

                    {/* Rows */}
                    {sessions.map((session) => (
                      <SessionRow key={session.session_id} session={session} />
                    ))}

                    {/* Pagination inside the card */}
                    {totalPages > 1 && (
                      <div className="px-5 pb-4">
                        <PaginationBar
                          page={page}
                          totalPages={totalPages}
                          onPrev={() => setPage((p) => Math.max(1, p - 1))}
                          onNext={() => setPage((p) => Math.min(totalPages, p + 1))}
                        />
                      </div>
                    )}
                  </GlassCard>
                </div>

                {/* Mobile card list */}
                <Stagger
                  className={cn('md:hidden space-y-3')}
                >
                  {sessions.map((session) => (
                    <SessionCard key={session.session_id} session={session} />
                  ))}
                  {totalPages > 1 && (
                    <PaginationBar
                      page={page}
                      totalPages={totalPages}
                      onPrev={() => setPage((p) => Math.max(1, p - 1))}
                      onNext={() => setPage((p) => Math.min(totalPages, p + 1))}
                    />
                  )}
                </Stagger>
              </Reveal>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
