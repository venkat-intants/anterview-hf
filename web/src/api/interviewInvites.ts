// interviewInvites.ts — HR 'invite to interview' API (HR workflow Phase 3).
// Authenticated, tenant-scoped server-side by the HR's company.

import { apiGet, apiPost, apiPatch } from './client';
import type { InviteStatus } from './publicInterview';
import { pathId } from './pathId';

export type { InviteStatus };

/** How the candidate meets the interviewer.
 *
 *  `avatar` uses whatever avatar provider the instance is configured with;
 *  `audio` is voice only, regardless of it. Deliberately not a vendor picker —
 *  which vendor to pay is an instance-level decision with contracts and data
 *  residency behind it, not an HR form field. */
export type AvatarMode = 'avatar' | 'audio';

/** One eligible APPLICATION (B5): a person appears once per opening they are
 *  eligible in, and the invite is for that opening. */
export interface EligibleApplicant {
  /** The person. */
  id: string;
  /** The application — send it back on createInvite. */
  enrolment_id?: string | null;
  opening_title?: string | null;
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
  avatar_mode: AvatarMode;
  status: InviteStatus;
  scheduled_at: string | null;
  expires_at: string;
  created_at: string;
  composite_score: number | null;
  scorecard_id: string | null;
  /** Still `invited`, but the scheduled join window has closed: the candidate
   *  can no longer start until HR gives a new time. */
  missed_slot: boolean;
  /** The server would accept a reschedule: nobody has started the interview,
   *  no newer invite replaced it, and the application is still open. */
  can_reschedule: boolean;
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
  /** True when the server answered a repeat of an identical request with the
   *  invite it had already made: no new link, no second email, and
   *  `magic_link` is empty. */
  reused?: boolean;
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
  /** The application this interview is for. Required by the server when the
   *  person has applied to several openings. */
  enrolment_id?: string | null;
  job_title?: string;
  level?: string;
  language?: 'en' | 'hi' | 'te';
  /** Omitted means `avatar`, which is what every invite did before this existed. */
  avatar_mode?: AvatarMode;
  scheduled_at?: string | null;
  ttl_hours?: number;
  /** The application already has a live link: revoke it and send a new one.
   *  Without it the server refuses with 409 rather than replacing silently. */
  replace_existing?: boolean;
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
  return apiPost<InterviewInvite>(`/hr/interviews/${pathId(inviteId)}/revoke`, {});
}

/** Why HR ended a started interview. A fixed code, never free text: it is
 *  kept in the append-only audit log, which erasure does not redact for it. */
export type EndInterviewReason = 'candidate_left' | 'link_shared' | 'technical_problem' | 'other';

/** End an interview the candidate has started but will not finish (they
 *  dropped out, or the link was forwarded). The server refuses a started link
 *  without `force`, requires a reason, audits it and marks the session
 *  abandoned. A new link can then be sent. */
export function endStartedInterview(
  inviteId: string,
  reason: EndInterviewReason,
): Promise<InterviewInvite> {
  return apiPost<InterviewInvite>(`/hr/interviews/${pathId(inviteId)}/revoke`, {
    force: true,
    reason,
  });
}

export function getInviteResult(inviteId: string): Promise<InterviewOutcome> {
  return apiGet<InterviewOutcome>(`/hr/interviews/${pathId(inviteId)}/result`);
}

export function rescheduleInvite(inviteId: string, scheduledAt: string): Promise<InviteResult> {
  return apiPatch<InviteResult>(`/hr/interviews/${pathId(inviteId)}`, {
    scheduled_at: scheduledAt,
  });
}

/** Switch one interview between the avatar and audio-only.
 *
 *  Only while the invite is still `invited` — the server 409s once the
 *  candidate has started, because the mode is copied onto the session at that
 *  point and a later change would silently do nothing. This is the path that
 *  matters for workflow-minted invites, which nobody chose a mode for. */
export function setInviteAvatarMode(
  inviteId: string,
  avatarMode: AvatarMode,
): Promise<InterviewInvite> {
  return apiPatch<InterviewInvite>(`/hr/interviews/${pathId(inviteId)}/avatar-mode`, {
    avatar_mode: avatarMode,
  });
}
