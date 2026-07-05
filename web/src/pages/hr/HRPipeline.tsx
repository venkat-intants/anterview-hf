// HRPipeline — single-table hiring funnel: resume → exam → interview → decision.
// Layout: design screen HRPipeline.tsx card visual styling (GlassCard, StatusTag, Avatar).
// Behavior: all live logic — server-paginated TABLE, persisted setApplicantDecision
//           (hired/rejected + rationale), canHire/canReject gating, embedded analytics.
// NOTE: design's drag-drop Kanban is NOT used — no stage-transition API endpoint exists.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  TrendingUp,
  CheckCircle2,
  XCircle,
  ChevronDown,
  Star,
  BarChart3,
  ClipboardCheck,
  Loader2,
} from '@/design/components/icons';
import {
  getPipeline,
  setApplicantDecision,
  type PipelineRow as Row,
  type PipelineStage,
  type ApplicantDecision,
} from '@/api/pipeline';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import {
  GlassCard,
  StatusTag,
  Avatar,
  SegTabs,
  type TagTone,
} from '@/design/components/primitives';
import { Reveal, Stagger, StaggerItem } from '@/design/components/Reveal';
import HRAnalytics from './HRAnalytics';

const PAGE_SIZE = 50;

/* ── Design-kit colour helpers (inlined — no cross-project import) ─────────── */

const GRADIENTS = [
  'linear-gradient(135deg,var(--accent),#a887dc)',
  'linear-gradient(135deg,#16c253,var(--accent))',
  'linear-gradient(135deg,#dd55e7,#a887dc)',
  'linear-gradient(135deg,#ffb764,#dd55e7)',
  'linear-gradient(135deg,#0fb7fa,#16c253)',
  'linear-gradient(135deg,#a887dc,var(--accent))',
];

function gradientFor(seed: number): string {
  return GRADIENTS[Math.abs(seed) % GRADIENTS.length];
}

function initialsOf(name: string): string {
  return name
    .split(' ')
    .map((w) => w[0] ?? '')
    .join('')
    .slice(0, 2)
    .toUpperCase();
}

function seedOf(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/* ── Score helpers ──────────────────────────────────────────────────────────── */

function scoreToneClass(n: number | null): string {
  if (n === null) return 'text-[#70757c]';
  if (n >= 70) return 'text-[#27c93f]';
  if (n >= 45) return 'text-[#ffb764]';
  return 'text-[#e6714f]';
}

/* ── Badge helpers ──────────────────────────────────────────────────────────── */

type BadgeInfo = { label: string; tone: TagTone };

function recBadge(rec: string | null): BadgeInfo {
  switch (rec) {
    case 'strong_fit':
      return { label: 'Strong fit', tone: 'forest' };
    case 'weak_fit':
      return { label: 'Weak fit', tone: 'ember' };
    case 'moderate_fit':
      return { label: 'Moderate fit', tone: 'amber' };
    default:
      return { label: 'Unscored', tone: 'neutral' };
  }
}

function interviewBadge(s: string | null): BadgeInfo {
  switch (s) {
    case 'completed':
      return { label: 'Completed', tone: 'forest' };
    case 'consumed':
      return { label: 'In progress', tone: 'electric' };
    case 'revoked':
      return { label: 'Revoked', tone: 'ember' };
    case 'expired':
      return { label: 'Expired', tone: 'neutral' };
    case 'invited':
      return { label: 'Invited', tone: 'amber' };
    default:
      return { label: 'Not invited', tone: 'neutral' };
  }
}

function statusBadge(s: Row['status']): BadgeInfo | null {
  switch (s) {
    case 'hired':
      return { label: 'Hired', tone: 'forest' };
    case 'rejected':
      return { label: 'Rejected', tone: 'ember' };
    case 'interviewed':
      return { label: 'Interviewed', tone: 'amber' };
    default:
      return null;
  }
}

/* ── Stage tabs ─────────────────────────────────────────────────────────────── */

const STAGE_TABS: { value: PipelineStage; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'shortlisted', label: 'Shortlisted' },
  { value: 'exam_passed', label: 'Exam passed' },
  { value: 'interviewed', label: 'Interviewed' },
  { value: 'decided', label: 'Decided' },
];

/* ── Score display ──────────────────────────────────────────────────────────── */

function StageScore({
  value,
  tone,
  unit,
}: {
  value: number | string | null;
  tone: string;
  unit: string;
}) {
  return (
    <div className="w-12 shrink-0 text-center">
      <div className={cn('text-[15px] font-semibold leading-none', tone)}>{value ?? '—'}</div>
      <div className="text-[10px] text-[#70757c]">{unit}</div>
    </div>
  );
}

/* ── Single pipeline card ───────────────────────────────────────────────────── */

function PipelineCard({ a }: { a: Row }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [rationale, setRationale] = useState('');

  const decideMut = useMutation({
    mutationFn: (decision: ApplicantDecision) =>
      setApplicantDecision(a.applicant_id, decision, rationale),
    onSuccess: (_res, decision) => {
      toast.success(decision === 'hired' ? 'Applicant hired' : 'Applicant rejected');
      void qc.invalidateQueries({ queryKey: ['hr', 'pipeline'] });
      void qc.invalidateQueries({ queryKey: ['hr', 'analytics'] });
    },
    onError: (e: unknown) =>
      toast.error(e instanceof Error ? e.message : 'Decision failed'),
  });

  const rec = recBadge(a.ats_recommendation);
  const iv = interviewBadge(a.interview_status);
  const pill = statusBadge(a.status);

  const canHire = a.status === 'shortlisted' || a.status === 'interviewed';
  const canReject =
    a.status === 'new' || a.status === 'shortlisted' || a.status === 'interviewed';

  const seed = seedOf(a.applicant_id || a.full_name);

  return (
    <div
      className={cn(
        'rounded-[20px] border bg-[#0f0f10] transition-colors',
        open
          ? 'border-[rgba(var(--accent-rgb),0.3)]'
          : 'border-white/[0.08] hover:border-[rgba(var(--accent-rgb),0.2)]',
      )}
    >
      {/* ── Collapsed row ── */}
      <div className="flex items-center gap-3 p-3.5">
        {/* Avatar */}
        <Avatar
          initials={initialsOf(a.full_name)}
          gradient={gradientFor(seed)}
          size={36}
          aria-hidden="true"
        />

        {/* ATS score */}
        <StageScore
          value={a.ats_overall}
          tone={scoreToneClass(a.ats_overall)}
          unit="ATS"
        />

        {/* Name + badges */}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-[13.5px] font-medium text-white">{a.full_name}</p>
            <StatusTag tone={rec.tone} className="text-[11px]">
              {rec.label}
            </StatusTag>
            {a.status === 'shortlisted' && (
              <StatusTag tone="electric" className="gap-1 text-[11px]">
                <Star size={11} aria-hidden="true" /> Shortlisted
              </StatusTag>
            )}
            {pill && (
              <StatusTag tone={pill.tone} className="text-[11px]">
                {pill.label}
              </StatusTag>
            )}
          </div>
          <p className="mt-0.5 truncate text-[11px] text-[#70757c]">
            {a.target_job_title} &middot; {a.target_level}
          </p>
        </div>

        {/* Exam score */}
        <div className="hidden items-center gap-1 sm:flex">
          <StageScore
            value={a.best_exam_percent}
            tone={scoreToneClass(a.best_exam_percent)}
            unit="exam %"
          />
          {a.exam_passed === true && (
            <ClipboardCheck
              size={14}
              className="text-[#27c93f]"
              aria-label="Exam passed"
            />
          )}
        </div>

        {/* Interview score + status */}
        <div className="hidden items-center gap-1.5 md:flex">
          <StageScore
            value={a.interview_score !== null ? a.interview_score.toFixed(1) : null}
            tone={a.interview_score !== null ? 'text-[#27c93f]' : 'text-[#70757c]'}
            unit="/10"
          />
          <StatusTag tone={iv.tone} className="text-[11px]">
            {iv.label}
          </StatusTag>
        </div>

        {/* Decision quick buttons */}
        <div className="flex shrink-0 items-center gap-1">
          {canHire && (
            <button
              type="button"
              className="flex h-8 w-8 items-center justify-center rounded-[9px] text-[#27c93f] hover:bg-[rgba(39,201,63,0.14)] transition-colors disabled:opacity-40"
              disabled={decideMut.isPending}
              onClick={() => decideMut.mutate('hired')}
              aria-label="Hire"
            >
              {decideMut.isPending && decideMut.variables === 'hired' ? (
                <Loader2 size={16} className="animate-spin" aria-hidden="true" />
              ) : (
                <CheckCircle2 size={16} aria-hidden="true" />
              )}
            </button>
          )}
          {canReject && (
            <button
              type="button"
              className="flex h-8 w-8 items-center justify-center rounded-[9px] text-[#e6714f] hover:bg-[rgba(230,113,79,0.14)] transition-colors disabled:opacity-40"
              disabled={decideMut.isPending}
              onClick={() => decideMut.mutate('rejected')}
              aria-label="Reject"
            >
              <XCircle size={16} aria-hidden="true" />
            </button>
          )}
          <button
            type="button"
            className="flex h-8 w-8 items-center justify-center rounded-[9px] text-[#888b91] hover:bg-white/[0.06] transition-colors"
            onClick={() => setOpen((v) => !v)}
            aria-label={open ? 'Collapse details' : 'Expand details'}
            aria-expanded={open}
          >
            <ChevronDown
              size={16}
              className={cn('transition-transform duration-200', open && 'rotate-180')}
              aria-hidden="true"
            />
          </button>
        </div>
      </div>

      {/* ── Expanded detail panel ── */}
      {open && (
        <div className="space-y-4 border-t border-white/[0.07] px-4 py-4 text-[13px]">
          {/* Per-stage score grid */}
          <div className="grid gap-3 sm:grid-cols-3">
            {/* ATS */}
            <div className="rounded-[14px] border border-white/[0.08] bg-[rgba(28,29,31,0.5)] p-3">
              <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.5px] text-[#70757c]">
                ATS score
              </p>
              <div className="flex items-center gap-2">
                <span className={cn('text-[18px] font-semibold', scoreToneClass(a.ats_overall))}>
                  {a.ats_overall ?? '—'}
                  <span className="text-[11px] font-normal text-[#70757c]">/100</span>
                </span>
                <StatusTag tone={rec.tone} className="text-[10px]">
                  {rec.label}
                </StatusTag>
              </div>
            </div>

            {/* Exam */}
            <div className="rounded-[14px] border border-white/[0.08] bg-[rgba(28,29,31,0.5)] p-3">
              <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.5px] text-[#70757c]">
                Exam
              </p>
              <div className="flex items-center gap-2">
                <span
                  className={cn('text-[18px] font-semibold', scoreToneClass(a.best_exam_percent))}
                >
                  {a.best_exam_percent !== null ? `${a.best_exam_percent}%` : '—'}
                </span>
                {a.total_exam_attempts > 0 && (
                  <span className="text-[12px] text-[#70757c]">
                    {a.total_exam_attempts} attempt{a.total_exam_attempts === 1 ? '' : 's'} &middot;{' '}
                    {a.exam_passed ? 'passed' : 'not passed'}
                  </span>
                )}
                {a.total_exam_attempts === 0 && (
                  <span className="text-[12px] text-[#70757c]">no exam</span>
                )}
              </div>
            </div>

            {/* Interview */}
            <div className="rounded-[14px] border border-white/[0.08] bg-[rgba(28,29,31,0.5)] p-3">
              <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.5px] text-[#70757c]">
                Interview
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[18px] font-semibold text-white">
                  {a.interview_score !== null ? (
                    <>
                      {a.interview_score.toFixed(1)}
                      <span className="text-[11px] font-normal text-[#70757c]">/10</span>
                    </>
                  ) : (
                    <span className="text-[#70757c]">—</span>
                  )}
                </span>
                <StatusTag tone={iv.tone} className="text-[10px]">
                  {iv.label}
                </StatusTag>
                {a.scorecard_id && (
                  <Link to={`/scorecard/${a.scorecard_id}`}>
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 rounded-[8px] border border-white/[0.1] bg-transparent px-2 py-1 text-[11px] text-[#888b91] hover:text-white hover:border-white/[0.2] transition-colors"
                    >
                      <BarChart3 size={12} aria-hidden="true" /> Scorecard
                    </button>
                  </Link>
                )}
              </div>
            </div>
          </div>

          {/* Rationale + decision buttons (persisted — audit trail) */}
          {(canHire || canReject) && (
            <div className="rounded-[14px] border border-white/[0.08] bg-[rgba(28,29,31,0.4)] p-3.5">
              <label className="mb-2 block text-[12px] font-semibold text-white">
                Hire decision{' '}
                <span className="font-normal text-[#70757c]">
                  — rationale is optional but logged for audit
                </span>
              </label>
              <textarea
                className="min-h-[44px] w-full resize-y rounded-[10px] border border-white/[0.1] bg-[rgba(28,29,31,0.6)] px-3 py-2 text-[12.5px] text-white placeholder:text-[#5a5f66] focus:outline-none focus:border-[var(--accent)] transition-colors"
                placeholder="Why this decision? (optional)…"
                value={rationale}
                onChange={(e) => setRationale(e.target.value)}
                aria-label="Decision rationale"
              />
              <div className="mt-2.5 flex gap-2">
                {canHire && (
                  <button
                    type="button"
                    className="inline-flex items-center gap-1.5 rounded-[9999px] bg-white px-4 py-2 text-[13px] font-semibold text-black hover:bg-[#eaeaea] disabled:opacity-50 transition-colors"
                    disabled={decideMut.isPending}
                    onClick={() => decideMut.mutate('hired')}
                    aria-busy={decideMut.isPending && decideMut.variables === 'hired'}
                  >
                    {decideMut.isPending && decideMut.variables === 'hired' ? (
                      <Loader2 size={14} className="animate-spin" aria-hidden="true" />
                    ) : (
                      <CheckCircle2 size={14} aria-hidden="true" />
                    )}
                    Hire
                  </button>
                )}
                {canReject && (
                  <button
                    type="button"
                    className="inline-flex items-center gap-1.5 rounded-[9999px] bg-[rgba(230,113,79,0.14)] border border-[rgba(230,113,79,0.35)] px-4 py-2 text-[13px] font-semibold text-[#e6714f] hover:bg-[rgba(230,113,79,0.22)] disabled:opacity-50 transition-colors"
                    disabled={decideMut.isPending}
                    onClick={() => decideMut.mutate('rejected')}
                    aria-busy={decideMut.isPending && decideMut.variables === 'rejected'}
                  >
                    {decideMut.isPending && decideMut.variables === 'rejected' ? (
                      <Loader2 size={14} className="animate-spin" aria-hidden="true" />
                    ) : (
                      <XCircle size={14} aria-hidden="true" />
                    )}
                    Reject
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Page ───────────────────────────────────────────────────────────────────── */

export default function HRPipeline() {
  const [stage, setStage] = useState<PipelineStage>('all');
  const [offset, setOffset] = useState(0);

  const { data, isLoading } = useQuery({
    queryKey: ['hr', 'pipeline', stage, offset],
    queryFn: () => getPipeline({ stage, limit: PAGE_SIZE, offset }),
  });

  const items = data?.items ?? [];
  const count = data?.count ?? 0;

  function pickStage(s: PipelineStage) {
    setStage(s);
    setOffset(0);
  }

  const fromRow = count === 0 ? 0 : offset + 1;
  const toRow = Math.min(offset + PAGE_SIZE, count);

  return (
    <div className="mx-auto max-w-[1400px] px-6 py-8 lg:px-8 space-y-8">
      {/* ── Page header ── */}
      <Reveal>
        <div className="flex items-center gap-3">
          <span className="inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-[rgba(var(--accent-rgb),0.14)] text-[#60a5fa]">
            <TrendingUp size={20} aria-hidden="true" />
          </span>
          <div>
            <h1 className="text-[28px] font-semibold tracking-[-1px] text-white">
              Hiring pipeline
            </h1>
            <p className="mt-1 text-[14px] text-[#888b91]">
              Every candidate, end to end — resume &rarr; exam &rarr; interview &rarr; decision.
            </p>
          </div>
        </div>
      </Reveal>

      {/* ── Embedded analytics panel ── */}
      <HRAnalytics />

      {/* ── Stage filter tabs ── */}
      <Reveal delay={0.05}>
        <SegTabs
          tabs={STAGE_TABS.map((t) => ({ key: t.value, label: t.label }))}
          active={stage}
          onChange={(k) => pickStage(k as PipelineStage)}
        />
      </Reveal>

      {/* ── Candidates list ── */}
      <section aria-label="Candidates">
        <Reveal delay={0.08}>
          <h2 className="mb-4 text-[14px] font-semibold text-white">
            Candidates{count > 0 ? ` (${count})` : ''}
          </h2>
        </Reveal>

        {isLoading ? (
          <div className="space-y-3" aria-busy="true" aria-label="Loading candidates">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-[68px] w-full rounded-[20px] bg-white/[0.05] animate-pulse" />
            ))}
          </div>
        ) : items.length === 0 ? (
          <GlassCard className="py-14 text-center">
            <p className="text-[14px] text-[#888b91]">
              {stage === 'all'
                ? 'No candidates yet — screen resumes to start the pipeline.'
                : `No candidates at the "${STAGE_TABS.find((t) => t.value === stage)?.label ?? stage}" stage.`}
            </p>
          </GlassCard>
        ) : (
          <>
            <Stagger className="flex flex-col gap-2.5">
              {items.map((a) => (
                <StaggerItem key={a.applicant_id}>
                  <PipelineCard a={a} />
                </StaggerItem>
              ))}
            </Stagger>

            {/* Pagination */}
            {count > PAGE_SIZE && (
              <div className="mt-5 flex items-center justify-between">
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 rounded-[9999px] border border-white/[0.1] bg-transparent px-4 py-2 text-[13px] text-[#888b91] hover:text-white hover:border-white/[0.2] disabled:opacity-40 transition-colors"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                  aria-label="Previous page"
                >
                  Previous
                </button>
                <span className="text-[12px] text-[#70757c]" aria-live="polite">
                  {fromRow}–{toRow} of {count}
                </span>
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 rounded-[9999px] border border-white/[0.1] bg-transparent px-4 py-2 text-[13px] text-[#888b91] hover:text-white hover:border-white/[0.2] disabled:opacity-40 transition-colors"
                  disabled={offset + PAGE_SIZE >= count}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                  aria-label="Next page"
                >
                  Next
                </button>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
