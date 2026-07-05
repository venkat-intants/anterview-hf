// interviewInvites.ts — HR 'invite to interview' API (HR workflow Phase 3).
// Authenticated, tenant-scoped server-side by the HR's company.

import { apiGet, apiPost, apiPatch } from './client';
import type { InviteStatus } from './publicInterview';

export type { InviteStatus };

export interface EligibleApplicant {
  id: string;
  full_name: string;
  target_job_title: string;
  target_level: string;
  status: string;
  ats_overall: number | null;
  passed_exam: boolean;
  has_active_invite: boolean;
}

export interface InterviewInvite {
  invite_id: string;
  applicant_id: string;
  applicant_name: string;
  job_title: string;
  language: string;
  status: InviteStatus;
  scheduled_at: string | null;
  expires_at: string;
  created_at: string;
  composite_score: number | null;
  scorecard_id: string | null;
}

export interface InviteResult {
  invite_id: string;
  applicant_id: string;
  applicant_name: string;
  job_title: string;
  magic_link: string; // raw token embedded — returned ONCE, at mint time only
  expires_at: string;
  scheduled_at: string | null;
  status: InviteStatus;
}

export interface InterviewOutcome {
  invite_id: string;
  applicant_id: string;
  applicant_name: string;
  status: InviteStatus;
  session_status: string | null;
  scorecard_id: string | null;
  composite_score: number | null;
  scores: Record<string, number> | null;
  strengths: string[] | null;
  improvements: unknown[] | null;
  summary: string | null;
}

export interface InviteCreateInput {
  applicant_id: string;
  job_title?: string;
  level?: string;
  language?: 'en' | 'hi' | 'te';
  scheduled_at?: string | null;
  ttl_hours?: number;
}

export function listEligibleApplicants(
  source: 'any' | 'shortlisted' | 'exam_passed' = 'any',
): Promise<EligibleApplicant[]> {
  return apiGet<EligibleApplicant[]>(
    `/hr/interviews/eligible-applicants?source=${encodeURIComponent(source)}`,
  );
}

export function listInvites(status?: InviteStatus): Promise<InterviewInvite[]> {
  const q = status ? `?status=${encodeURIComponent(status)}` : '';
  return apiGet<InterviewInvite[]>(`/hr/interviews${q}`);
}

export function createInvite(input: InviteCreateInput): Promise<InviteResult> {
  return apiPost<InviteResult>('/hr/interviews', input);
}

export function revokeInvite(inviteId: string): Promise<InterviewInvite> {
  return apiPost<InterviewInvite>(`/hr/interviews/${inviteId}/revoke`, {});
}

export function getInviteResult(inviteId: string): Promise<InterviewOutcome> {
  return apiGet<InterviewOutcome>(`/hr/interviews/${inviteId}/result`);
}

export function rescheduleInvite(
  inviteId: string,
  scheduledAt: string,
): Promise<InviteResult> {
  return apiPatch<InviteResult>(`/hr/interviews/${inviteId}`, { scheduled_at: scheduledAt });
}
