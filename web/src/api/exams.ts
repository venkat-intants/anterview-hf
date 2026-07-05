// exams.ts — HR MCQ exam authoring + results API (HR workflow Phase 2).
// Calls data_gateway (VITE_API_BASE_URL) via the central authenticated client.
// Tenant-scoped server-side by the HR's company. correct_index is returned here
// (HR only); the applicant take path lives in publicExam.ts and never sees it.

import { apiGet, apiPost, apiPut, apiDelete, apiPatch, clientFetch, uploadWithProgress } from './client';
import { getToken } from './tokenStore';

// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const API_BASE: string = import.meta.env.VITE_API_BASE_URL;

export type ExamStatus = 'draft' | 'published' | 'closed';
export type RoundStatus = 'draft' | 'published';

export interface ExamQuestion {
  id: string;
  prompt: string;
  options: string[];
  correct_index: number;
  points: number;
  position: number;
}

export type ExamKind = 'mcq' | 'coding';

// ── Round / Section / Structure types ────────────────────────────────────────

export interface Section {
  id: string;
  round_id: string;
  title: string;
  kind: ExamKind;
  time_limit_seconds: number | null;
  position: number;
  question_count: number;
}

export interface Round {
  id: string;
  title: string;
  round_number: number;
  pass_threshold: number;
  time_limit_seconds: number | null;
  advances_to_interview: boolean;
  status: RoundStatus;
  position: number;
  sections: Section[];
}

export interface ExamStructure {
  exam_id: string;
  rounds: Round[];
}

export interface RoundCreateInput {
  title: string;
  pass_threshold: number;
  time_limit_seconds: number | null;
  advances_to_interview: boolean;
}

export type RoundUpdateInput = Partial<{
  title: string;
  pass_threshold: number;
  time_limit_seconds: number | null;
  advances_to_interview: boolean;
  status: RoundStatus;
}>;

export interface SectionCreateInput {
  title: string;
  kind: ExamKind;
  time_limit_seconds: number | null;
}

export type SectionUpdateInput = Partial<{
  title: string;
  time_limit_seconds: number | null;
}>;

export interface Exam {
  id: string;
  title: string;
  description: string | null;
  target_job_title: string | null;
  pass_threshold: number;
  time_limit_seconds: number | null;
  allow_retake: boolean;
  auto_advance_on_pass: boolean;
  status: ExamStatus;
  kind: ExamKind;
  created_at: string;
}

export interface ExamSummary extends Exam {
  question_count: number;
  attempt_count: number;
}

export interface ExamDetail extends Exam {
  questions: ExamQuestion[];
  attempt_count: number;
}

export interface Assignment {
  assignment_id: string;
  applicant_id: string;
  applicant_name: string;
  status: string;
  expires_at: string;
  consumed_at: string | null;
  created_at: string;
  round_id?: string | null;
  scheduled_at?: string | null;
}

export interface AssignResult {
  assignment_id: string;
  applicant_id: string;
  applicant_name: string;
  magic_link: string;
  expires_at: string;
  status: string;
  round_id?: string | null;
  scheduled_at?: string | null;
}

export interface AttemptResult {
  attempt_id: string;
  applicant_id: string;
  applicant_name: string;
  score_raw: number | null;
  score_max: number | null;
  score_percent: number | null;
  passed: boolean | null;
  status: string;
  submitted_at: string | null;
  attempt_no: number;
}

export interface ExamCreateInput {
  title: string;
  description?: string;
  target_job_title?: string;
  pass_threshold?: number;
  time_limit_seconds?: number | null;
  allow_retake?: boolean;
  auto_advance_on_pass?: boolean;
  kind?: ExamKind;
}

export type ExamUpdateInput = Partial<{
  title: string;
  description: string;
  target_job_title: string;
  pass_threshold: number;
  time_limit_seconds: number | null;
  allow_retake: boolean;
  auto_advance_on_pass: boolean;
  status: ExamStatus;
}>;

export interface QuestionInput {
  prompt: string;
  options: string[];
  correct_index: number;
  points?: number;
}

// ── Exams ──────────────────────────────────────────────────────────────────
export function listExams(status?: ExamStatus): Promise<ExamSummary[]> {
  const q = status ? `?status=${encodeURIComponent(status)}` : '';
  return apiGet<ExamSummary[]>(`/hr/exams${q}`);
}

export function getExam(id: string): Promise<ExamDetail> {
  return apiGet<ExamDetail>(`/hr/exams/${id}`);
}

export function createExam(input: ExamCreateInput): Promise<Exam> {
  return apiPost<Exam>('/hr/exams', input);
}

export function updateExam(id: string, patch: ExamUpdateInput): Promise<Exam> {
  return clientFetch<Exam>(`${API_BASE}/hr/exams/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

// ── Questions ────────────────────────────────────────────────────────────────
export function addQuestion(examId: string, q: QuestionInput): Promise<ExamQuestion> {
  return apiPost<ExamQuestion>(`/hr/exams/${examId}/questions`, q);
}

export function updateQuestion(
  examId: string,
  qid: string,
  q: Partial<QuestionInput>,
): Promise<ExamQuestion> {
  return clientFetch<ExamQuestion>(`${API_BASE}/hr/exams/${examId}/questions/${qid}`, {
    method: 'PATCH',
    body: JSON.stringify(q),
  });
}

export function deleteQuestion(examId: string, qid: string): Promise<void> {
  return apiDelete<void>(`/hr/exams/${examId}/questions/${qid}`);
}

// ── Bulk add / AI generate / Excel import ────────────────────────────────────
export type ExamDifficulty = 'easy' | 'medium' | 'hard' | 'mixed';
export type ExamLanguage = 'en' | 'hi' | 'te';

export interface GeneratedQuestion {
  prompt: string;
  options: string[];
  correct_index: number;
  points: number;
}

export interface GenerateParams {
  topic: string;
  num_questions: number;
  difficulty: ExamDifficulty;
  language: ExamLanguage;
}

export interface ImportRowError {
  row: number;
  message: string;
}

export interface ImportResult {
  added: number;
  errors: ImportRowError[];
  questions: ExamQuestion[];
}

/** Ask Gemini (via backend) to draft questions — returned for preview, NOT saved. */
export function generateQuestions(
  examId: string,
  params: GenerateParams,
): Promise<{ questions: GeneratedQuestion[] }> {
  return apiPost<{ questions: GeneratedQuestion[] }>(
    `/hr/exams/${examId}/questions/generate`,
    params,
  );
}

/** Insert many already-built questions at once (AI 'add all', reviewed import). */
export function bulkAddQuestions(
  examId: string,
  questions: QuestionInput[],
): Promise<ExamQuestion[]> {
  return apiPost<ExamQuestion[]>(`/hr/exams/${examId}/questions/bulk`, { questions });
}

/** Upload an .xlsx/.csv in the template layout; backend parses + inserts valid rows. */
export function importQuestions(examId: string, file: File): Promise<ImportResult> {
  const fd = new FormData();
  fd.append('file', file);
  return uploadWithProgress<ImportResult>(
    `${API_BASE}/hr/exams/${examId}/questions/import`,
    fd,
  );
}

/** Fetch the .xlsx bulk-upload template (auth-scoped) and trigger a browser download. */
export async function downloadQuestionTemplate(): Promise<void> {
  const token = getToken();
  const res = await fetch(`${API_BASE}/hr/exam-question-template`, {
    credentials: 'include',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(`Could not download template (HTTP ${res.status})`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'exam-questions-template.xlsx';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function reorderQuestions(examId: string, questionIds: string[]): Promise<ExamQuestion[]> {
  return apiPut<ExamQuestion[]>(`/hr/exams/${examId}/questions/order`, {
    question_ids: questionIds,
  });
}

// ── Coding questions (exams of kind='coding') ────────────────────────────────
export const CODING_LANGUAGES = [
  'python', 'javascript', 'typescript', 'java', 'cpp', 'c', 'go', 'csharp', 'ruby', 'rust',
] as const;
export type CodingLanguage = (typeof CODING_LANGUAGES)[number];

export interface CodingTestCase {
  stdin: string;
  expected_output: string;
  is_sample: boolean;
  weight: number;
}

/** HR-facing coding question — INCLUDES reference_solution + all test cases. */
export interface CodingQuestion {
  id: string;
  prompt: string;
  allowed_languages: string[];
  starter_code: string | null;
  reference_solution: string | null;
  test_cases: CodingTestCase[];
  time_limit_ms: number;
  points: number;
  position: number;
}

export interface CodingQuestionInput {
  prompt: string;
  allowed_languages: string[];
  starter_code?: string | null;
  reference_solution?: string | null;
  test_cases: CodingTestCase[];
  time_limit_ms?: number;
  points?: number;
}

export function listCodingQuestions(examId: string): Promise<CodingQuestion[]> {
  return apiGet<CodingQuestion[]>(`/hr/exams/${examId}/coding-questions`);
}

export function addCodingQuestion(examId: string, q: CodingQuestionInput): Promise<CodingQuestion> {
  return apiPost<CodingQuestion>(`/hr/exams/${examId}/coding-questions`, q);
}

export function updateCodingQuestion(
  examId: string,
  qid: string,
  q: Partial<CodingQuestionInput>,
): Promise<CodingQuestion> {
  return clientFetch<CodingQuestion>(`${API_BASE}/hr/exams/${examId}/coding-questions/${qid}`, {
    method: 'PATCH',
    body: JSON.stringify(q),
  });
}

export function deleteCodingQuestion(examId: string, qid: string): Promise<void> {
  return apiDelete<void>(`/hr/exams/${examId}/coding-questions/${qid}`);
}

// ── Rounds ────────────────────────────────────────────────────────────────────
export function getStructure(examId: string): Promise<ExamStructure> {
  return apiGet<ExamStructure>(`/hr/exams/${examId}/structure`);
}

export function createRound(examId: string, input: RoundCreateInput): Promise<Round> {
  return apiPost<Round>(`/hr/exams/${examId}/rounds`, input);
}

export function updateRound(
  examId: string,
  roundId: string,
  patch: RoundUpdateInput,
): Promise<Round> {
  return apiPatch<Round>(`/hr/exams/${examId}/rounds/${roundId}`, patch);
}

export function deleteRound(examId: string, roundId: string): Promise<void> {
  return apiDelete<void>(`/hr/exams/${examId}/rounds/${roundId}`);
}

export function reorderRounds(examId: string, ids: string[]): Promise<Round[]> {
  return apiPut<Round[]>(`/hr/exams/${examId}/rounds/order`, { ids });
}

// ── Sections ──────────────────────────────────────────────────────────────────
export function createSection(
  examId: string,
  roundId: string,
  input: SectionCreateInput,
): Promise<Section> {
  return apiPost<Section>(`/hr/exams/${examId}/rounds/${roundId}/sections`, input);
}

export function updateSection(
  examId: string,
  roundId: string,
  sectionId: string,
  patch: SectionUpdateInput,
): Promise<Section> {
  return apiPatch<Section>(
    `/hr/exams/${examId}/rounds/${roundId}/sections/${sectionId}`,
    patch,
  );
}

export function deleteSection(
  examId: string,
  roundId: string,
  sectionId: string,
): Promise<void> {
  return apiDelete<void>(`/hr/exams/${examId}/rounds/${roundId}/sections/${sectionId}`);
}

// ── Section questions (MCQ) ───────────────────────────────────────────────────
export function listSectionQuestions(
  examId: string,
  sectionId: string,
): Promise<ExamQuestion[]> {
  return apiGet<ExamQuestion[]>(`/hr/exams/${examId}/sections/${sectionId}/questions`);
}

export function addSectionQuestion(
  examId: string,
  sectionId: string,
  q: QuestionInput,
): Promise<ExamQuestion> {
  return apiPost<ExamQuestion>(`/hr/exams/${examId}/sections/${sectionId}/questions`, q);
}

// ── Section coding questions ──────────────────────────────────────────────────
export function listSectionCodingQuestions(
  examId: string,
  sectionId: string,
): Promise<CodingQuestion[]> {
  return apiGet<CodingQuestion[]>(
    `/hr/exams/${examId}/sections/${sectionId}/coding-questions`,
  );
}

export function addSectionCodingQuestion(
  examId: string,
  sectionId: string,
  q: CodingQuestionInput,
): Promise<CodingQuestion> {
  return apiPost<CodingQuestion>(
    `/hr/exams/${examId}/sections/${sectionId}/coding-questions`,
    q,
  );
}

export function deleteSectionQuestion(
  examId: string,
  sectionId: string,
  qid: string,
): Promise<void> {
  return apiDelete<void>(`/hr/exams/${examId}/sections/${sectionId}/questions/${qid}`);
}

export function deleteSectionCodingQuestion(
  examId: string,
  sectionId: string,
  qid: string,
): Promise<void> {
  return apiDelete<void>(
    `/hr/exams/${examId}/sections/${sectionId}/coding-questions/${qid}`,
  );
}

// ── Assignments (magic links) ────────────────────────────────────────────────
export function listAssignments(examId: string): Promise<Assignment[]> {
  return apiGet<Assignment[]>(`/hr/exams/${examId}/assignments`);
}

export function assignExam(
  examId: string,
  applicantIds: string[],
  ttlHours?: number,
  roundId?: string,
  scheduledAt?: string,
): Promise<AssignResult[]> {
  return apiPost<AssignResult[]>(`/hr/exams/${examId}/assignments`, {
    applicant_ids: applicantIds,
    ttl_hours: ttlHours,
    ...(roundId !== undefined ? { round_id: roundId } : {}),
    ...(scheduledAt !== undefined ? { scheduled_at: scheduledAt } : {}),
  });
}

export function revokeAssignment(examId: string, aid: string): Promise<Assignment> {
  return apiPost<Assignment>(`/hr/exams/${examId}/assignments/${aid}/revoke`, {});
}

// ── Results ──────────────────────────────────────────────────────────────────
export function listAttempts(examId: string): Promise<AttemptResult[]> {
  return apiGet<AttemptResult[]>(`/hr/exams/${examId}/attempts`);
}

/** One graded coding question, from the frozen graded_snapshot (HR-only). */
export interface CodingResult {
  points: number;
  raw?: number;
  language?: string;
  submitted?: boolean;
  error?: string;
  tests?: unknown[];
}

/**
 * HR-only per-question breakdown for a single attempt.
 * `per_question` maps an MCQ question_id → whether it was answered correctly.
 * `coding` maps a coding question_id → its graded result.
 */
export interface AttemptBreakdown {
  attempt_id: string;
  score_percent: number | null;
  passed: boolean | null;
  per_question: Record<string, boolean>;
  coding: Record<string, CodingResult>;
}

export function getAttemptBreakdown(
  examId: string,
  attemptId: string,
): Promise<AttemptBreakdown> {
  return apiGet<AttemptBreakdown>(`/hr/exams/${examId}/attempts/${attemptId}/breakdown`);
}
