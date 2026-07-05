// publicInterview.ts — applicant interview magic-link API (HR workflow Phase 3).
//
// PUBLIC: no account. The opaque token lives in the URL #fragment and is sent in
// the X-Interview-Token header. skipAuth so the central client never injects the
// user session nor bounces a 401 to /login — a bad link surfaces in-page.

import { clientFetch } from './client';

// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const API_BASE: string = import.meta.env.VITE_API_BASE_URL;

export type InviteStatus = 'invited' | 'consumed' | 'completed' | 'expired' | 'revoked';

export interface InterviewInviteInfo {
  applicant_name: string;
  job_title: string;
  level: string;
  language: 'en' | 'hi' | 'te';
  status: InviteStatus;
  already_completed: boolean;
  scheduled_at: string | null;
}

export interface InterviewRedeem {
  session_id: string;
  access_token: string;
  language: 'en' | 'hi' | 'te';
  user_id: string;
  full_name: string;
  email: string | null;
  roles: string[];
}

function tokenHeaders(token: string): Record<string, string> {
  return { 'X-Interview-Token': token };
}

export function getInterviewInvite(token: string): Promise<InterviewInviteInfo> {
  return clientFetch<InterviewInviteInfo>(`${API_BASE}/interview-invite`, {
    skipAuth: true,
    headers: tokenHeaders(token),
  });
}

export function redeemInterviewInvite(
  token: string,
  consentGranted: boolean,
): Promise<InterviewRedeem> {
  return clientFetch<InterviewRedeem>(`${API_BASE}/interview-invite/redeem`, {
    method: 'POST',
    skipAuth: true,
    headers: tokenHeaders(token),
    body: JSON.stringify({ consent_granted: consentGranted }),
  });
}

/**
 * Resume an in-progress interview after a page reload, using the httpOnly resume
 * cookie that redeem set (sent automatically — clientFetch always uses
 * credentials:'include'). No token in the URL, no login. Rejects if the link
 * can't resume (expired / finished / revoked); the caller then shows a
 * re-open-link prompt instead of bouncing to /login.
 */
export function resumeInterview(): Promise<InterviewRedeem> {
  return clientFetch<InterviewRedeem>(`${API_BASE}/interview-invite/resume`, {
    method: 'POST',
    skipAuth: true,
  });
}
