// applicants.ts — HR resume screening API (HR workflow Phase 1).
// Calls data_gateway (VITE_API_BASE_URL) via the central client. Tenant-scoped
// server-side by the HR's company — the frontend just consumes its own list.

import { apiGet, apiPost, clientFetch, uploadWithProgress } from './client';

// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const API_BASE: string = import.meta.env.VITE_API_BASE_URL;

export type ApplicantStatus = 'new' | 'shortlisted' | 'rejected';

export interface Applicant {
  id: string;
  full_name: string;
  /**
   * What the CV said, when it disagrees with the name on file. Null when they
   * agree — the server only sends it on a discrepancy, because "the CV says
   * the same thing" is not information.
   */
  parsed_full_name?: string | null;
  /** 'candidate' | 'hr' | 'filename' | 'resume' — who supplied the name. */
  full_name_source?: string | null;
  phone?: string | null;
  years_experience?: number | null;
  current_company?: string | null;
  current_title?: string | null;
  linkedin_url?: string | null;
  github_url?: string | null;
  email: string | null;
  target_job_title: string;
  target_level: string;
  status: ApplicantStatus;
  /** ATS fit score 0-100, or null until scored. */
  ats_overall: number | null;
  ats_breakdown: Record<string, number> | null;
  ats_strengths: string[] | null;
  ats_concerns: string[] | null;
  ats_recommendation: string | null;
  ats_summary: string | null;
  /**
   * True while this resume is stored but not yet read. The name comes from the
   * filename and there is no score yet, so it must render as in-progress — an
   * unscored row and a badly scored one look identical in a list and mean
   * opposite things.
   */
  pending_enrichment?: boolean;
  created_at: string;
  /** Linked candidate user id (set after interview-invite redeem); null otherwise. */
  user_id?: string | null;
  /** Relevance 0-100 for the active semantic search; present only on ?q= results. */
  match_score?: number | null;
}

export interface ListApplicantsParams {
  status?: ApplicantStatus;
  /** Hybrid semantic + exact-keyword search phrase. */
  q?: string;
  /** Filter by target job title (contains). */
  job?: string;
}

/**
 * Applicant list for the caller's company. Default: ranked by ATS score. With
 * `q` it becomes a hybrid semantic + exact-keyword search and each result
 * carries a `match_score`. `status` / `job` stack with either mode.
 */
export function listApplicants(params: ListApplicantsParams = {}): Promise<Applicant[]> {
  const qs = new URLSearchParams();
  if (params.status) qs.set('status', params.status);
  if (params.q && params.q.trim()) qs.set('q', params.q.trim());
  if (params.job && params.job.trim()) qs.set('job', params.job.trim());
  const suffix = qs.toString();
  return apiGet<Applicant[]>(`/hr/applicants${suffix ? `?${suffix}` : ''}`);
}

/** Lazy one-sentence explanation of why a candidate matches the search phrase. */
export function whyMatch(id: string, q: string): Promise<{ reason: string }> {
  return apiGet<{ reason: string }>(
    `/hr/applicants/${id}/why-match?q=${encodeURIComponent(q)}`,
  );
}

export interface ReindexResult {
  reindexed: number;
  failed: number;
  remaining: number;
}

/** How many of this company's applicants still lack a search embedding. */
export function getReindexStatus(): Promise<ReindexResult> {
  return apiGet<ReindexResult>('/hr/applicants/reindex-status');
}

/** Backfill embeddings for existing applicants (process one batch; call until remaining=0). */
export function reindexApplicants(): Promise<ReindexResult> {
  return apiPost<ReindexResult>('/hr/applicants/reindex', {});
}

export function getApplicant(id: string): Promise<Applicant> {
  return apiGet<Applicant>(`/hr/applicants/${id}`);
}

/**
 * Upload + auto-score an applicant resume. `form` must contain: file (PDF),
 * full_name, target_job_title, and optionally email, target_level, target_jd_text.
 */
export function uploadApplicant(
  form: FormData,
  onProgress?: (pct: number) => void,
): Promise<Applicant> {
  return uploadWithProgress<Applicant>(`${API_BASE}/hr/applicants`, form, onProgress);
}

/** What the server says when it accepts a bulk upload — before anything is read (E5). */
export interface BulkUploadAccepted {
  batch_id: string;
  requisition_id: string;
  total_files: number;
  /** Stored and queued for the background reader. */
  accepted: number;
  failed_count: number;
  /** Refused at upload — not a PDF, empty, too large, or not stored. */
  failed: { filename: string; error: string }[];
}

/** One bulk upload's progress through the background reader and scorer (E5). */
export interface UploadProgress {
  batch_id: string;
  requisition_id: string;
  requisition_title: string;
  uploaded_by: string | null;
  total_files: number;
  /** Stored, waiting to be read. */
  queued: number;
  /** Became applicants under the opening. */
  created: number;
  /** Refused at upload, unreadable, or given up on — each with a reason. */
  failed: number;
  /** Added, and still being read and scored. */
  being_scored: number;
  finished: boolean;
  created_at: string;
  finished_at: string | null;
  failures?: { filename: string; error: string | null }[];
}

/**
 * Upload many resumes for ONE opening: each PDF under `files`, plus
 * `requisition_id`. Returns as soon as the files are STORED. Reading them,
 * filing each candidate under the opening and scoring happen in the background
 * — follow them with getUploadProgress(batch_id).
 */
export function bulkUploadApplicants(
  form: FormData,
  onProgress?: (pct: number) => void,
): Promise<BulkUploadAccepted> {
  return uploadWithProgress<BulkUploadAccepted>(
    `${API_BASE}/hr/applicants/bulk`,
    form,
    onProgress,
  );
}

export function getUploadProgress(batchId: string): Promise<UploadProgress> {
  return apiGet<UploadProgress>(`/hr/uploads/${batchId}`);
}

/** Recent bulk uploads, newest first — for one opening when given. */
export function listUploads(requisitionId?: string): Promise<UploadProgress[]> {
  const q = requisitionId ? `?requisition_id=${encodeURIComponent(requisitionId)}` : '';
  return apiGet<UploadProgress[]>(`/hr/uploads${q}`);
}

/**
 * Set a status. `enrolmentId` names the application it is about (B5) — the
 * server refuses a change for someone with several applications that does not
 * say which.
 *
 * `reason` and `reasonCode` (O4) are REQUIRED by the server when `status` is
 * 'rejected' (the only terminal status this endpoint can set — 'hired' is not
 * a member of ApplicantStatus, so it never reaches this call): reason needs
 * at least 3 characters, or 10 when the chosen reason has
 * requires_explanation. A non-terminal status (e.g. 'shortlisted') needs
 * neither and the server does not ask for them.
 */
export function updateApplicantStatus(
  id: string,
  status: ApplicantStatus,
  enrolmentId?: string | null,
  reason?: string,
  reasonCode?: string,
): Promise<Applicant> {
  return clientFetch<Applicant>(`${API_BASE}/hr/applicants/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({
      status,
      ...(enrolmentId ? { enrolment_id: enrolmentId } : {}),
      ...(reason ? { reason } : {}),
      ...(reasonCode ? { reason_code: reasonCode } : {}),
    }),
  });
}

/** One of a person's applications, with ITS assessment (B5). The applicant's own
 *  ats_* fields describe only their latest application. */
export interface Application {
  enrolment_id: string;
  requisition_id: string | null;
  opening_title: string | null;
  /** Derived, as on the pipeline board. */
  status: string;
  /** What is recorded on the application — what a status change compares with. */
  stored_status: string;
  ats_overall: number | null;
  ats_breakdown: Record<string, number> | null;
  ats_strengths: string[] | null;
  ats_concerns: string[] | null;
  ats_recommendation: string | null;
  ats_summary: string | null;
  best_exam_percent: number | null;
  exam_passed: boolean | null;
  interview_score: number | null;
  scorecard_id: string | null;
  applied_at: string;
  is_latest: boolean;
}

/** Every live application this person holds, oldest first. */
export function listApplications(applicantId: string): Promise<Application[]> {
  return apiGet<Application[]>(`/hr/applicants/${applicantId}/applications`);
}

export function rescoreApplicant(id: string): Promise<Applicant> {
  return apiPost<Applicant>(`/hr/applicants/${id}/rescore`, {});
}

/* ── Per-round scores (C8 two-layer scoring) ────────────────────────────── */

/** One competency's score inside a round, with the evidence behind it. */
export interface CriterionScore {
  competency_id: string;
  name: string;
  score: number | null;
  evidence: string | null;
}

/**
 * One round a candidate has sat, in both layers.
 *
 * `criteria` is the evaluation that decided progression — it varies per round.
 * `axes` is the frozen four-axis comparison, present on interview rounds only,
 * which is what keeps composites comparable across roles (D-02).
 */
export interface RoundResult {
  round_id: string;
  round_title: string;
  position: number;
  kind: string;
  percent: number | null;
  passed: boolean | null;
  graded_by: string;
  evidence: string | null;
  criteria: CriterionScore[];
  axes: Record<string, number>;
  created_at: string;
}

/**
 * Why a candidate scored what they scored.
 *
 * Superseded retakes are excluded server-side, so this is what actually
 * counted. Empty for a candidate who has not sat a scored round yet — which
 * is a normal state, not an error.
 */
/** Per-round results — for one application when `enrolmentId` is given. */
export function listRoundResults(
  applicantId: string,
  enrolmentId?: string | null,
): Promise<RoundResult[]> {
  const q = enrolmentId ? `?enrolment_id=${encodeURIComponent(enrolmentId)}` : '';
  return apiGet<RoundResult[]>(`/hr/applicants/${applicantId}/round-results${q}`);
}
