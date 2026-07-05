// ExamResults — graded attempts for one exam (HR workflow Phase 2).
// Layout: faithfully reproduces anterview-pages/src/screens/hr/ExamResults.tsx
//   (back link, title + exam-id, StatCards strip, table with candidate/score/
//    duration/status/action columns, export CSV button).
// Behavior: getExam + listAttempts queries, score_percent tone, score_raw/max,
//   attempt_no, submitted_at, in_progress/passed/failed logic, pass-threshold
//   summary, loading + empty states, correct back-to-exam route.
//   Client-side CSV export from loaded attempts (no fake endpoint).
//   No flagged/proctoring field (no backend field).

import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getExam, listAttempts, type AttemptResult } from '@/api/exams';
import { Reveal, Stagger, StaggerItem } from '@/design/components/Reveal';
import { GlassCard, StatCard, StatusTag, Avatar, Pill } from '@/design/components/primitives';
import {
  ArrowLeft,
  Download,
  Eye,
  CheckCircle2,
  XCircle,
  Clock,
} from '@/design/components/icons';

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Map 0-100 score to design spec colour. */
function scoreColor(p: number | null): string {
  if (p === null) return '#888b91';
  if (p >= 70) return '#27c93f';
  if (p >= 45) return '#ffb764';
  return '#e6714f';
}

/** Deterministic gradient from a short string seed. */
function gradientFor(seed: string): string {
  const hues = [200, 260, 170, 30, 340, 310, 140];
  const h = hues[seed.charCodeAt(0) % hues.length];
  return `linear-gradient(135deg,hsl(${h},80%,55%),hsl(${(h + 60) % 360},70%,45%))`;
}

function initialsOf(name: string): string {
  return name
    .split(' ')
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? '')
    .join('');
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}

function formatDuration(submittedAt: string | null): string {
  if (!submittedAt) return '—';
  return formatDate(submittedAt);
}

// ── Client-side CSV export ────────────────────────────────────────────────────
function exportCsv(attempts: AttemptResult[], examTitle: string): void {
  const header = ['Candidate', 'Attempt #', 'Score %', 'Raw / Max', 'Status', 'Submitted At'];
  const rows = attempts.map((a) => [
    `"${a.applicant_name.replace(/"/g, '""')}"`,
    String(a.attempt_no),
    a.score_percent !== null ? String(a.score_percent) : '',
    a.score_raw !== null && a.score_max !== null ? `${a.score_raw}/${a.score_max}` : '',
    a.status === 'in_progress' ? 'In Progress' : a.passed ? 'Passed' : 'Failed',
    a.submitted_at ? formatDate(a.submitted_at) : '',
  ]);
  const csv = [header.join(','), ...rows.map((r) => r.join(','))].join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${examTitle.replace(/\s+/g, '_')}_results.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default function ExamResults() {
  const { examId = '' } = useParams<{ examId: string }>();

  const { data: exam } = useQuery({
    queryKey: ['hr', 'exam', examId],
    queryFn: () => getExam(examId),
  });

  const { data: attempts, isLoading } = useQuery({
    queryKey: ['hr', 'exam', examId, 'attempts'],
    queryFn: () => listAttempts(examId),
  });

  const list = attempts ?? [];
  const completedList = list.filter((a) => a.score_percent !== null);
  const passedCount = list.filter((a) => a.passed).length;
  const avgScore =
    completedList.length > 0
      ? Math.round(
          completedList.reduce((sum, a) => sum + (a.score_percent ?? 0), 0) /
            completedList.length,
        )
      : 0;
  const topScore =
    completedList.length > 0
      ? Math.max(...completedList.map((a) => a.score_percent ?? 0))
      : 0;

  return (
    <div className="mx-auto max-w-[1080px] px-6 py-8 lg:px-8">
      {/* ── Back link ── */}
      <Link
        to={`/hr/exams/${examId}`}
        className="inline-flex items-center gap-1.5 text-[13px] text-[#888b91] hover:text-white"
      >
        <ArrowLeft size={15} aria-hidden="true" /> Back to exams
      </Link>

      {/* ── Title bar ── */}
      <Reveal>
        <div className="mt-4 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-[28px] font-semibold tracking-[-1px]">
              {exam?.title ?? 'Exam'} · Results
            </h1>
            <p className="mt-1 font-mono text-[13.5px] text-[#888b91]">
              exam #{examId} · {list.length} attempt{list.length !== 1 ? 's' : ''} ·{' '}
              {passedCount} passed · pass ≥ {exam?.pass_threshold ?? '—'}%
            </p>
          </div>
          {list.length > 0 && (
            <Pill
              variant="ghost"
              className="px-4 py-2.5"
              onClick={() => exportCsv(list, exam?.title ?? 'exam')}
              aria-label="Export results as CSV"
            >
              <Download size={15} aria-hidden="true" /> Export CSV
            </Pill>
          )}
        </div>
      </Reveal>

      {/* ── Stat strip ── */}
      {!isLoading && list.length > 0 && (
        <Reveal delay={0.06}>
          <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <StatCard
              label="Attempts"
              value={String(list.length)}
              delta="all-time"
              trend="flat"
            />
            <StatCard
              label="Average score"
              value={`${avgScore}%`}
              delta={`${passedCount} passed`}
              trend="up"
              feature
            />
            <StatCard
              label="Top score"
              value={`${topScore}%`}
              delta="best attempt"
              trend="flat"
            />
            <StatCard
              label="Pass rate"
              value={
                list.length > 0
                  ? `${Math.round((passedCount / list.length) * 100)}%`
                  : '—'
              }
              delta={`≥ ${exam?.pass_threshold ?? '—'}% threshold`}
              trend={passedCount > 0 ? 'up' : 'flat'}
            />
          </div>
        </Reveal>
      )}

      {/* ── Results table ── */}
      <Reveal delay={0.1}>
        <GlassCard className="mt-5 overflow-hidden p-0">
          {/* Table header */}
          <div className="grid grid-cols-[2fr_1fr_1fr_1fr_0.8fr] gap-3 border-b border-white/[0.06] px-6 py-3.5 text-[11.5px] uppercase tracking-[0.5px] text-[#70757c]">
            <div>Candidate</div>
            <div>Score</div>
            <div>Submitted</div>
            <div>Status</div>
            <div>Action</div>
          </div>

          {/* Loading state */}
          {isLoading && (
            <div className="space-y-0">
              {[0, 1, 2].map((i) => (
                <div
                  key={i}
                  className="h-[56px] animate-pulse border-b border-white/[0.04] bg-white/[0.02] last:border-0"
                />
              ))}
            </div>
          )}

          {/* Empty state */}
          {!isLoading && list.length === 0 && (
            <div className="py-12 text-center">
              <p className="text-[13px] text-[#888b91]">
                No attempts yet — assign the exam and share the links.
              </p>
            </div>
          )}

          {/* Rows */}
          {!isLoading && list.length > 0 && (
            <Stagger className="flex flex-col">
              {list.map((a) => (
                <StaggerItem key={a.attempt_id}>
                  <AttemptRow a={a} examId={examId} />
                </StaggerItem>
              ))}
            </Stagger>
          )}
        </GlassCard>
      </Reveal>
    </div>
  );
}

// ── Attempt row ───────────────────────────────────────────────────────────────
function AttemptRow({ a, examId }: { a: AttemptResult; examId: string }) {
  const inProgress = a.status === 'in_progress' || a.submitted_at === null;

  return (
    <div className="grid grid-cols-[2fr_1fr_1fr_1fr_0.8fr] items-center gap-3 border-b border-white/[0.04] px-6 py-3.5 last:border-0">
      {/* Candidate */}
      <div className="flex items-center gap-3">
        <Avatar
          initials={initialsOf(a.applicant_name)}
          gradient={gradientFor(a.applicant_id)}
          size={34}
        />
        <span className="truncate text-[13.5px] font-medium">{a.applicant_name}</span>
      </div>

      {/* Score */}
      <div
        className="text-[15px] font-semibold"
        style={{ color: scoreColor(a.score_percent) }}
      >
        {a.score_percent !== null ? `${a.score_percent}%` : '—'}
      </div>

      {/* Submitted */}
      <div className="font-mono text-[13px] text-[#888b91]">
        {a.submitted_at ? formatDuration(a.submitted_at) : '—'}
      </div>

      {/* Status tag */}
      <div>
        {inProgress ? (
          <StatusTag tone="electric">
            <Clock size={11} aria-hidden="true" /> In progress
          </StatusTag>
        ) : a.passed ? (
          <StatusTag tone="forest" dot>
            <CheckCircle2 size={11} aria-hidden="true" /> Passed
          </StatusTag>
        ) : (
          <StatusTag tone="ember">
            <XCircle size={11} aria-hidden="true" /> Failed
          </StatusTag>
        )}
      </div>

      {/* Action */}
      <div>
        <Link to={`/hr/exams/${examId}/attempts/${a.attempt_id}`}>
          <Pill variant="ghost" className="py-1.5 text-[12px]">
            <Eye size={13} aria-hidden="true" /> View
          </Pill>
        </Link>
      </div>
    </div>
  );
}

