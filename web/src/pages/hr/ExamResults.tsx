// ExamResults — graded attempts for one exam (HR workflow Phase 2).
// Layout: faithfully reproduces anterview-pages/src/screens/hr/ExamResults.tsx
//   (back link, title + exam-id, StatCards strip, table with candidate/score/
//    duration/status/action columns, export CSV button).
// Behavior: getExam + listAttempts queries, score_percent tone, score_raw/max,
//   attempt_no, submitted_at, in_progress/passed/failed logic, pass-threshold
//   summary, loading + empty states, correct back-to-exam route.
//   Client-side CSV export from loaded attempts (no fake endpoint).
//   No flagged/proctoring field (no backend field).

import { useMemo, type ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getExam, listAttempts, type AttemptResult } from '@/api/exams';
import { listSimilaritySignals } from '@/api/codeEvidence';
import { PageHeader, Breadcrumbs, Page, EmptyState } from '@/design/components/page';
import { QueryErrorPanel, QueryState } from '@/components/QueryState';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import { csvRow } from '@/lib/csv';
import { GlassCard, StatCard, StatusTag, Avatar } from '@/design/components/primitives';
import { Button } from '@/components/ui/button';
import {
  Download,
  Eye,
  CheckCircle2,
  XCircle,
  Clock,
  ListChecks,
} from '@/design/components/icons';
import { ACTIVE_POLL_MS } from '../../lib/polling';
import { formatDateTime } from '@/lib/formatters';

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

/** Still being sat: never read as a fail. */
function isInProgress(a: AttemptResult): boolean {
  return a.status === 'in_progress' || a.submitted_at === null;
}

/** The outcome in words. An attempt with no verdict yet is "Not graded", not
 *  "Failed" (audit E9: the CSV called every such attempt a fail). */
function outcomeLabel(a: AttemptResult): string {
  if (isInProgress(a)) return 'In progress';
  if (a.passed === true) return 'Passed';
  if (a.passed === false) return 'Failed';
  return 'Not graded';
}

/** "Round 2 · Coding" for an attempt that sat a round; '-' for a legacy one. */
function roundLabel(a: AttemptResult): string {
  if (a.round_number == null) return '-';
  return a.round_title ? `Round ${a.round_number} · ${a.round_title}` : `Round ${a.round_number}`;
}

// ── Client-side CSV export ────────────────────────────────────────────────────
function exportCsv(attempts: AttemptResult[], examTitle: string): void {
  const header = [
    'Candidate',
    'Round',
    'Attempt #',
    'Score %',
    'Raw / Max',
    'Pass mark %',
    'Status',
    'Submitted at',
  ];
  // csvRow, not hand-quoting: the name comes from the unauthenticated apply
  // form, and quoting alone does not stop a spreadsheet evaluating a cell that
  // starts with = + - @ (see lib/csv.ts).
  const rows = attempts.map((a) => [
    a.applicant_name,
    a.round_number == null ? '' : roundLabel(a),
    a.attempt_no,
    a.score_percent,
    a.score_raw !== null && a.score_max !== null ? `${a.score_raw}/${a.score_max}` : '',
    a.round_pass_threshold ?? '',
    outcomeLabel(a),
    a.submitted_at ? formatDateTime(a.submitted_at) : '',
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

  const examQuery = useQuery({
    queryKey: ['hr', 'exam', examId],
    queryFn: () => getExam(examId),
  });
  const exam = examQuery.data;

  const attemptsQuery = useQuery({
    queryKey: ['hr', 'exam', examId, 'attempts'],
    // A5: attempts land while the manager watches.
    refetchInterval: ACTIVE_POLL_MS,
    queryFn: () => listAttempts(examId),
  });
  const attempts = attemptsQuery.data;

  // PH4-D3 — counts only, never the content: the compare view (matched
  // regions, excerpts) lives one click away on the attempt itself.
  const showSimilarity = exam?.kind === 'coding';
  const signalsQuery = useQuery({
    queryKey: ['hr', 'exam', examId, 'similarity'],
    queryFn: () => listSimilaritySignals(examId),
    enabled: Boolean(examId) && showSimilarity,
  });
  const signals = signalsQuery.data;
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
  // Pass rate over GRADED attempts only: counting the ones still being sat as
  // not-passed understated it (audit E9).
  const gradedList = list.filter((a) => !isInProgress(a) && a.passed !== null);
  // Each attempt is judged by its own round's pass mark (audit E8), so the
  // page shows a single mark only when every attempt shares it.
  const marks = new Set(list.map((a) => a.round_pass_threshold ?? exam?.pass_threshold));
  const sharedMark = marks.size === 1 ? [...marks][0] : undefined;
  const showRound = list.some((a) => a.round_number != null);
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
          <span className="break-words text-[13px]">
            {list.length} attempt{list.length !== 1 ? 's' : ''} · {passedCount} passed
            {sharedMark !== undefined
              ? ` · pass mark ${sharedMark}%`
              : list.length > 0
                ? ' · each round has its own pass mark'
                : ''}
          </span>
        }
        actions={
          list.length > 0 ? (
            <Button
              variant="outline"
              onClick={() => exportCsv(list, exam?.title ?? 'exam')}
              aria-label="Export results as CSV"
            >
              <Download aria-hidden="true" /> Export CSV
            </Button>
          ) : undefined
        }
      />

      {/* The exam's own details only feed the title and the pass mark, so a
          failure there is reported without hiding the attempts below. */}
      {examQuery.isError && !exam ? (
        <QueryErrorPanel
          title="Could not load this exam's details."
          error={examQuery.error}
          onRetry={() => void examQuery.refetch()}
          retrying={examQuery.isFetching}
        />
      ) : null}

      {/* ── Summary KPI row ── */}
      {list.length > 0 && (
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
            value={
              gradedList.length > 0
                ? `${Math.round((passedCount / gradedList.length) * 100)}%`
                : '-'
            }
            delta={`of ${gradedList.length} graded`}
            trend={passedCount > 0 ? 'up' : 'flat'}
          />
        </div>
      )}

      {/* ── Results table ── */}
      {showSimilarity && (
        <p className="text-[13px] leading-relaxed text-muted-foreground">
          Similarity signals are automated and unreviewed - similar code is not evidence of
          misconduct on its own. See an attempt&rsquo;s own Code evidence tab for detail.
        </p>
      )}
      {showSimilarity && signalsQuery.isError && !signals ? (
        // Without this the column read "-" for every attempt, which says "no
        // signals" when the truth is "we could not check".
        <QueryErrorPanel
          title="Could not load the similarity signals."
          error={signalsQuery.error}
          onRetry={() => void signalsQuery.refetch()}
          retrying={signalsQuery.isFetching}
        />
      ) : null}
      <QueryState
        query={attemptsQuery}
        errorTitle="Could not load the attempts for this exam."
        loading={
          <ResultsTable showSimilarity={showSimilarity} showRound={false} busy>
            {[0, 1, 2].map((i) => (
              <tr key={i} aria-hidden="true">
                <td colSpan={showSimilarity ? 6 : 5} className="border-b border-border p-0">
                  <Skeleton className="h-[56px] rounded-none" />
                </td>
              </tr>
            ))}
          </ResultsTable>
        }
        isEmpty={(rows) => rows.length === 0}
        empty={
          <EmptyState
            icon={ListChecks}
            title="No attempts yet"
            description="Assign the exam and share the links. Attempts appear here as candidates submit."
          />
        }
      >
        {(rows) => (
          <ResultsTable showSimilarity={showSimilarity} showRound={showRound}>
            {rows.map((a) => (
              <AttemptRow
                key={a.attempt_id}
                a={a}
                examId={examId}
                showRound={showRound}
                showSimilarity={showSimilarity}
                similarityCount={similarityByAttempt.get(a.attempt_id) ?? 0}
              />
            ))}
          </ResultsTable>
        )}
      </QueryState>
    </Page>
  );
}

const TH = 'sticky top-0 z-[1] whitespace-nowrap border-b border-border bg-card px-6 py-3.5';

// ── Results table frame ───────────────────────────────────────────────────────
function ResultsTable({
  showSimilarity,
  showRound,
  busy = false,
  children,
}: {
  showSimilarity: boolean;
  showRound: boolean;
  busy?: boolean;
  children: ReactNode;
}) {
  return (
    <GlassCard className="overflow-hidden p-0">
      {/* The scroll container is what makes the header sticky: it scrolls
          the rows under a header that stays put, and scrolls sideways on a
          narrow screen instead of squeezing the columns. */}
      <div className="max-h-[70vh] overflow-auto" aria-busy={busy ? 'true' : undefined}>
        <table className="w-full min-w-[760px] border-collapse text-left">
          <caption className="sr-only">Graded attempts for this exam</caption>
          <thead>
            <tr className="text-[12px] font-semibold uppercase tracking-[0.5px] text-muted-foreground">
              <th scope="col" className={TH}>
                Candidate
              </th>
              {showRound && (
                <th scope="col" className={TH}>
                  Round
                </th>
              )}
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
          <tbody>{children}</tbody>
        </table>
      </div>
    </GlassCard>
  );
}
const TD = 'border-b border-border px-6 py-3.5 align-middle group-last:border-0';

// ── Attempt row ───────────────────────────────────────────────────────────────
function AttemptRow({
  a,
  examId,
  showRound,
  showSimilarity,
  similarityCount,
}: {
  a: AttemptResult;
  examId: string;
  showRound: boolean;
  showSimilarity: boolean;
  similarityCount: number;
}) {
  const inProgress = isInProgress(a);

  return (
    <tr className="group transition-colors hover:bg-[var(--ui-inset-soft)]">
      {/* Candidate */}
      <td className={TD}>
        <Avatar initials={initialsOf(a.applicant_name)} size={34} className="mr-3 align-middle" />
        <span className="align-middle text-[13.5px] font-medium">{a.applicant_name}</span>
      </td>

      {/* Round, with the pass mark this attempt was judged against. */}
      {showRound && (
        <td className={cn(TD, 'text-[13px]')}>
          <span className="block">{roundLabel(a)}</span>
          {a.round_pass_threshold != null ? (
            <span className="block text-[12px] text-muted-foreground">
              pass mark {a.round_pass_threshold}%
            </span>
          ) : null}
        </td>
      )}

      {/* Score — right-aligned and tabular so a column of percentages lines up. */}
      <td
        className={cn(TD, 'text-right text-[15px] font-semibold tabular-nums')}
        style={{ color: scoreColor(a.score_percent) }}
      >
        {a.score_percent !== null ? `${a.score_percent}%` : '-'}
      </td>

      {/* Submitted */}
      <td className={cn(TD, 'font-mono text-[13px] text-muted-foreground')}>
        {formatDateTime(a.submitted_at)}
      </td>

      {/* Status — a tinted pill that always carries its word. */}
      <td className={TD}>
        {inProgress ? (
          <StatusTag tone="electric">
            <Clock size={11} aria-hidden="true" /> In progress
          </StatusTag>
        ) : a.passed === true ? (
          <StatusTag tone="forest" dot>
            <CheckCircle2 size={11} aria-hidden="true" /> Passed
          </StatusTag>
        ) : a.passed === false ? (
          <StatusTag tone="ember">
            <XCircle size={11} aria-hidden="true" /> Failed
          </StatusTag>
        ) : (
          <StatusTag tone="neutral">Not graded</StatusTag>
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
            '-'
          )}
        </td>
      )}

      {/* Action */}
      <td className={cn(TD, 'text-right')}>
        {/* One control: a link styled as a button. A button nested in a
            link is invalid and announced twice (audit E27). */}
        <Button asChild variant="ghost" size="sm">
          <Link
            to={`/hr/exams/${examId}/attempts/${a.attempt_id}`}
            aria-label={`View attempt by ${a.applicant_name}`}
          >
            <Eye aria-hidden="true" /> View
          </Link>
        </Button>
      </td>
    </tr>
  );
}
