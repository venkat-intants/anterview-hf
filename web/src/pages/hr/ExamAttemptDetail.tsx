// ExamAttemptDetail — per-attempt review for one candidate (HR workflow).
// Route: /hr/exams/:examId/attempts/:attemptId
//
// Replaces the old (broken) "View → /scorecard/:id" link, which mistakenly
// pointed exam attempts at the INTERVIEW scorecard route and always 404'd.
// An exam attempt is graded by data_gateway, not feedback_billing — this page
// renders the HR-only breakdown (GET /hr/exams/:examId/attempts/:aid/breakdown):
// per-MCQ correctness + per-coding-question points, plus the score summary.

import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  getExam,
  listAttempts,
  getAttemptBreakdown,
  type ExamQuestion,
} from '@/api/exams';
import { Reveal, Stagger, StaggerItem } from '@/design/components/Reveal';
import { GlassCard, StatCard, StatusTag, Avatar } from '@/design/components/primitives';
import { ArrowLeft, CheckCircle2, XCircle, Clock } from '@/design/components/icons';

// ── Helpers ───────────────────────────────────────────────────────────────────

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
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default function ExamAttemptDetail() {
  const { examId = '', attemptId = '' } = useParams<{ examId: string; attemptId: string }>();

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

  const { data: breakdown, isLoading, isError } = useQuery({
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
  const mcqCorrect = mcqEntries.filter(([, ok]) => ok).length;
  const totalQuestions = mcqEntries.length + codingEntries.length;

  const passThreshold = exam?.pass_threshold ?? null;
  const scorePercent = attempt?.score_percent ?? breakdown?.score_percent ?? null;
  const passed = attempt?.passed ?? breakdown?.passed ?? null;
  const inProgress = attempt?.status === 'in_progress' || attempt?.submitted_at === null;

  return (
    <div className="mx-auto max-w-[1080px] px-6 py-8 lg:px-8">
      {/* ── Back link ── */}
      <Link
        to={`/hr/exams/${examId}/results`}
        className="inline-flex items-center gap-1.5 text-[13px] text-[#888b91] hover:text-white"
      >
        <ArrowLeft size={15} aria-hidden="true" /> Back to results
      </Link>

      {/* ── Title bar ── */}
      <Reveal>
        <div className="mt-4 flex flex-wrap items-end justify-between gap-4">
          <div className="flex items-center gap-3">
            {attempt && (
              <Avatar
                initials={initialsOf(attempt.applicant_name)}
                gradient={gradientFor(attempt.applicant_id)}
                size={44}
              />
            )}
            <div>
              <h1 className="text-[28px] font-semibold tracking-[-1px]">
                {attempt?.applicant_name ?? 'Attempt'}
              </h1>
              <p className="mt-1 font-mono text-[13px] text-[#888b91]">
                {exam?.title ?? 'Exam'}
                {attempt ? ` · attempt #${attempt.attempt_no}` : ''}
                {attempt?.submitted_at ? ` · ${formatDate(attempt.submitted_at)}` : ''}
              </p>
            </div>
          </div>

          {scorePercent !== null && (
            <div>
              {inProgress ? (
                <StatusTag tone="electric">
                  <Clock size={11} aria-hidden="true" /> In progress
                </StatusTag>
              ) : passed ? (
                <StatusTag tone="forest" dot>
                  <CheckCircle2 size={11} aria-hidden="true" /> Passed
                </StatusTag>
              ) : (
                <StatusTag tone="ember">
                  <XCircle size={11} aria-hidden="true" /> Failed
                </StatusTag>
              )}
            </div>
          )}
        </div>
      </Reveal>

      {/* ── Loading / error ── */}
      {isLoading && (
        <GlassCard className="mt-6 p-8 text-center">
          <div
            className="mx-auto h-7 w-7 animate-spin rounded-full border-2 border-white/15 border-t-[#60a5fa]"
            role="status"
            aria-label="Loading attempt"
          />
        </GlassCard>
      )}

      {!isLoading && isError && (
        <GlassCard className="mt-6 p-8 text-center">
          <p className="text-[14px] font-medium text-white">Could not load this attempt</p>
          <p className="mt-1 text-[13px] text-[#888b91]">
            It may have been removed, or you don&apos;t have access to it.
          </p>
        </GlassCard>
      )}

      {!isLoading && !isError && breakdown && (
        <>
          {/* ── Stat strip ── */}
          <Reveal delay={0.06}>
            <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
              <StatCard
                label="Score"
                value={scorePercent !== null ? `${scorePercent}%` : '—'}
                delta={passed ? 'passed' : 'did not pass'}
                trend={passed ? 'up' : 'flat'}
                feature
              />
              <StatCard
                label="Points"
                value={
                  attempt && attempt.score_raw !== null && attempt.score_max !== null
                    ? `${attempt.score_raw}/${attempt.score_max}`
                    : '—'
                }
                delta="raw / max"
                trend="flat"
              />
              <StatCard
                label="MCQ correct"
                value={mcqEntries.length > 0 ? `${mcqCorrect}/${mcqEntries.length}` : '—'}
                delta={`${totalQuestions} question${totalQuestions !== 1 ? 's' : ''}`}
                trend="flat"
              />
              <StatCard
                label="Pass mark"
                value={passThreshold !== null ? `${passThreshold}%` : '—'}
                delta="threshold"
                trend="flat"
              />
            </div>
          </Reveal>

          {/* ── MCQ breakdown ── */}
          {mcqEntries.length > 0 && (
            <Reveal delay={0.1}>
              <GlassCard className="mt-5 overflow-hidden p-0">
                <div className="border-b border-white/[0.06] px-6 py-3.5 text-[11.5px] uppercase tracking-[0.5px] text-[#70757c]">
                  Multiple choice · {mcqCorrect}/{mcqEntries.length} correct
                </div>
                <Stagger className="flex flex-col">
                  {mcqEntries.map(([qid, correct], i) => {
                    const q = qMap.get(qid);
                    return (
                      <StaggerItem key={qid}>
                        <div className="flex items-start justify-between gap-3 border-b border-white/[0.04] px-6 py-3.5 last:border-0">
                          <div className="min-w-0">
                            <p className="text-[13.5px] text-white">
                              <span className="text-[#70757c]">Q{i + 1}.</span>{' '}
                              {q?.prompt ?? `Question ${i + 1}`}
                            </p>
                            {q && !correct && (
                              <p className="mt-1 text-[12.5px] text-[#888b91]">
                                Correct answer: {q.options[q.correct_index] ?? '—'}
                              </p>
                            )}
                          </div>
                          <div className="flex flex-none items-center gap-3">
                            <span className="font-mono text-[12px] text-[#70757c]">
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
                <div className="border-b border-white/[0.06] px-6 py-3.5 text-[11.5px] uppercase tracking-[0.5px] text-[#70757c]">
                  Coding · {codingEntries.length} question{codingEntries.length !== 1 ? 's' : ''}
                </div>
                <Stagger className="flex flex-col">
                  {codingEntries.map(([qid, r], i) => {
                    const earned = r.raw ?? 0;
                    const full = earned >= r.points && r.points > 0;
                    const partial = earned > 0 && earned < r.points;
                    return (
                      <StaggerItem key={qid}>
                        <div className="flex items-start justify-between gap-3 border-b border-white/[0.04] px-6 py-3.5 last:border-0">
                          <div className="min-w-0">
                            <p className="text-[13.5px] text-white">
                              <span className="text-[#70757c]">Q{i + 1}.</span> Coding question
                            </p>
                            <p className="mt-1 text-[12.5px] text-[#888b91]">
                              {r.submitted === false
                                ? 'Not submitted'
                                : r.error
                                  ? r.error
                                  : `Language: ${r.language ?? 'unknown'}`}
                            </p>
                          </div>
                          <div className="flex flex-none items-center gap-3">
                            <span className="font-mono text-[12px] text-[#70757c]">
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
              <p className="text-[13px] text-[#888b91]">
                No per-question breakdown is available for this attempt.
              </p>
            </GlassCard>
          )}
        </>
      )}
    </div>
  );
}
