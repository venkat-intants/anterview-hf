// ExamResults — graded attempts for one exam (HR workflow Phase 2).
// Layout: faithfully reproduces anterview-pages/src/screens/hr/ExamResults.tsx
//   (back link, title + exam-id, StatCards strip, table with candidate/score/
//    duration/status/action columns, export CSV button).
// Behavior: getExam + listAttempts queries, score_percent tone, score_raw/max,
//   attempt_no, submitted_at, in_progress/passed/failed logic, pass-threshold
//   summary, loading + empty states, correct back-to-exam route.
//   Client-side CSV export from loaded attempts (no fake endpoint).
//   No flagged/proctoring field (no backend field).

import { useMemo } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getExam, listAttempts, type AttemptResult } from '@/api/exams';
import { listSimilaritySignals } from '@/api/codeEvidence';
import { PageHeader, Breadcrumbs, Page } from '@/design/components/page';
import { cn } from '@/lib/utils';
import { csvRow } from '@/lib/csv';
import { GlassCard, StatCard, StatusTag, Avatar, Pill } from '@/design/components/primitives';
import { Download, Eye, CheckCircle2, XCircle, Clock } from '@/design/components/icons';
import { ACTIVE_POLL_MS } from '../../lib/polling';

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Map 0-100 score to design spec colour. */
function scoreColor(p: number | null): string {
  if (p === null) return 'var(--ui-faint)';
  if (p >= 70) return 'var(--ui-ok)';
  if (p >= 45) return 'var(--ui-warn)';
  return 'var(--ui-danger)';
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
  // csvRow, not hand-quoting: the name comes from the unauthenticated apply
  // form, and quoting alone does not stop a spreadsheet evaluating a cell that
  // starts with = + - @ (see lib/csv.ts).
  const rows = attempts.map((a) => [
    a.applicant_name,
    a.attempt_no,
    a.score_percent,
    a.score_raw !== null && a.score_max !== null ? `${a.score_raw}/${a.score_max}` : '',
    a.status === 'in_progress' ? 'In Progress' : a.passed ? 'Passed' : 'Failed',
    a.submitted_at ? formatDate(a.submitted_at) : '',
  ]);
  const csv = [csvRow(header), ...rows.map(csvRow)].join('\n');
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
    // A5: attempts land while the manager watches.
    refetchInterval: ACTIVE_POLL_MS,
    queryFn: () => listAttempts(examId),
  });

  // PH4-D3 — counts only, never the content: the compare view (matched
  // regions, excerpts) lives one click away on the attempt itself.
  const showSimilarity = exam?.kind === 'coding';
  const { data: signals } = useQuery({
    queryKey: ['hr', 'exam', examId, 'similarity'],
    queryFn: () => listSimilaritySignals(examId),
    enabled: Boolean(examId) && showSimilarity,
  });
  const similarityByAttempt = useMemo(() => {
    const m = new Map<string, number>();
    for (const s of signals ?? []) {
      m.set(s.attempt_low_id, (m.get(s.attempt_low_id) ?? 0) + 1);
      if (s.attempt_high_id) m.set(s.attempt_high_id, (m.get(s.attempt_high_id) ?? 0) + 1);
    }
    return m;
  }, [signals]);

  const list = attempts ?? [];
  const completedList = list.filter((a) => a.score_percent !== null);
  const passedCount = list.filter((a) => a.passed).length;
  const avgScore =
    completedList.length > 0
      ? Math.round(
          completedList.reduce((sum, a) => sum + (a.score_percent ?? 0), 0) / completedList.length,
        )
      : 0;
  const topScore =
    completedList.length > 0 ? Math.max(...completedList.map((a) => a.score_percent ?? 0)) : 0;

  return (
    <Page width="default">
      <PageHeader
        breadcrumbs={
          <Breadcrumbs
            items={[
              { label: 'Exams', to: '/hr/exams' },
              { label: exam?.title ?? 'Exam', to: `/hr/exams/${examId}` },
              { label: 'Results' },
            ]}
          />
        }
        title={`${exam?.title ?? 'Exam'} · Results`}
        description={
          <span className="break-words font-mono text-[13px]">
            exam #{examId} · {list.length} attempt{list.length !== 1 ? 's' : ''} · {passedCount}{' '}
            passed · pass ≥ {exam?.pass_threshold ?? '—'}%
          </span>
        }
        actions={
          list.length > 0 ? (
            <Pill
              variant="outline"
              className="px-4 py-2.5"
              onClick={() => exportCsv(list, exam?.title ?? 'exam')}
              aria-label="Export results as CSV"
            >
              <Download size={15} aria-hidden="true" /> Export CSV
            </Pill>
          ) : undefined
        }
      />

      {/* ── Summary KPI row ── */}
      {!isLoading && list.length > 0 && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatCard label="Attempts" value={String(list.length)} delta="all-time" trend="flat" />
          <StatCard
            label="Average score"
            value={`${avgScore}%`}
            delta={`${passedCount} passed`}
            trend="up"
          />
          <StatCard label="Top score" value={`${topScore}%`} delta="best attempt" trend="flat" />
          <StatCard
            label="Pass rate"
            value={list.length > 0 ? `${Math.round((passedCount / list.length) * 100)}%` : '—'}
            delta={`≥ ${exam?.pass_threshold ?? '—'}% threshold`}
            trend={passedCount > 0 ? 'up' : 'flat'}
          />
        </div>
      )}

      {/* ── Results table ── */}
      {showSimilarity && (
        <p className="text-[13px] leading-relaxed text-muted-foreground">
          Similarity signals are automated and unreviewed — similar code is not evidence of
          misconduct on its own. See an attempt&rsquo;s own Code evidence tab for detail.
        </p>
      )}
      <GlassCard className="overflow-hidden p-0">
        {/* The scroll container is what makes the header sticky: it scrolls
            the rows under a header that stays put, and scrolls sideways on a
            narrow screen instead of squeezing the columns. */}
        <div className="max-h-[70vh] overflow-auto">
          <table className="w-full min-w-[760px] border-collapse text-left">
            <caption className="sr-only">Graded attempts for this exam</caption>
            <thead>
              <tr className="text-[12px] font-semibold uppercase tracking-[0.5px] text-muted-foreground">
                <th scope="col" className={TH}>
                  Candidate
                </th>
                <th scope="col" className={cn(TH, 'text-right')}>
                  Score
                </th>
                <th scope="col" className={TH}>
                  Submitted
                </th>
                <th scope="col" className={TH}>
                  Status
                </th>
                {showSimilarity && (
                  <th scope="col" className={cn(TH, 'text-right')}>
                    Similarity signals
                  </th>
                )}
                <th scope="col" className={cn(TH, 'text-right')}>
                  Action
                </th>
              </tr>
            </thead>
            <tbody>
              {/* Loading state */}
              {isLoading &&
                [0, 1, 2].map((i) => (
                  <tr key={i} aria-hidden="true">
                    <td colSpan={showSimilarity ? 6 : 5} className="border-b border-border p-0">
                      <div className="h-[56px] animate-pulse bg-[var(--ui-inset-soft)]" />
                    </td>
                  </tr>
                ))}

              {/* Empty state */}
              {!isLoading && list.length === 0 && (
                <tr>
                  <td
                    colSpan={showSimilarity ? 6 : 5}
                    className="px-6 py-12 text-center text-[14px] text-muted-foreground"
                  >
                    No attempts yet — assign the exam and share the links.
                  </td>
                </tr>
              )}

              {/* Rows */}
              {!isLoading &&
                list.map((a) => (
                  <AttemptRow
                    key={a.attempt_id}
                    a={a}
                    examId={examId}
                    showSimilarity={showSimilarity}
                    similarityCount={similarityByAttempt.get(a.attempt_id) ?? 0}
                  />
                ))}
            </tbody>
          </table>
        </div>
      </GlassCard>
    </Page>
  );
}

const TH = 'sticky top-0 z-[1] whitespace-nowrap border-b border-border bg-card px-6 py-3.5';
const TD = 'border-b border-border px-6 py-3.5 align-middle group-last:border-0';

// ── Attempt row ───────────────────────────────────────────────────────────────
function AttemptRow({
  a,
  examId,
  showSimilarity,
  similarityCount,
}: {
  a: AttemptResult;
  examId: string;
  showSimilarity: boolean;
  similarityCount: number;
}) {
  const inProgress = a.status === 'in_progress' || a.submitted_at === null;

  return (
    <tr className="group transition-colors hover:bg-[var(--ui-inset-soft)]">
      {/* Candidate */}
      <td className={TD}>
        <Avatar initials={initialsOf(a.applicant_name)} size={34} className="mr-3 align-middle" />
        <span className="align-middle text-[13.5px] font-medium">{a.applicant_name}</span>
      </td>

      {/* Score — right-aligned and tabular so a column of percentages lines up. */}
      <td
        className={cn(TD, 'text-right text-[15px] font-semibold tabular-nums')}
        style={{ color: scoreColor(a.score_percent) }}
      >
        {a.score_percent !== null ? `${a.score_percent}%` : '—'}
      </td>

      {/* Submitted */}
      <td className={cn(TD, 'font-mono text-[13px] text-muted-foreground')}>
        {a.submitted_at ? formatDuration(a.submitted_at) : '—'}
      </td>

      {/* Status — a tinted pill that always carries its word. */}
      <td className={TD}>
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
      </td>

      {/* Similarity signals — counts only, never the content (PH4-D3). */}
      {showSimilarity && (
        <td
          className={cn(TD, 'text-right font-mono text-[13px] tabular-nums text-muted-foreground')}
        >
          {similarityCount > 0 ? (
            <span className="text-[var(--ui-warn)]">{similarityCount}</span>
          ) : (
            '—'
          )}
        </td>
      )}

      {/* Action */}
      <td className={cn(TD, 'text-right')}>
        <Link to={`/hr/exams/${examId}/attempts/${a.attempt_id}`}>
          <Pill variant="ghost" className="py-1.5 text-[12px]">
            <Eye size={13} aria-hidden="true" /> View
          </Pill>
        </Link>
      </td>
    </tr>
  );
}
