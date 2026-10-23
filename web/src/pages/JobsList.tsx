// JobsList — protected page inside AppShell; fetches GET /jobs; renders a grid
// of job cards. "Start Interview" is gated by DPDP Act 2023 consent (S3-011).
// Language picker persisted to localStorage ('intants:interview-language').
// The page title is rendered here (PageHeader); AppShell supplies only the top bar.

import { useState, useCallback, useEffect, useRef } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { useAuth } from '@/context/AuthContext';
import { readPref, writePref } from '@/lib/localPref';
import { useCandidateError } from '@/lib/candidateCopy';
import { useConsent } from '@/context/ConsentContext';
import { getJobs } from '@/api/jobs';
import { createSession } from '@/api/sessions';
import JobCard from '@/components/JobCard';
import OpenRoles from '@/components/OpenRoles';
import ConsentModal from '@/components/ConsentModal';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Input } from '@/components/ui/input';
import { SelectControl } from '@/components/ui/select-control';
import { Page, PageHeader, EmptyState } from '@/design/components/page';
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

type LevelFilter = '' | 'entry' | 'mid' | 'senior';

// Level filter pills — labels resolved with t() at render time.
const LEVEL_FILTERS: { key: LevelFilter; labelKey: string }[] = [
  { key: '', labelKey: 'jobs.allLevels' },
  { key: 'entry', labelKey: 'jobs.levelEntry' },
  { key: 'mid', labelKey: 'jobs.levelMid' },
  { key: 'senior', labelKey: 'jobs.levelSenior' },
];

// ── Loading skeleton card ──────────────────────────────────────────────────────

function JobCardSkeleton() {
  return (
    <div
      className="flex flex-col gap-4 rounded-card border border-border bg-card p-5 shadow-card"
      aria-hidden="true"
    >
      <Skeleton className="h-5 w-3/4" />
      <div className="flex gap-1.5">
        <Skeleton className="h-6 w-20 rounded-full" />
        <Skeleton className="h-6 w-16 rounded-full" />
      </div>
      <div className="space-y-2">
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-5/6" />
      </div>
      <Skeleton className="h-10 w-36 rounded-full" />
    </div>
  );
}

// ── JobsList page ──────────────────────────────────────────────────────────────

export default function JobsList() {
  const { t } = useTranslation();
  const { accessToken } = useAuth();
  const navigate = useNavigate();
  const { consented, loading: consentLoading, recordConsent } = useConsent();
  const errorText = useCandidateError();
  const [searchParams] = useSearchParams();

  // Language picker — persisted to localStorage
  const [selectedLanguage, setSelectedLanguage] = useState<Language>(() => {
    const stored = readPref(LANGUAGE_STORAGE_KEY);
    if (stored === 'en' || stored === 'hi' || stored === 'te') return stored;
    return 'en';
  });

  useEffect(() => {
    writePref(LANGUAGE_STORAGE_KEY, selectedLanguage);
  }, [selectedLanguage]);

  // Level filter (client-side only)
  const [levelFilter, setLevelFilter] = useState<LevelFilter>('');

  // Design search box — additive presentation only (no backend; filters by title)
  // Seeded from ?q=, which the top-bar search sends (audit CA-M5), and kept in
  // step when that search is used again while already on this page.
  const qParam = searchParams.get('q') ?? '';
  const [searchQuery, setSearchQuery] = useState(qParam);
  useEffect(() => {
    setSearchQuery(qParam);
  }, [qParam]);

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
  // The start error is shown above the list; move focus there so it is seen
  // from whichever card was pressed (audit CA-L4).
  const startErrorRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (startInterviewMutation.isError) startErrorRef.current?.focus();
  }, [startInterviewMutation.isError]);

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
      // One session at a time: a second card must not start a second one.
      if (startInterviewMutation.isPending) return;
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
    [consented, proceedToSession, startInterviewMutation.isPending],
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
      setConsentError(errorText(err, t('jobs.consentError')));
    } finally {
      setIsSubmittingConsent(false);
    }
  }, [recordConsent, pendingJobId, proceedToSession, t, errorText]);

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

  // ── Loading state ───────────────────────────────────────────────────────────

  if (isLoading || consentLoading) {
    return (
      <Page width="default" data-testid="jobs-loading">
        <div aria-busy="true" className="flex flex-col gap-6">
        <div>
          <Skeleton className="mb-2 h-8 w-48" />
          <Skeleton className="h-4 w-72 max-w-full" />
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Skeleton className="h-10 w-[260px] max-w-full rounded-full" />
          <Skeleton className="h-10 w-52 rounded-full" />
          <Skeleton className="h-10 w-44 rounded-[12px]" />
        </div>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <JobCardSkeleton key={i} />
          ))}
        </div>
        </div>
      </Page>
    );
  }

  // ── Error state ─────────────────────────────────────────────────────────────

  if (isError) {
    return (
      <Page width="default">
        <div
          role="alert"
          className="flex flex-col items-start gap-4 rounded-card border border-[var(--ui-danger)]/30 bg-[var(--ui-danger-wash)] p-6 sm:flex-row sm:items-center"
        >
          <AlertTriangle className="h-5 w-5 shrink-0 text-[var(--ui-danger)]" aria-hidden="true" />
          <p className="flex-1 text-[14px] text-foreground">
            {errorText(error, t('jobs.loadError'))}
          </p>
          <Button type="button" variant="outline" size="sm" onClick={() => void refetch()}>
            <RefreshCw aria-hidden="true" />
            {t('jobs.retry')}
          </Button>
        </div>
      </Page>
    );
  }

  // ── Main render ─────────────────────────────────────────────────────────────

  return (
    <Page width="default">
      <PageHeader title={t('jobs.pageTitle')} description={t('jobs.pageSubtitle')} />

      {/* Real openings — above the practice catalogue, because a candidate
          arriving here is far more likely to be looking for a job than for a
          rehearsal, and because the section below is mock interviews, which
          the copy has to keep separate. Renders nothing when nobody is hiring. */}
      <OpenRoles />

      {/* Decline banner */}
      {showDeclineBanner && (
        <div
          role="alert"
          className="flex items-start justify-between gap-4 rounded-[14px] bg-[var(--ui-warn-wash)] px-4 py-3"
        >
          <p className="text-[14px] text-foreground">{t('jobs.declineBanner')}</p>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={() => setShowDeclineBanner(false)}
            aria-label={t('jobs.dismiss')}
            className="h-8 w-8 shrink-0 text-muted-foreground hover:text-foreground"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
      )}

      {/* Session-start error banner */}
      {startInterviewMutation.isError && (
        <div
          ref={startErrorRef}
          role="alert"
          tabIndex={-1}
          data-testid="start-error"
          className="rounded-[14px] bg-[var(--ui-danger-wash)] px-4 py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          <p className="text-[14px] text-foreground">
            {errorText(startInterviewMutation.error, t('jobs.startError'))}
          </p>
        </div>
      )}

      <section aria-labelledby="practice-heading">
        <h2 id="practice-heading" className="mb-4 text-[20px] font-bold tracking-[-0.3px] text-foreground">
          {t('jobs.practiceTitle')}
        </h2>

        {/* Toolbar: search, level pills, interview language, count. The language
            control is the shared SelectControl, which is still a native <select>
            with the shared styling: the most reliable control on a phone, and it
            keeps the label that tests and screen readers rely on. */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative w-full sm:w-[280px]">
            <Search
              size={16}
              className="pointer-events-none absolute start-4 top-1/2 -translate-y-1/2 text-[var(--ui-soft)]"
              aria-hidden="true"
            />
            <Input
              type="search"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t('jobs.searchPlaceholder')}
              aria-label={t('jobs.searchPlaceholder')}
              className="rounded-full ps-10 pe-4"
            />
          </div>

          <div role="group" aria-label={t('jobs.filterByLevel')} className="flex flex-wrap gap-2">
            {LEVEL_FILTERS.map((opt) => {
              const on = levelFilter === opt.key;
              return (
                <button
                  key={opt.key || 'all'}
                  type="button"
                  aria-pressed={on}
                  onClick={() => setLevelFilter(opt.key)}
                  className={cn(
                    'h-10 rounded-full border px-4 text-[13.5px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
                    on
                      ? 'border-primary bg-highlight text-highlight-foreground'
                      : 'border-input bg-card text-foreground hover:border-[var(--ui-line-strong)] hover:bg-[var(--ui-inset-soft)]',
                  )}
                >
                  {t(opt.labelKey)}
                </button>
              );
            })}
          </div>

          <div className="flex items-center gap-2">
            <label
              htmlFor="interview-language"
              className="shrink-0 text-[13px] font-semibold text-foreground"
            >
              {t('jobs.interviewLanguage')}
            </label>
            <SelectControl
              id="interview-language"
              value={selectedLanguage}
              onChange={(e) => setSelectedLanguage(e.target.value as Language)}
              className="w-auto"
            >
              {LANGUAGE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </SelectControl>
          </div>

          <p className="text-[13px] text-muted-foreground sm:ml-auto" aria-live="polite">
            {t('jobs.positionsCount', { count: jobs.length })}
            {(levelFilter || searchQuery) && (
              <span className="ml-1">{t('jobs.filteredFrom', { count: allJobs.length })}</span>
            )}
          </p>
        </div>

        {/* Empty state — no jobs at all */}
        {jobs.length === 0 && allJobs.length === 0 && (
          <EmptyState
            size="page"
            className="mt-5"
            icon={Briefcase}
            title={t('jobs.noPositionsTitle')}
            description={t('jobs.noPositionsEmpty')}
          />
        )}

        {/* Empty state — filters returned no results */}
        {jobs.length === 0 && allJobs.length > 0 && (
          <EmptyState
            size="page"
            className="mt-5"
            icon={Briefcase}
            title={t('jobs.noPositionsTitle')}
            description={t('jobs.noPositionsFiltered')}
            action={
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setLevelFilter('');
                  setSearchQuery('');
                }}
              >
                {t('jobs.clearFilter')}
              </Button>
            }
          />
        )}

        {/* Job grid */}
        {jobs.length > 0 && (
          <div className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-2">
            {jobs.map((job) => (
              <JobCard
                key={job.id}
                job={job}
                onStartInterview={handleStartInterview}
                isStarting={
                  startInterviewMutation.isPending &&
                  startInterviewMutation.variables?.jobId === job.id
                }
                disabled={startInterviewMutation.isPending || isSubmittingConsent}
              />
            ))}
          </div>
        )}
      </section>

      {/* Consent gate modal — overlays everything */}
      {showConsent && (
        <ConsentModal
          onAgree={handleAgree}
          onDecline={handleDecline}
          isSubmitting={isSubmittingConsent}
          error={consentError}
        />
      )}
    </Page>
  );
}
