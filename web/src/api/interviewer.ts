// interviewer.ts — the interviewer console (D4-1): assignments, one
// scorecard at a time, the read-only interview kit, and private working
// notes.
//
// An interviewer sees ONLY scorecards assigned to them — enforced
// server-side (GET /interviewer/scorecards/:id 404s for anyone else's), so
// this module never takes a company or interviewer id; the session carries
// it. Scorecards never decide a hiring outcome (CLAUDE.md D-05) — this API
// can only ever produce, or after submission correct, one interviewer's
// assessment. The decision queue (api/workflows.ts) is where a hire/reject
// is recorded, and it only ever reads scores as a summary.
//
// Field names match the data_gateway contract EXACTLY.

import { apiGet, apiPost, apiPut } from './client';
import { pathId } from './pathId';

/** The lifecycle state the server computes (adds 'late' on top of status). */
export type ScorecardState = 'assigned' | 'in_progress' | 'late' | 'submitted';

/** What was actually written — 'late' is a state, not a stored status. */
export type ScorecardStatus = 'assigned' | 'in_progress' | 'submitted';

export interface InterviewerAssignment {
  scorecard_id: string;
  state: ScorecardState;
  status: ScorecardStatus;
  due_at: string | null;
  submitted_at: string | null;
  candidate_name: string;
  job_title: string;
  round_title: string;
  is_correction: boolean;
}

/** Already sorted server-side: late first, then due soonest, submitted last. */
export function listAssignments(): Promise<InterviewerAssignment[]> {
  return apiGet<InterviewerAssignment[]>('/interviewer/assignments');
}

export interface ScorecardAnchors {
  low: string;
  mid: string;
  high: string;
}

export interface ScorecardCriterionDetail {
  competency_id: string;
  competency_name: string;
  weight: number;
  anchors: ScorecardAnchors | null;
  score: number | null;
  not_assessed: boolean;
  evidence: string | null;
}

export interface ScorecardDetail {
  scorecard_id: string;
  state: ScorecardState;
  status: ScorecardStatus;
  candidate_name: string;
  job_title: string;
  round_title: string;
  due_at: string | null;
  submitted_at: string | null;
  summary: string | null;
  correction_reason: string | null;
  is_correction: boolean;
  /** A submitted scorecard that a correction has since replaced. Read-only. */
  superseded: boolean;
  can_edit: boolean;
  can_correct: boolean;
  /** PH4-D2 — the ONLY thing this scorecard, or any interviewer payload, ever
   *  says about a candidate's accommodations: the effective `interviewer_note`
   *  for this round, or null when there is none. Never a value, a basis, or
   *  who recorded it — the server does not send those to an interviewer. */
  adjustments_note: string | null;
  criteria: ScorecardCriterionDetail[];
}

export function getScorecard(scorecardId: string): Promise<ScorecardDetail> {
  return apiGet<ScorecardDetail>(`/interviewer/scorecards/${pathId(scorecardId)}`);
}

export interface ScoreInput {
  competency_id: string;
  score: number | null;
  not_assessed: boolean;
  evidence: string | null;
}

export interface ScorecardSaveBody {
  scores: ScoreInput[];
  summary: string | null;
}

export interface ScorecardSaveResult {
  scorecard_id: string;
  status: ScorecardStatus;
}

/** Save a draft. Does not close out the scorecard — can_edit stays true. */
export function saveScorecard(
  scorecardId: string,
  body: ScorecardSaveBody,
): Promise<ScorecardSaveResult> {
  return apiPut<ScorecardSaveResult>(`/interviewer/scorecards/${pathId(scorecardId)}`, body);
}

export interface ScorecardSubmitResult {
  scorecard_id: string;
  status: 'submitted';
  submitted_at: string;
  late: boolean;
}

/** Submitted scorecards can't be edited — only corrected, and the original is kept. */
export function submitScorecard(
  scorecardId: string,
  body: ScorecardSaveBody,
): Promise<ScorecardSubmitResult> {
  return apiPost<ScorecardSubmitResult>(`/interviewer/scorecards/${pathId(scorecardId)}/submit`, body);
}

export interface CorrectionResult {
  /** The id of the NEW draft — navigate here, not back to the original. */
  scorecard_id: string;
  /** The id of the submitted scorecard this one corrects. */
  corrects: string;
}

/** Start a correction on a submitted scorecard. The original stays, marked superseded. */
export function requestCorrection(scorecardId: string, reason: string): Promise<CorrectionResult> {
  return apiPost<CorrectionResult>(`/interviewer/scorecards/${pathId(scorecardId)}/correction`, {
    reason: reason.trim(),
  });
}

// ── The interview kit — HR's guidance for this round, read-only here ────────
//
// Shared with the HR-authoring side (api/scorecards.ts getRoundKit /
// updateRoundKit), which writes what this reads.

export interface KitCriterion {
  competency_id: string;
  competency_name: string;
  weight: number;
  anchors: ScorecardAnchors | null;
  /** From the published rubric — frozen; not editable from either console. */
  frozen_probes: string[];
  /** HR's own additions. Empty when nobody has written a custom kit yet. */
  what_to_evaluate: string[];
  look_for: string[];
  probes: string[];
}

export interface InterviewKit {
  round_title: string;
  instructions: string | null;
  interviewer_notes_from_hr: string | null;
  /** False means the no-kit fallback below is showing frozen data only. */
  has_custom_kit: boolean;
  updated_at: string | null;
  criteria: KitCriterion[];
}

export function getScorecardKit(scorecardId: string): Promise<InterviewKit> {
  return apiGet<InterviewKit>(`/interviewer/scorecards/${pathId(scorecardId)}/kit`);
}

// ── Private working notes — never shown to HR, never part of the scorecard ──

export interface PrivateNotes {
  notes: string;
  updated_at: string | null;
}

export function getPrivateNotes(scorecardId: string): Promise<PrivateNotes> {
  return apiGet<PrivateNotes>(`/interviewer/scorecards/${pathId(scorecardId)}/notes`);
}

export function savePrivateNotes(
  scorecardId: string,
  notes: string,
): Promise<{ updated_at: string }> {
  return apiPut<{ updated_at: string }>(`/interviewer/scorecards/${pathId(scorecardId)}/notes`, {
    notes,
  });
}
