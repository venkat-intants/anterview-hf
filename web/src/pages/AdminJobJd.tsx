// AdminJobJd — admin page: JD PDF upload for the platform-wide PRACTICE job catalogue.
// Route: /admin/jd (gated by AdminRoute).
//
// Layout: reproduced from design screen (AdminJobJd.tsx).
//   • Page header (title + New role Pill)
//   • Functional upload card (live: getJobs + uploadJd + FileUploadZone)
//   (The static design-mock "JD library" grid was removed — QA ANTHIRE-011: it showed
//   made-up roles as Published on a live admin page.)
//
// Behavior: 100% live — getJobs (data_gateway) + accessToken gating;
//   job picker Select; FileUploadZone + uploadJd (PDF/10MB/progress/char-count toast);
//   jobs loading/error+Retry/empty; uploadKey reset on job change.

import { useCallback, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertCircle, Briefcase } from '@/design/components/icons';
import { useAuth } from '@/context/AuthContext';
import { getJobs } from '@/api/jobs';
import { uploadJd } from '@/api/jd';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import FileUploadZone from '@/components/FileUploadZone';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { GlassCard } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { PageHeader } from '@/design/components/page';

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
  } = useQuery({
    queryKey: ['jobs'],
    queryFn: () => {
      if (!accessToken) throw new Error('No access token');
      return getJobs(accessToken);
    },
    enabled: accessToken !== null,
    staleTime: 60_000,
    retry: 1,
  });

  const jobs = jobsData?.items ?? [];

  // ── Handlers ────────────────────────────────────────────────────────────────

  const handleJobChange = useCallback((value: string) => {
    setSelectedJobId(value);
    setUploadKey((k) => k + 1);
  }, []);

  const handleJdUpload = useCallback(
    (file: File, onProgress: (pct: number) => void) => {
      if (!accessToken) return Promise.reject(new Error('No access token'));
      if (!selectedJobId) return Promise.reject(new Error('Select a job first'));
      return uploadJd(selectedJobId, file, accessToken, onProgress).then((result) => {
        toast.success(
          `JD processed — ${result.text_length.toLocaleString()} characters extracted.`,
        );
        return { text_length: result.text_length };
      });
    },
    [accessToken, selectedJobId],
  );

  const selectedJobTitle = jobs.find((j) => j.id === selectedJobId)?.title;

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className="mx-auto max-w-[1080px] px-0 py-2 space-y-6">
      {/* ── Page header ──────────────────────────────────────────────────── */}
      <PageHeader
        className="mb-0"
        title="Practice-interview job descriptions"
        description="Attach a job description to a role in the platform-wide practice catalogue that candidates use for practice interviews. A company's own openings are not managed here — each company's HR adds the job description on the opening itself (Openings → the opening → Role and posting)."
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
              <h2 className="text-[17px] font-semibold text-foreground">Upload Job Description</h2>
              <p className="text-[13px] text-muted-foreground">
                PDF only — maximum 10 MB. The document will be parsed and indexed for AI question
                generation.
              </p>
            </div>
          </div>

          <div className="h-px bg-[var(--ui-inset)]" role="separator" aria-hidden="true" />

          {/* ── Job picker ──────────────────────────────────────────────── */}
          <div className="space-y-2">
            <label htmlFor="job-select" className="text-[12.5px] font-medium text-[var(--ui-soft)]">
              Job posting
            </label>

            {/* Loading state */}
            {jobsLoading && (
              <div
                className="flex items-center gap-2 text-[13px] text-muted-foreground"
                aria-live="polite"
              >
                <div
                  className="h-4 w-4 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent"
                  role="status"
                  aria-label="Loading jobs"
                />
                <span>Loading jobs…</span>
                <Skeleton className="h-10 w-full rounded-[12px] mt-1 bg-[var(--ui-inset)]" />
              </div>
            )}

            {/* Error state */}
            {jobsError && (
              <div
                role="alert"
                className="rounded-[16px] border border-[var(--ui-danger)]/35 bg-[var(--ui-danger-wash)] p-4 flex items-start gap-3"
              >
                <AlertCircle
                  className="h-5 w-5 text-[var(--ui-danger)] shrink-0 mt-0.5"
                  aria-hidden="true"
                />
                <p className="text-[13px] text-[var(--ui-danger)] flex-1">
                  {jobsFetchError instanceof Error
                    ? jobsFetchError.message
                    : 'Failed to load jobs.'}
                </p>
                <Button
                  type="button"
                  variant="destructive"
                  size="sm"
                  onClick={() => void refetchJobs()}
                  className="shrink-0"
                >
                  Retry
                </Button>
              </div>
            )}

            {/* Select — shown when data ready */}
            {!jobsLoading && !jobsError && (
              <Select value={selectedJobId} onValueChange={handleJobChange}>
                <SelectTrigger
                  id="job-select"
                  aria-label="Select job posting"
                  className={cn(
                    'w-full rounded-[12px] border border-border bg-secondary',
                    'text-[14px] text-foreground placeholder:text-[var(--ui-faint)]',
                    'focus:border-[var(--accent)] focus:ring-0',
                  )}
                >
                  <SelectValue placeholder="— select a job —" />
                </SelectTrigger>
                <SelectContent>
                  {jobs.map((job) => (
                    <SelectItem key={job.id} value={job.id}>
                      {job.title}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>

          {/* ── Upload zone / placeholder ────────────────────────────────── */}
          {selectedJobId ? (
            <div className="space-y-3">
              <p className="text-[13px] text-muted-foreground">
                JD document for{' '}
                <span className="font-medium text-foreground">{selectedJobTitle}</span>
              </p>
              <FileUploadZone
                key={uploadKey}
                label="Job Description"
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
                Select a job above to enable JD upload
              </p>
            </div>
          )}
        </GlassCard>
      </Reveal>

    </div>
  );
}
