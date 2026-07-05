// rooms.ts — LiveKit room token API
//
// Calls interview_core POST /api/rooms/{session_id}/token.
// The backend creates the LiveKit room (if needed), mints a participant JWT,
// and launches the interviewer agent as a side-effect — so the caller can
// connect immediately after receiving the token.
//
// Auth is injected automatically by clientFetch (Bearer + httpOnly cookie).

import { clientFetch } from './client';

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
    `${INTERVIEW_BASE}/api/rooms/${encodeURIComponent(sessionId)}/token`,
    { method: 'POST', body: JSON.stringify({}) },
  );
}
