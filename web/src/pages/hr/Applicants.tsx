// Applicants — HR resume screening dashboard.
// Layout: design screen Applicants.tsx (GlassCard table, SegTabs, Avatar, StatusTag, Pill).
// Behavior: all live logic — listApplicants query, bulk PDF upload (multi/de-dupe/cap 25),
//           progress bar, failure list, shortlist/reject/rescore mutations,
//           real ats_breakdown/strengths/concerns in the detail drawer.

import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Upload,
  FileText,
  X,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  RefreshCw,
  Search,
  Star,
  ArrowRight,
} from '@/design/components/icons';
import {
  listApplicants,
  bulkUploadApplicants,
  updateApplicantStatus,
  rescoreApplicant,
  whyMatch,
  getReindexStatus,
  reindexApplicants,
  type Applicant,
  type ApplicantStatus,
  type BulkUploadResult,
} from '@/api/applicants';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import {
  GlassCard,
  StatusTag,
  Avatar,
  SegTabs,
  Pill,
  type TagTone,
} from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import { staggerParent, staggerChild } from '@/design/lib/motion';
import { initialsOf, gradientFor, scoreColor } from '@/design/data/shared';

// ── Constants ────────────────────────────────────────────────────────────────

const MAX_BULK_FILES = 25;

const STATUS_FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'new', label: 'New' },
  { key: 'shortlisted', label: 'Shortlisted' },
  { key: 'rejected', label: 'Rejected' },
];

const STATUS_TONE: Record<ApplicantStatus, TagTone> = {
  new: 'neutral',
  shortlisted: 'forest',
  rejected: 'ember',
};

const STATUS_LABEL: Record<ApplicantStatus, string> = {
  new: 'New',
  shortlisted: 'Shortlisted',
  rejected: 'Rejected',
};

const BREAKDOWN_LABELS: Record<string, string> = {
  skills_match: 'Skills',
  experience_relevance: 'Experience',
  education_fit: 'Education',
  role_alignment: 'Role alignment',
};

// ── Helpers ──────────────────────────────────────────────────────────────────

type RecInfo = { label: string; tone: TagTone };

function recInfo(rec: string | null): RecInfo {
  switch (rec) {
    case 'strong_fit':
      return { label: 'Strong fit', tone: 'forest' };
    case 'moderate_fit':
      return { label: 'Moderate fit', tone: 'amber' };
    case 'weak_fit':
      return { label: 'Weak fit', tone: 'ember' };
    default:
      return { label: 'Unscored', tone: 'neutral' };
  }
}

function seedFrom(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (Math.imul(31, h) + name.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/** Debounce a fast-changing value (search box) so we don't query on every keystroke. */
function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}

/** Match-score colour: warm at high relevance, cool at low. */
function matchColor(score: number): string {
  if (score >= 70) return '#27c93f';
  if (score >= 45) return '#ffb764';
  return '#888b91';
}

// ── Input base class ─────────────────────────────────────────────────────────

const inputCls =
  'w-full rounded-[10px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-3 py-2 ' +
  'text-[14px] text-white placeholder:text-[#5a5f66] focus:outline-none ' +
  'focus:border-[var(--accent)] transition-colors';

// ── Slide-in drawer ───────────────────────────────────────────────────────────

interface DrawerProps {
  applicant: Applicant | null;
  onClose: () => void;
  onShortlist: (id: string) => void;
  onReject: (id: string) => void;
  onRescore: (id: string) => void;
  statusPending: boolean;
  rescorePending: boolean;
  /** Active search phrase — when set, the drawer shows match score + "why matched". */
  searchQuery: string;
}

function ApplicantDrawer({
  applicant: a,
  onClose,
  onShortlist,
  onReject,
  onRescore,
  statusPending,
  rescorePending,
  searchQuery,
}: DrawerProps) {
  // Lazy "why matched": fetched only when a candidate is open during a search,
  // so the LLM cost is paid per-look — not for every result on every keystroke.
  const showMatch = searchQuery.trim().length > 0;
  // Skip the LLM call for non-matches (match_score 0) — nothing to explain.
  const worthExplaining = a == null || a.match_score == null || a.match_score > 0;
  const { data: why, isLoading: whyLoading } = useQuery({
    queryKey: ['hr', 'why-match', a?.id, searchQuery.trim()],
    queryFn: () => whyMatch(a!.id, searchQuery.trim()),
    enabled: Boolean(a) && showMatch && worthExplaining,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  // Close on Escape (hook must run before any early return).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Lock background scroll while the panel is open (restored on close).
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  if (!a) return null;

  const seed = seedFrom(a.full_name);
  const atsDisplay = a.ats_overall ?? null;
  const rec = recInfo(a.ats_recommendation);

  return createPortal(
    <motion.div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-4 backdrop-blur-xl"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
      onClick={onClose}
    >
      <motion.div
        className="relative flex max-h-[90vh] w-full max-w-[1040px] flex-col overflow-hidden rounded-[24px] border border-white/10 bg-[#0a0b0d] shadow-[0_30px_90px_rgba(0,0,0,0.65)]"
        initial={{ opacity: 0, scale: 0.96, y: 12 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.97, y: 8 }}
        transition={{ type: 'spring', stiffness: 320, damping: 32, mass: 0.85 }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={`${a.full_name} applicant details`}
      >
        {/* Header — pinned */}
        <div className="flex shrink-0 items-center justify-between border-b border-white/[0.07] px-7 py-5">
          <span className="text-[12px] uppercase tracking-[1px] text-[#70757c]">Candidate</span>
          <button
            onClick={onClose}
            aria-label="Close"
            className="flex h-8 w-8 items-center justify-center rounded-[9px] border border-white/10 bg-white/[0.05] text-[#b8babf] hover:text-white transition-colors"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        {/* Body — sizes to content; scrolls only as a safety net on very short screens */}
        <div className="min-h-0 flex-1 overflow-y-auto px-7 py-6">
        {/* Wide landscape layout: two columns side by side */}
        <div className="grid gap-x-8 gap-y-6 md:grid-cols-2">
          {/* LEFT column — identity, score/status, role, summary */}
          <div className="space-y-5">
            {/* Identity */}
            <div className="flex items-center gap-4">
              <Avatar initials={initialsOf(a.full_name)} gradient={gradientFor(seed)} size={58} />
              <div className="min-w-0">
                <div className="text-[20px] font-semibold tracking-[-0.5px] text-white">{a.full_name}</div>
                <div className="truncate text-[13px] text-[#70757c]">{a.email ?? 'No email on file'}</div>
                {a.user_id && (
                  <Link
                    to={`/u/${a.user_id}`}
                    className="mt-1 inline-flex items-center gap-1 text-[12px] font-medium text-[#60a5fa] hover:underline"
                  >
                    View full profile <ArrowRight size={12} aria-hidden="true" />
                  </Link>
                )}
              </div>
            </div>

            {/* Score + status tiles */}
            <div className="grid grid-cols-2 gap-2.5">
              <div className="rounded-[12px] border border-white/[0.08] bg-[#0f0f10] p-4">
                <div className="text-[11px] uppercase tracking-[0.5px] text-[#70757c]">ATS score</div>
                <div
                  className="mt-1 text-[28px] font-semibold tracking-[-1px]"
                  style={{ color: atsDisplay !== null ? scoreColor(atsDisplay) : '#70757c' }}
                >
                  {atsDisplay ?? '—'}
                </div>
              </div>
              <div className="rounded-[12px] border border-white/[0.08] bg-[#0f0f10] p-4">
                <div className="text-[11px] uppercase tracking-[0.5px] text-[#70757c]">Status</div>
                <div className="mt-2">
                  <StatusTag tone={STATUS_TONE[a.status]} dot>
                    {STATUS_LABEL[a.status]}
                  </StatusTag>
                </div>
              </div>
            </div>

            {/* Role meta */}
            <div className="space-y-1.5 text-[12.5px] text-[#70757c]">
              <p>
                Role &middot;{' '}
                <span className="text-[#b8babf]">
                  {a.target_job_title} ({a.target_level})
                </span>
              </p>
              <div>
                <StatusTag tone={rec.tone} className="text-[11.5px]">
                  {rec.label}
                </StatusTag>
              </div>
            </div>

            {/* ATS summary */}
            {a.ats_summary && (
              <p className="text-[13px] leading-relaxed text-[#888b91]">{a.ats_summary}</p>
            )}

            {/* Why matched — only during a search */}
            {showMatch && (
              <div className="rounded-[12px] border border-[rgba(var(--accent-rgb),0.25)] bg-[rgba(var(--accent-rgb),0.06)] p-4">
                <div className="flex items-center justify-between">
                  <span className="flex items-center gap-1.5 text-[12px] font-semibold text-[#60a5fa]">
                    <Search size={13} aria-hidden="true" />
                    Why this matched
                  </span>
                  {a.match_score != null && (
                    <span
                      className="text-[13px] font-semibold"
                      style={{ color: matchColor(a.match_score) }}
                    >
                      {a.match_score}% match
                    </span>
                  )}
                </div>
                <p className="mt-2 text-[13px] leading-relaxed text-[#b8babf]">
                  {!worthExplaining
                    ? 'Low relevance to this search.'
                    : whyLoading
                      ? 'Analysing the resume against your search…'
                      : why?.reason || 'No explanation available.'}
                </p>
              </div>
            )}
          </div>

          {/* RIGHT column — score breakdown + strengths / concerns */}
          <div className="space-y-5">
            {/* ATS breakdown bars — REAL data, not fabricated competencies */}
            {a.ats_breakdown && Object.keys(a.ats_breakdown).length > 0 && (
              <div>
                <div className="text-[13px] font-semibold text-white">Score breakdown</div>
                <div className="mt-3 flex flex-col gap-3">
                  {Object.entries(a.ats_breakdown).map(([k, v]) => (
                    <div key={k}>
                      <div className="mb-1 flex justify-between text-[12.5px]">
                        <span className="text-[#b8babf]">{BREAKDOWN_LABELS[k] ?? k}</span>
                        <span className="font-mono text-[#888b91]">{v}</span>
                      </div>
                      <div className="h-1.5 rounded-full bg-white/[0.07]">
                        <div
                          className="h-full rounded-full bg-[linear-gradient(90deg,var(--accent),#a887dc)]"
                          style={{ width: `${Math.max(0, Math.min(100, v))}%` }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Strengths + Concerns */}
            {((a.ats_strengths && a.ats_strengths.length > 0) ||
              (a.ats_concerns && a.ats_concerns.length > 0)) && (
              <div className="grid gap-4">
                {a.ats_strengths && a.ats_strengths.length > 0 && (
                  <div>
                    <p className="text-[12.5px] font-semibold text-[#27c93f]">Strengths</p>
                    <ul className="mt-2 space-y-1">
                      {a.ats_strengths.map((s, i) => (
                        <li key={i} className="flex items-start gap-1.5 text-[12px] text-[#70757c]">
                          <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[#27c93f]" />
                          {s}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {a.ats_concerns && a.ats_concerns.length > 0 && (
                  <div>
                    <p className="text-[12.5px] font-semibold text-[#e6714f]">Concerns</p>
                    <ul className="mt-2 space-y-1">
                      {a.ats_concerns.map((c, i) => (
                        <li key={i} className="flex items-start gap-1.5 text-[12px] text-[#70757c]">
                          <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[#e6714f]" />
                          {c}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
        </div>

        {/* Actions — grouped on the right of the card */}
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-2.5 border-t border-white/[0.07] px-7 py-5">
          <Pill
            variant="outline"
            onClick={() => onRescore(a.id)}
            disabled={rescorePending}
            aria-label="Re-score"
            className="gap-1.5"
          >
            <RefreshCw
              size={14}
              className={cn(rescorePending && 'animate-spin')}
              aria-hidden="true"
            />
            Re-score
          </Pill>
          <Pill
            variant="ghost"
            onClick={() => onShortlist(a.id)}
            disabled={statusPending || a.status === 'shortlisted'}
            aria-label="Shortlist"
            className="gap-1.5"
          >
            <CheckCircle2 size={15} aria-hidden="true" />
            Shortlist
          </Pill>
          <Pill
            variant="danger"
            onClick={() => onReject(a.id)}
            disabled={statusPending || a.status === 'rejected'}
            aria-label="Reject"
            className="gap-1.5"
          >
            <XCircle size={15} aria-hidden="true" />
            Reject
          </Pill>
        </div>
      </motion.div>
    </motion.div>,
    document.body,
  );
}

// ── Table row ─────────────────────────────────────────────────────────────────

function ApplicantRow({
  a,
  onSelect,
}: {
  a: Applicant;
  onSelect: (applicant: Applicant) => void;
}) {
  const seed = seedFrom(a.full_name);
  const atsDisplay = a.ats_overall;
  const rec = recInfo(a.ats_recommendation);

  return (
    <button
      onClick={() => onSelect(a)}
      className="grid w-full grid-cols-[2fr_1.3fr_1fr_0.8fr_0.8fr_0.5fr] items-center gap-3 border-b border-white/[0.04] px-6 py-3.5 text-left transition-colors last:border-0 hover:bg-white/[0.03] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-inset"
      aria-label={`Open details for ${a.full_name}`}
    >
      {/* Candidate */}
      <div className="flex min-w-0 items-center gap-3">
        <Avatar initials={initialsOf(a.full_name)} gradient={gradientFor(seed)} size={36} />
        <div className="min-w-0">
          <div className="truncate text-[14px] font-medium text-white">{a.full_name}</div>
          <div className="truncate text-[12px] text-[#70757c]">{a.email ?? 'No email'}</div>
        </div>
      </div>

      {/* Role */}
      <div className="min-w-0">
        <div className="truncate text-[13.5px] text-[#b8babf]">{a.target_job_title}</div>
        <div className="text-[11.5px] text-[#70757c]">{a.target_level}</div>
      </div>

      {/* Status */}
      <div className="flex flex-col gap-1">
        <StatusTag tone={STATUS_TONE[a.status]} dot>
          {STATUS_LABEL[a.status]}
        </StatusTag>
        {a.ats_recommendation && (
          <StatusTag tone={rec.tone} className="text-[10.5px]">
            {rec.label}
          </StatusTag>
        )}
      </div>

      {/* ATS score */}
      <div
        className="text-[15px] font-semibold"
        style={{ color: atsDisplay !== null ? scoreColor(atsDisplay) : '#70757c' }}
      >
        {atsDisplay ?? '—'}
      </div>

      {/* Match score (during search) — else shortlisted badge */}
      <div>
        {a.match_score != null ? (
          <span
            className="inline-flex items-center gap-1 rounded-full bg-white/[0.06] px-2 py-0.5 text-[11.5px] font-semibold"
            style={{ color: matchColor(a.match_score) }}
            title="Relevance to your search"
          >
            {a.match_score}% match
          </span>
        ) : (
          a.status === 'shortlisted' && (
            <span className="inline-flex items-center gap-1 text-[11.5px] font-semibold text-[#27c93f]">
              <Star size={12} aria-hidden="true" />
              Shortlisted
            </span>
          )
        )}
      </div>

      {/* Arrow */}
      <div className="flex justify-end">
        <ArrowRight size={14} className="text-[#70757c]" aria-hidden="true" />
      </div>
    </button>
  );
}

// ── Upload section ────────────────────────────────────────────────────────────

interface UploadSectionProps {
  files: File[];
  jobTitle: string;
  level: string;
  jd: string;
  progress: number;
  pending: boolean;
  lastResult: BulkUploadResult | null;
  onFilesAdd: (fl: FileList | null) => void;
  onFileRemove: (idx: number) => void;
  onFilesClear: () => void;
  onJobTitle: (v: string) => void;
  onLevel: (v: string) => void;
  onJd: (v: string) => void;
  onSubmit: (e: React.FormEvent) => void;
}

function UploadSection({
  files, jobTitle, level, jd, progress, pending, lastResult,
  onFilesAdd, onFileRemove, onFilesClear, onJobTitle, onLevel, onJd, onSubmit,
}: UploadSectionProps) {
  return (
    <GlassCard className="p-6">
      {/* Card header */}
      <div className="mb-5 flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-[10px] bg-[rgba(var(--accent-rgb),0.14)] text-[#60a5fa]">
          <Upload size={16} aria-hidden="true" />
        </div>
        <div>
          <p className="text-[15px] font-semibold text-white">Bulk upload resumes</p>
          <p className="text-[12.5px] text-[#888b91]">
            Pick the role once, then select up to {MAX_BULK_FILES} PDF resumes — names extracted automatically.
          </p>
        </div>
      </div>

      <form onSubmit={onSubmit} className="space-y-4">
        {/* Role + level */}
        <div className="grid gap-3 sm:grid-cols-2">
          <input
            className={inputCls}
            placeholder="Role (e.g. Blockchain Engineer)"
            value={jobTitle}
            onChange={(e) => onJobTitle(e.target.value)}
            aria-label="Target role"
          />
          <select
            className={inputCls}
            value={level}
            onChange={(e) => onLevel(e.target.value)}
            aria-label="Experience level"
          >
            <option value="entry">Entry level</option>
            <option value="mid">Mid level</option>
            <option value="senior">Senior level</option>
          </select>
        </div>

        {/* File drop zone */}
        <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-[16px] border-2 border-dashed border-white/[0.12] bg-white/[0.02] px-4 py-8 text-center transition-colors hover:border-[rgba(var(--accent-rgb),0.5)] hover:bg-[rgba(var(--accent-rgb),0.04)]">
          <FileText size={28} className="text-[#60a5fa]" aria-hidden="true" />
          <span className="text-[14px] font-medium text-white">
            Click to choose PDF resumes
          </span>
          <span className="text-[12px] text-[#70757c]">
            Select multiple files &middot; up to {MAX_BULK_FILES} per batch
          </span>
          <input
            type="file"
            accept="application/pdf"
            multiple
            className="sr-only"
            onChange={(e) => {
              onFilesAdd(e.target.files);
              e.target.value = '';
            }}
            aria-label="Resume PDFs"
          />
        </label>

        {/* Selected file list */}
        {files.length > 0 && (
          <div className="rounded-[14px] border border-white/[0.08] bg-white/[0.03] p-3">
            <div className="mb-2 flex items-center justify-between px-1">
              <span className="text-[12px] font-medium text-[#888b91]">
                {files.length} resume{files.length === 1 ? '' : 's'} selected
              </span>
              <button
                type="button"
                className="text-[12px] text-[#888b91] hover:text-white transition-colors"
                onClick={onFilesClear}
              >
                Clear all
              </button>
            </div>
            <ul className="max-h-36 space-y-0.5 overflow-y-auto">
              {files.map((f, i) => (
                <li
                  key={`${f.name}:${f.size}:${i}`}
                  className="flex items-center gap-2 rounded-[8px] px-2 py-1 text-[12px] text-[#70757c] hover:bg-white/[0.04]"
                >
                  <FileText size={13} className="shrink-0 text-[#5a5f66]" aria-hidden="true" />
                  <span className="min-w-0 flex-1 truncate">{f.name}</span>
                  <span className="shrink-0 text-[#5a5f66]">
                    {(f.size / 1024).toFixed(0)} KB
                  </span>
                  <button
                    type="button"
                    aria-label={`Remove ${f.name}`}
                    className="shrink-0 text-[#5a5f66] hover:text-[#e6714f] transition-colors"
                    onClick={() => onFileRemove(i)}
                  >
                    <X size={13} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* JD textarea */}
        <textarea
          className={cn(inputCls, 'min-h-[72px] resize-y')}
          placeholder="Job description (optional — applied to the whole batch, improves scoring accuracy)"
          value={jd}
          onChange={(e) => onJd(e.target.value)}
          aria-label="Job description"
        />

        {/* Upload progress */}
        {pending && (
          <div className="space-y-1.5">
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.08]">
              <div
                className="h-1.5 rounded-full bg-[linear-gradient(90deg,var(--accent),#a887dc)] transition-all"
                style={{ width: `${Math.min(progress, 100)}%` }}
                role="progressbar"
                aria-valuenow={progress}
                aria-valuemin={0}
                aria-valuemax={100}
              />
            </div>
            <p className="text-[12px] text-[#70757c]">
              {progress < 100
                ? `Uploading ${progress}%…`
                : `Scoring ${files.length || 'the'} resume${files.length === 1 ? '' : 's'} — this can take a moment…`}
            </p>
          </div>
        )}

        {/* Submit */}
        <Pill
          type="submit"
          variant="primary"
          disabled={pending || files.length === 0}
          aria-busy={pending}
          className="gap-1.5"
        >
          <Upload size={15} aria-hidden="true" />
          {pending
            ? 'Processing…'
            : `Upload & score${files.length > 0 ? ` ${files.length} resume${files.length === 1 ? '' : 's'}` : ''}`}
        </Pill>
      </form>

      {/* Per-file failure summary */}
      {lastResult && lastResult.failed_count > 0 && (
        <div className="mt-4 rounded-[14px] border border-[rgba(255,183,100,0.25)] bg-[rgba(255,183,100,0.08)] p-3.5">
          <p className="mb-2 flex items-center gap-1.5 text-[12.5px] font-semibold text-[#ffb764]">
            <AlertTriangle size={14} aria-hidden="true" />
            {lastResult.failed_count} file{lastResult.failed_count === 1 ? '' : 's'} skipped
          </p>
          <ul className="space-y-0.5 text-[12px] text-[#ffb764]">
            {lastResult.failed.map((f, i) => (
              <li key={i} className="truncate">
                <span className="font-medium">{f.filename}</span> — {f.error}
              </li>
            ))}
          </ul>
        </div>
      )}
    </GlassCard>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function Applicants() {
  const qc = useQueryClient();

  // Upload form state
  const [files, setFiles] = useState<File[]>([]);
  const [jobTitle, setJobTitle] = useState('');
  const [level, setLevel] = useState('mid');
  const [jd, setJd] = useState('');
  const [progress, setProgress] = useState(0);
  const [lastResult, setLastResult] = useState<BulkUploadResult | null>(null);

  // List state
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const debouncedQuery = useDebouncedValue(query, 350);
  const trimmedQuery = debouncedQuery.trim();
  const searching = trimmedQuery.length > 0;
  const statusParam: ApplicantStatus | undefined =
    filter === 'all' ? undefined : (filter as ApplicantStatus);

  // Drawer state
  const [selected, setSelected] = useState<Applicant | null>(null);

  // ── Queries ──────────────────────────────────────────────────────────────
  // Server-side hybrid search: q + status go to data_gateway, which runs the
  // pgvector + full-text ranking. Previous results stay on screen while the next
  // query resolves (no flicker between keystrokes).
  const { data: applicants, isLoading, isFetching } = useQuery({
    queryKey: ['hr', 'applicants', 'list', trimmedQuery, statusParam ?? 'all'],
    queryFn: () => listApplicants({ q: trimmedQuery || undefined, status: statusParam }),
    placeholderData: (prev) => prev,
  });

  // How many existing applicants still need a search embedding (one-click backfill).
  const { data: reindexStatus } = useQuery({
    queryKey: ['hr', 'applicants', 'reindex-status'],
    queryFn: getReindexStatus,
  });

  const reindexMut = useMutation({
    mutationFn: reindexApplicants,
    onSuccess: (res) => {
      void qc.invalidateQueries({ queryKey: ['hr', 'applicants'] });
      if (res.remaining > 0 && res.reindexed > 0) {
        reindexMut.mutate(); // keep draining batches until none remain
      } else if (res.remaining > 0) {
        toast.error('Some resumes could not be indexed — try again shortly.');
      } else {
        toast.success('All resumes are now searchable.');
      }
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Indexing failed'),
  });

  // ── Mutations ─────────────────────────────────────────────────────────────
  const uploadMut = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      files.forEach((f) => fd.append('files', f));
      fd.append('target_job_title', jobTitle.trim());
      fd.append('target_level', level);
      if (jd.trim()) fd.append('target_jd_text', jd.trim());
      setProgress(0);
      return bulkUploadApplicants(fd, setProgress);
    },
    onSuccess: (res) => {
      setLastResult(res);
      if (res.created_count > 0) {
        toast.success(
          `${res.created_count} resume${res.created_count === 1 ? '' : 's'} added & scored` +
            (res.failed_count > 0 ? ` · ${res.failed_count} skipped` : ''),
        );
      } else {
        toast.error('No resumes could be processed — see details below.');
      }
      setFiles([]);
      setJd('');
      void qc.invalidateQueries({ queryKey: ['hr', 'applicants'] });
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Upload failed'),
  });

  const statusMut = useMutation({
    mutationFn: ({ id, status }: { id: string; status: ApplicantStatus }) =>
      updateApplicantStatus(id, status),
    onSuccess: (updated) => {
      setSelected((prev) => (prev?.id === updated.id ? updated : prev));
      void qc.invalidateQueries({ queryKey: ['hr', 'applicants'] });
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Update failed'),
  });

  const rescoreMut = useMutation({
    mutationFn: (id: string) => rescoreApplicant(id),
    onSuccess: (updated) => {
      toast.success('Rescored');
      setSelected((prev) => (prev?.id === updated.id ? updated : prev));
      void qc.invalidateQueries({ queryKey: ['hr', 'applicants'] });
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Rescore failed'),
  });

  // ── File helpers ─────────────────────────────────────────────────────────
  function addFiles(fileList: FileList | null) {
    if (!fileList) return;
    const incoming = Array.from(fileList).filter((f) => f.type === 'application/pdf');
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
      const merged = [...prev];
      for (const f of incoming) {
        const key = `${f.name}:${f.size}`;
        if (!seen.has(key)) {
          seen.add(key);
          merged.push(f);
        }
      }
      if (merged.length > MAX_BULK_FILES) {
        toast.error(`Max ${MAX_BULK_FILES} resumes per batch — extra files ignored.`);
      }
      return merged.slice(0, MAX_BULK_FILES);
    });
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (files.length === 0) return toast.error('Choose one or more PDF resumes.');
    if (!jobTitle.trim()) return toast.error('The role to screen for is required.');
    uploadMut.mutate();
  }

  // The server already applied the status filter + hybrid search ranking — use
  // the list exactly as returned (relevance order when searching, ATS order otherwise).
  const list = applicants ?? [];

  const pending = uploadMut.isPending;

  return (
    <div className="mx-auto max-w-[1280px] px-6 py-8 lg:px-8 space-y-8">
      {/* Page header */}
      <Reveal>
        <h1 className="text-[28px] font-semibold tracking-[-1px] text-white">Resume screening</h1>
        <p className="mt-1 text-[14px] text-[#888b91]">
          Drop in many resumes at once — each candidate&apos;s name &amp; email are read straight
          from the resume, AI-scored against the role, then ranked.
        </p>
      </Reveal>

      {/* Upload panel */}
      <UploadSection
        files={files}
        jobTitle={jobTitle}
        level={level}
        jd={jd}
        progress={progress}
        pending={pending}
        lastResult={lastResult}
        onFilesAdd={addFiles}
        onFileRemove={(idx) => setFiles((prev) => prev.filter((_, j) => j !== idx))}
        onFilesClear={() => setFiles([])}
        onJobTitle={setJobTitle}
        onLevel={setLevel}
        onJd={setJd}
        onSubmit={onSubmit}
      />

      {/* List section */}
      <div className="space-y-4">
        {/* Backfill banner — existing resumes need embedding before they're searchable */}
        {reindexStatus && reindexStatus.remaining > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-[14px] border border-[rgba(255,183,100,0.25)] bg-[rgba(255,183,100,0.07)] px-4 py-3">
            <p className="flex items-center gap-2 text-[12.5px] text-[#ffb764]">
              <AlertTriangle size={14} aria-hidden="true" />
              {reindexStatus.remaining} earlier resume{reindexStatus.remaining === 1 ? '' : 's'}{' '}
              {reindexStatus.remaining === 1 ? "isn't" : "aren't"} searchable yet — index{' '}
              {reindexStatus.remaining === 1 ? 'it' : 'them'} to include in semantic search.
            </p>
            <Pill
              variant="outline"
              onClick={() => reindexMut.mutate()}
              disabled={reindexMut.isPending}
              className="gap-1.5"
            >
              <RefreshCw
                size={14}
                className={cn(reindexMut.isPending && 'animate-spin')}
                aria-hidden="true"
              />
              {reindexMut.isPending ? 'Indexing…' : 'Make searchable'}
            </Pill>
          </div>
        )}

        {/* Controls bar */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex w-[320px] items-center gap-2 rounded-[9999px] border border-white/[0.08] bg-[rgba(28,29,31,0.7)] px-3.5 py-2.5">
            <Search size={15} className="shrink-0 text-[#70757c]" aria-hidden="true" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by skill, role, or meaning…"
              aria-label="Search applicants by skills or meaning"
              className="min-w-0 flex-1 bg-transparent text-[13px] text-white placeholder:text-[#5a5f66] focus:outline-none"
            />
            {searching && isFetching && (
              <RefreshCw size={13} className="shrink-0 animate-spin text-[#5a5f66]" aria-hidden="true" />
            )}
            {query && (
              <button
                type="button"
                onClick={() => setQuery('')}
                aria-label="Clear search"
                className="shrink-0 text-[#5a5f66] hover:text-white transition-colors"
              >
                <X size={13} aria-hidden="true" />
              </button>
            )}
          </div>

          <SegTabs tabs={STATUS_FILTERS} active={filter} onChange={setFilter} />

          <span className="ml-auto text-[12.5px] text-[#70757c]">
            {isLoading
              ? '…'
              : searching
                ? `${list.length} match${list.length === 1 ? '' : 'es'}`
                : `${list.length} applicant${list.length === 1 ? '' : 's'}`}
          </span>
        </div>

        {/* Table */}
        {isLoading ? (
          <div className="space-y-2" role="status" aria-label="Loading applicants" aria-busy="true">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-16 w-full rounded-[16px] bg-white/[0.05] animate-pulse" />
            ))}
          </div>
        ) : list.length === 0 ? (
          <GlassCard className="py-16 text-center">
            <p className="text-[15px] font-medium text-white">
              {searching || statusParam ? 'No matching candidates' : 'No applicants yet'}
            </p>
            <p className="mt-1 text-[13px] text-[#70757c]">
              {searching
                ? 'Try a broader phrase or different keywords.'
                : statusParam
                  ? 'No applicants in this status.'
                  : 'Upload resumes above to get started.'}
            </p>
          </GlassCard>
        ) : (
          <GlassCard className="overflow-hidden p-0">
            {/* Table header */}
            <div className="grid grid-cols-[2fr_1.3fr_1fr_0.8fr_0.8fr_0.5fr] gap-3 border-b border-white/[0.06] px-6 py-3.5 text-[11.5px] uppercase tracking-[0.5px] text-[#70757c]">
              <div>Candidate</div>
              <div>Role</div>
              <div>Status</div>
              <div>ATS</div>
              <div>Badge</div>
              <div />
            </div>

            {/* Staggered rows */}
            <motion.div
              variants={staggerParent}
              initial="hidden"
              animate="show"
            >
              {list.map((a) => (
                <motion.div key={a.id} variants={staggerChild}>
                  <ApplicantRow a={a} onSelect={setSelected} />
                </motion.div>
              ))}
            </motion.div>
          </GlassCard>
        )}
      </div>

      {/* Slide-in drawer */}
      <AnimatePresence>
        {selected && (
          <ApplicantDrawer
            key={selected.id}
            applicant={selected}
            onClose={() => setSelected(null)}
            onShortlist={(id) => statusMut.mutate({ id, status: 'shortlisted' })}
            onReject={(id) => statusMut.mutate({ id, status: 'rejected' })}
            onRescore={(id) => rescoreMut.mutate(id)}
            statusPending={statusMut.isPending}
            rescorePending={rescoreMut.isPending}
            searchQuery={trimmedQuery}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
