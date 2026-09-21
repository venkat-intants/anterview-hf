// CodeEvidencePanel — PH4-D3. The "Code evidence" tab on ExamAttemptDetail:
// per coding question, a read-only source viewer, a quality card, its
// findings, the sandbox's own test RESULTS (never coverage — it is not
// available for any language), an integrity panel, similarity SIGNALS and,
// separately, integrity FINDINGS.
//
// THE LOAD-BEARING RULE ON THIS SCREEN: a signal is never a finding.
// `similarity_signals` is SYSTEM evidence — automated, unreviewed, and never
// presented as a conclusion. `findings` is HUMAN evidence — a named
// `hr_manager`'s judgement call, always with a rationale. They are rendered
// as two visually separate sections on purpose; do not merge them into one
// list, and do not let a signal's containment number read like a verdict.
//
// Source is audited server-side every time it is fetched (D3 #24) — this
// panel only calls `getCodeSource` once HR clicks "View source" for a given
// question, never on mount.

import { Suspense, lazy, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getCodeEvidence,
  getCodeSource,
  triggerCodeAnalysis,
  type AttemptSimilaritySignal,
  type CodeEvidence,
  type CodeQualityReport,
  type IntegrityFinding,
} from '@/api/codeEvidence';
import type { CodingResult } from '@/api/exams';
import { CODING_LANGUAGES } from '@/api/exams';
import { toast } from '@/lib/toast';
import { GlassCard, StatusTag } from '@/design/components/primitives';
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardCheck,
  Clock,
  Code2,
  Eye,
  Info,
  Loader2,
  ShieldCheck,
  Users,
  XCircle,
} from '@/design/components/icons';
import CodeSimilarityCompare from './CodeSimilarityCompare';

const CodeEditor = lazy(() => import('@/components/CodeEditor'));

function errText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function shortId(id: string): string {
  return id.slice(0, 8);
}

function pct(n: number): string {
  return `${Math.round(n * 100)}%`;
}

const CODING_LANGUAGE_SET = new Set<string>(CODING_LANGUAGES);

/** `CodeEditor`'s Prism map only knows the ten coding languages — anything
 *  else (or `null`) falls back to its own generic grammar, never a crash. */
function editorLanguage(language: string | null | undefined): string {
  return language && CODING_LANGUAGE_SET.has(language) ? language : 'text';
}

// ---------------------------------------------------------------------------
// Quality card
// ---------------------------------------------------------------------------

function analyserLabel(report: CodeQualityReport): string {
  if (report.analyser === 'python-ast') return 'Full analysis — Python AST';
  return `Token-level approximation — ${report.language}`;
}

function QualityMetrics({ report }: { report: CodeQualityReport }) {
  const metrics = report.metrics;
  if (report.status === 'unsupported') {
    return (
      <p className="text-[12.5px] text-muted-foreground">
        No structured quality metrics for {report.language}.
      </p>
    );
  }
  if (report.status === 'failed') {
    return (
      <p className="text-[12.5px] text-[var(--ui-warn)]">
        Analysis failed{report.error_class ? ` (${report.error_class})` : ''} — the submission
        itself is unaffected.
      </p>
    );
  }
  if (!('kind' in metrics)) {
    return <p className="text-[12.5px] text-muted-foreground">No metrics recorded.</p>;
  }

  const tiles: Array<{ label: string; value: string }> =
    metrics.kind === 'python-ast'
      ? [
          { label: 'Functions', value: String(metrics.function_count) },
          { label: 'Avg. complexity', value: metrics.complexity_avg.toFixed(2) },
          { label: 'Max. nesting', value: String(Math.max(0, ...metrics.functions.map((f) => f.nesting_depth))) },
          { label: 'Maintainability', value: metrics.maintainability_index.toFixed(0) },
          { label: 'Duplication', value: pct(metrics.duplication_ratio) },
          { label: 'Lines', value: String(metrics.lines_of_code) },
        ]
      : [
          { label: 'Decision density', value: metrics.decision_point_density.toFixed(2) },
          { label: 'Max. nesting', value: String(metrics.max_nesting_depth) },
          { label: 'Maintainability', value: metrics.maintainability_index.toFixed(0) },
          { label: 'Duplication', value: pct(metrics.duplication_ratio) },
          { label: 'Comment ratio', value: pct(metrics.comment_ratio) },
          { label: 'Lines', value: String(metrics.lines_of_code) },
        ];

  return (
    <div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {tiles.map((t) => (
          <div key={t.label} className="rounded-[10px] border border-border p-2.5">
            <div className="text-[11px] uppercase tracking-[0.4px] text-[var(--ui-faint)]">{t.label}</div>
            <div className="mt-0.5 font-mono text-[15px] text-foreground">{t.value}</div>
          </div>
        ))}
      </div>

      {metrics.kind === 'python-ast' && metrics.functions.length > 0 ? (
        <ul className="mt-3 flex flex-col gap-1" aria-label="Per-function metrics">
          {metrics.functions.map((f) => (
            <li
              key={`${f.name}-${f.line}`}
              className="flex flex-wrap items-center justify-between gap-x-3 gap-y-0.5 rounded-[8px] bg-[var(--ui-inset-soft)] px-2.5 py-1.5 text-[11.5px] text-[var(--ui-soft)]"
            >
              <span className="font-mono">
                {f.name}() · line {f.line}
              </span>
              <span className="text-[var(--ui-faint)]">
                complexity {f.cyclomatic_complexity} · nesting {f.nesting_depth} · {f.length_lines} lines ·{' '}
                {f.parameter_count} params
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      {report.findings.length > 0 ? (
        <ul className="mt-3 flex flex-col gap-1.5" aria-label="Quality findings">
          {report.findings.map((f, i) => (
            <li key={`${f.rule}-${i}`} className="flex items-start gap-2 text-[12px] text-[var(--ui-soft)]">
              {f.severity === 'warning' ? (
                <AlertTriangle size={12} className="mt-0.5 shrink-0 text-[var(--ui-warn)]" aria-hidden="true" />
              ) : (
                <Info size={12} className="mt-0.5 shrink-0 text-[var(--ui-faint)]" aria-hidden="true" />
              )}
              <span>
                {f.message} <span className="text-[var(--ui-faint)]">(line {f.line})</span>
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-[12px] text-muted-foreground">No findings for this submission.</p>
      )}
    </div>
  );
}

function QualityCard({ report }: { report: CodeQualityReport | undefined }) {
  return (
    <div className="rounded-[12px] border border-border p-3.5">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-[12.5px] font-medium text-foreground">Code quality</h4>
        <span className="text-[11px] text-[var(--ui-faint)]">Signals, not a score.</span>
      </div>
      {report ? (
        <>
          <p className="mt-1 text-[11px] uppercase tracking-[0.4px] text-[var(--ui-faint)]">
            {analyserLabel(report)}
          </p>
          <div className="mt-2.5">
            <QualityMetrics report={report} />
          </div>
        </>
      ) : (
        <p className="mt-2 text-[12.5px] text-muted-foreground">
          Not analysed yet — run analysis for this attempt to see quality metrics here.
        </p>
      )}
      {/* Coverage is never available for any language — a reason, never a
          figure (checklist #6). */}
      <p className="mt-3 flex items-start gap-1.5 text-[11px] text-[var(--ui-faint)]">
        <Info size={11} className="mt-0.5 shrink-0" aria-hidden="true" />
        Test coverage: not available —{' '}
        {report?.coverage.reason ??
          'coverage requires instrumented execution, which this analyser never does.'}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Test results (labelled as results, never coverage)
// ---------------------------------------------------------------------------

function TestResultsCard({ result }: { result: CodingResult | undefined }) {
  if (!result) {
    return (
      <div className="rounded-[12px] border border-border p-3.5">
        <h4 className="text-[12.5px] font-medium text-foreground">Test results</h4>
        <p className="mt-1.5 text-[12.5px] text-muted-foreground">
          No test results recorded for this question.
        </p>
      </div>
    );
  }
  const earned = result.raw ?? 0;
  const full = earned >= result.points && result.points > 0;
  const partial = earned > 0 && earned < result.points;
  return (
    <div className="rounded-[12px] border border-border p-3.5">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-[12.5px] font-medium text-foreground">Test results</h4>
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
      <p className="mt-1.5 text-[12.5px] text-muted-foreground">
        {result.submitted === false
          ? 'Not submitted'
          : result.error
            ? result.error
            : `Language: ${result.language ?? 'unknown'}`}
      </p>
      <p className="mt-1 font-mono text-[12px] text-[var(--ui-faint)]">
        {earned}/{result.points} pts
        {Array.isArray(result.tests) ? ` · ${result.tests.length} test case${result.tests.length === 1 ? '' : 's'}` : ''}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Similarity signals — automated, unreviewed
// ---------------------------------------------------------------------------

function SimilaritySignalsCard({
  attemptId,
  signals,
  onCompare,
}: {
  attemptId: string;
  signals: AttemptSimilaritySignal[];
  onCompare: (signal: AttemptSimilaritySignal) => void;
}) {
  return (
    <div className="rounded-[12px] border border-border p-3.5">
      <div className="flex items-center gap-1.5">
        <Users size={13} className="text-[var(--ui-faint)]" aria-hidden="true" />
        <h4 className="text-[12.5px] font-medium text-foreground">
          Similarity signals (automated, unreviewed)
        </h4>
      </div>
      {signals.length === 0 ? (
        <p className="mt-1.5 text-[12.5px] text-muted-foreground">
          No similarity signals for this question.
        </p>
      ) : (
        <ul className="mt-2 flex flex-col gap-2" aria-label="Similarity signals">
          {signals.map((s) => {
            const otherAttemptId = s.attempt_low_id === attemptId ? s.attempt_high_id : s.attempt_low_id;
            return (
            <li key={s.id} className="rounded-[10px] bg-[var(--ui-inset-soft)] p-2.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-[12px] text-foreground">
                  {s.reference_kind === 'reference_solution'
                    ? 'Matches the reference solution'
                    : `Matches attempt ${shortId(otherAttemptId ?? '')}`}
                </span>
                <button
                  type="button"
                  onClick={() => onCompare(s)}
                  className="inline-flex items-center gap-1 rounded-[8px] border border-border px-2.5 py-1 text-[11.5px] text-[var(--ui-info)] hover:underline focus:outline-none"
                >
                  <Eye size={11} aria-hidden="true" />
                  Compare &amp; record a finding
                </button>
              </div>
              <p className="mt-1 font-mono text-[11px] text-[var(--ui-faint)]">
                containment {pct(s.containment_low)}
                {s.containment_high != null ? ` / ${pct(s.containment_high)}` : ''} · jaccard{' '}
                {pct(s.jaccard)} · {s.shared_fingerprints} shared fingerprints
              </p>
              <p className="mt-1.5 text-[11px] leading-relaxed text-[var(--ui-faint)]">{s.caption}</p>
            </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Integrity findings — recorded by people
// ---------------------------------------------------------------------------

const OUTCOME_TONE: Record<IntegrityFinding['outcome'], 'neutral' | 'amber' | 'ember'> = {
  no_concern: 'neutral',
  follow_up: 'amber',
  confirmed: 'ember',
};
const OUTCOME_LABEL: Record<IntegrityFinding['outcome'], string> = {
  no_concern: 'No concern',
  follow_up: 'Follow up',
  confirmed: 'Confirmed',
};

function IntegrityFindingsCard({ findings }: { findings: IntegrityFinding[] }) {
  return (
    <div className="rounded-[12px] border border-border p-3.5">
      <div className="flex items-center gap-1.5">
        <ClipboardCheck size={13} className="text-[var(--ui-faint)]" aria-hidden="true" />
        <h4 className="text-[12.5px] font-medium text-foreground">
          Integrity findings (recorded by people)
        </h4>
      </div>
      {findings.length === 0 ? (
        <p className="mt-1.5 text-[12.5px] text-muted-foreground">
          No finding has been recorded for this question.
        </p>
      ) : (
        <ul className="mt-2 flex flex-col gap-2" aria-label="Integrity findings">
          {findings.map((f) => (
            <li key={f.id} className="rounded-[10px] bg-[var(--ui-inset-soft)] p-2.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <StatusTag tone={OUTCOME_TONE[f.outcome]}>{OUTCOME_LABEL[f.outcome]}</StatusTag>
                <span className="text-[11px] text-[var(--ui-faint)]">
                  {new Date(f.created_at).toLocaleString()} · recorded by {shortId(f.recorded_by_user_id)}
                </span>
              </div>
              <p className="mt-1.5 text-[12px] text-[var(--ui-soft)]">
                {f.redacted || f.rationale === null ? '[redacted]' : f.rationale}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Source viewer — fetched only once HR asks to see it (D3 #24)
// ---------------------------------------------------------------------------

function SourceViewer({
  examId,
  attemptId,
  questionId,
}: {
  examId: string;
  attemptId: string;
  questionId: string;
}) {
  const [open, setOpen] = useState(false);
  const source = useQuery({
    queryKey: ['hr', 'exam', examId, 'attempt', attemptId, 'code', questionId],
    queryFn: () => getCodeSource(examId, attemptId, questionId),
    enabled: open,
    staleTime: Infinity,
    retry: false,
  });

  return (
    <div className="rounded-[12px] border border-border p-3.5">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-[12.5px] font-medium text-foreground">Submitted source</h4>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="inline-flex items-center gap-1 rounded-[8px] border border-border px-2.5 py-1 text-[11.5px] text-[var(--ui-info)] hover:underline focus:outline-none"
        >
          <Eye size={11} aria-hidden="true" />
          {open ? 'Hide source' : 'View source'}
        </button>
      </div>
      {/* Viewing this is audited on the server every time — this only fires
          the request once HR clicked the button above. */}
      {open ? (
        source.isLoading ? (
          <p className="mt-2 flex items-center gap-1.5 text-[12px] text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            Loading…
          </p>
        ) : source.isError ? (
          <p className="mt-2 text-[12px] text-[var(--ui-danger)]">
            {errText(source.error, 'Could not load this submission.')}
          </p>
        ) : source.data?.redacted ? (
          <p className="mt-2 text-[12px] text-muted-foreground">
            This submission&rsquo;s source has been redacted (retention or an erasure request).
          </p>
        ) : (
          <div className="mt-2">
            <Suspense
              fallback={
                <div className="flex h-24 items-center justify-center rounded-[10px] border border-border bg-card">
                  <Loader2 className="h-4 w-4 animate-spin text-[var(--ui-info)]" aria-hidden="true" />
                </div>
              }
            >
              <CodeEditor
                value={source.data?.source ?? ''}
                language={editorLanguage(source.data?.language)}
                readOnly
                minHeight={160}
                textareaId={`ce-evidence-${questionId}`}
              />
            </Suspense>
          </div>
        )
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Integrity summary (once, for the whole attempt)
// ---------------------------------------------------------------------------

function IntegritySummaryCard({ integrity }: { integrity: CodeEvidence['integrity'] }) {
  const counts = Object.entries(integrity.event_counts ?? {});
  return (
    <GlassCard className="p-4">
      <div className="flex items-center gap-1.5">
        <ShieldCheck size={14} className="text-[var(--ui-faint)]" aria-hidden="true" />
        <h3 className="text-[13px] font-medium text-foreground">Integrity</h3>
      </div>
      {integrity.integrity_score !== null ? (
        <p className="mt-1.5 text-[13px] text-foreground">
          Integrity score <span className="font-mono">{integrity.integrity_score}/100</span>
          {integrity.proctoring_summary?.violations != null
            ? ` · ${integrity.proctoring_summary.violations} violation${integrity.proctoring_summary.violations === 1 ? '' : 's'}`
            : ''}
        </p>
      ) : (
        <p className="mt-1.5 text-[12.5px] text-muted-foreground">No proctoring data for this attempt.</p>
      )}
      {counts.length > 0 ? (
        <ul className="mt-2 flex flex-wrap gap-2" aria-label="Proctoring event counts">
          {counts.map(([type, n]) => (
            <li
              key={type}
              className="rounded-[8px] border border-border px-2 py-1 text-[11.5px] text-[var(--ui-soft)]"
            >
              {type} × {n}
            </li>
          ))}
        </ul>
      ) : null}
    </GlassCard>
  );
}

// ---------------------------------------------------------------------------
// Per-question card
// ---------------------------------------------------------------------------

function QuestionEvidenceCard({
  examId,
  attemptId,
  questionId,
  index,
  report,
  testResult,
  signals,
  findings,
  onCompare,
}: {
  examId: string;
  attemptId: string;
  questionId: string;
  index: number;
  report: CodeQualityReport | undefined;
  testResult: CodingResult | undefined;
  signals: AttemptSimilaritySignal[];
  findings: IntegrityFinding[];
  onCompare: (signal: AttemptSimilaritySignal) => void;
}) {
  return (
    <GlassCard className="p-4">
      <div className="flex items-center gap-1.5">
        <Code2 size={13} className="text-[var(--ui-faint)]" aria-hidden="true" />
        <h3 className="text-[13px] font-medium text-foreground">
          Coding question {index + 1}
          <span className="ml-1.5 font-mono text-[11px] text-[var(--ui-faint)]">
            {shortId(questionId)}
          </span>
        </h3>
      </div>
      <div className="mt-3 flex flex-col gap-3">
        <SourceViewer examId={examId} attemptId={attemptId} questionId={questionId} />
        <QualityCard report={report} />
        <TestResultsCard result={testResult} />
        <SimilaritySignalsCard attemptId={attemptId} signals={signals} onCompare={onCompare} />
        <IntegrityFindingsCard findings={findings} />
      </div>
    </GlassCard>
  );
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------

export default function CodeEvidencePanel({
  examId,
  attemptId,
}: {
  examId: string;
  attemptId: string;
}): JSX.Element {
  const qc = useQueryClient();
  const [compareSignal, setCompareSignal] = useState<AttemptSimilaritySignal | null>(null);

  const evidence = useQuery({
    queryKey: ['hr', 'exam', examId, 'attempt', attemptId, 'code-evidence'],
    queryFn: () => getCodeEvidence(examId, attemptId),
    enabled: Boolean(examId) && Boolean(attemptId),
    retry: false,
  });

  const analyseMut = useMutation({
    mutationFn: () => triggerCodeAnalysis(examId, attemptId),
    onSuccess: (r) => {
      toast.success(
        r.reports_written > 0
          ? `Analysed ${r.reports_written} submission${r.reports_written === 1 ? '' : 's'}`
          : 'Nothing new to analyse',
      );
      void qc.invalidateQueries({
        queryKey: ['hr', 'exam', examId, 'attempt', attemptId, 'code-evidence'],
      });
    },
    onError: (e) => toast.error(errText(e, 'Could not run analysis for this attempt')),
  });

  const questionIds = useMemo(() => {
    const ids = new Set<string>();
    const ev = evidence.data;
    Object.keys(ev?.test_results ?? {}).forEach((id) => ids.add(id));
    (ev?.reports ?? []).forEach((r) => ids.add(r.coding_question_id));
    (ev?.similarity_signals ?? []).forEach((s) => ids.add(s.coding_question_id));
    (ev?.findings ?? []).forEach((f) => ids.add(f.coding_question_id));
    return Array.from(ids);
  }, [evidence.data]);

  if (evidence.isLoading) {
    return (
      <GlassCard className="p-8 text-center">
        <Loader2 className="mx-auto h-6 w-6 animate-spin text-[var(--ui-info)]" aria-hidden="true" />
      </GlassCard>
    );
  }
  if (evidence.isError || !evidence.data) {
    return (
      <GlassCard className="p-6 text-center">
        <p className="text-[13.5px] text-[var(--ui-danger)]">
          {errText(evidence.error, 'Could not load code evidence for this attempt.')}
        </p>
      </GlassCard>
    );
  }

  const ev = evidence.data;

  return (
    <div className="flex flex-col gap-4">
      {ev.code_redacted ? (
        <div className="flex items-start gap-2 rounded-[12px] border border-[var(--ui-warn)]/30 bg-[var(--ui-warn)]/[0.06] p-3 text-[12.5px] leading-relaxed text-[var(--ui-soft)]">
          <Info size={13} className="mt-0.5 shrink-0 text-[var(--ui-warn)]" aria-hidden="true" />
          Source and program output for this attempt have been redacted (retention or an erasure
          request). Scores are unaffected.
        </div>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[12px] text-muted-foreground">
          The sweep analyses recent submissions automatically. Use this for an older attempt, or
          to re-run after a fix.
        </p>
        <button
          type="button"
          onClick={() => analyseMut.mutate()}
          disabled={analyseMut.isPending}
          className="inline-flex items-center gap-1.5 rounded-[10px] border border-border px-3 py-1.5 text-[12px] text-foreground disabled:opacity-50"
        >
          {analyseMut.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <Clock size={13} aria-hidden="true" />
          )}
          Run analysis now
        </button>
      </div>

      <IntegritySummaryCard integrity={ev.integrity} />

      {questionIds.length === 0 ? (
        <GlassCard className="p-6 text-center">
          <p className="text-[13px] text-muted-foreground">
            No coding evidence recorded for this attempt yet.
          </p>
        </GlassCard>
      ) : (
        questionIds.map((qid, i) => (
          <QuestionEvidenceCard
            key={qid}
            examId={examId}
            attemptId={attemptId}
            questionId={qid}
            index={i}
            report={ev.reports.find((r) => r.coding_question_id === qid)}
            testResult={ev.test_results[qid]}
            signals={ev.similarity_signals.filter((s) => s.coding_question_id === qid)}
            findings={ev.findings.filter((f) => f.coding_question_id === qid)}
            onCompare={setCompareSignal}
          />
        ))
      )}

      {compareSignal ? (
        <CodeSimilarityCompare
          signal={compareSignal}
          attemptId={attemptId}
          onClose={() => setCompareSignal(null)}
          onRecorded={() => {
            void qc.invalidateQueries({
              queryKey: ['hr', 'exam', examId, 'attempt', attemptId, 'code-evidence'],
            });
          }}
        />
      ) : null}
    </div>
  );
}
