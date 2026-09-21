// codeEvidence.ts — PH4-D3 code quality + similarity EVIDENCE. HR only — every
// route lives under services/data_gateway/app/routers/code_evidence.py's
// `hr_router` (`/hr`, HrCtxDep). There is no super-admin, interviewer,
// candidate or agent route for any of this.
//
// A SIGNAL IS NEVER A FINDING. `code_quality_reports`, `code_fingerprints` and
// `code_similarity_signals` are SYSTEM evidence, written only by the sweep or
// an on-demand re-run, and immutable once written. `code_integrity_findings`
// is the only HUMAN evidence — a named `hr_manager`'s judgement call, always
// with a mandatory rationale. Screens built against this module must keep
// those two kinds visually and structurally separate; see
// components/hr/CodeEvidencePanel.tsx and CodeSimilarityCompare.tsx.
//
// Test COVERAGE is not available for any language — `coverage.available` is
// always `false`. Never render a coverage figure; the sandbox's own TEST
// RESULTS (`test_results`, from `graded_snapshot`) are shown instead, labelled
// as results, not coverage.
//
// Viewing a candidate's source, or a similarity comparison's excerpts, is
// audited server-side on every call (D3 #24) — callers must not prefetch
// either before HR has actually asked to see it.
//
// Field names match app/code_evidence.py's `evidence_for_attempt`,
// `compare_view`, `signals_for_exam` and `record_finding` EXACTLY.

import type { CodingResult } from './exams';
import { apiGet, apiPost } from './client';
import { pathId } from './pathId';

// ---------------------------------------------------------------------------
// Quality — reports (SYSTEM evidence)
// ---------------------------------------------------------------------------

export type AnalyserKind = 'python-ast' | 'pygments-tokens';
export type ReportStatus = 'complete' | 'unsupported' | 'failed' | 'skipped';

export interface CodeFinding {
  rule: string;
  severity: 'info' | 'warning';
  line: number;
  message: string;
}

export interface HalsteadMetrics {
  distinct_operators: number;
  distinct_operands: number;
  total_operators: number;
  total_operands: number;
  vocabulary: number;
  length: number;
  volume: number;
  difficulty: number;
  effort: number;
}

/** Python only — real, per-function metrics from the AST. */
export interface PythonFunctionMetric {
  name: string;
  line: number;
  cyclomatic_complexity: number;
  nesting_depth: number;
  length_lines: number;
  parameter_count: number;
}

export interface PythonMetrics {
  kind: 'python-ast';
  functions: PythonFunctionMetric[];
  function_count: number;
  complexity_total: number;
  complexity_avg: number;
  halstead: HalsteadMetrics;
  maintainability_index: number;
  lines_of_code: number;
  duplication_ratio: number;
}

/** The other nine languages (and Python source `ast.parse` could not read) —
 *  a TOKEN-LEVEL APPROXIMATION. Never present this as equal to `PythonMetrics`. */
export interface TokenMetrics {
  kind: 'token-approximate';
  decision_points: number;
  decision_point_density: number;
  max_nesting_depth: number;
  comment_ratio: number;
  long_line_count: number;
  lines_of_code: number;
  halstead: HalsteadMetrics;
  maintainability_index: number;
  duplication_ratio: number;
}

/** `{}` for `unsupported` / `failed` / `skipped` reports. */
export type CodeQualityMetrics = PythonMetrics | TokenMetrics | Record<string, never>;

/** Always `available: false` — coverage needs instrumented execution, which
 *  this analyser (pure static analysis) never does. Never render a figure
 *  here; show `test_results` instead. */
export interface CoverageUnavailable {
  available: false;
  reason: string;
}

export interface CodeQualityReport {
  coding_question_id: string;
  language: string;
  analyser: AnalyserKind;
  analyser_version: string;
  status: ReportStatus;
  metrics: CodeQualityMetrics;
  findings: CodeFinding[];
  coverage: CoverageUnavailable;
  error_class: string | null;
  source_sha256: string;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Integrity — the proctoring summary already captured at submit time
// ---------------------------------------------------------------------------

export interface ProctoringSummary {
  counts?: Record<string, number>;
  violations?: number;
}

export interface CodeEvidenceIntegrity {
  integrity_score: number | null;
  proctoring_summary: ProctoringSummary | null;
  event_counts: Record<string, number>;
}

// ---------------------------------------------------------------------------
// Similarity signals — SYSTEM evidence, automated and unreviewed
// ---------------------------------------------------------------------------

export type SimilarityReferenceKind = 'submission' | 'reference_solution';

/** One signal as it appears on an attempt's own evidence — carries the fixed
 *  caption server-side, verbatim: similar code is not evidence of misconduct
 *  on its own. */
export interface AttemptSimilaritySignal {
  id: string;
  coding_question_id: string;
  attempt_low_id: string;
  /** `null` when `reference_kind` is `reference_solution` — the "other side"
   *  is the question's reference solution, not another attempt. */
  attempt_high_id: string | null;
  reference_kind: SimilarityReferenceKind;
  containment_low: number;
  containment_high: number | null;
  jaccard: number;
  shared_fingerprints: number;
  tokens_low: number;
  tokens_high: number;
  algorithm_version: string;
  created_at: string;
  caption: string;
}

/** One signal in an exam-wide list (`signals_for_exam`) — no `caption`,
 *  `tokens_*` or `algorithm_version`; render the same fixed caption in the UI
 *  rather than treating its absence as "reviewed". */
export interface ExamSimilaritySignal {
  id: string;
  coding_question_id: string;
  attempt_low_id: string;
  attempt_high_id: string | null;
  reference_kind: SimilarityReferenceKind;
  containment_low: number;
  containment_high: number | null;
  jaccard: number;
  shared_fingerprints: number;
  created_at: string;
}

export interface MatchedRegion {
  low_start: number;
  low_end: number;
  high_start: number;
  high_end: number;
}

/** One contiguous run of source lines, carrying the ABSOLUTE line it starts
 *  on. Number and highlight from `start_line + index`, never from a block's
 *  own position in the array — `matched_regions` are absolute source line
 *  numbers too, and the previous version of this screen numbered rows 1..N
 *  by excerpt position, which only ever lined up while excerpts were the
 *  first 200 lines of the file (security review, PH4 wave 5; server fix in
 *  commit c62756b). */
export interface ExcerptBlock {
  start_line: number;
  lines: string[];
}

export interface SimilarityExcerpt {
  language: string | null;
  /** The same content as `blocks`, flattened to one string with an `...`
   *  separator between non-adjacent blocks. NOT the first 200 lines of the
   *  file — cut around the matched regions instead (±3 lines of context,
   *  merged, capped at 200 lines total). Kept for any caller that only wants
   *  a flat string; screens render from `blocks` (see CodeSimilarityCompare's
   *  `ExcerptPane`), since this string alone carries no line numbers to
   *  highlight against. */
  excerpt: string;
  /** Render from this, not `excerpt` — see `ExcerptBlock`. */
  blocks: ExcerptBlock[];
}

export interface SimilarityCompare {
  signal_id: string;
  low: SimilarityExcerpt;
  high: SimilarityExcerpt;
  /** Empty for a `reference_solution` comparison — the server only pairs
   *  matched regions between two stored fingerprints. */
  matched_regions: MatchedRegion[];
  caption: string;
}

// ---------------------------------------------------------------------------
// Integrity findings — HUMAN evidence, recorded by a named person
// ---------------------------------------------------------------------------

export type FindingOutcome = 'no_concern' | 'follow_up' | 'confirmed';

export interface IntegrityFinding {
  id: string;
  coding_question_id: string;
  signal_id: string | null;
  outcome: FindingOutcome;
  /** `null` once redacted (retention/erasure) — `redacted` says so. */
  rationale: string | null;
  recorded_by_user_id: string;
  created_at: string;
  redacted: boolean;
}

/** The server enforces 20–2000 characters — mirror both bounds so a save
 *  never round-trips into a 422. */
export const FINDING_RATIONALE_MIN = 20;
export const FINDING_RATIONALE_MAX = 2000;

export interface RecordFindingInput {
  attempt_id: string;
  coding_question_id: string;
  outcome: FindingOutcome;
  rationale: string;
  signal_id?: string | null;
  supersedes_id?: string | null;
}

// ---------------------------------------------------------------------------
// Source + evidence bundle
// ---------------------------------------------------------------------------

export interface CodeSource {
  language: string | null;
  /** `null` once redacted — `redacted` says so; never render a placeholder as
   *  if it were the candidate's code. */
  source: string | null;
  redacted: boolean;
}

export interface CodeEvidence {
  attempt_id: string;
  code_redacted: boolean;
  reports: CodeQualityReport[];
  /** The coding round's existing sandbox RESULTS, from `graded_snapshot` —
   *  never coverage. Keyed by `coding_question_id`. */
  test_results: Record<string, CodingResult>;
  integrity: CodeEvidenceIntegrity;
  similarity_signals: AttemptSimilaritySignal[];
  findings: IntegrityFinding[];
}

export interface AnalysisTriggerResult {
  attempts_scanned: number;
  reports_written: number;
  fingerprints_written: number;
  signals_written: number;
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

/** Reports, test results, the integrity summary, similarity signals and
 *  findings for every coding question in this attempt — one call backs the
 *  whole "Code evidence" tab. */
export function getCodeEvidence(examId: string, attemptId: string): Promise<CodeEvidence> {
  return apiGet<CodeEvidence>(
    `/hr/exams/${pathId(examId)}/attempts/${pathId(attemptId)}/code-evidence`,
  );
}

/**
 * The candidate's own submitted source for one coding question — AUDITED ON
 * THE SERVER EVERY TIME THIS IS CALLED. Callers must fetch this only once HR
 * has explicitly asked to see it (a "View source" action), never eagerly
 * alongside the rest of the evidence tab.
 */
export function getCodeSource(
  examId: string,
  attemptId: string,
  codingQuestionId: string,
): Promise<CodeSource> {
  return apiGet<CodeSource>(
    `/hr/exams/${pathId(examId)}/attempts/${pathId(attemptId)}/code/${pathId(codingQuestionId)}`,
  );
}

/** On-demand analysis for one attempt — the sweep already covers recent
 *  submissions; this is for an older one, or a re-run. */
export function triggerCodeAnalysis(
  examId: string,
  attemptId: string,
): Promise<AnalysisTriggerResult> {
  return apiPost<AnalysisTriggerResult>(
    `/hr/exams/${pathId(examId)}/attempts/${pathId(attemptId)}/code-analysis`,
    {},
  );
}

/** Every similarity signal for one exam — counts only are safe to surface on
 *  a list screen; the content lives behind `getSimilarityCompare`. */
/** How much code evidence an application has: counts only -- no source, no
 *  content, no names. Not audited: it reveals nothing about anyone's code. */
export interface CodeEvidenceSummary {
  signal_count: number;
  finding_count: number;
}

export function getCodeEvidenceSummary(enrolmentId: string): Promise<CodeEvidenceSummary> {
  return apiGet<CodeEvidenceSummary>(
    `/hr/enrolments/${pathId(enrolmentId)}/code-evidence-summary`,
  );
}

export function listSimilaritySignals(examId: string): Promise<ExamSimilaritySignal[]> {
  return apiGet<ExamSimilaritySignal[]>(`/hr/exams/${pathId(examId)}/similarity`);
}

/**
 * Excerpts of each matched region, ±3 lines, at most 200 lines per side —
 * AUDITED ON THE SERVER EVERY TIME THIS IS CALLED. Fetch only when HR opens
 * the compare dialog for this specific signal, never ahead of that.
 */
export function getSimilarityCompare(signalId: string): Promise<SimilarityCompare> {
  return apiGet<SimilarityCompare>(`/hr/code-similarity/${pathId(signalId)}`);
}

/** Findings for one attempt, redacted ones excluded by default. `evidence_for_attempt`
 *  already embeds the same (unfiltered) list; this standalone read exists for a
 *  screen that only needs the findings, e.g. after recording a new one. */
export function listIntegrityFindings(
  examId: string,
  attemptId: string,
  includeRedacted = false,
): Promise<IntegrityFinding[]> {
  const q = includeRedacted ? '?include_redacted=true' : '';
  return apiGet<IntegrityFinding[]>(
    `/hr/exams/${pathId(examId)}/attempts/${pathId(attemptId)}/integrity-findings${q}`,
  );
}

// ---------------------------------------------------------------------------
// Writes — HUMAN evidence only
// ---------------------------------------------------------------------------

/** Record a named person's judgement call. `rationale` is mandatory
 *  (20–2000 chars, enforced again by the server) — there is no path to
 *  record a finding without one. */
export function recordCodeIntegrityFinding(body: RecordFindingInput): Promise<{ id: string }> {
  return apiPost<{ id: string }>('/hr/code-integrity-findings', body);
}
