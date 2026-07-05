// JD (Job Description) upload API — uses the central uploadWithProgress helper
// which uses XMLHttpRequest to report real upload progress and injects the
// current Bearer token from the token store automatically.
// Does NOT set Content-Type — the browser sets multipart/form-data boundary.

import { uploadWithProgress } from './client';

// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const API_BASE: string = import.meta.env.VITE_API_BASE_URL;

export interface JdUploadResponse {
  message: string;
  jd_s3_key: string;
  text_length: number;
}

/**
 * Upload a PDF job description for a specific job posting.
 *
 * @param jobId      UUID of the job to attach the JD to
 * @param file       The PDF File object to upload (must be application/pdf, ≤ 10 MB)
 * @param _token     Accepted for backwards-compatibility; ignored (client injects token)
 * @param onProgress Optional progress callback, receives 0–100
 */
export function uploadJd(
  jobId: string,
  file: File,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _token?: string,
  onProgress?: (pct: number) => void,
): Promise<JdUploadResponse> {
  const formData = new FormData();
  formData.append('file', file);
  return uploadWithProgress<JdUploadResponse>(
    `${API_BASE}/jobs/${jobId}/jd-document`,
    formData,
    onProgress,
  );
}
