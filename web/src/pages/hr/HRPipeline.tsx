// HRPipeline — single-table hiring funnel: resume → exam → interview → decision.
// Layout: design screen HRPipeline.tsx card visual styling (GlassCard, StatusTag, Avatar).
// Behavior: all live logic — server-paginated TABLE, persisted setApplicantDecision
//           (hired/rejected + rationale), canHire/canReject gating, embedded analytics.
// NOTE: design's drag-drop Kanban is NOT used — no stage-transition API endpoint exists.

import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Users,
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
import { DecisionReasonSelect } from '@/components/hr/DecisionReasonSelect';
import { MIN_REASON_EXPLAINED, useDecisionReasons } from '@/lib/decisionReasons';
import { toast } from '@/lib/toast';
import CandidateDrawer from '@/components/CandidateDrawer';
import { applicationKey } from '@/lib/applicationKey';
import { offerOutcomeTag } from '@/lib/offerOutcome';
import PipelineBoard from '@/components/PipelineBoard';
import { cn } from '@/lib/utils';
import { StatusTag, Avatar, SegTabs, type TagTone } from '@/design/components/primitives';
import { PageHeader, EmptyState, Toolbar, Page } from '@/design/components/page';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import HRAnalytics from './HRAnalytics';
import { LIVE_POLL_MS } from '../../lib/polling';

const PAGE_SIZE = 50;

function initialsOf(name: string): string {
  return name
    .split(' ')
    .map((w) => w[0] ?? '')
    .join('')
    .slice(0, 2)
    .toUpperCase();
}

/* ── Score helpers ──────────────────────────────────────────────────────────── */

function scoreToneClass(n: number | null): string {
  if (n === null) return 'text-muted-foreground';
  if (n >= 70) return 'text-[var(--ui-ok)]';
  if (n >= 45) return 'text-[var(--ui-warn)]';
  return 'text-[var(--ui-danger)]';
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

const VIEW_KEY = 'intants:pipeline-view';

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
      <div className="text-[12px] text-muted-foreground">{unit}</div>
    </div>
  );
}

/* ── Single pipeline card ───────────────────────────────────────────────────── */

function PipelineCard({ a, onOpen }: { a: Row; onOpen: () => void }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [rationale, setRationale] = useState('');

  const [reasonCode, setReasonCode] = useState('');
  const reasonsQuery = useDecisionReasons();
  // Both outcomes are offered from the same control here (there is no
  // separate "which decision am I about to make" step, unlike the decision
  // queue) — so every active reason is shown rather than filtering by
  // applies_to. Unlike the decision queue, the free-text rationale here is
  // OPTIONAL unless the chosen reason itself demands an explanation — that
  // policy is this screen's own, so it is not folded into minReasonLength.
  const chosenReason = (reasonsQuery.data ?? []).find((r) => r.code === reasonCode) ?? null;
  const rationaleReady =
    !chosenReason?.requires_explanation || rationale.trim().length >= MIN_REASON_EXPLAINED;
  // O4: a reason code is now required for every hire/reject write.
  const canDecide = Boolean(reasonCode) && rationaleReady;

  const decideMut = useMutation({
    mutationFn: (decision: ApplicantDecision) =>
      setApplicantDecision(a.applicant_id, decision, reasonCode, rationale, a.enrolment_id),
    onSuccess: (_res, decision) => {
      toast.success(decision === 'hired' ? 'Applicant hired' : 'Applicant rejected');
      setReasonCode('');
      setRationale('');
      void qc.invalidateQueries({ queryKey: ['hr', 'pipeline'] });
      void qc.invalidateQueries({ queryKey: ['hr', 'analytics'] });
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Decision failed'),
  });

  const rec = recBadge(a.ats_recommendation);
  const iv = interviewBadge(a.interview_status);
  const pill = statusBadge(a.status);
  const offerTag = offerOutcomeTag(a.offer_outcome);

  const canHire = a.status === 'shortlisted' || a.status === 'interviewed';
  const canReject = a.status === 'new' || a.status === 'shortlisted' || a.status === 'interviewed';

  return (
    <div
      className={cn(
        'rounded-card border bg-card shadow-card transition-colors',
        open
          ? 'border-[var(--ui-line-strong)]'
          : 'border-border hover:border-[var(--ui-line-strong)]',
      )}
    >
      {/* ── Collapsed row ── */}
      <div className="flex items-center gap-3 p-3.5">
        {/* Avatar */}
        <Avatar initials={initialsOf(a.full_name)} size={36} />

        {/* ATS score */}
        <StageScore value={a.ats_overall} tone={scoreToneClass(a.ats_overall)} unit="ATS" />

        {/* Name + badges */}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-[14px] font-semibold text-foreground">{a.full_name}</p>
            <StatusTag tone={rec.tone}>{rec.label}</StatusTag>
            {a.status === 'shortlisted' && (
              <StatusTag tone="electric" className="gap-1">
                <Star size={12} aria-hidden="true" /> Shortlisted
              </StatusTag>
            )}
            {pill && <StatusTag tone={pill.tone}>{pill.label}</StatusTag>}
            {offerTag && <StatusTag tone={offerTag.tone}>{offerTag.label}</StatusTag>}
          </div>
          <p className="mt-0.5 truncate text-[13px] text-muted-foreground">
            {a.opening_title ?? a.target_job_title} &middot; {a.target_level}
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
            <ClipboardCheck size={14} className="text-[var(--ui-ok)]" aria-label="Exam passed" />
          )}
        </div>

        {/* Interview score + status */}
        <div className="hidden items-center gap-1.5 md:flex">
          <StageScore
            value={a.interview_score !== null ? a.interview_score.toFixed(1) : null}
            tone={a.interview_score !== null ? 'text-[var(--ui-ok)]' : 'text-muted-foreground'}
            unit="/10"
          />
          <StatusTag tone={iv.tone}>{iv.label}</StatusTag>
        </div>

        {/* A hire/reject decision now always needs a reason code (O4), so the
            one-click quick actions that used to live here are gone — Expand
            details is the one way in, and the reason + rationale + Hire/Reject
            controls live in the panel below. */}
        <div className="flex shrink-0 items-center gap-1">
          {/* Opens the drawer rather than expanding in place: the full record is
              more than a row can hold, and it is a read — a recruiter looking
              somebody up should not lose their place in the list. */}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onOpen}
            aria-label={`Open details for ${a.full_name}`}
          >
            Details
          </Button>
          <button
            type="button"
            className="flex h-10 w-10 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-[var(--ui-inset)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
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
        <div className="space-y-4 border-t border-border px-4 py-4 text-[13px]">
          {/* Per-stage score grid */}
          <div className="grid gap-3 sm:grid-cols-3">
            {/* ATS */}
            <div className="rounded-[16px] border border-border bg-[var(--ui-inset-soft)] p-3.5">
              <p className="mb-1.5 text-[12px] font-semibold text-muted-foreground">ATS score</p>
              <div className="flex items-center gap-2">
                <span className={cn('text-[18px] font-bold', scoreToneClass(a.ats_overall))}>
                  {a.ats_overall ?? '—'}
                  <span className="text-[12px] font-normal text-muted-foreground">/100</span>
                </span>
                <StatusTag tone={rec.tone}>{rec.label}</StatusTag>
              </div>
            </div>

            {/* Exam */}
            <div className="rounded-[16px] border border-border bg-[var(--ui-inset-soft)] p-3.5">
              <p className="mb-1.5 text-[12px] font-semibold text-muted-foreground">Exam</p>
              <div className="flex items-center gap-2">
                <span className={cn('text-[18px] font-bold', scoreToneClass(a.best_exam_percent))}>
                  {a.best_exam_percent !== null ? `${a.best_exam_percent}%` : '—'}
                </span>
                {a.total_exam_attempts > 0 && (
                  <span className="text-[13px] text-muted-foreground">
                    {a.total_exam_attempts} attempt{a.total_exam_attempts === 1 ? '' : 's'} &middot;{' '}
                    {a.exam_passed ? 'passed' : 'not passed'}
                  </span>
                )}
                {a.total_exam_attempts === 0 && (
                  <span className="text-[13px] text-muted-foreground">no exam</span>
                )}
              </div>
            </div>

            {/* Interview */}
            <div className="rounded-[16px] border border-border bg-[var(--ui-inset-soft)] p-3.5">
              <p className="mb-1.5 text-[12px] font-semibold text-muted-foreground">Interview</p>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[18px] font-bold text-foreground">
                  {a.interview_score !== null ? (
                    <>
                      {a.interview_score.toFixed(1)}
                      <span className="text-[12px] font-normal text-muted-foreground">/10</span>
                    </>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </span>
                <StatusTag tone={iv.tone}>{iv.label}</StatusTag>
                {a.scorecard_id && (
                  <Button asChild variant="outline" size="sm">
                    <Link to={`/scorecard/${a.scorecard_id}`}>
                      <BarChart3 aria-hidden="true" /> Scorecard
                    </Link>
                  </Button>
                )}
              </div>
            </div>
          </div>

          {/* Reason + rationale + decision buttons (persisted — audit trail) */}
          {(canHire || canReject) && (
            <div className="rounded-[16px] border border-border bg-[var(--ui-inset-soft)] p-4">
              <DecisionReasonSelect
                id={`reason-code-${a.applicant_id}`}
                reasons={reasonsQuery.data ?? []}
                value={reasonCode}
                onChange={setReasonCode}
              />

              <label className="mb-2 mt-3 block text-[13px] font-semibold text-foreground">
                Hire decision{' '}
                <span className="font-normal text-muted-foreground">
                  {chosenReason?.requires_explanation
                    ? '— this reason needs at least 10 characters of explanation'
                    : '— rationale is optional but logged for audit'}
                </span>
              </label>
              <textarea
                className="min-h-[44px] w-full resize-y rounded-[12px] border border-input bg-card px-3.5 py-2.5 text-[14px] text-foreground placeholder:text-[var(--ui-faint)] transition-colors focus-visible:border-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]/30"
                placeholder="Why this decision? (optional)…"
                value={rationale}
                onChange={(e) => setRationale(e.target.value)}
                aria-label="Decision rationale"
              />
              <div className="mt-2.5 flex flex-wrap items-center gap-2">
                {canHire && (
                  <Button
                    type="button"
                    disabled={decideMut.isPending || !canDecide}
                    onClick={() => decideMut.mutate('hired')}
                    aria-busy={decideMut.isPending && decideMut.variables === 'hired'}
                  >
                    {decideMut.isPending && decideMut.variables === 'hired' ? (
                      <Loader2 className="animate-spin" aria-hidden="true" />
                    ) : (
                      <CheckCircle2 aria-hidden="true" />
                    )}
                    Hire
                  </Button>
                )}
                {canReject && (
                  <Button
                    type="button"
                    variant="outline"
                    className="border-[var(--ui-danger)]/40 text-[var(--ui-danger)] hover:bg-[var(--ui-danger-wash)] hover:text-[var(--ui-danger)]"
                    disabled={decideMut.isPending || !canDecide}
                    onClick={() => decideMut.mutate('rejected')}
                    aria-busy={decideMut.isPending && decideMut.variables === 'rejected'}
                  >
                    {decideMut.isPending && decideMut.variables === 'rejected' ? (
                      <Loader2 className="animate-spin" aria-hidden="true" />
                    ) : (
                      <XCircle aria-hidden="true" />
                    )}
                    Reject
                  </Button>
                )}
                {!canDecide ? (
                  <span className="text-[13px] text-[var(--ui-warn)]">
                    {!reasonCode
                      ? 'Choose a reason first.'
                      : 'Write at least 10 characters explaining why.'}
                  </span>
                ) : null}
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
  // Which candidate's drawer is open. Null is closed — one piece of state
  // rather than a boolean plus an id that can disagree with it.
  // The row opened: its application as well as the person, so the drawer can
  // show that application's answers and history (B5).
  const [drawer, setDrawer] = useState<Row | null>(null);

  // Remembered, because re-picking a view on every visit is the kind of small
  // friction that makes one feel like a toy. Wrapped: a browser with site data
  // blocked throws on read, and a preference is not worth a blank page.
  const [view, setView] = useState<'list' | 'board'>(() => {
    try {
      return localStorage.getItem(VIEW_KEY) === 'board' ? 'board' : 'list';
    } catch {
      return 'list';
    }
  });

  function pickView(next: 'list' | 'board'): void {
    setView(next);
    try {
      localStorage.setItem(VIEW_KEY, next);
    } catch {
      // A remembered preference is a convenience, not a feature to fail over.
    }
  }

  const { data, isLoading } = useQuery({
    queryKey: ['hr', 'pipeline', stage, offset],
    // A5: candidates advance between stages unattended.
    refetchInterval: LIVE_POLL_MS,
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
    <Page width="workspace">
      <PageHeader
        title="Hiring pipeline"
        description="Every candidate, end to end — resume → exam → interview → decision."
      />

      {/* ── Candidates: filters in one toolbar row, then the list ── */}
      <section aria-label="Candidates" className="space-y-4">
        <Toolbar
          className="mb-0"
          trailing={
            <>
              <h2 className="text-[13px] font-semibold tabular-nums text-muted-foreground">
                Candidates{count > 0 ? ` (${count})` : ''}
              </h2>
              {/* Same rows, same query — an arrangement, not a different dataset. */}
              <div
                role="group"
                aria-label="Pipeline view"
                className="inline-flex rounded-full border border-input bg-card p-0.5"
              >
                {(['list', 'board'] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    onClick={() => pickView(v)}
                    aria-pressed={view === v}
                    className={cn(
                      'min-h-9 rounded-full px-4 text-[13px] font-semibold capitalize focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
                      view === v
                        ? 'bg-foreground text-background'
                        : 'text-muted-foreground hover:text-foreground',
                    )}
                  >
                    {v}
                  </button>
                ))}
              </div>
            </>
          }
        >
          <div className="max-w-full overflow-x-auto">
            <SegTabs
              tabs={STAGE_TABS.map((t) => ({ key: t.value, label: t.label }))}
              active={stage}
              onChange={(k) => pickStage(k as PipelineStage)}
            />
          </div>
        </Toolbar>

        {isLoading ? (
          <div className="space-y-3" aria-busy="true" aria-label="Loading candidates">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-[68px] w-full rounded-card" />
            ))}
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            size="page"
            icon={Users}
            title={
              stage === 'all'
                ? 'No candidates yet — screen resumes to start the pipeline.'
                : `No candidates at the "${STAGE_TABS.find((t) => t.value === stage)?.label ?? stage}" stage.`
            }
          />
        ) : (
          <>
            {view === 'board' ? (
              <PipelineBoard rows={items} onOpen={(row) => setDrawer(row)} />
            ) : (
              <div className="flex flex-col gap-2.5">
                {items.map((a) => (
                  <PipelineCard key={applicationKey(a)} a={a} onOpen={() => setDrawer(a)} />
                ))}
              </div>
            )}

            {/* Pagination */}
            {count > PAGE_SIZE && (
              <div className="flex items-center justify-between pt-1">
                <Button
                  type="button"
                  variant="outline"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                  aria-label="Previous page"
                >
                  Previous
                </Button>
                <span className="text-[13px] text-muted-foreground" aria-live="polite">
                  {fromRow}–{toRow} of {count}
                </span>
                <Button
                  type="button"
                  variant="outline"
                  disabled={offset + PAGE_SIZE >= count}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                  aria-label="Next page"
                >
                  Next
                </Button>
              </div>
            )}
          </>
        )}
      </section>

      {/* ── Funnel analytics, below the people it summarises ── */}
      <div>
        <HRAnalytics />
      </div>

      <CandidateDrawer
        applicantId={drawer?.applicant_id ?? null}
        enrolmentId={drawer?.enrolment_id ?? null}
        onClose={() => setDrawer(null)}
      />
    </Page>
  );
}
