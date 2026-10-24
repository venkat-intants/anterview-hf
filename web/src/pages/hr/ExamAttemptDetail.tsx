// ExamAttemptDetail — per-attempt review for one candidate (HR workflow).
// Route: /hr/exams/:examId/attempts/:attemptId
//
// Replaces the old (broken) "View → /scorecard/:id" link, which mistakenly
// pointed exam attempts at the INTERVIEW scorecard route and always 404'd.
// An exam attempt is graded by data_gateway, not feedback_billing — this page
// renders the HR-only breakdown (GET /hr/exams/:examId/attempts/:aid/breakdown):
// per-MCQ correctness + per-coding-question points, plus the score summary.

import { Suspense, lazy, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  getExam,
  listAttempts,
  listCodingQuestions,
  getAttemptBreakdown,
  type AttemptAdjustment,
  type CodingQuestion,
  type ExamQuestion,
} from '@/api/exams';
import { Reveal, Stagger, StaggerItem } from '@/design/components/Reveal';
import { GlassCard, StatCard, StatusTag, Avatar } from '@/design/components/primitives';
import { CheckCircle2, XCircle, Clock, Code2 } from '@/design/components/icons';
import { cn } from '@/lib/utils';
import { PageHeader, Breadcrumbs, Page } from '@/design/components/page';
import AttemptProctoringSummary from '@/components/hr/AttemptProctoringSummary';
import { QueryErrorPanel } from '@/components/QueryState';
import { Skeleton } from '@/components/ui/skeleton';
import { formatDateTime, plural } from '@/lib/formatters';
import { useTabKeys } from '@/lib/tabKeys';

// Code-split: the evidence tab pulls in the read-only CodeEditor (Prism +
// language grammars), which most HR sessions on this page never open.
const CodeEvidencePanel = lazy(() => import('@/components/hr/CodeEvidencePanel'));

// ── Helpers ───────────────────────────────────────────────────────────────────

function initialsOf(name: string): string {
  return name
    .split(' ')
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? '')
    .join('');
}

/**
 * PH4-D2 — what THIS attempt was given, in plain words. Facts only: no note,
 * no basis, no recorder — `AttemptAdjustment` carries none of those, so there
 * is nothing here to decorate with.
 */
function describeAdjustment(adj: AttemptAdjustment): string {
  const parts: string[] = [];
  if (adj.extra_time_percent != null) {
    parts.push(`Time adjustment applied: +${adj.extra_time_percent}%`);
  } else if (adj.extra_time_seconds > 0) {
    parts.push(`Time adjustment applied: +${adj.extra_time_seconds}s`);
  }
  if (adj.auto_submit_relaxed) {
    parts.push(
      parts.length > 0 ? 'auto-submit relaxed' : 'Auto-submit was relaxed for this attempt',
    );
  }
  return parts.join(' · ');
}

const ATTEMPT_TABS = ['overview', 'code-evidence'] as const;
type AttemptTab = (typeof ATTEMPT_TABS)[number];

/** The first line of a coding prompt, trimmed for a row label. */
function excerpt(prompt: string, max = 120): string {
  const line = prompt.trim().split('\n')[0] ?? '';
  return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default function ExamAttemptDetail() {
  const { examId = '', attemptId = '' } = useParams<{ examId: string; attemptId: string }>();
  const [tab, setTab] = useState<AttemptTab>('overview');
  const tabs = useTabKeys({
    idBase: `attempt-${attemptId}`,
    keys: ATTEMPT_TABS,
    active: tab,
    onChange: setTab,
  });

  const { data: exam } = useQuery({
    queryKey: ['hr', 'exam', examId],
    queryFn: () => getExam(examId),
  });

  // Shares the cache key with ExamResults, so this is instant when navigating
  // from the results table — and self-sufficient on a direct page load.
  const { data: attempts } = useQuery({
    queryKey: ['hr', 'exam', examId, 'attempts'],
    queryFn: () => listAttempts(examId),
  });

  const {
    data: breakdown,
    error: breakdownError,
    isLoading,
    isError,
    isFetching,
    refetch,
  } = useQuery({
    queryKey: ['hr', 'exam', examId, 'attempt', attemptId, 'breakdown'],
    queryFn: () => getAttemptBreakdown(examId, attemptId),
    enabled: Boolean(examId) && Boolean(attemptId),
    retry: false,
  });

  const attempt = attempts?.find((a) => a.attempt_id === attemptId) ?? null;

  // Best-effort prompt lookup — populated for flat MCQ exams; section/round
  // exams fall back to numbered labels.
  const qMap = new Map<string, ExamQuestion>((exam?.questions ?? []).map((q) => [q.id, q]));

  const mcqEntries = Object.entries(breakdown?.per_question ?? {});
  const codingEntries = Object.entries(breakdown?.coding ?? {});

  // The coding prompts, so a row says which problem it is rather than only
  // "Coding question" (audit E24). Fetched only when there is a coding row.
  const { data: codingQuestions } = useQuery({
    queryKey: ['hr', 'exam', examId, 'coding-questions'],
    queryFn: () => listCodingQuestions(examId),
    enabled: Boolean(examId) && codingEntries.length > 0,
  });
  const codingMap = new Map<string, CodingQuestion>(
    (codingQuestions ?? []).map((q) => [q.id, q]),
  );
  const mcqCorrect = mcqEntries.filter(([, ok]) => ok).length;
  const totalQuestions = mcqEntries.length + codingEntries.length;

  // The pass mark this attempt was judged against: its own round's, not the
  // exam-level one (audit E8). The exam's is only a fallback for a legacy
  // attempt that predates rounds.
  const passThreshold = attempt?.round_pass_threshold ?? exam?.pass_threshold ?? null;
  const scorePercent = attempt?.score_percent ?? breakdown?.score_percent ?? null;
  const passed = attempt?.passed ?? breakdown?.passed ?? null;
  const inProgress = attempt?.status === 'in_progress' || attempt?.submitted_at === null;
  // Not yet graded reads as such, never as "did not pass" (audit E24).
  const verdict = inProgress || passed === null ? 'not graded yet' : passed ? 'passed' : 'did not pass';

  return (
    <Page width="default">
      <PageHeader
        breadcrumbs={
          <Breadcrumbs
            items={[
              { label: 'Exams', to: '/hr/exams' },
              { label: exam?.title ?? 'Exam', to: `/hr/exams/${examId}` },
              { label: 'Results', to: `/hr/exams/${examId}/results` },
              { label: attempt ? `Attempt #${attempt.attempt_no}` : 'Attempt' },
            ]}
          />
        }
        title={
          <span className="flex items-center gap-3">
            {attempt && (
              <span aria-hidden="true" className="flex">
                <Avatar initials={initialsOf(attempt.applicant_name)} size={44} />
              </span>
            )}
            <span className="min-w-0 break-words">{attempt?.applicant_name ?? 'Attempt'}</span>
          </span>
        }
        description={
          <span className="font-mono text-[13px]">
            {exam?.title ?? 'Exam'}
            {attempt ? ` · attempt #${attempt.attempt_no}` : ''}
            {attempt?.round_number != null
              ? ` · round ${attempt.round_number}${attempt.round_title ? ` (${attempt.round_title})` : ''}`
              : ''}
            {attempt?.submitted_at ? ` · ${formatDateTime(attempt.submitted_at)}` : ''}
          </span>
        }
        actions={
          scorePercent !== null ? (
            inProgress ? (
              <StatusTag tone="electric">
                <Clock size={11} aria-hidden="true" /> In progress
              </StatusTag>
            ) : passed === null ? (
              // A score with no pass/fail yet (grading not finished) is not a
              // failure: the stat card says "not graded yet" and the results
              // table "Not graded" for the same attempt.
              <StatusTag tone="neutral">Not graded</StatusTag>
            ) : passed ? (
              <StatusTag tone="forest" dot>
                <CheckCircle2 size={11} aria-hidden="true" /> Passed
              </StatusTag>
            ) : (
              <StatusTag tone="ember">
                <XCircle size={11} aria-hidden="true" /> Failed
              </StatusTag>
            )
          ) : undefined
        }
      />

      {/* ── PH4-D2 — what this attempt was actually given, read off the
          attempt itself. Facts only: no note, no basis, no recorder. ── */}
      {breakdown?.adjustment ? (
        <Reveal delay={0.04}>
          <div className="inline-flex items-center gap-1.5 rounded-[10px] border border-[var(--ui-line-strong)] bg-[var(--ui-inset-soft)] px-3 py-1.5 text-[13px] text-[var(--ui-soft)]">
            <Clock size={13} className="text-[var(--ui-info)]" aria-hidden="true" />
            {describeAdjustment(breakdown.adjustment)}
          </div>
        </Reveal>
      ) : null}

      {/* ── Camera-proctoring contract §7 — the attempt's proctoring
          timeline. Independent of the grading breakdown below (an attempt
          can be watched whether or not it has graded yet), and shown
          whichever tab is active. ── */}
      <Reveal delay={0.05}>
        <AttemptProctoringSummary examId={examId} attemptId={attemptId} />
      </Reveal>

      {/* ── Loading / error ── */}
      {isLoading && (
        <div className="mt-6 flex flex-col gap-4" role="status" aria-label="Loading attempt">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-[92px] rounded-card" />
            ))}
          </div>
          <Skeleton className="h-48 w-full rounded-card" />
        </div>
      )}

      {/* errorMessage words a 404 or 403 itself, so a removed attempt and one
          this account cannot see each read as what they are. */}
      {!isLoading && isError && (
        <QueryErrorPanel
          title="Could not load this attempt."
          error={breakdownError}
          onRetry={() => void refetch()}
          retrying={isFetching}
        />
      )}

      {!isLoading && !isError && breakdown && (
        <>
          {/* ── Tabs — the evidence tab only exists where there is a coding
              question to have evidence about. ── */}
          {codingEntries.length > 0 && (
            <Reveal delay={0.05}>
              <div
                className="mt-5 flex gap-1 border-b border-border"
                role="tablist"
                aria-label="Attempt views"
                {...tabs.tabListProps}
              >
                <button
                  type="button"
                  {...tabs.tabProps('overview')}
                  onClick={() => setTab('overview')}
                  className={cn(
                    'min-h-[40px] border-b-2 px-3 py-2 text-[13px] font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
                    tab === 'overview'
                      ? 'border-[var(--accent)] text-foreground'
                      : 'border-transparent text-muted-foreground hover:text-foreground',
                  )}
                >
                  Overview
                </button>
                <button
                  type="button"
                  {...tabs.tabProps('code-evidence')}
                  onClick={() => setTab('code-evidence')}
                  className={cn(
                    'inline-flex min-h-[40px] items-center gap-1.5 border-b-2 px-3 py-2 text-[13px] font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
                    tab === 'code-evidence'
                      ? 'border-[var(--accent)] text-foreground'
                      : 'border-transparent text-muted-foreground hover:text-foreground',
                  )}
                >
                  <Code2 size={13} aria-hidden="true" />
                  Code evidence
                </button>
              </div>
            </Reveal>
          )}

          {tab === 'code-evidence' && codingEntries.length > 0 ? (
            <div className="mt-5" {...tabs.panelProps('code-evidence')}>
              <Suspense
                fallback={
                  <div role="status" aria-label="Loading code evidence">
                    <Skeleton className="h-64 w-full rounded-card" />
                  </div>
                }
              >
                <CodeEvidencePanel examId={examId} attemptId={attemptId} />
              </Suspense>
            </div>
          ) : (
            <div {...(codingEntries.length > 0 ? tabs.panelProps('overview') : {})}>
              {/* ── Stat strip ── */}
              <Reveal delay={0.06}>
                <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
                  <StatCard
                    label="Score"
                    value={scorePercent !== null ? `${scorePercent}%` : '-'}
                    delta={verdict}
                    trend={verdict === 'passed' ? 'up' : 'flat'}
                  />
                  <StatCard
                    label="Points"
                    value={
                      attempt && attempt.score_raw !== null && attempt.score_max !== null
                        ? `${attempt.score_raw}/${attempt.score_max}`
                        : '-'
                    }
                    delta="raw / max"
                    trend="flat"
                  />
                  <StatCard
                    label="MCQ correct"
                    value={mcqEntries.length > 0 ? `${mcqCorrect}/${mcqEntries.length}` : '-'}
                    delta={plural(totalQuestions, 'question')}
                    trend="flat"
                  />
                  <StatCard
                    label="Pass mark"
                    value={passThreshold !== null ? `${passThreshold}%` : '-'}
                    delta={attempt?.round_pass_threshold != null ? "this round's threshold" : 'threshold'}
                    trend="flat"
                  />
                </div>
              </Reveal>

              {/* ── MCQ breakdown ── */}
              {mcqEntries.length > 0 && (
                <Reveal delay={0.1}>
                  <GlassCard className="mt-5 overflow-hidden p-0">
                    <div className="border-b border-border px-6 py-3.5 text-[12px] font-semibold uppercase tracking-[0.5px] text-[var(--ui-soft)]">
                      Multiple choice · {mcqCorrect}/{mcqEntries.length} correct
                    </div>
                    <Stagger className="flex flex-col">
                      {mcqEntries.map(([qid, correct], i) => {
                        const q = qMap.get(qid);
                        return (
                          <StaggerItem key={qid}>
                            <div className="flex items-start justify-between gap-3 border-b border-border px-6 py-3.5 last:border-0">
                              <div className="min-w-0">
                                <p className="text-[13.5px] text-foreground">
                                  <span className="text-[var(--ui-faint)]">Q{i + 1}.</span>{' '}
                                  {q?.prompt ?? `Question ${i + 1}`}
                                </p>
                                {q && !correct && (
                                  <p className="mt-1 text-[12.5px] text-muted-foreground">
                                    Correct answer: {q.options[q.correct_index] ?? '-'}
                                  </p>
                                )}
                              </div>
                              <div className="flex flex-none items-center gap-3">
                                <span className="font-mono text-[12px] text-[var(--ui-faint)]">
                                  {q ? `${correct ? q.points : 0}/${q.points} pts` : ''}
                                </span>
                                {correct ? (
                                  <StatusTag tone="forest" dot>
                                    <CheckCircle2 size={11} aria-hidden="true" /> Correct
                                  </StatusTag>
                                ) : (
                                  <StatusTag tone="ember">
                                    <XCircle size={11} aria-hidden="true" /> Incorrect
                                  </StatusTag>
                                )}
                              </div>
                            </div>
                          </StaggerItem>
                        );
                      })}
                    </Stagger>
                  </GlassCard>
                </Reveal>
              )}

              {/* ── Coding breakdown ── */}
              {codingEntries.length > 0 && (
                <Reveal delay={0.14}>
                  <GlassCard className="mt-5 overflow-hidden p-0">
                    <div className="border-b border-border px-6 py-3.5 text-[12px] font-semibold uppercase tracking-[0.5px] text-[var(--ui-soft)]">
                      Coding · {plural(codingEntries.length, 'question')}
                    </div>
                    <Stagger className="flex flex-col">
                      {codingEntries.map(([qid, r], i) => {
                        const earned = r.raw ?? 0;
                        const full = earned >= r.points && r.points > 0;
                        const partial = earned > 0 && earned < r.points;
                        const cq = codingMap.get(qid);
                        return (
                          <StaggerItem key={qid}>
                            <div className="flex items-start justify-between gap-3 border-b border-border px-6 py-3.5 last:border-0">
                              <div className="min-w-0">
                                <p className="text-[13.5px] text-foreground">
                                  <span className="text-[var(--ui-faint)]">Q{i + 1}.</span>{' '}
                                  {cq ? excerpt(cq.prompt) : 'Coding question'}
                                </p>
                                <p className="mt-1 text-[12.5px] text-muted-foreground">
                                  {r.submitted === false
                                    ? 'Not submitted'
                                    : r.error
                                      ? r.error
                                      : `Language: ${r.language ?? 'unknown'}`}
                                </p>
                              </div>
                              <div className="flex flex-none items-center gap-3">
                                <span className="font-mono text-[12px] text-[var(--ui-faint)]">
                                  {earned}/{r.points} pts
                                </span>
                                {full ? (
                                  <StatusTag tone="forest" dot>
                                    <CheckCircle2 size={11} aria-hidden="true" /> Passed
                                  </StatusTag>
                                ) : partial ? (
                                  <StatusTag tone="amber">Partial</StatusTag>
                                ) : (
                                  <StatusTag tone="ember">
                                    <XCircle size={11} aria-hidden="true" /> Failed
                                  </StatusTag>
                                )}
                              </div>
                            </div>
                          </StaggerItem>
                        );
                      })}
                    </Stagger>
                  </GlassCard>
                </Reveal>
              )}

              {totalQuestions === 0 && (
                <GlassCard className="mt-5 p-8 text-center">
                  <p className="text-[13px] text-muted-foreground">
                    No per-question breakdown is available for this attempt.
                  </p>
                </GlassCard>
              )}
            </div>
          )}
        </>
      )}
    </Page>
  );
}
