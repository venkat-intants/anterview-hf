// JobsList — protected page inside AppShell; fetches GET /jobs; renders a grid
// of job cards. "Start Interview" is gated by DPDP Act 2023 consent (S3-011).
// Language picker persisted to localStorage ('intants:interview-language').
// AppShell provides the top bar — this page does NOT render its own header.
//
// Design: anterview-pages/src/screens/candidate/JobsList.tsx layout merged
// with all live behavior (consent, session start, i18n, filters).

import { useState, useCallback, useEffect } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { useAuth } from '@/context/AuthContext';
import { useConsent } from '@/context/ConsentContext';
import { getJobs } from '@/api/jobs';
import { createSession } from '@/api/sessions';
import JobCard from '@/components/JobCard';
import ConsentModal from '@/components/ConsentModal';
import { Reveal, Stagger, StaggerItem } from '@/design/components/Reveal';
import { GlassCard, SegTabs } from '@/design/components/primitives';
import { Search, Briefcase, X, AlertTriangle, RefreshCw } from '@/design/components/icons';
import { cn } from '@/lib/utils';
import type { Language } from '@/types/interview';

// ── Constants ──────────────────────────────────────────────────────────────────

const LANGUAGE_STORAGE_KEY = 'intants:interview-language';

const LANGUAGE_OPTIONS: { value: Language; label: string }[] = [
  { value: 'en', label: 'English' },
  { value: 'hi', label: 'हिंदी (Hindi)' },
  { value: 'te', label: 'తెలుగు (Telugu)' },
];

// Label resolved via t() at render time so it follows the UI language.
const LEVEL_OPTIONS = [
  { value: '', labelKey: 'jobs.allLevels' },
  { value: 'entry', labelKey: 'jobs.levelEntry' },
  { value: 'mid', labelKey: 'jobs.levelMid' },
  { value: 'senior', labelKey: 'jobs.levelSenior' },
] as const;

type LevelFilter = '' | 'entry' | 'mid' | 'senior';

// SegTabs items for the level filter — labels resolved once per render inside
// the component where t() is available.
const LEVEL_TAB_KEYS = [
  { key: '', labelKey: 'jobs.allLevels' },
  { key: 'entry', labelKey: 'jobs.levelEntry' },
  { key: 'mid', labelKey: 'jobs.levelMid' },
  { key: 'senior', labelKey: 'jobs.levelSenior' },
] as const;

// ── Inline skeleton — avoids @/components/ui/* shadcn dep ────────────────────

function Sk({ className }: { className?: string }) {
  return (
    <div
      className={cn('animate-pulse rounded-md bg-white/[0.06]', className)}
      aria-hidden="true"
    />
  );
}

// ── Loading skeleton card ──────────────────────────────────────────────────────

function JobCardSkeleton() {
  return (
    <div
      className="rounded-[24px] border border-white/[0.08] bg-[#0f0f10] p-5 flex flex-col gap-4"
      aria-hidden="true"
    >
      <div className="flex items-start gap-3">
        <Sk className="h-12 w-12 flex-none rounded-[12px]" />
        <div className="flex-1 space-y-2">
          <Sk className="h-4 w-3/4 rounded" />
          <Sk className="h-3 w-1/2 rounded" />
        </div>
        <Sk className="h-5 w-20 rounded-full" />
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Sk className="h-5 w-16 rounded-full" />
        <Sk className="h-5 w-20 rounded-full" />
        <Sk className="h-5 w-14 rounded-full" />
      </div>
      <div className="flex items-center gap-4">
        <Sk className="h-3 w-24 rounded" />
        <Sk className="h-3 w-16 rounded" />
      </div>
      <div className="flex items-center gap-2.5 border-t border-white/[0.06] pt-4">
        <Sk className="h-10 flex-1 rounded-full" />
        <Sk className="h-10 w-20 rounded-full" />
      </div>
    </div>
  );
}

// ── JobsList page ──────────────────────────────────────────────────────────────

export default function JobsList() {
  const { t } = useTranslation();
  const { accessToken } = useAuth();
  const navigate = useNavigate();
  const { consented, loading: consentLoading, recordConsent } = useConsent();

  // Language picker — persisted to localStorage
  const [selectedLanguage, setSelectedLanguage] = useState<Language>(() => {
    const stored = localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (stored === 'en' || stored === 'hi' || stored === 'te') return stored;
    return 'en';
  });

  useEffect(() => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, selectedLanguage);
  }, [selectedLanguage]);

  // Level filter (client-side only)
  const [levelFilter, setLevelFilter] = useState<LevelFilter>('');

  // Design search box — additive presentation only (no backend; filters by title)
  const [searchQuery, setSearchQuery] = useState('');

  // Pending job that was clicked while consent was needed
  const [pendingJobId, setPendingJobId] = useState<string | null>(null);
  // Whether the consent modal is visible
  const [showConsent, setShowConsent] = useState(false);
  // In-flight state for POST /consent
  const [isSubmittingConsent, setIsSubmittingConsent] = useState(false);
  const [consentError, setConsentError] = useState<string | null>(null);
  // Banner shown after Decline
  const [showDeclineBanner, setShowDeclineBanner] = useState(false);

  // ── Data fetching ───────────────────────────────────────────────────────────

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['jobs'],
    queryFn: () => {
      if (!accessToken) throw new Error('No access token');
      return getJobs(accessToken);
    },
    enabled: accessToken !== null,
    staleTime: 60 * 1000,
    retry: 1,
  });

  const startInterviewMutation = useMutation({
    mutationFn: ({ jobId, language }: { jobId: string; language: Language }) => {
      if (!accessToken) return Promise.reject(new Error('No access token'));
      return createSession({ job_id: jobId, language }, accessToken);
    },
    onSuccess: (result) => {
      void navigate(`/interview/${result.session_id}`);
    },
  });

  /** Kick off the session once consent is confirmed */
  const proceedToSession = useCallback(
    (jobId: string) => {
      startInterviewMutation.mutate({ jobId, language: selectedLanguage });
    },
    [startInterviewMutation, selectedLanguage],
  );

  /** Called when a JobCard "Start Interview" button is clicked */
  const handleStartInterview = useCallback(
    (jobId: string) => {
      setShowDeclineBanner(false);
      setConsentError(null);

      if (consented === true) {
        // Already consented — go straight to session create
        proceedToSession(jobId);
      } else {
        // Need consent first: store the pending job and show the modal
        setPendingJobId(jobId);
        setShowConsent(true);
      }
    },
    [consented, proceedToSession],
  );

  /** User clicked "I Agree" in the modal */
  const handleAgree = useCallback(async () => {
    setIsSubmittingConsent(true);
    setConsentError(null);
    try {
      await recordConsent();
      setShowConsent(false);
      if (pendingJobId) {
        proceedToSession(pendingJobId);
        setPendingJobId(null);
      }
    } catch (err) {
      setConsentError(err instanceof Error ? err.message : t('jobs.consentError'));
    } finally {
      setIsSubmittingConsent(false);
    }
  }, [recordConsent, pendingJobId, proceedToSession, t]);

  /** User clicked "Decline" or pressed Esc in the modal */
  const handleDecline = useCallback(() => {
    setShowConsent(false);
    setPendingJobId(null);
    setConsentError(null);
    setShowDeclineBanner(true);
    void navigate('/jobs');
  }, [navigate]);

  // ── Derived data ────────────────────────────────────────────────────────────

  const allJobs = data?.items ?? [];

  const jobs = allJobs.filter((j) => {
    const matchesLevel = levelFilter === '' || j.level === levelFilter;
    const matchesSearch =
      searchQuery === '' || j.title.toLowerCase().includes(searchQuery.toLowerCase());
    return matchesLevel && matchesSearch;
  });

  // SegTabs labels built with t() — fine to compute inline since hooks aren't called
  const levelTabs = LEVEL_TAB_KEYS.map((opt) => ({ key: opt.key, label: t(opt.labelKey) }));

  // ── Loading state ───────────────────────────────────────────────────────────

  if (isLoading || consentLoading) {
    return (
      <div className="mx-auto max-w-[1100px] px-6 py-8 lg:px-8 space-y-6">
        <div>
          <Sk className="h-7 w-40 rounded mb-2" />
          <Sk className="h-4 w-72 rounded" />
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Sk className="h-10 w-[260px] rounded-full" />
          <Sk className="h-10 w-52 rounded-full" />
          <Sk className="h-10 w-44 rounded-[9px]" />
          <Sk className="h-10 w-36 rounded-[9px]" />
        </div>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <JobCardSkeleton key={i} />
          ))}
        </div>
      </div>
    );
  }

  // ── Error state ─────────────────────────────────────────────────────────────

  if (isError) {
    return (
      <div className="mx-auto max-w-[1100px] px-6 py-8 lg:px-8">
        <div
          role="alert"
          className="rounded-[24px] border border-[rgba(230,113,79,0.3)] bg-[rgba(230,113,79,0.07)] p-6 flex flex-col sm:flex-row items-start sm:items-center gap-4"
        >
          <AlertTriangle className="h-5 w-5 text-[#e6714f] shrink-0" aria-hidden="true" />
          <p className="text-[14px] text-[#e6714f] flex-1">
            {error instanceof Error ? error.message : t('jobs.loadError')}
          </p>
          <button
            type="button"
            onClick={() => void refetch()}
            className="inline-flex items-center gap-1.5 rounded-[9999px] border border-[rgba(230,113,79,0.35)] bg-[rgba(230,113,79,0.1)] px-4 py-2 text-[13px] font-medium text-[#e6714f] hover:bg-[rgba(230,113,79,0.18)] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#e6714f]"
          >
            <RefreshCw size={14} aria-hidden="true" />
            {t('jobs.retry')}
          </button>
        </div>
      </div>
    );
  }

  // ── Main render ─────────────────────────────────────────────────────────────

  return (
    <div className="mx-auto max-w-[1100px] px-6 py-8 lg:px-8">

      {/* ── Page heading ─────────────────────────────────────────────────────── */}
      <Reveal>
        <h1 className="text-[28px] font-semibold tracking-[-1px]">
          {t('jobs.pageTitle')}
        </h1>
        <p className="mt-1 text-[14px] text-[#888b91]">
          {t('jobs.pageSubtitle')}
        </p>
      </Reveal>

      {/* ── Decline banner ───────────────────────────────────────────────────── */}
      {showDeclineBanner && (
        <div
          role="alert"
          className="mt-4 rounded-[16px] border border-[rgba(255,183,100,0.3)] bg-[rgba(255,183,100,0.08)] px-4 py-3 flex items-start justify-between gap-4"
        >
          <p className="text-[14px] text-[#ffb764]">{t('jobs.declineBanner')}</p>
          <button
            type="button"
            onClick={() => setShowDeclineBanner(false)}
            aria-label={t('jobs.dismiss')}
            className="shrink-0 rounded text-[#ffb764]/80 hover:text-[#ffb764] focus:outline-none focus-visible:ring-2 focus-visible:ring-[#ffb764]/50"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      )}

      {/* ── Session-start error banner ────────────────────────────────────────── */}
      {startInterviewMutation.isError && (
        <div
          role="alert"
          className="mt-4 rounded-[16px] border border-[rgba(230,113,79,0.3)] bg-[rgba(230,113,79,0.07)] px-4 py-3"
        >
          <p className="text-[14px] text-[#e6714f]">
            {startInterviewMutation.error instanceof Error
              ? startInterviewMutation.error.message
              : t('jobs.startError')}
          </p>
        </div>
      )}

      {/* ── Toolbar ──────────────────────────────────────────────────────────── */}
      {/*
        Layout: [search box] [SegTabs for level] [language select] [level select (sr-only + hidden)] [count]
        The native <select id="interview-language"> and <select id="level-filter"> MUST remain
        present for test-suite compatibility (tests use them directly). SegTabs is additive UX;
        it syncs with the hidden level select.
      */}
      <div className="mt-6 flex flex-wrap items-center gap-3">

        {/* Design search box — additive; client-filters by title */}
        <div className="flex w-[260px] items-center gap-2 rounded-[9999px] border border-white/[0.08] bg-[rgba(28,29,31,0.7)] px-3.5 py-2.5">
          <Search size={15} className="text-[#70757c] shrink-0" aria-hidden="true" />
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t('jobs.searchPlaceholder')}
            aria-label={t('jobs.searchPlaceholder')}
            className="min-w-0 flex-1 bg-transparent text-[13px] text-white placeholder:text-[#5a5f66] focus:outline-none"
          />
        </div>

        {/* SegTabs for level — additive design element, synced with hidden select */}
        <SegTabs
          tabs={levelTabs}
          active={levelFilter}
          onChange={(key) => setLevelFilter(key as LevelFilter)}
        />

        {/* Interview language label + native select — test-id accessible */}
        <label
          htmlFor="interview-language"
          className="text-[12.5px] font-medium text-[#888b91] shrink-0"
        >
          {t('jobs.interviewLanguage')}
        </label>
        <select
          id="interview-language"
          value={selectedLanguage}
          onChange={(e) => setSelectedLanguage(e.target.value as Language)}
          className={cn(
            'h-10 rounded-[9px] border border-white/[0.08] bg-[rgba(28,29,31,0.7)]',
            'px-3 py-2 text-[13px] text-white',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-black',
            'disabled:cursor-not-allowed disabled:opacity-50',
          )}
        >
          {LANGUAGE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>

        {/* Level filter — native select (sr-only label); SegTabs above is the visible UI.
            This element MUST stay in the DOM — tests use #level-filter selectOptions() */}
        <label htmlFor="level-filter" className="sr-only">
          {t('jobs.filterByLevel')}
        </label>
        <select
          id="level-filter"
          value={levelFilter}
          onChange={(e) => setLevelFilter(e.target.value as LevelFilter)}
          className={cn(
            'h-10 rounded-[9px] border border-white/[0.08] bg-[rgba(28,29,31,0.7)]',
            'px-3 py-2 text-[13px] text-white',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-black',
            'disabled:cursor-not-allowed disabled:opacity-50',
          )}
        >
          {LEVEL_OPTIONS.map((opt) => (
            <option key={`level-${opt.value}`} value={opt.value}>
              {t(opt.labelKey)}
            </option>
          ))}
        </select>

        {/* Count */}
        <p className="ml-auto text-[12.5px] text-[#70757c] whitespace-nowrap">
          {t('jobs.positionsCount', { count: jobs.length })}
          {(levelFilter || searchQuery) && (
            <span className="ml-1 text-[11.5px] text-[#5a5f66]">
              {t('jobs.filteredFrom', { count: allJobs.length })}
            </span>
          )}
        </p>
      </div>

      {/* ── Empty state — no jobs at all ─────────────────────────────────────── */}
      {jobs.length === 0 && allJobs.length === 0 && (
        <GlassCard className="mt-5 flex flex-col items-center justify-center py-20 px-6 text-center gap-4">
          <div className="inline-flex h-14 w-14 items-center justify-center rounded-full bg-[rgba(var(--accent-rgb),0.1)] border border-[rgba(var(--accent-rgb),0.2)]">
            <Briefcase className="h-7 w-7 text-[#60a5fa]" aria-hidden="true" />
          </div>
          <div>
            <p className="text-[16px] font-semibold text-white">
              {t('jobs.noPositionsTitle')}
            </p>
            <p className="mt-1 text-[14px] text-[#888b91]">
              {t('jobs.noPositionsEmpty')}
            </p>
          </div>
        </GlassCard>
      )}

      {/* ── Empty state — filters returned no results ─────────────────────────── */}
      {jobs.length === 0 && allJobs.length > 0 && (
        <GlassCard className="mt-5 flex flex-col items-center justify-center py-20 px-6 text-center gap-4">
          <div className="inline-flex h-14 w-14 items-center justify-center rounded-full bg-[rgba(var(--accent-rgb),0.1)] border border-[rgba(var(--accent-rgb),0.2)]">
            <Briefcase className="h-7 w-7 text-[#60a5fa]" aria-hidden="true" />
          </div>
          <div>
            <p className="text-[16px] font-semibold text-white">
              {t('jobs.noPositionsTitle')}
            </p>
            <p className="mt-1 text-[14px] text-[#888b91]">
              {t('jobs.noPositionsFiltered')}
            </p>
          </div>
          <button
            type="button"
            onClick={() => {
              setLevelFilter('');
              setSearchQuery('');
            }}
            className="inline-flex items-center gap-1.5 rounded-[9999px] border border-white/10 bg-transparent px-4 py-2 text-[13px] font-medium text-white hover:bg-white/[0.06] hover:border-white/20 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            {t('jobs.clearFilter')}
          </button>
        </GlassCard>
      )}

      {/* ── Job grid ─────────────────────────────────────────────────────────── */}
      {jobs.length > 0 && (
        <Stagger
          className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-2"
        >
          {jobs.map((job) => (
            <StaggerItem key={job.id}>
              <JobCard
                job={job}
                onStartInterview={handleStartInterview}
                isStarting={
                  startInterviewMutation.isPending &&
                  startInterviewMutation.variables?.jobId === job.id
                }
              />
            </StaggerItem>
          ))}
        </Stagger>
      )}

      {/* Consent gate modal — overlays everything */}
      {showConsent && (
        <ConsentModal
          onAgree={handleAgree}
          onDecline={handleDecline}
          isSubmitting={isSubmittingConsent}
          error={consentError}
        />
      )}
    </div>
  );
}
