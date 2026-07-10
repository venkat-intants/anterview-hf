// Integrity (proctoring) API — Phase B.
// Sends client-detected proctoring events to interview_core. Raw video NEVER
// leaves the browser; only these lightweight events are transmitted.

import { interviewGet, interviewPost } from './client';

export type IntegrityEventType =
  | 'gaze_away'
  | 'face_absent'
  | 'multiple_faces'
  | 'tab_blur'
  | 'fullscreen_exit'
  | 'copy'
  | 'paste'
  | 'second_voice'
  | 'devtools_open'
  // Diagnostic: the detection pipeline itself failed (model load, worker crash).
  // Stored + shown in the summary but contributes no score penalty — it exists
  // so "no camera flags" is distinguishable from "detection never ran".
  | 'proctor_error';

export interface IntegrityEventOut {
  type: IntegrityEventType;
  /** ISO-8601 UTC start timestamp. */
  started_at: string;
  /** ISO-8601 UTC end timestamp for ranged events; omit for instantaneous. */
  ended_at?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface IntegrityBatchResult {
  integrity_score: number;
  summary: Record<string, unknown>;
  stored: number;
}

/**
 * POST a batch of integrity events for a session.
 * An EMPTY batch is allowed and meaningful: it acts as a "proctoring is active"
 * heartbeat so the backend marks the session as proctored (score 100, no flags)
 * even when the candidate triggers nothing. Without this, a clean interview
 * would look identical to "proctoring was never on".
 * Best-effort: proctoring must NEVER break the interview, so any error is
 * swallowed and null is returned.
 */
export async function postIntegrityEvents(
  sessionId: string,
  events: IntegrityEventOut[],
): Promise<IntegrityBatchResult | null> {
  try {
    return await interviewPost<IntegrityBatchResult>(
      `/api/sessions/${sessionId}/integrity-events`,
      { events },
    );
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Read-back — the candidate's own integrity report (scorecard page)
// ---------------------------------------------------------------------------

/** One stored proctoring event, time-ordered. */
export interface IntegrityTimelineEvent {
  event_type: string;
  /** ISO-8601 UTC start timestamp. */
  started_at: string;
  ended_at: string | null;
  /** Seconds for ranged events (gaze_away, face_absent, ...); null for instantaneous. */
  duration_seconds: number | null;
}

export interface IntegrityReport {
  /** 0–100, higher = cleaner. null = proctoring never ran for this session. */
  integrity_score: number | null;
  summary: {
    by_type?: Record<string, number>;
    flagged_seconds?: Record<string, number>;
    total_events?: number;
    total_flagged_seconds?: number;
  } | null;
  /** Session start — used to render events as mm:ss offsets into the interview. */
  session_started_at: string | null;
  events: IntegrityTimelineEvent[];
}

/**
 * GET the caller's own integrity report for a session (owner-only).
 * Best-effort: the scorecard page must render fine without proctoring data,
 * so any error resolves to null instead of throwing.
 */
export async function getSessionIntegrity(
  sessionId: string,
): Promise<IntegrityReport | null> {
  try {
    return await interviewGet<IntegrityReport>(`/api/sessions/${sessionId}/integrity`);
  } catch {
    return null;
  }
}
