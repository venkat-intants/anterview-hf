// scorecards.ts — HR's side of human-interview scorecards (D4-1): who can be
// assigned, what each round's assignments look like, and the interview kit HR
// authors for the round's interviewers. Also the structured decision-reason
// taxonomy (O4) HR's dropdown reads from.
//
// Scorecards never decide anything (D-05) — hire/reject stays on the decision
// queue and the pipeline; both read scores only as a summary. Field names
// match the data_gateway contract EXACTLY.

import { apiGet, apiPost, apiPut } from './client';
import { pathId } from './pathId';
import type { InterviewKit } from './interviewer';

/** Someone who can be assigned a scorecard — an interviewer, or an HR manager
 *  acting as one (D4-1). */
export interface Interviewer {
  user_id: string;
  full_name: string;
  email: string;
  role: 'interviewer' | 'hr_manager';
}

export function listInterviewers(): Promise<Interviewer[]> {
  return apiGet<Interviewer[]>('/hr/interviewers');
}

export type EnrolmentScorecardState =
  | 'assigned'
  | 'in_progress'
  | 'late'
  | 'submitted'
  | 'withdrawn';

export interface EnrolmentScorecardScore {
  score: number | null;
  not_assessed: boolean;
  evidence: string | null;
}

export interface EnrolmentScorecard {
  scorecard_id: string;
  interviewer_user_id: string;
  interviewer_name: string;
  state: EnrolmentScorecardState;
  due_at: string | null;
  submitted_at: string | null;
  /** A submitted scorecard a correction has since replaced — shown collapsed. */
  superseded: boolean;
  is_correction: boolean;
  /** Null while hidden_until_you_submit applies to the caller, same as scores/summary. */
  correction_reason: string | null;
  /** True when this correction was opened after peers had already submitted —
   *  worth flagging neutrally, never implying anything improper. */
  corrected_after_peers_visible: boolean;
  withdrawn_reason: string | null;
  /** DPDP erasure: the candidate's personal data was erased, so this
   *  scorecard's written text — summary, evidence, correction and withdrawal
   *  reasons — was removed. The scores themselves are kept. Nothing to do with
   *  `hidden_until_you_submit` on the round, which is about the viewer. */
  redacted: boolean;
  summary: string | null;
  /** Keyed by competency_id. Null until submitted, and null for the other
   *  interviewers' scorecards while the round is hidden_until_you_submit. */
  scores: Record<string, EnrolmentScorecardScore> | null;
}

export interface EnrolmentScorecardCriterion {
  competency_id: string;
  competency_name: string;
  weight: number;
}

export interface EnrolmentScorecardRound {
  round_id: string;
  round_title: string;
  position: number;
  hidden_until_you_submit: boolean;
  criteria: EnrolmentScorecardCriterion[];
  scorecards: EnrolmentScorecard[];
}

export function getEnrolmentScorecards(
  enrolmentId: string,
): Promise<{ rounds: EnrolmentScorecardRound[] }> {
  return apiGet<{ rounds: EnrolmentScorecardRound[] }>(`/hr/enrolments/${pathId(enrolmentId)}/scorecards`);
}

export interface AssignInterviewersBody {
  round_id: string;
  /** 1-10. */
  interviewer_user_ids: string[];
  /** ISO with timezone. */
  due_at?: string;
}

export interface AssignInterviewersResult {
  created: { scorecard_id: string; interviewer_user_id: string }[];
  /** Informational, not an error — someone already had this round. */
  already_assigned: string[];
  due_at: string;
}

export function assignInterviewers(
  enrolmentId: string,
  body: AssignInterviewersBody,
): Promise<AssignInterviewersResult> {
  return apiPost<AssignInterviewersResult>(`/hr/enrolments/${pathId(enrolmentId)}/scorecards`, body);
}

/** Withdraw an unsubmitted assignment (204). */
export function withdrawScorecard(scorecardId: string, reason?: string): Promise<void> {
  return apiPost<void>(`/hr/scorecards/${pathId(scorecardId)}/withdraw`, {
    reason: reason?.trim() || null,
  });
}

// ── Interview kit authoring (HR) — the interviewer console reads this back ──

export function getRoundKit(roundId: string): Promise<InterviewKit> {
  return apiGet<InterviewKit>(`/hr/rounds/${pathId(roundId)}/kit`);
}

export interface RoundKitCriterionInput {
  competency_id: string;
  what_to_evaluate: string[];
  look_for: string[];
  probes: string[];
}

/** HR edits guidance only — criteria themselves are frozen on the round and
 *  cannot be added, removed, renamed or re-weighted through the kit. */
export interface RoundKitInput {
  instructions: string | null;
  interviewer_notes_from_hr: string | null;
  criteria: RoundKitCriterionInput[];
}

export function updateRoundKit(roundId: string, body: RoundKitInput): Promise<InterviewKit> {
  return apiPut<InterviewKit>(`/hr/rounds/${pathId(roundId)}/kit`, body);
}

// ── Structured decision reasons (O4) ─────────────────────────────────────────

export type DecisionOutcome = 'hired' | 'rejected' | 'both';

export interface DecisionReason {
  code: string;
  label: string;
  applies_to: DecisionOutcome;
  requires_explanation: boolean;
}

/** Active reasons only, in display order — what the hire/reject dropdown offers. */
export function listDecisionReasons(): Promise<DecisionReason[]> {
  return apiGet<DecisionReason[]>('/hr/decision-reasons');
}
