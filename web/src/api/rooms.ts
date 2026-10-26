// rooms.ts — LiveKit room token API
//
// Calls interview_core POST /api/rooms/{session_id}/token.
// The backend creates the LiveKit room (if needed), mints a participant JWT,
// and launches the interviewer agent as a side-effect — so the caller can
// connect immediately after receiving the token.
//
// Auth is injected automatically by clientFetch (Bearer + httpOnly cookie).

import { clientFetch } from './client';
import { pathId } from './pathId';

// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const INTERVIEW_BASE: string = import.meta.env.VITE_INTERVIEW_API_URL;

export interface RoomTokenResponse {
  /** LiveKit server WebSocket URL, e.g. wss://your-project.livekit.cloud */
  url: string;
  /** Signed LiveKit participant JWT for this candidate + session */
  token: string;
  /** Room name — matches the session_id on the backend */
  room_name: string;
}

/**
 * Fetch a LiveKit room token for the given session.
 *
 * POST /api/rooms/{session_id}/token
 *
 * The backend simultaneously launches the interviewer agent into the room, so
 * the caller should connect to the room as soon as this resolves — the avatar
 * greeting appears within a few seconds of joining.
 */
export async function getRoomToken(sessionId: string): Promise<RoomTokenResponse> {
  return clientFetch<RoomTokenResponse>(
    `${INTERVIEW_BASE}/api/rooms/${pathId(sessionId)}/token`,
    { method: 'POST', body: JSON.stringify({}) },
  );
}

export interface RedispatchResponse {
  /**
   * already_connected - an interviewer is in the room.
   * starting          - a dispatch has a running job; the interviewer is about to join.
   * dispatched        - a fresh dispatch was created.
   * automatic         - explicit dispatch is switched off server-side.
   */
  status: 'already_connected' | 'starting' | 'dispatched' | 'automatic';
  /** Re-dispatch calls made so far for this session. */
  attempt: number;
  /** Server-side ceiling; beyond it the endpoint answers 429. */
  max_attempts: number;
}

/**
 * Ask the server to (re-)dispatch the interviewer into this room.
 *
 * POST /api/rooms/{session_id}/redispatch
 *
 * Idempotent: safe to call when an interviewer is already present or starting
 * (the server does nothing). Called by useLiveKitInterview when no interviewer
 * has joined within a few seconds of the candidate connecting.
 */
export async function redispatchInterviewer(sessionId: string): Promise<RedispatchResponse> {
  return clientFetch<RedispatchResponse>(
    `${INTERVIEW_BASE}/api/rooms/${pathId(sessionId)}/redispatch`,
    { method: 'POST', body: JSON.stringify({}) },
  );
}
