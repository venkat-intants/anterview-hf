// jobTasks.ts — PH4-D4, the staff side of job simulations and portfolio
// rounds: HR authors the brief/items/materials, HR reads and manages
// submissions, and an interviewer reads the ONE submission their scorecard
// owns.
//
// Nothing here decides anything (CLAUDE.md constraint 9 / D-05): a
// submission is evidence, and the round's pass/hold verdict is recorded
// through the EXISTING `recordRoundReview` (api/workflows.ts) — there is no
// reject call anywhere in this module, on purpose.
//
// Field names match services/data_gateway/app/routers/job_tasks.py's
// `hr_router` and `iv_router` EXACTLY.

import { apiDelete, apiGet, apiPost, apiPut, uploadWithProgress } from './client';
import { pathId } from './pathId';

// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const API_BASE: string = import.meta.env.VITE_API_BASE_URL;

export type TaskKind = 'job_simulation' | 'portfolio';
export type TaskResponseType = 'text' | 'file' | 'link';
export type TaskLinkKind = 'repository' | 'design' | 'document' | 'video' | 'website' | 'other';
export type TaskSubmissionStatus =
  | 'assigned'
  | 'in_progress'
  | 'submitted'
  | 'expired'
  | 'withdrawn';

/** Domains a portfolio accepts when HR sets no explicit allow-list — mirrors
 *  `app.job_tasks.DEFAULT_LINK_DOMAINS` EXACTLY, so the picker's placeholder
 *  text never drifts from what the server actually accepts. */
export const DEFAULT_LINK_DOMAINS: readonly string[] = [
  'github.com',
  'gitlab.com',
  'bitbucket.org',
  'behance.net',
  'dribbble.com',
  'figma.com',
  'kaggle.com',
  'youtube.com',
  'vimeo.com',
];

export interface TaskItem {
  key: string;
  prompt: string;
  response_type: TaskResponseType;
  required: boolean;
  max_chars: number | null;
}

export interface TaskConfig {
  round_id: string;
  kind: TaskKind;
  brief: string;
  brief_translations: { hi?: string; te?: string } | null;
  items: TaskItem[];
  min_artifacts: number | null;
  max_artifacts: number | null;
  allow_files: boolean;
  allow_links: boolean;
  allowed_link_domains: string[] | null;
}

export interface TaskConfigInput {
  brief: string;
  brief_translations: { hi?: string; te?: string } | null;
  items: TaskItem[];
  min_artifacts?: number | null;
  max_artifacts?: number | null;
  allow_files?: boolean;
  allow_links?: boolean;
  allowed_link_domains?: string[] | null;
}

// ── HR — configuration ──────────────────────────────────────────────────────

/** Null when nobody has configured this round's task yet. */
export function getRoundTask(roundId: string): Promise<TaskConfig | null> {
  return apiGet<TaskConfig | null>(`/hr/rounds/${pathId(roundId)}/task`);
}

export function putRoundTask(roundId: string, body: TaskConfigInput): Promise<TaskConfig> {
  return apiPut<TaskConfig>(`/hr/rounds/${pathId(roundId)}/task`, body);
}

export interface RoundTaskMaterial {
  id: string;
  title: string;
  original_name: string | null;
  content_type: string | null;
  size_bytes: number | null;
  position: number;
}

export function listRoundTaskMaterials(roundId: string): Promise<RoundTaskMaterial[]> {
  return apiGet<RoundTaskMaterial[]>(`/hr/rounds/${pathId(roundId)}/task/materials`);
}

/** PDF/JPEG/PNG, up to `task_material_max_bytes` (10 MB) — checked by the
 *  server from the file's content, this is only so a bad upload fails fast. */
export function addRoundTaskMaterial(
  roundId: string,
  file: File,
  title: string,
  onProgress?: (pct: number) => void,
): Promise<RoundTaskMaterial> {
  const form = new FormData();
  form.append('file', file);
  form.append('title', title);
  return uploadWithProgress<RoundTaskMaterial>(
    `${API_BASE}/hr/rounds/${pathId(roundId)}/task/materials`,
    form,
    onProgress,
  );
}

export function removeRoundTaskMaterial(roundId: string, materialId: string): Promise<void> {
  return apiDelete<void>(`/hr/rounds/${pathId(roundId)}/task/materials/${pathId(materialId)}`);
}

export interface SignedDownload {
  url: string;
  expires_in: number;
}

export function downloadRoundTaskMaterial(
  roundId: string,
  materialId: string,
): Promise<SignedDownload> {
  return apiGet<SignedDownload>(
    `/hr/rounds/${pathId(roundId)}/task/materials/${pathId(materialId)}/download`,
  );
}

// ── HR — submissions ─────────────────────────────────────────────────────────

export interface TaskSubmissionSummary {
  id: string;
  enrolment_id: string;
  round_id: string;
  round_title: string | null;
  kind: TaskKind;
  status: TaskSubmissionStatus;
  candidate_name: string | null;
  due_at: string | null;
  started_at: string | null;
  submitted_at: string | null;
  attempt_no: number;
  created_at: string;
}

/** Every submission ever issued for this application, newest attempt first —
 *  a re-issue supersedes rather than replaces, so a withdrawn or expired
 *  attempt is kept in the list, not silently dropped. */
export function listEnrolmentTasks(enrolmentId: string): Promise<TaskSubmissionSummary[]> {
  return apiGet<TaskSubmissionSummary[]>(`/hr/enrolments/${pathId(enrolmentId)}/tasks`);
}

export function listRequisitionTaskSubmissions(
  requisitionId: string,
): Promise<TaskSubmissionSummary[]> {
  return apiGet<TaskSubmissionSummary[]>(
    `/hr/requisitions/${pathId(requisitionId)}/task-submissions`,
  );
}

export function downloadTaskArtifact(
  submissionId: string,
  responseId: string,
): Promise<SignedDownload> {
  return apiGet<SignedDownload>(
    `/hr/task-submissions/${pathId(submissionId)}/artifacts/${pathId(responseId)}/download`,
  );
}

/** Supersede this submission and issue a fresh link for the same round —
 *  the exam-link precedent. Refused (409) once nothing here can be reissued
 *  against (a newer link already exists, or the round has no configuration). */
export function reissueTaskSubmission(submissionId: string): Promise<TaskSubmissionSummary> {
  return apiPost<TaskSubmissionSummary>(`/hr/task-submissions/${pathId(submissionId)}/reissue`, {});
}

/** Only while the submission is still open (`assigned`/`in_progress`) — the
 *  server refuses once it has been submitted, expired or already withdrawn. */
export function withdrawTaskSubmission(
  submissionId: string,
  reason?: string,
): Promise<TaskSubmissionSummary> {
  return apiPost<TaskSubmissionSummary>(`/hr/task-submissions/${pathId(submissionId)}/withdraw`, {
    reason: reason?.trim() || null,
  });
}

// ── Interviewer — through the existing scorecard ownership check ────────────

export interface TaskResponseOut {
  id: string;
  item_key: string | null;
  response_type: TaskResponseType;
  text_value: string | null;
  link_url: string | null;
  link_kind: TaskLinkKind | null;
  title: string | null;
  description: string | null;
  original_name: string | null;
  content_type: string | null;
  size_bytes: number | null;
}

export interface ScorecardSubmission {
  submission_id: string;
  status: TaskSubmissionStatus;
  kind: TaskKind;
  submitted_at: string | null;
  materials: RoundTaskMaterial[];
  responses: TaskResponseOut[];
}

/** 404 for a round with no submission yet, and for anyone else's scorecard —
 *  ownership is enforced server-side, in the same query that loads it. */
export function getScorecardSubmission(scorecardId: string): Promise<ScorecardSubmission> {
  return apiGet<ScorecardSubmission>(`/interviewer/scorecards/${pathId(scorecardId)}/submission`);
}

export function downloadScorecardArtifact(
  scorecardId: string,
  responseId: string,
): Promise<SignedDownload> {
  return apiGet<SignedDownload>(
    `/interviewer/scorecards/${pathId(scorecardId)}/submission/artifacts/${pathId(responseId)}/download`,
  );
}
