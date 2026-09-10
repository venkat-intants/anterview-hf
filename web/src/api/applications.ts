// applications.ts — what a candidate can see about their own applications.
//
// The other side of `publicApply.ts`. That module is how a stranger applies;
// this one is how they follow what happened next, once they have an account.
//
// Two things the shapes here deliberately do not carry, because the API does
// not send them: any score, and any threshold. A candidate sees which stage
// they are at and what happens next — never an evaluation of themselves. The
// reasoning lives in `candidate_applications.py`; the useful thing to know
// here is that adding an `ats_overall` to these interfaces would not make one
// appear, and would be an argument to have on the server.

import { apiGet, apiPost } from './client';

/** One application, from the candidate's side. */
export interface MyApplication {
  id: string;
  job_title: string;
  company_name: string;
  applied_at: string;
  updated_at: string;
  /**
   * A candidate-facing label — "Under review", not the internal status. The
   * server maps it, so the words stay consistent between the page and the
   * emails and cannot drift apart in two codebases.
   */
  stage: string;
  next_step: string;
  closed: boolean;
  /** All null until the first round is assigned, which is most of the wait. */
  current_round_title: string | null;
  current_round_kind: string | null;
  round_number: number | null;
  total_rounds: number | null;
  /**
   * A live interview invitation, when one is waiting. Null the rest of the time,
   * and null once the interview has been started.
   *
   * Deliberately NOT a link. Only the HMAC of an invite token is stored, so a
   * usable URL cannot be rebuilt server-side — asking for one is a separate
   * call that rotates the token, rather than something a list hands out on
   * every render.
   */
  interview_invite_id: string | null;
  interview_scheduled_at: string | null;
}

export interface StageEvent {
  stage: string;
  occurred_at: string;
  /** False when the workflow moved them rather than a person. */
  by_a_person: boolean;
}

export interface MyApplicationDetail extends MyApplication {
  history: StageEvent[];
}

/**
 * Every opening this candidate has applied to, newest first.
 *
 * An empty array is a normal answer, not an error: a practice-only account has
 * no applications, and neither does an applicant who has not activated the
 * account their application created.
 */
export function listMyApplications(): Promise<MyApplication[]> {
  return apiGet<MyApplication[]>('/users/me/applications');
}

export function getMyApplication(id: string): Promise<MyApplicationDetail> {
  return apiGet<MyApplicationDetail>(`/users/me/applications/${id}`);
}

/**
 * An opening a signed-in candidate could apply to, right now.
 *
 * Cross-tenant, unlike the public careers board — see the endpoint's own note.
 * The short version: these are the same rows the board already serves to the
 * whole internet, and a candidate account whose job list is empty unless
 * somebody sends them a link is not much of an account.
 */
export interface OpenRole {
  requisition_id: string;
  title: string;
  company_name: string;
  company_slug: string;
  level: string;
  department: string | null;
  location: string | null;
  employment_type: string | null;
  experience_min_years: number | null;
  experience_max_years: number | null;
  skills: string[];
  posted_at: string;
  /** Null covers a withheld band and an unrecorded one alike. */
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
  /** So a card says "applied" instead of inviting a second application. */
  already_applied: boolean;
}

/** Newest first, and nothing else — no ranking, no promotion. */
export function listOpenRoles(): Promise<OpenRole[]> {
  return apiGet<OpenRole[]>('/users/me/open-roles');
}

/** A freshly minted link to an interview this candidate was invited to. */
export interface InterviewLink {
  interview_url: string;
  expires_at: string;
}

/**
 * Mint a working link to the candidate's own interview.
 *
 * The invitation is emailed, but email is not a dependable channel: filtered,
 * mistyped or caught by a local mail sink, it leaves an interview that exists
 * and cannot be reached. This is the path that does not need mail.
 *
 * Each call ROTATES the token, so any previously issued link — including the
 * emailed one — stops working. One invitation, one live link.
 */
export function mintMyInterviewLink(inviteId: string): Promise<InterviewLink> {
  return apiPost<InterviewLink>(`/users/me/interviews/${inviteId}/link`, {});
}
