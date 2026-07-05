// Sessions API — switches between mock and real backend via VITE_USE_MOCK env var.
// Mirrors interview_core POST /api/sessions and GET /api/sessions endpoints
// (port 8001 / VITE_INTERVIEW_API_URL).

import type { CreateSessionRequest, CreateSessionResponse } from '../types/interview';
import { simulateDelay, mockSessionsResponse } from './mock';
import { interviewPost, clientFetch } from './client';

// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const INTERVIEW_BASE: string = import.meta.env.VITE_INTERVIEW_API_URL;

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true';

// ---------------------------------------------------------------------------
// Types — mirror interview_core SessionListItem / SessionListResponse
// ---------------------------------------------------------------------------

export interface SessionListItem {
  session_id: string;
  job_title: string;
  language: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  duration_seconds: number | null;
  created_at: string;
  scorecard_id: string | null;
}

export interface SessionListResponse {
  items: SessionListItem[];
  total: number;
  page: number;
  per_page: number;
}

export interface ListSessionsParams {
  page?: number;
  perPage?: number;
  status?: string;
}

// ---------------------------------------------------------------------------
// createSession
// ---------------------------------------------------------------------------

/**
 * Create a new interview session.
 * The `_token` parameter is accepted for backwards-compatibility but ignored —
 * the central client injects the current token automatically.
 */
export async function createSession(
  request: CreateSessionRequest,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _token?: string,
): Promise<CreateSessionResponse> {
  if (USE_MOCK) {
    await simulateDelay(600);
    return {
      session_id: `mock-sess-${request.job_id.slice(0, 8)}`,
      language: request.language,
    };
  }
  // Include avatar_id only when explicitly set; null/undefined omitted so the
  // backend default ("anna") applies when the picker fails to load.
  const body: CreateSessionRequest = {
    job_id: request.job_id,
    language: request.language,
    ...(request.avatar_id != null ? { avatar_id: request.avatar_id } : {}),
  };
  return interviewPost<CreateSessionResponse>('/api/sessions', body);
}

// ---------------------------------------------------------------------------
// listSessions
// ---------------------------------------------------------------------------

/**
 * Fetch the authenticated user's interview sessions, newest-first.
 * Maps to GET /api/sessions on interview_core (VITE_INTERVIEW_API_URL).
 *
 * @param params.page    1-indexed page number (default 1)
 * @param params.perPage Items per page, 1–100 (default 20)
 * @param params.status  Optional status filter: created | in_progress | completed | abandoned | failed
 */
export async function listSessions(params: ListSessionsParams = {}): Promise<SessionListResponse> {
  if (USE_MOCK) {
    await simulateDelay(500);
    return mockSessionsResponse;
  }

  const { page = 1, perPage = 20, status } = params;
  const qs = new URLSearchParams();
  qs.set('page', String(page));
  qs.set('per_page', String(perPage));
  if (status !== undefined) qs.set('status', status);

  const url = `${INTERVIEW_BASE}/api/sessions?${qs.toString()}`;
  return clientFetch<SessionListResponse>(url);
}
