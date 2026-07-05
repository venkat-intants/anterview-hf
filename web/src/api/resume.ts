// Resume API — upload, history, and version management.
// Mirrors data_gateway endpoints under /users/me/resume(s) (VITE_API_BASE_URL).
// Switches between mock and real backend via VITE_USE_MOCK env var.

import { uploadWithProgress, apiGet, apiPost, apiDelete } from './client';
import { simulateDelay, mockResumesResponse, mockCurrentResume } from './mock';

// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const API_BASE: string = import.meta.env.VITE_API_BASE_URL;

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true';

// ---------------------------------------------------------------------------
// Types — mirror data_gateway ResumeVersionItem / ResumeCurrentResponse / etc.
// ---------------------------------------------------------------------------

export interface ResumeUploadResponse {
  message: string;
  /** UUID of the newly created resume version. */
  resume_id: string;
  resume_s3_key: string;
  text_length: number;
}

/** A single resume version in the history list (GET /users/me/resumes). */
export interface ResumeVersionItem {
  resume_id: string;
  filename: string;
  resume_s3_key: string;
  text_length: number;
  is_current: boolean;
  /** ISO-8601 datetime string. */
  uploaded_at: string;
  /** ISO-8601 datetime string. */
  created_at: string;
  /** Pre-signed 7-day download URL; null when S3 is not configured. */
  download_url: string | null;
}

/** Response for GET /users/me/resume (current version metadata). */
export interface ResumeCurrentResponse {
  resume_id: string;
  filename: string;
  resume_s3_key: string;
  text_length: number;
  /** ISO-8601 datetime string. */
  uploaded_at: string;
  /** ISO-8601 datetime string. */
  created_at: string;
  /** Pre-signed 7-day download URL; null when S3 is not configured. */
  download_url: string | null;
}

export interface SetCurrentResumeResponse {
  message: string;
  resume_id: string;
}

export interface DeleteResumeResponse {
  message: string;
}

// ---------------------------------------------------------------------------
// uploadResume — POST /users/me/resume (B-031 backward-compat)
// ---------------------------------------------------------------------------

/**
 * Upload a PDF resume for the authenticated user.
 *
 * @param file       The PDF File object to upload (must be application/pdf, <= 5 MB)
 * @param _token     Accepted for backwards-compatibility; ignored (client injects token)
 * @param onProgress Optional progress callback, receives 0-100
 */
export function uploadResume(
  file: File,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _token?: string,
  onProgress?: (pct: number) => void,
): Promise<ResumeUploadResponse> {
  const formData = new FormData();
  formData.append('file', file);
  return uploadWithProgress<ResumeUploadResponse>(
    `${API_BASE}/users/me/resume`,
    formData,
    onProgress,
  );
}

// ---------------------------------------------------------------------------
// listResumes — GET /users/me/resumes
// ---------------------------------------------------------------------------

/**
 * Fetch all resume versions for the authenticated user, newest-first.
 * Each item includes a 7-day pre-signed download URL (null in dev without S3).
 */
export async function listResumes(): Promise<ResumeVersionItem[]> {
  if (USE_MOCK) {
    await simulateDelay(400);
    return mockResumesResponse;
  }
  return apiGet<ResumeVersionItem[]>('/users/me/resumes');
}

// ---------------------------------------------------------------------------
// getCurrentResume — GET /users/me/resume
// ---------------------------------------------------------------------------

/**
 * Fetch the currently active resume version metadata.
 * Returns null when no resume has been uploaded yet ("no resume on file" is a
 * normal empty state — the API returns 200/null, not a 404 error).
 */
export async function getCurrentResume(): Promise<ResumeCurrentResponse | null> {
  if (USE_MOCK) {
    await simulateDelay(300);
    return mockCurrentResume;
  }
  return apiGet<ResumeCurrentResponse | null>('/users/me/resume');
}

// ---------------------------------------------------------------------------
// setCurrentResume — POST /users/me/resumes/{id}/set-current
// ---------------------------------------------------------------------------

/**
 * Promote a resume version to current (is_current=true).
 * Demotes all other versions. Syncs users.resume_text for B-033 enrichment.
 *
 * @param resumeId UUID of the resume version to promote
 */
export async function setCurrentResume(resumeId: string): Promise<SetCurrentResumeResponse> {
  if (USE_MOCK) {
    await simulateDelay(400);
    return { message: 'Resume set as current.', resume_id: resumeId };
  }
  return apiPost<SetCurrentResumeResponse>(`/users/me/resumes/${resumeId}/set-current`, {});
}

// ---------------------------------------------------------------------------
// deleteResume — DELETE /users/me/resumes/{id}
// ---------------------------------------------------------------------------

/**
 * Delete a resume version from the database and S3/R2 (DPDP Act 2023 §17 erasure).
 * If the deleted version was current the next-newest is auto-promoted.
 *
 * @param resumeId UUID of the resume version to delete
 */
export async function deleteResume(resumeId: string): Promise<DeleteResumeResponse> {
  if (USE_MOCK) {
    await simulateDelay(400);
    return { message: `Resume ${resumeId} deleted.` };
  }
  return apiDelete<DeleteResumeResponse>(`/users/me/resumes/${resumeId}`);
}
