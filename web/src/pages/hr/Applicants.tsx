// Applicants — HR resume screening dashboard.
// Layout: design screen Applicants.tsx (GlassCard table, SegTabs, Avatar, StatusTag, Pill).
// Behavior: all live logic — listApplicants query, bulk PDF upload into one opening
//           (stored, then read and scored in the background, with a progress panel
//           that lists every failed file), shortlist/reject/rescore mutations,
//           real ats_breakdown/strengths/concerns in the detail drawer.

import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { motion, AnimatePresence } from 'framer-motion';
import CandidatePanel from '@/components/agent/CandidatePanel';
import CandidateDrawer from '@/components/CandidateDrawer';
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
  getUploadProgress,
  updateApplicantStatus,
  rescoreApplicant,
  listApplications,
  whyMatch,
  getReindexStatus,
  reindexApplicants,
  type Applicant,
  type ApplicantStatus,
} from '@/api/applicants';
import { listRequisitions, type Requisition } from '@/api/requisitions';
import { DecisionReasonSelect } from '@/components/hr/DecisionReasonSelect';
import { minReasonLength, reasonsFor, useDecisionReasons } from '@/lib/decisionReasons';
import { useSourceOptions } from '@/lib/sourceOptions';
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
import { ACTIVE_POLL_MS, LIVE_POLL_MS } from '../../lib/polling';

// ── Constants ────────────────────────────────────────────────────────────────

// Mirrors the server's bound (services/data_gateway/app/routers/hr_applicants.py:
// _MAX_BULK_FILES / _MAX_BULK_TOTAL_BYTES). E5 raised the server to 500 files /
// 250 MB when scoring moved to the reconciler; this constant stayed at 25, so
// the console silently truncated to 25 and told the user that was the limit —
// the 200-CV graduate intake E5 exists for still could not be done from the UI.
// The byte bound is mirrored too: failing here names the problem, where a 413
// from the proxy does not.
const MAX_BULK_FILES = 500;
const MAX_BULK_TOTAL_BYTES = 250 * 1024 * 1024;

const STATUS_FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'new', label: 'New' },
  { key: 'shortlisted', label: 'Shortlisted' },
  { key: 'rejected', label: 'Rejected' },
];

// Keyed by string and covering all SIX statuses the API returns — not the
// three the exported `ApplicantStatus` union lists. The maps used to be typed to
// that union and indexed with no fallback, so a held, interviewed or hired
// applicant rendered an EMPTY badge: no colour, no word. Worst for `held`, the
// D-05 state — below a round threshold, still in play, awaiting a person —
// which is exactly the one that must stay visible.
//
// `held` is amber, never the terminal red: nobody has decided anything yet.
const STATUS_TONE: Record<string, TagTone> = {
  new: 'neutral',
  shortlisted: 'forest',
  held: 'amber',
  interviewed: 'lavender',
  hired: 'forest',
  rejected: 'ember',
};

const STATUS_LABEL: Record<string, string> = {
  new: 'New',
  shortlisted: 'Shortlisted',
  held: 'Held',
  interviewed: 'Interviewed',
  hired: 'Hired',
  rejected: 'Rejected',
};

/** A tone that claims nothing for a status this build has never seen. */
function statusTone(status: string): TagTone {
  return STATUS_TONE[status] ?? 'neutral';
}

/** The raw value rather than a blank: a person can report "foo", not nothing. */
function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status;
}

const BREAKDOWN_LABELS: Record<string, string> = {
  skills_match: 'Skills',
  experience_relevance: 'Experience',
  education_fit: 'Education',
  role_alignment: 'Role alignment',
};

// ── Helpers ──────────────────────────────────────────────────────────────────

type RecInfo = { label: string; tone: TagTone };

/**
 * The badge for a row whose resume has not been read yet.
 *
 * Separate from "Unscored" on purpose. Both have a null ats_overall, but one
 * means "we looked and could not score this" and the other means "we have not
 * looked yet" — and a manager triaging a list will treat an unscored candidate
 * as a weak one if nothing says otherwise.
 */
function atsInfo(a: { ats_recommendation: string | null; pending_enrichment?: boolean }) {
  if (a.pending_enrichment) {
    return { label: 'Reading CV…', tone: 'electric' as const };
  }
  return recInfo(a.ats_recommendation);
}

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
  'w-full rounded-[10px] border border-border bg-secondary px-3 py-2 ' +
  'text-[14px] text-foreground placeholder:text-[var(--ui-faint)] focus:outline-none ' +
  'focus:border-[var(--accent)] transition-colors';

// ── Reject, with a required structured reason (O4) ──────────────────────────
//
// Rejecting is a terminal decision — the server now refuses a reject with
// neither `reason` nor `reason_code`. The button therefore expands into a
// small inline form rather than firing immediately, the same requirement
// DecisionQueue and HRPipeline gate hire/reject on; this reuses their shared
// DecisionReasonSelect rather than a third copy of the filtering/validation.
function RejectAction({
  idSuffix,
  onConfirm,
  disabled,
  ariaLabel = 'Reject',
}: {
  /** Makes every id/label on the page unique when several rows render this. */
  idSuffix: string;
  onConfirm: (reasonCode: string, reason: string) => void;
  disabled?: boolean;
  ariaLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const [reasonCode, setReasonCode] = useState('');
  const [reason, setReason] = useState('');
  const reasonsQuery = useDecisionReasons();
  const reasons = reasonsFor(reasonsQuery.data, 'rejected');
  const chosen = reasons.find((r) => r.code === reasonCode) ?? null;
  const minLen = minReasonLength(chosen);
  const canConfirm = Boolean(reasonCode) && reason.trim().length >= minLen;

  if (!open) {
    return (
      <Pill
        variant="danger"
        onClick={() => setOpen(true)}
        disabled={disabled}
        aria-label={ariaLabel}
        className="gap-1.5"
      >
        <XCircle size={15} aria-hidden="true" />
        Reject
      </Pill>
    );
  }

  return (
    <div className="w-full space-y-2 rounded-[12px] border border-border bg-[var(--ui-inset)] p-3">
      <DecisionReasonSelect
        id={`reject-reason-${idSuffix}`}
        reasons={reasons}
        value={reasonCode}
        onChange={setReasonCode}
      />
      <div>
        <label
          htmlFor={`reject-why-${idSuffix}`}
          className="block text-[12px] text-[var(--ui-soft)]"
        >
          Why{chosen?.requires_explanation ? ` (at least ${minLen} characters)` : ''}
        </label>
        <textarea
          id={`reject-why-${idSuffix}`}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={2}
          className={inputCls}
        />
      </div>
      {!canConfirm ? (
        <p className="text-[11px] text-[var(--ui-warn)]">
          {!reasonCode ? 'Choose a reason first.' : `Write at least ${minLen} characters.`}
        </p>
      ) : null}
      <div className="flex gap-2">
        <Pill
          variant="danger"
          disabled={!canConfirm}
          onClick={() => {
            onConfirm(reasonCode, reason.trim());
            setOpen(false);
            setReasonCode('');
            setReason('');
          }}
          aria-label={`Confirm ${ariaLabel.toLowerCase()}`}
        >
          Confirm reject
        </Pill>
        <Pill variant="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Pill>
      </div>
    </div>
  );
}

// ── Slide-in drawer ───────────────────────────────────────────────────────────

interface DrawerProps {
  applicant: Applicant | null;
  onClose: () => void;
  /** `enrolmentId` names the application (B5); always sent when known. */
  onShortlist: (id: string, enrolmentId?: string | null) => void;
  /** Rejecting is terminal (O4) — always carries the chosen reason code and
   *  the free-text reason behind it. */
  onReject: (
    id: string,
    enrolmentId: string | null | undefined,
    reasonCode: string,
    reason: string,
  ) => void;
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

  // The person's applications (B5). With one, the actions below act on it;
  // with several, each gets its own actions — the status on this row is only
  // the latest application's, and a shortlist has to say which opening.
  const apps = useQuery({
    queryKey: ['hr', 'applicant', a?.id, 'applications'],
    queryFn: () => listApplications(a?.id as string),
    enabled: Boolean(a),
    retry: false,
    throwOnError: false,
  });
  const applications = apps.data ?? [];
  const only = applications.length === 1 ? applications[0] : null;
  const several = applications.length > 1;
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
  const rec = atsInfo(a);

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
        className="relative flex max-h-[90vh] w-full max-w-[1040px] flex-col overflow-hidden rounded-[24px] border border-border bg-card shadow-[0_30px_90px_rgba(0,0,0,0.65)]"
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
        <div className="flex shrink-0 items-center justify-between border-b border-border px-7 py-5">
          <span className="text-[12px] uppercase tracking-[1px] text-[var(--ui-faint)]">
            Candidate
          </span>
          <button
            onClick={onClose}
            aria-label="Close"
            className="flex h-8 w-8 items-center justify-center rounded-[9px] border border-border bg-[var(--ui-inset)] text-[var(--ui-soft)] hover:text-foreground transition-colors"
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
                  <div className="text-[20px] font-semibold tracking-[-0.5px] text-foreground">
                    {a.full_name}
                  </div>
                  <div className="truncate text-[13px] text-[var(--ui-faint)]">
                    {a.email ?? 'No email on file'}
                  </div>
                  {a.user_id && (
                    <Link
                      to={`/u/${a.user_id}`}
                      className="mt-1 inline-flex items-center gap-1 text-[12px] font-medium text-[var(--ui-info)] hover:underline"
                    >
                      View full profile <ArrowRight size={12} aria-hidden="true" />
                    </Link>
                  )}
                </div>
              </div>

              {/* Score + status tiles */}
              <div className="grid grid-cols-2 gap-2.5">
                <div className="rounded-[12px] border border-border bg-card p-4">
                  <div className="text-[11px] uppercase tracking-[0.5px] text-[var(--ui-faint)]">
                    ATS score
                  </div>
                  <div
                    className="mt-1 text-[28px] font-semibold tracking-[-1px]"
                    style={{ color: atsDisplay !== null ? scoreColor(atsDisplay) : '#70757c' }}
                  >
                    {atsDisplay ?? '—'}
                  </div>
                </div>
                <div className="rounded-[12px] border border-border bg-card p-4">
                  <div className="text-[11px] uppercase tracking-[0.5px] text-[var(--ui-faint)]">
                    Status
                  </div>
                  <div className="mt-2">
                    <StatusTag tone={statusTone(a.status)} dot>
                      {statusLabel(a.status)}
                    </StatusTag>
                  </div>
                </div>
              </div>

              {/* Role meta */}
              <div className="space-y-1.5 text-[12.5px] text-[var(--ui-faint)]">
                <p>
                  Role &middot;{' '}
                  <span className="text-[var(--ui-soft)]">
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
                <p className="text-[13px] leading-relaxed text-muted-foreground">{a.ats_summary}</p>
              )}

              {/* Why matched — only during a search */}
              {showMatch && (
                <div className="rounded-[12px] border border-[rgba(var(--accent-rgb),0.25)] bg-[rgba(var(--accent-rgb),0.06)] p-4">
                  <div className="flex items-center justify-between">
                    <span className="flex items-center gap-1.5 text-[12px] font-semibold text-[var(--ui-info)]">
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
                  <p className="mt-2 text-[13px] leading-relaxed text-[var(--ui-soft)]">
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
                  <div className="text-[13px] font-semibold text-foreground">Score breakdown</div>
                  <div className="mt-3 flex flex-col gap-3">
                    {Object.entries(a.ats_breakdown).map(([k, v]) => (
                      <div key={k}>
                        <div className="mb-1 flex justify-between text-[12.5px]">
                          <span className="text-[var(--ui-soft)]">{BREAKDOWN_LABELS[k] ?? k}</span>
                          <span className="font-mono text-muted-foreground">{v}</span>
                        </div>
                        <div className="h-1.5 rounded-full bg-[var(--ui-inset-strong)]">
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

              {/* Cross-signal assessment — the specialist panel. Placed above the
                ATS strengths/concerns because it spans every round, whereas
                those describe the resume alone. User-triggered inside the
                component, so opening a drawer costs nothing. */}
              <CandidatePanel applicantId={a.id} applicantName={a.full_name} />

              {/* Several applications: each with where it is and its own actions. */}
              {several && (
                <div>
                  <p className="text-[12.5px] font-semibold text-foreground">
                    Applications ({applications.length})
                  </p>
                  <ul className="mt-2 space-y-2" aria-label="Applications">
                    {applications.map((app) => (
                      <li
                        key={app.enrolment_id}
                        className="flex flex-wrap items-center justify-between gap-2 rounded-[12px] border border-border px-3 py-2"
                      >
                        <div className="min-w-0">
                          <p className="truncate text-[12.5px] font-medium text-foreground">
                            {app.opening_title ?? 'Untitled opening'}
                          </p>
                          <p className="text-[11.5px] text-[var(--ui-faint)]">
                            {app.status}
                            {app.ats_overall != null ? ` · resume ${app.ats_overall}/100` : ''}
                            {app.is_latest ? ' · latest' : ''}
                          </p>
                        </div>
                        <div className="flex w-full flex-wrap items-center justify-end gap-1.5 sm:w-auto">
                          <Pill
                            variant="ghost"
                            onClick={() => onShortlist(a.id, app.enrolment_id)}
                            disabled={statusPending || app.stored_status === 'shortlisted'}
                            aria-label={`Shortlist for ${app.opening_title ?? 'this opening'}`}
                          >
                            Shortlist
                          </Pill>
                          <RejectAction
                            idSuffix={app.enrolment_id}
                            ariaLabel={`Reject for ${app.opening_title ?? 'this opening'}`}
                            disabled={statusPending || app.stored_status === 'rejected'}
                            onConfirm={(reasonCode, reason) =>
                              onReject(a.id, app.enrolment_id, reasonCode, reason)
                            }
                          />
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Strengths + Concerns */}
              {((a.ats_strengths && a.ats_strengths.length > 0) ||
                (a.ats_concerns && a.ats_concerns.length > 0)) && (
                <div className="grid gap-4">
                  {a.ats_strengths && a.ats_strengths.length > 0 && (
                    <div>
                      <p className="text-[12.5px] font-semibold text-[var(--ui-ok)]">Strengths</p>
                      <ul className="mt-2 space-y-1">
                        {a.ats_strengths.map((s, i) => (
                          <li
                            key={i}
                            className="flex items-start gap-1.5 text-[12px] text-[var(--ui-faint)]"
                          >
                            <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--ui-ok)]" />
                            {s}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {a.ats_concerns && a.ats_concerns.length > 0 && (
                    <div>
                      <p className="text-[12.5px] font-semibold text-[var(--ui-danger)]">
                        Concerns
                      </p>
                      <ul className="mt-2 space-y-1">
                        {a.ats_concerns.map((c, i) => (
                          <li
                            key={i}
                            className="flex items-start gap-1.5 text-[12px] text-[var(--ui-faint)]"
                          >
                            <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--ui-danger)]" />
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
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-2.5 border-t border-border px-7 py-5">
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
          {/* With several applications the actions live on each one above. */}
          {!several && (
            <>
              <Pill
                variant="ghost"
                onClick={() => onShortlist(a.id, only?.enrolment_id)}
                disabled={statusPending || a.status === 'shortlisted'}
                aria-label="Shortlist"
                className="gap-1.5"
              >
                <CheckCircle2 size={15} aria-hidden="true" />
                Shortlist
              </Pill>
              <RejectAction
                idSuffix={only?.enrolment_id ?? a.id}
                disabled={statusPending || a.status === 'rejected'}
                onConfirm={(reasonCode, reason) =>
                  onReject(a.id, only?.enrolment_id, reasonCode, reason)
                }
              />
            </>
          )}
        </div>
      </motion.div>
    </motion.div>,
    document.body,
  );
}

// ── Table row ─────────────────────────────────────────────────────────────────

function ApplicantRow({ a, onSelect }: { a: Applicant; onSelect: (applicant: Applicant) => void }) {
  const seed = seedFrom(a.full_name);
  const atsDisplay = a.ats_overall;
  const rec = atsInfo(a);

  return (
    <button
      onClick={() => onSelect(a)}
      className="grid w-full grid-cols-[2fr_1.3fr_1fr_0.8fr_0.8fr_0.5fr] items-center gap-3 border-b border-border px-6 py-3.5 text-left transition-colors last:border-0 hover:bg-[var(--ui-inset-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-inset"
      aria-label={`Open details for ${a.full_name}`}
    >
      {/* Candidate */}
      <div className="flex min-w-0 items-center gap-3">
        <Avatar initials={initialsOf(a.full_name)} gradient={gradientFor(seed)} size={36} />
        <div className="min-w-0">
          <div className="truncate text-[14px] font-medium text-foreground">{a.full_name}</div>
          <div className="truncate text-[12px] text-[var(--ui-faint)]">{a.email ?? 'No email'}</div>
        </div>
      </div>

      {/* Role */}
      <div className="min-w-0">
        <div className="truncate text-[13.5px] text-[var(--ui-soft)]">{a.target_job_title}</div>
        <div className="text-[11.5px] text-[var(--ui-faint)]">{a.target_level}</div>
      </div>

      {/* Status */}
      <div className="flex flex-col gap-1">
        <StatusTag tone={statusTone(a.status)} dot>
          {statusLabel(a.status)}
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
            className="inline-flex items-center gap-1 rounded-full bg-[var(--ui-inset)] px-2 py-0.5 text-[11.5px] font-semibold"
            style={{ color: matchColor(a.match_score) }}
            title="Relevance to your search"
          >
            {a.match_score}% match
          </span>
        ) : (
          a.status === 'shortlisted' && (
            <span className="inline-flex items-center gap-1 text-[11.5px] font-semibold text-[var(--ui-ok)]">
              <Star size={12} aria-hidden="true" />
              Shortlisted
            </span>
          )
        )}
      </div>

      {/* Arrow */}
      <div className="flex justify-end">
        <ArrowRight size={14} className="text-[var(--ui-faint)]" aria-hidden="true" />
      </div>
    </button>
  );
}

// ── Upload section ────────────────────────────────────────────────────────────

interface UploadSectionProps {
  files: File[];
  progress: number;
  pending: boolean;
  /** The upload the progress panel follows, once the server has accepted it. */
  batchId: string | null;
  onFilesAdd: (fl: FileList | null) => void;
  onFileRemove: (idx: number) => void;
  onFilesClear: () => void;
  onSubmit: (e: React.FormEvent) => void;
  /** Open openings to file the batch under. Required: a typed role is not an opening (E5). */
  openings: Requisition[];
  openingId: string;
  onOpening: (id: string) => void;
  /** PH5 wave-1 follow-up (B) — where this upload's candidate(s) came from;
   *  applies to the whole batch. Defaults to "internal". */
  source: string;
  onSource: (source: string) => void;
}

/**
 * One upload's progress, polled until it is finished (E5).
 *
 * The upload request only stores the files. Reading them, filing each person
 * under the opening and scoring them happen in the background, so this is how
 * HR sees where a batch has got to — and which files failed, and why — without
 * having to keep the page open for it.
 */
function UploadBatchProgress({ batchId }: { batchId: string }) {
  const { data, isError } = useQuery({
    queryKey: ['hr', 'upload', batchId],
    queryFn: () => getUploadProgress(batchId),
    refetchInterval: (q) => (q.state.data?.finished ? false : 3000),
  });
  if (isError) {
    return (
      <p className="mt-4 text-[12.5px] text-muted-foreground">
        Could not load this upload&apos;s progress. It is still being processed.
      </p>
    );
  }
  if (!data) return null;
  const read = data.created + data.failed;
  const pct = data.total_files ? Math.round((read / data.total_files) * 100) : 100;
  return (
    <div
      className="mt-4 rounded-[14px] border border-border bg-[var(--ui-inset-soft)] p-3.5"
      data-testid="upload-progress"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-[13px] font-medium text-foreground">
          {data.finished ? 'Upload finished' : 'Processing upload'} — {data.requisition_title}
        </p>
        <span className="text-[12px] tabular-nums text-muted-foreground">
          {read} of {data.total_files} files read
        </span>
      </div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-[var(--ui-inset-strong)]">
        <div
          className="h-1.5 rounded-full bg-[var(--accent)] transition-all"
          style={{ width: `${pct}%` }}
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
        />
      </div>
      <p className="mt-2 text-[12px] text-[var(--ui-soft)]">
        {data.queued} waiting · {data.created} added · {data.being_scored} being scored ·{' '}
        {data.failed} failed
      </p>
      <p className="mt-1 text-[12px] text-muted-foreground">
        {data.finished
          ? 'Every added candidate is filed under this opening.'
          : 'You can leave this page — it carries on, and you will be notified when it is done.'}
      </p>
      {data.failures && data.failures.length > 0 ? (
        <ul className="mt-2 space-y-0.5 text-[12px] text-[var(--ui-warn)]">
          {data.failures.map((f, i) => (
            <li key={`${f.filename}-${i}`} className="truncate">
              <span className="font-medium">{f.filename}</span> — {f.error}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function UploadSection({
  files,
  progress,
  pending,
  batchId,
  onFilesAdd,
  onFileRemove,
  onFilesClear,
  onSubmit,
  openings,
  openingId,
  onOpening,
  source,
  onSource,
}: UploadSectionProps) {
  const { options: sourceOptions } = useSourceOptions();
  return (
    <GlassCard className="p-6">
      {/* Card header */}
      <div className="mb-5 flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-[10px] bg-[rgba(var(--accent-rgb),0.14)] text-[var(--ui-info)]">
          <Upload size={16} aria-hidden="true" />
        </div>
        <div>
          <p className="text-[15px] font-semibold text-foreground">Bulk upload resumes</p>
          <p className="text-[12.5px] text-muted-foreground">
            Pick the opening, then select up to {MAX_BULK_FILES} PDF resumes. They are read, filed
            and scored in the background.
          </p>
        </div>
      </div>

      <form onSubmit={onSubmit} className="space-y-4">
        {/* The opening. Required: every resume is filed under it and scored
            against its role and job description (E5). */}
        {openings.length === 0 ? (
          <p className="text-[12.5px] text-muted-foreground">
            There is no open opening to upload into.{' '}
            <Link to="/hr/requisitions" className="text-[var(--accent)] hover:underline">
              Create one first
            </Link>
            .
          </p>
        ) : (
          <select
            className={inputCls}
            value={openingId}
            onChange={(e) => onOpening(e.target.value)}
            aria-label="Opening"
          >
            <option value="">Choose the opening…</option>
            {openings.map((o) => (
              <option key={o.id} value={o.id}>
                {o.title} · {o.level}
              </option>
            ))}
          </select>
        )}

        {/* PH5 wave-1 follow-up (B) — applies to the whole upload, not per file:
            there is one "add applicant(s)" flow in this console, used for one
            resume or many the same way. */}
        <div>
          <label
            htmlFor="upload-source"
            className="mb-1.5 block text-[12px] font-medium text-[var(--ui-soft)]"
          >
            Where did this candidate come from?
          </label>
          <select
            id="upload-source"
            className={inputCls}
            value={source}
            onChange={(e) => onSource(e.target.value)}
          >
            {sourceOptions.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
          <p className="mt-1 text-[11.5px] text-[var(--ui-faint)]">Applies to the whole upload.</p>
        </div>

        {/* File drop zone */}
        <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-[16px] border-2 border-dashed border-[var(--ui-line-strong)] bg-[var(--ui-inset-soft)] px-4 py-8 text-center transition-colors hover:border-[rgba(var(--accent-rgb),0.5)] hover:bg-[rgba(var(--accent-rgb),0.04)]">
          <FileText size={28} className="text-[var(--ui-info)]" aria-hidden="true" />
          <span className="text-[14px] font-medium text-foreground">
            Click to choose PDF resumes
          </span>
          <span className="text-[12px] text-[var(--ui-faint)]">
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
          <div className="rounded-[14px] border border-border bg-[var(--ui-inset-soft)] p-3">
            <div className="mb-2 flex items-center justify-between px-1">
              <span className="text-[12px] font-medium text-muted-foreground">
                {files.length} resume{files.length === 1 ? '' : 's'} selected
              </span>
              <button
                type="button"
                className="text-[12px] text-muted-foreground hover:text-foreground transition-colors"
                onClick={onFilesClear}
              >
                Clear all
              </button>
            </div>
            <ul className="max-h-36 space-y-0.5 overflow-y-auto">
              {files.map((f, i) => (
                <li
                  key={`${f.name}:${f.size}:${i}`}
                  className="flex items-center gap-2 rounded-[8px] px-2 py-1 text-[12px] text-[var(--ui-faint)] hover:bg-[var(--ui-inset)]"
                >
                  <FileText
                    size={13}
                    className="shrink-0 text-[var(--ui-faint)]"
                    aria-hidden="true"
                  />
                  <span className="min-w-0 flex-1 truncate">{f.name}</span>
                  <span className="shrink-0 text-[var(--ui-faint)]">
                    {(f.size / 1024).toFixed(0)} KB
                  </span>
                  <button
                    type="button"
                    aria-label={`Remove ${f.name}`}
                    className="shrink-0 text-[var(--ui-faint)] hover:text-[var(--ui-danger)] transition-colors"
                    onClick={() => onFileRemove(i)}
                  >
                    <X size={13} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Upload progress — the bytes only; reading happens afterwards */}
        {pending && (
          <div className="space-y-1.5">
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--ui-inset-strong)]">
              <div
                className="h-1.5 rounded-full bg-[linear-gradient(90deg,var(--accent),#a887dc)] transition-all"
                style={{ width: `${Math.min(progress, 100)}%` }}
                role="progressbar"
                aria-valuenow={progress}
                aria-valuemin={0}
                aria-valuemax={100}
              />
            </div>
            <p className="text-[12px] text-[var(--ui-faint)]">
              {progress < 100 ? `Uploading ${progress}%…` : 'Storing the files…'}
            </p>
          </div>
        )}

        {/* Submit */}
        <Pill
          type="submit"
          variant="primary"
          disabled={pending || files.length === 0 || !openingId}
          aria-busy={pending}
          className="gap-1.5"
        >
          <Upload size={15} aria-hidden="true" />
          {pending
            ? 'Uploading…'
            : `Upload${files.length > 0 ? ` ${files.length} resume${files.length === 1 ? '' : 's'}` : ''}`}
        </Pill>
      </form>

      {batchId ? <UploadBatchProgress batchId={batchId} /> : null}
    </GlassCard>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function Applicants() {
  const qc = useQueryClient();

  // ── A citation, or an evidence-trail link, naming one person ──────────────
  //
  // `/hr/applicants/{id}` is CITATION_ROUTES.applicant AND .scorecard (a
  // scorecard has no page of its own, so it is cited through the person), and it
  // is also the href every evidence-graph node carries —
  // `/hr/applicants/{applicant_id}?enrolment={enrolment_id}#section`
  // (evidence_graph/loaders.py::_href). Until this route existed, all of those
  // opened the 404 page.
  //
  // It opens CandidateDrawer rather than this page's own ApplicantDrawer for
  // that last reason: the drawer is the other half of the anchor contract those
  // hrefs are built against (it owns the `#screening`, `#round-results`,
  // `#human-interview`, `#offers`… section ids), and it is enrolment-scoped, so
  // a link about ONE application shows that application's scores rather than
  // the person's latest. Same component, same props, as the four other surfaces
  // that open it from state.
  const { applicantId: citedApplicantId } = useParams<{ applicantId?: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  // Upload form state
  const [files, setFiles] = useState<File[]>([]);
  const [openingId, setOpeningId] = useState('');
  // PH5 wave-1 follow-up (B) — defaults to "internal", matching what the
  // server assumes when the field is omitted entirely.
  const [source, setSource] = useState('internal');
  const [progress, setProgress] = useState(0);
  // The upload the progress panel follows, once the server has accepted it.
  const [batchId, setBatchId] = useState<string | null>(null);
  // Open openings for the upload picker (B5).
  const { data: openingsData } = useQuery({
    queryKey: ['hr', 'requisitions', 'open'],
    queryFn: () => listRequisitions({ status: 'open', limit: 200 }),
    staleTime: 60_000,
  });
  const openings = openingsData ?? [];

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
  const {
    data: applicants,
    isLoading,
    isFetching,
  } = useQuery({
    queryKey: ['hr', 'applicants', 'list', trimmedQuery, statusParam ?? 'all'],
    // A5: new applicants arrive from bulk upload and, later, self-apply.
    refetchInterval: LIVE_POLL_MS,
    queryFn: () => listApplicants({ q: trimmedQuery || undefined, status: statusParam }),
    placeholderData: (prev) => prev,
  });

  // How many existing applicants still need a search embedding (one-click backfill).
  const { data: reindexStatus } = useQuery({
    queryKey: ['hr', 'applicants', 'reindex-status'],
    // A5: the reconciliation loop drains this without a click.
    refetchInterval: ACTIVE_POLL_MS,
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
      fd.append('requisition_id', openingId);
      fd.append('source', source);
      setProgress(0);
      return bulkUploadApplicants(fd, setProgress);
    },
    onSuccess: (res) => {
      setBatchId(res.batch_id);
      if (res.accepted > 0) {
        toast.success(
          `${res.accepted} resume${res.accepted === 1 ? '' : 's'} accepted — reading and ` +
            'scoring them in the background' +
            (res.failed_count > 0 ? ` · ${res.failed_count} refused` : ''),
        );
      } else {
        toast.error('None of those files could be accepted — see why below.');
      }
      setFiles([]);
      void qc.invalidateQueries({ queryKey: ['hr', 'applicants'] });
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Upload failed'),
  });

  const statusMut = useMutation({
    mutationFn: ({
      id,
      status,
      enrolmentId,
      reason,
      reasonCode,
    }: {
      id: string;
      status: ApplicantStatus;
      enrolmentId?: string | null;
      reason?: string;
      reasonCode?: string;
    }) =>
      // Only a reject carries a reason/reason_code (O4) — shortlisting stays a
      // plain 3-argument call rather than two always-undefined trailing ones.
      reason !== undefined || reasonCode !== undefined
        ? updateApplicantStatus(id, status, enrolmentId, reason, reasonCode)
        : updateApplicantStatus(id, status, enrolmentId),
    onSuccess: (updated) => {
      setSelected((prev) => (prev?.id === updated.id ? updated : prev));
      void qc.invalidateQueries({ queryKey: ['hr', 'applicants'] });
      void qc.invalidateQueries({ queryKey: ['hr', 'applicant', updated.id, 'applications'] });
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
      const capped = merged.slice(0, MAX_BULK_FILES);
      const bytes = capped.reduce((total, f) => total + f.size, 0);
      if (bytes > MAX_BULK_TOTAL_BYTES) {
        toast.error(
          `That batch is ${Math.round(bytes / 1024 / 1024)} MB — the limit is ` +
            `${Math.round(MAX_BULK_TOTAL_BYTES / 1024 / 1024)} MB. Split it into two uploads.`,
        );
      }
      return capped;
    });
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (files.length === 0) return toast.error('Choose one or more PDF resumes.');
    if (!openingId) return toast.error('Choose the opening these resumes are for.');
    uploadMut.mutate();
  }

  // The server already applied the status filter + hybrid search ranking — use
  // the list exactly as returned (relevance order when searching, ATS order otherwise).
  const list = applicants ?? [];

  /** Server-side page ceiling for GET /hr/applicants (_SEARCH_LIMIT / _LIST_PAGE_SIZE). */
  const APPLICANTS_PAGE_SIZE = 200;

  /** "200+" rather than "200" when the page is full.
   *
   * The endpoint returns one bounded page and no total, so a company with 5,000
   * applicants would otherwise read a flat "200 applicants" as a definitive
   * count — a confidently-wrong number, which is worse than an obviously
   * incomplete one. A full page means "at least this many"; say that.
   * When a real total or a pager lands, this can show the true figure. */
  const countLabel = list.length >= APPLICANTS_PAGE_SIZE ? `${list.length}+` : `${list.length}`;

  const pending = uploadMut.isPending;

  return (
    <div className="mx-auto max-w-[1280px] px-6 py-8 lg:px-8 space-y-8">
      {/* Page header */}
      <Reveal>
        <h1 className="text-[28px] font-semibold tracking-[-1px] text-foreground">
          Resume screening
        </h1>
        <p className="mt-1 text-[14px] text-muted-foreground">
          Drop in many resumes at once — each candidate&apos;s name &amp; email are read straight
          from the resume, AI-scored against the role, then ranked.
        </p>
      </Reveal>

      {/* Upload panel */}
      <UploadSection
        files={files}
        progress={progress}
        pending={pending}
        batchId={batchId}
        onFilesAdd={addFiles}
        onFileRemove={(idx) => setFiles((prev) => prev.filter((_, j) => j !== idx))}
        onFilesClear={() => setFiles([])}
        onSubmit={onSubmit}
        openings={openings}
        openingId={openingId}
        onOpening={setOpeningId}
        source={source}
        onSource={setSource}
      />

      {/* List section */}
      <div className="space-y-4">
        {/* Backfill banner — existing resumes need embedding before they're searchable */}
        {reindexStatus && reindexStatus.remaining > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-[14px] border border-[rgba(255,183,100,0.25)] bg-[rgba(255,183,100,0.07)] px-4 py-3">
            <p className="flex items-center gap-2 text-[12.5px] text-[var(--ui-warn)]">
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
          <div className="flex w-[320px] items-center gap-2 rounded-[9999px] border border-border bg-secondary px-3.5 py-2.5">
            <Search size={15} className="shrink-0 text-[var(--ui-faint)]" aria-hidden="true" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by skill, role, or meaning…"
              aria-label="Search applicants by skills or meaning"
              className="min-w-0 flex-1 bg-transparent text-[13px] text-foreground placeholder:text-[var(--ui-faint)] focus:outline-none"
            />
            {searching && isFetching && (
              <RefreshCw
                size={13}
                className="shrink-0 animate-spin text-[var(--ui-faint)]"
                aria-hidden="true"
              />
            )}
            {query && (
              <button
                type="button"
                onClick={() => setQuery('')}
                aria-label="Clear search"
                className="shrink-0 text-[var(--ui-faint)] hover:text-foreground transition-colors"
              >
                <X size={13} aria-hidden="true" />
              </button>
            )}
          </div>

          <SegTabs tabs={STATUS_FILTERS} active={filter} onChange={setFilter} />

          <span className="ml-auto text-[12.5px] text-[var(--ui-faint)]">
            {isLoading
              ? '…'
              : searching
                ? `${countLabel} match${list.length === 1 ? '' : 'es'}`
                : `${countLabel} applicant${list.length === 1 ? '' : 's'}`}
          </span>
        </div>

        {/* Table */}
        {isLoading ? (
          <div className="space-y-2" role="status" aria-label="Loading applicants" aria-busy="true">
            {Array.from({ length: 4 }).map((_, i) => (
              <div
                key={i}
                className="h-16 w-full rounded-[16px] bg-[var(--ui-inset)] animate-pulse"
              />
            ))}
          </div>
        ) : list.length === 0 ? (
          <GlassCard className="py-16 text-center">
            <p className="text-[15px] font-medium text-foreground">
              {searching || statusParam ? 'No matching candidates' : 'No applicants yet'}
            </p>
            <p className="mt-1 text-[13px] text-[var(--ui-faint)]">
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
            <div className="grid grid-cols-[2fr_1.3fr_1fr_0.8fr_0.8fr_0.5fr] gap-3 border-b border-border px-6 py-3.5 text-[11.5px] uppercase tracking-[0.5px] text-[var(--ui-faint)]">
              <div>Candidate</div>
              <div>Role</div>
              <div>Status</div>
              <div>ATS</div>
              <div>Badge</div>
              <div />
            </div>

            {/* Staggered rows */}
            <motion.div variants={staggerParent} initial="hidden" animate="show">
              {list.map((a) => (
                <motion.div key={a.id} variants={staggerChild}>
                  <ApplicantRow a={a} onSelect={setSelected} />
                </motion.div>
              ))}
            </motion.div>
          </GlassCard>
        )}
      </div>

      {/* The person a citation or an evidence link named. Rendered with a null
          id on the plain list route, exactly as the four other surfaces do;
          closing it returns to the list rather than leaving a URL that would
          re-open it. */}
      <CandidateDrawer
        applicantId={citedApplicantId ?? null}
        enrolmentId={searchParams.get('enrolment')}
        onClose={() => void navigate('/hr/applicants', { replace: true })}
      />

      {/* Slide-in drawer */}
      <AnimatePresence>
        {selected && (
          <ApplicantDrawer
            key={selected.id}
            applicant={selected}
            onClose={() => setSelected(null)}
            onShortlist={(id, enrolmentId) =>
              statusMut.mutate({ id, status: 'shortlisted', enrolmentId })
            }
            onReject={(id, enrolmentId, reasonCode, reason) =>
              statusMut.mutate({ id, status: 'rejected', enrolmentId, reason, reasonCode })
            }
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
