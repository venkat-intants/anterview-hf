// Integrity (proctoring) API — Phase B.
// Sends client-detected proctoring events to interview_core. Raw video NEVER
// leaves the browser; only these lightweight events are transmitted.

import { interviewPost } from './client';

export type IntegrityEventType =
  | 'gaze_away'
  | 'face_absent'
  | 'multiple_faces'
  | 'tab_blur'
  | 'fullscreen_exit'
  | 'copy'
  | 'paste'
  | 'second_voice'
  | 'devtools_open';

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
