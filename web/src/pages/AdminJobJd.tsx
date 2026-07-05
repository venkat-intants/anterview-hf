// AdminJobJd — admin page: JD PDF upload (functional) + JD library (presentation only).
// Route: /admin/jd (gated by AdminRoute).
//
// Layout: reproduced from design screen (AdminJobJd.tsx).
//   • Page header (title + New role Pill)
//   • Functional upload card (live: getJobs + uploadJd + FileUploadZone)
//   • JD library card grid (design layout: zoom reveal, 2-col grid)
//
// Behavior: 100% live — getJobs (data_gateway) + accessToken gating;
//   job picker Select; FileUploadZone + uploadJd (PDF/10MB/progress/char-count toast);
//   jobs loading/error+Retry/empty; uploadKey reset on job change.

import { useCallback, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertCircle, Briefcase, Pencil } from '@/design/components/icons';
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
import {
  GlassCard,
  Pill,
  StatusTag,
} from '@/design/components/primitives';
import { Reveal, Stagger, StaggerItem } from '@/design/components/Reveal';
import { Plus } from '@/design/components/icons';
import { JOB_JDS } from '@/design/data/admin';

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
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-[28px] font-semibold tracking-[-1px] text-white">
            Jobs &amp; JD library
          </h1>
          <p className="mt-1 text-[14px] text-[#888b91]">
            Define roles and the competencies each interview scores.
          </p>
        </div>
        {/* New role — presentation only (no backend endpoint) */}
        <Pill
          className="px-5 py-2.5 opacity-50 cursor-not-allowed"
          disabled
          aria-label="New role — coming soon"
          title="Coming soon"
        >
          <Plus size={16} aria-hidden="true" />
          New role
        </Pill>
      </div>

      {/* ══════════════════════════════════════════════════════════════════════
          SECTION 1 — FUNCTIONAL: JD PDF upload
          This is the page's core purpose. Live backend: data_gateway.
      ══════════════════════════════════════════════════════════════════════ */}
      <Reveal delay={0.05}>
        <GlassCard className="p-6 space-y-6">
          {/* Section heading */}
          <div className="flex items-center gap-3">
            <span
              className="flex h-10 w-10 flex-none items-center justify-center rounded-[12px] bg-[rgba(var(--accent-rgb),0.12)] text-[#60a5fa]"
              aria-hidden="true"
            >
              <Briefcase size={20} />
            </span>
            <div>
              <h2 className="text-[17px] font-semibold text-white">Upload Job Description</h2>
              <p className="text-[13px] text-[#888b91]">
                PDF only — maximum 10 MB. The document will be parsed and indexed for AI
                question generation.
              </p>
            </div>
          </div>

          <div
            className="h-px bg-white/[0.06]"
            role="separator"
            aria-hidden="true"
          />

          {/* ── Job picker ──────────────────────────────────────────────── */}
          <div className="space-y-2">
            <label
              htmlFor="job-select"
              className="text-[12.5px] font-medium text-[#b8babf]"
            >
              Job posting
            </label>

            {/* Loading state */}
            {jobsLoading && (
              <div
                className="flex items-center gap-2 text-[13px] text-[#888b91]"
                aria-live="polite"
              >
                <div
                  className="h-4 w-4 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent"
                  role="status"
                  aria-label="Loading jobs"
                />
                <span>Loading jobs…</span>
                <Skeleton className="h-10 w-full rounded-[12px] mt-1 bg-white/[0.05]" />
              </div>
            )}

            {/* Error state */}
            {jobsError && (
              <div
                role="alert"
                className="rounded-[16px] border border-[rgba(230,113,79,0.3)] bg-[rgba(230,113,79,0.08)] p-4 flex items-start gap-3"
              >
                <AlertCircle
                  className="h-5 w-5 text-[#e6714f] shrink-0 mt-0.5"
                  aria-hidden="true"
                />
                <p className="text-[13px] text-[#e6714f] flex-1">
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
                    'w-full rounded-[12px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)]',
                    'text-[14px] text-white placeholder:text-[#5a5f66]',
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
              <p className="text-[13px] text-[#888b91]">
                JD document for{' '}
                <span className="font-medium text-white">{selectedJobTitle}</span>
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
                'rounded-[16px] border-2 border-dashed border-white/[0.08]',
                'bg-white/[0.02] p-10 text-center',
              )}
            >
              <Briefcase
                className="mx-auto h-10 w-10 text-[#5a5f66] mb-3"
                aria-hidden="true"
              />
              <p className="text-[13px] text-[#70757c]">
                Select a job above to enable JD upload
              </p>
            </div>
          )}
        </GlassCard>
      </Reveal>

      {/* ══════════════════════════════════════════════════════════════════════
          SECTION 2 — PRESENTATION ONLY: JD library card grid
          Design layout: zoom reveal, 2-col grid.
          No backend endpoints — rendered as static chrome only.
      ══════════════════════════════════════════════════════════════════════ */}
      <Stagger className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2">
        {JOB_JDS.map((j) => (
          <StaggerItem key={j.id}>
            <Reveal dir="zoom">
              <GlassCard hover className="p-5">
                <div className="flex items-start gap-3">
                  <span
                    className="flex h-11 w-11 flex-none items-center justify-center rounded-[12px] bg-[rgba(var(--accent-rgb),0.12)] text-[#60a5fa]"
                    aria-hidden="true"
                  >
                    <Briefcase size={20} aria-hidden="true" />
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-2">
                      <h3 className="text-[16px] font-semibold text-white">
                        {j.title} · {j.level}
                      </h3>
                      <StatusTag
                        tone={j.status === 'Published' ? 'forest' : 'neutral'}
                        dot={j.status === 'Published'}
                        className="shrink-0"
                      >
                        {j.status}
                      </StatusTag>
                    </div>
                    <div className="mt-1 text-[12px] text-[#70757c]">
                      Updated {j.updated}
                    </div>
                  </div>
                </div>

                {/* Competency chips */}
                <div className="mt-4 flex flex-wrap gap-1.5">
                  {j.competencies.map((c) => (
                    <span
                      key={c}
                      className="rounded-pill border border-white/[0.08] bg-white/[0.03] px-2.5 py-1 text-[11.5px] text-[#b8babf]"
                    >
                      {c}
                    </span>
                  ))}
                </div>

                {/* Actions — presentation only, no handlers */}
                <div className="mt-5 flex gap-2 border-t border-white/[0.06] pt-4">
                  <Pill
                    variant="ghost"
                    className="flex-1 py-2 text-[12.5px] opacity-50 cursor-not-allowed"
                    disabled
                    aria-label={`Edit JD for ${j.title} — coming soon`}
                    title="Coming soon"
                  >
                    <Pencil size={14} aria-hidden="true" />
                    Edit JD
                  </Pill>
                  <Pill
                    variant="accent"
                    className="flex-1 py-2 text-[12.5px] opacity-50 cursor-not-allowed"
                    disabled
                    aria-label={`Map exam for ${j.title} — coming soon`}
                    title="Coming soon"
                  >
                    Map exam
                  </Pill>
                </div>
              </GlassCard>
            </Reveal>
          </StaggerItem>
        ))}
      </Stagger>
    </div>
  );
}
