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

export interface BulkUploadResult {
  created: Applicant[];
  failed: { filename: string; error: string }[];
  created_count: number;
  failed_count: number;
}

/**
 * Bulk-upload many resumes for ONE role. The candidate name + email are
 * auto-extracted from each resume server-side (no manual entry). Append each
 * PDF under the `files` key, plus target_job_title and optionally
 * target_level / target_jd_text.
 */
export function bulkUploadApplicants(
  form: FormData,
  onProgress?: (pct: number) => void,
): Promise<BulkUploadResult> {
  return uploadWithProgress<BulkUploadResult>(
    `${API_BASE}/hr/applicants/bulk`,
    form,
    onProgress,
  );
}

export function updateApplicantStatus(
  id: string,
  status: ApplicantStatus,
): Promise<Applicant> {
  return clientFetch<Applicant>(`${API_BASE}/hr/applicants/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  });
}

export function rescoreApplicant(id: string): Promise<Applicant> {
  return apiPost<Applicant>(`/hr/applicants/${id}/rescore`, {});
}
