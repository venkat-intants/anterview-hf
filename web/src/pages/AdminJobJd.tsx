// AdminJobJd — admin page: JD PDF upload for the platform-wide PRACTICE job catalogue.
// Route: /admin/jd (gated by AdminRoute).
//
// Layout: reproduced from design screen (AdminJobJd.tsx).
//   • Page header (title + New role button)
//   • Functional upload card (live: getJobs + uploadJd + FileUploadZone)
//   (The static design-mock "JD library" grid was removed — QA ANTHIRE-011: it showed
//   made-up roles as Published on a live admin page.)
//
// Behavior: 100% live — getJobs (data_gateway) + accessToken gating;
//   job picker SelectControl; FileUploadZone + uploadJd (PDF/10MB/progress/char-count toast);
//   jobs loading/error+Retry/empty; uploadKey reset on job change.

import { useCallback, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Briefcase, Info } from '@/design/components/icons';
import { useAuth } from '@/context/AuthContext';
import { getAllJobs } from '@/api/jobs';
import { uploadJd } from '@/api/jd';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import FileUploadZone from '@/components/FileUploadZone';
import { Skeleton } from '@/components/ui/skeleton';
import { QueryErrorPanel } from '@/components/QueryState';
import { SelectControl } from '@/components/ui/select-control';
import { fieldLabelClass } from '@/components/ui/control-styles';
import { GlassCard } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { EmptyState, Page, PageHeader } from '@/design/components/page';

// ── Constants ──────────────────────────────────────────────────────────────────

const JD_MAX_BYTES = 10 * 1024 * 1024; // 10 MB

// ── Component ──────────────────────────────────────────────────────────────────

export default function AdminJobJd() {
  const { accessToken } = useAuth();
  const [selectedJobId, setSelectedJobId] = useState<string>('');
  const [uploadKey, setUploadKey] = useState(0);

  // ── Jobs query ──────────────────────────────────────────────────────────────

  const {
    data: jobsData,
    isLoading: jobsLoading,
    isError: jobsError,
    error: jobsFetchError,
    refetch: refetchJobs,
    isFetching: jobsFetching,
  } = useQuery({
    // Its own key: every page of the catalogue, not the first page of 20 that
    // the candidate job list caches under ['jobs'] (AD-F10).
    queryKey: ['jobs', 'all-practice-roles'],
    queryFn: () => getAllJobs(),
    enabled: accessToken !== null,
    staleTime: 60_000,
    retry: 1,
  });

  const jobs = jobsData ?? [];

  // ── Handlers ────────────────────────────────────────────────────────────────

  const handleJobChange = useCallback((value: string) => {
    if (!value) return;
    setSelectedJobId(value);
    setUploadKey((k) => k + 1);
  }, []);

  const handleJdUpload = useCallback(
    (file: File, onProgress: (pct: number) => void) => {
      if (!accessToken) return Promise.reject(new Error('No access token'));
      if (!selectedJobId) return Promise.reject(new Error('Select a practice role first.'));
      return uploadJd(selectedJobId, file, accessToken, onProgress).then((result) => {
        toast.success(
          `Job description saved - ${result.text_length.toLocaleString('en-IN')} characters read`,
        );
        return { text_length: result.text_length };
      });
    },
    [accessToken, selectedJobId],
  );

  const selectedJobTitle = jobs.find((j) => j.id === selectedJobId)?.title;

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <Page width="default">
      {/* ── Page header ──────────────────────────────────────────────────── */}
      <PageHeader
        title="Practice JDs"
        description="Attach a job description to a role in the platform-wide practice catalogue that candidates use for practice interviews. A company's own openings are not managed here - each company's HR adds the job description on the opening itself (Openings → the opening → Opening and job description)."
      />

      {/* ══════════════════════════════════════════════════════════════════════
          SECTION 1 — FUNCTIONAL: JD PDF upload
          This is the page's core purpose. Live backend: data_gateway.
      ══════════════════════════════════════════════════════════════════════ */}
      <Reveal delay={0.05}>
        <GlassCard className="p-6 space-y-6">
          {/* Section heading */}
          <div className="flex items-center gap-3">
            <span
              className="flex h-10 w-10 flex-none items-center justify-center rounded-[12px] bg-[var(--ui-info-wash)] text-[var(--ui-info)]"
              aria-hidden="true"
            >
              <Briefcase size={20} />
            </span>
            <div>
              <h2 className="text-[17px] font-semibold text-foreground">
                Upload a job description
              </h2>
              <p className="text-[13px] text-muted-foreground">
                PDF only - maximum 10 MB. The document will be parsed and indexed for AI question
                generation.
              </p>
            </div>
          </div>

          <div className="h-px bg-[var(--ui-inset)]" role="separator" aria-hidden="true" />

          {/* ── Job picker ──────────────────────────────────────────────── */}
          <div className="space-y-2">
            {/* "Practice role", not "job posting" (AD-F25): these are catalogue
                roles for practice interviews, not a company's openings. */}
            <label htmlFor="job-select" className={cn('block', fieldLabelClass)}>
              Practice role
            </label>

            {/* Loading state */}
            {jobsLoading && (
              <div role="status" aria-busy="true">
                <span className="sr-only">Loading practice roles…</span>
                <Skeleton className="h-10 w-full" />
              </div>
            )}

            {/* Error state */}
            {jobsError && (
              <QueryErrorPanel
                title="Could not load the practice roles."
                error={jobsFetchError}
                onRetry={() => void refetchJobs()}
                retrying={jobsFetching}
              />
            )}

            {/* An empty catalogue says so, rather than an empty select (AD-F25). */}
            {!jobsLoading && !jobsError && jobs.length === 0 && (
              <EmptyState
                icon={Briefcase}
                title="The practice catalogue has no roles yet."
                description="Add a practice role first, then attach its job description here."
              />
            )}

            {/* Select — shown when data ready */}
            {!jobsLoading && !jobsError && jobs.length > 0 && (
              <SelectControl
                id="job-select"
                aria-label="Select a practice role"
                value={selectedJobId}
                onChange={(e) => handleJobChange(e.target.value)}
              >
                {/* The prompt, until a role is picked; it cannot be picked back. */}
                <option value="" disabled>
                  Select a practice role
                </option>
                {jobs.map((job) => (
                  <option key={job.id} value={job.id}>
                    {job.title}
                  </option>
                ))}
              </SelectControl>
            )}
          </div>

          {/* ── Upload zone / placeholder ────────────────────────────────── */}
          {selectedJobId ? (
            <div className="space-y-3">
              <p className="text-[13px] text-muted-foreground">
                Job description for{' '}
                <span className="font-medium text-foreground">{selectedJobTitle}</span>
              </p>
              {/* The upload overwrites the role's stored document (AD-F25). */}
              <p className="flex items-start gap-1.5 text-[12.5px] text-muted-foreground">
                <Info
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--ui-info)]"
                  aria-hidden="true"
                />
                Uploading replaces any job description this role already has.
              </p>
              <FileUploadZone
                key={uploadKey}
                label="Job description"
                accept="application/pdf"
                maxBytes={JD_MAX_BYTES}
                onUpload={handleJdUpload}
              />
            </div>
          ) : (
            <div
              className={cn(
                'rounded-[16px] border-2 border-dashed border-border',
                'bg-[var(--ui-inset-soft)] p-10 text-center',
              )}
            >
              <Briefcase
                className="mx-auto h-10 w-10 text-[var(--ui-faint)] mb-3"
                aria-hidden="true"
              />
              <p className="text-[13px] text-[var(--ui-faint)]">
                Select a practice role above to upload its job description.
              </p>
            </div>
          )}
        </GlassCard>
      </Reveal>
    </Page>
  );
}
