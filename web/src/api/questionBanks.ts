// questionBanks.ts — PH4-D1. Reusable question banks, and the locking of
// published exam content.
//
// Three families, matching services/data_gateway/app/routers/question_banks.py
// EXACTLY (`svc.bank_summary`, `svc.question_out`, `svc.search`,
// `svc.review_queue`, `svc.get_question`):
//   `/hr`    — HR managers: banks, authoring, review (never your own question),
//              search, and copying into (or out of) an exam section.
//   `/admin` — the company super admin: the same review actions, so a
//              single-HR company is never blocked on a second approver.
//
// Every id interpolated into a URL goes through `pathId`.

import {
  apiDelete,
  apiGet,
  apiPatch,
  apiPost,
  fetchBlobWithAuth,
  uploadWithProgress,
} from './client';

// client.ts does not export this — api/exams.ts declares its own the same way.
// (The build caught it; `tsc --noEmit` did not, which is worth knowing about
// this project's local gate: an unresolved named import reached rollup.)
const API_BASE: string = import.meta.env.VITE_API_BASE_URL;
import { pathId } from './pathId';
import type { CodingTestCase, ExamLanguage } from './exams';

export type BankQuestionStatus = 'draft' | 'in_review' | 'approved' | 'retired';
export type BankQuestionKind = 'mcq' | 'coding';
export type BankDifficulty = 'easy' | 'medium' | 'hard';
export type BankQuestionOrigin = 'authored' | 'ai_draft' | 'from_exam';

export interface Competency {
  id: string;
  name: string;
}

export interface BankOut {
  id: string;
  name: string;
  description: string | null;
  /** Question count keyed by status, e.g. {draft: 2, approved: 5}. */
  counts: Partial<Record<BankQuestionStatus, number>>;
  used_in_exams: number;
  created_at: string;
  updated_at: string;
}

export interface BankQuestion {
  id: string;
  bank_id: string;
  root_id: string;
  version: number;
  kind: BankQuestionKind;
  prompt: string;
  points: number;
  options: string[] | null;
  correct_index: number | null;
  starter_code: string | null;
  reference_solution: string | null;
  allowed_languages: string[] | null;
  test_cases: CodingTestCase[] | null;
  time_limit_ms: number | null;
  difficulty: BankDifficulty;
  language: ExamLanguage;
  competencies: Competency[];
  tags: string[];
  status: BankQuestionStatus;
  origin: BankQuestionOrigin;
  content_hash: string;
  created_by_user_id: string | null;
  submitted_by_user_id: string | null;
  submitted_at: string | null;
  reviewed_by_user_id: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  retired_by_user_id: string | null;
  retired_at: string | null;
  created_at: string;
  updated_at: string;
}

/** A row from the cross-bank picker search — the exam editor's "Add from
 *  bank" reads `already_in_exam` to disable a row it cannot add twice. */
export interface BankQuestionSearchRow extends BankQuestion {
  bank_name: string;
  already_in_exam: boolean;
}

/** A row in the review queue — `own_submission` is why the approve/request-
 *  changes controls are disabled for a question you wrote or submitted. */
export interface ReviewQueueRow extends BankQuestion {
  bank_name: string;
  own_submission: boolean;
  reason: string | null;
}

export interface BankQuestionDetail {
  question: BankQuestion;
  /** Every version in this lineage, oldest first. */
  versions: BankQuestion[];
  used_in_exams: { exam_id: string; exam_title: string }[];
}

export interface BankCreateInput {
  name: string;
  description?: string | null;
}

export interface BankUpdateInput {
  name?: string;
  description?: string | null;
}

/** The shape of one bank question, either kind — mirrors `BankQuestionIn` on
 *  the server, which validates it through the SAME Pydantic models the exam-
 *  authoring routes use. */
export interface BankQuestionInput {
  kind: BankQuestionKind;
  prompt: string;
  points?: number;
  difficulty?: BankDifficulty;
  language?: ExamLanguage;
  competencies?: Competency[];
  tags?: string[];
  options?: string[];
  correct_index?: number;
  starter_code?: string | null;
  reference_solution?: string | null;
  allowed_languages?: string[];
  test_cases?: CodingTestCase[];
  time_limit_ms?: number;
}

/** A draft-only patch — `kind` is fixed at creation and never appears here,
 *  matching `BankQuestionUpdateIn` on the server. */
export type BankQuestionUpdateInput = Partial<Omit<BankQuestionInput, 'kind'>>;

export interface BankQuestionFilters {
  q?: string;
  kind?: BankQuestionKind;
  difficulty?: BankDifficulty;
  language?: ExamLanguage;
  competency?: string;
  tag?: string;
  status?: BankQuestionStatus | '';
}

function filterQuery(f: BankQuestionFilters, extra: Record<string, string> = {}): string {
  const p = new URLSearchParams();
  if (f.q) p.set('q', f.q);
  if (f.kind) p.set('kind', f.kind);
  if (f.difficulty) p.set('difficulty', f.difficulty);
  if (f.language) p.set('language', f.language);
  if (f.competency) p.set('competency', f.competency);
  if (f.tag) p.set('tag', f.tag);
  if (f.status) p.set('status', f.status);
  for (const [k, v] of Object.entries(extra)) p.set(k, v);
  const s = p.toString();
  return s ? `?${s}` : '';
}

// ---------------------------------------------------------------------------
// Banks
// ---------------------------------------------------------------------------

export function listQuestionBanks(): Promise<BankOut[]> {
  return apiGet<BankOut[]>('/hr/question-banks');
}

export function createQuestionBank(body: BankCreateInput): Promise<BankOut> {
  return apiPost<BankOut>('/hr/question-banks', body);
}

export function updateQuestionBank(bankId: string, body: BankUpdateInput): Promise<BankOut> {
  return apiPatch<BankOut>(`/hr/question-banks/${pathId(bankId)}`, body);
}

export function archiveQuestionBank(bankId: string): Promise<{ id: string; archived: boolean }> {
  return apiPost<{ id: string; archived: boolean }>(
    `/hr/question-banks/${pathId(bankId)}/archive`,
    {},
  );
}

// ---------------------------------------------------------------------------
// Questions within a bank
// ---------------------------------------------------------------------------

export function listBankQuestions(
  bankId: string,
  filters: BankQuestionFilters = {},
): Promise<BankQuestion[]> {
  return apiGet<BankQuestion[]>(
    `/hr/question-banks/${pathId(bankId)}/questions${filterQuery(filters)}`,
  );
}

export function createBankQuestion(
  bankId: string,
  body: BankQuestionInput,
): Promise<BankQuestion> {
  return apiPost<BankQuestion>(`/hr/question-banks/${pathId(bankId)}/questions`, body);
}

/** The AI-preview "add all" path — every question lands as `origin: 'ai_draft'`. */
export function createBankQuestionsBulk(
  bankId: string,
  questions: BankQuestionInput[],
): Promise<BankQuestion[]> {
  return apiPost<BankQuestion[]>(`/hr/question-banks/${pathId(bankId)}/questions/bulk`, {
    questions,
  });
}

// ---------------------------------------------------------------------------
// Cross-bank search, the competency catalogue, the review queue
// ---------------------------------------------------------------------------

/** The picker's search — approved questions of a kind, across every bank in
 *  the company, flagging any already copied into `excludeExamId`. */
export function searchBankQuestions(
  filters: BankQuestionFilters & { bankId?: string; excludeExamId?: string } = {},
): Promise<BankQuestionSearchRow[]> {
  const extra: Record<string, string> = {};
  if (filters.bankId) extra.bank_id = pathId(filters.bankId);
  if (filters.excludeExamId) extra.exclude_exam_id = pathId(filters.excludeExamId);
  return apiGet<BankQuestionSearchRow[]>(`/hr/bank-questions${filterQuery(filters, extra)}`);
}

export function listBankCompetencies(): Promise<Competency[]> {
  return apiGet<Competency[]>('/hr/bank-questions/competencies');
}

export function listBankReviewQueue(): Promise<ReviewQueueRow[]> {
  return apiGet<ReviewQueueRow[]>('/hr/bank-questions/review-queue');
}

// ---------------------------------------------------------------------------
// One question
// ---------------------------------------------------------------------------

export function getBankQuestion(qid: string): Promise<BankQuestionDetail> {
  return apiGet<BankQuestionDetail>(`/hr/bank-questions/${pathId(qid)}`);
}

/** Draft only — the server 409s anything else. */
export function updateBankQuestion(
  qid: string,
  body: BankQuestionUpdateInput,
): Promise<BankQuestion> {
  return apiPatch<BankQuestion>(`/hr/bank-questions/${pathId(qid)}`, body);
}

/** Draft only, and never a question already copied into an exam. */
export function deleteBankQuestion(qid: string): Promise<void> {
  return apiDelete<void>(`/hr/bank-questions/${pathId(qid)}`);
}

export function submitBankQuestion(qid: string): Promise<BankQuestion> {
  return apiPost<BankQuestion>(`/hr/bank-questions/${pathId(qid)}/submit`, {});
}

/** Only the person who submitted it may withdraw — a 403 otherwise, shown as written. */
export function withdrawBankQuestion(qid: string): Promise<BankQuestion> {
  return apiPost<BankQuestion>(`/hr/bank-questions/${pathId(qid)}/withdraw`, {});
}

export function approveBankQuestion(qid: string, note?: string | null): Promise<BankQuestion> {
  return apiPost<BankQuestion>(`/hr/bank-questions/${pathId(qid)}/approve`, {
    note: note?.trim() || null,
  });
}

/** The server requires a note of at least 5 characters. */
export function requestBankQuestionChanges(qid: string, note: string): Promise<BankQuestion> {
  return apiPost<BankQuestion>(`/hr/bank-questions/${pathId(qid)}/request-changes`, {
    note: note.trim(),
  });
}

export function newBankQuestionVersion(
  qid: string,
  body: BankQuestionUpdateInput,
): Promise<BankQuestion> {
  return apiPost<BankQuestion>(`/hr/bank-questions/${pathId(qid)}/new-version`, body);
}

export function retireBankQuestion(qid: string): Promise<BankQuestion> {
  return apiPost<BankQuestion>(`/hr/bank-questions/${pathId(qid)}/retire`, {});
}

// ---------------------------------------------------------------------------
// Copying into (or out of) an exam
// ---------------------------------------------------------------------------

export interface AddToSectionResult {
  added: number;
  skipped: { id: string; reason: string }[];
}

export function addBankQuestionsToSection(
  examId: string,
  sectionId: string,
  ids: string[],
): Promise<AddToSectionResult> {
  return apiPost<AddToSectionResult>(
    `/hr/exams/${pathId(examId)}/sections/${pathId(sectionId)}/bank-questions`,
    { ids },
  );
}

export interface SaveToBankInput {
  bank_id: string;
  difficulty?: BankDifficulty;
  language?: ExamLanguage;
  competencies?: Competency[];
  tags?: string[];
}

export function saveExamQuestionToBank(
  examId: string,
  questionId: string,
  body: SaveToBankInput,
): Promise<BankQuestion> {
  return apiPost<BankQuestion>(
    `/hr/exams/${pathId(examId)}/questions/${pathId(questionId)}/save-to-bank`,
    body,
  );
}

export function saveCodingQuestionToBank(
  examId: string,
  questionId: string,
  body: SaveToBankInput,
): Promise<BankQuestion> {
  return apiPost<BankQuestion>(
    `/hr/exams/${pathId(examId)}/coding-questions/${pathId(questionId)}/save-to-bank`,
    body,
  );
}

// ---------------------------------------------------------------------------
// The other two ways to fill a bank: AI, and a spreadsheet.
//
// Authoring one at a time was the only way in until 2026-10-03, while an
// EXAM's section had both of these — backwards, since the bank is the thing
// worth filling once and reusing.
// ---------------------------------------------------------------------------

export interface BankGenerateParams {
  topic: string;
  num_questions?: number;
  difficulty?: BankDifficulty;
  language?: ExamLanguage;
  /** Role context: lets the generator spread questions across what the role is
   *  actually assessed on, instead of the topic alone. */
  job_title?: string;
  experience_level?: string;
}

export interface BankImportRowError {
  /** 1-based, exactly as the spreadsheet numbers it — header included. */
  row: number;
  message: string;
}

export interface BankImportResult {
  added: number;
  errors: BankImportRowError[];
  questions: BankQuestion[];
}

export interface BulkReviewResult {
  acted: number;
  /** Not an error list: the two-person rule answering, per question. */
  skipped: { question_id: string; reason: string }[];
}

/** Draft MCQs with the configured LLM — returned for PREVIEW, nothing saved.
 *  `createBankQuestionsBulk` is what stores the ones HR keeps. */
export function generateBankQuestions(
  bankId: string,
  params: BankGenerateParams,
): Promise<{ questions: BankQuestionInput[] }> {
  return apiPost<{ questions: BankQuestionInput[] }>(
    `/hr/question-banks/${pathId(bankId)}/questions/generate`,
    params,
  );
}

/** Upload an .xlsx/.csv; the server parses it and saves the valid rows as
 *  DRAFTS. `difficulty` and `language` are the defaults for rows that leave
 *  those columns blank, so a plain seven-column exam sheet imports too. */
export function importBankQuestions(
  bankId: string,
  file: File,
  opts: { difficulty?: BankDifficulty; language?: ExamLanguage } = {},
): Promise<BankImportResult> {
  const fd = new FormData();
  fd.append('file', file);
  const qs = new URLSearchParams();
  if (opts.difficulty) qs.set('difficulty', opts.difficulty);
  if (opts.language) qs.set('language', opts.language);
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  return uploadWithProgress<BankImportResult>(
    `${API_BASE}/hr/question-banks/${pathId(bankId)}/questions/import${suffix}`,
    fd,
  );
}

/** Fetch the .xlsx template (auth-scoped) and trigger a browser download. */
export async function downloadBankQuestionTemplate(): Promise<void> {
  // The .xlsx body must bypass clientFetch's JSON parsing but keep its
  // refresh-on-401 — fetchBlobWithAuth is the helper that does both.
  const res = await fetchBlobWithAuth(`${API_BASE}/hr/question-bank-template`);
  if (!res.ok) throw new Error(`Could not download template (HTTP ${res.status})`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'question-bank-template.xlsx';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** Submit every draft in this bank for review. */
export function submitAllBankQuestions(bankId: string): Promise<BulkReviewResult> {
  return apiPost<BulkReviewResult>(
    `/hr/question-banks/${pathId(bankId)}/questions/submit-all`,
    {},
  );
}

/**
 * Approve every submitted question in this bank that YOU may approve.
 *
 * The two-person rule is not relaxed: the server loops the same review() the
 * per-question button calls, so the author, the submitter and anyone who edited
 * a question's content are each refused — per question, with the reason, in
 * `skipped`. A one-person bank therefore approves nothing here, and the UI
 * shows that rather than a success.
 */
export function approveAllBankQuestions(bankId: string): Promise<BulkReviewResult> {
  return apiPost<BulkReviewResult>(
    `/hr/question-banks/${pathId(bankId)}/questions/approve-all`,
    {},
  );
}

// ---------------------------------------------------------------------------
// Super admin — the same review actions (never their own question either).
// ---------------------------------------------------------------------------

export function listAdminQuestionReviews(): Promise<ReviewQueueRow[]> {
  return apiGet<ReviewQueueRow[]>('/admin/question-reviews');
}

export function approveBankQuestionAdmin(
  qid: string,
  note?: string | null,
): Promise<BankQuestion> {
  return apiPost<BankQuestion>(`/admin/bank-questions/${pathId(qid)}/approve`, {
    note: note?.trim() || null,
  });
}

export function requestBankQuestionChangesAdmin(
  qid: string,
  note: string,
): Promise<BankQuestion> {
  return apiPost<BankQuestion>(`/admin/bank-questions/${pathId(qid)}/request-changes`, {
    note: note.trim(),
  });
}
