// Interview types — mirror backend Pydantic models in interview_core + data_gateway
// Shape matches sprint-02/plan.md §"API Contracts"

export interface Job {
  id: string;
  title: string;
  description: string;
  level: 'entry' | 'mid' | 'senior';
  language: string;
  is_active: boolean;
}

export interface JobsListResponse {
  items: Job[];
  total: number;
  page: number;
  per_page: number;
}

export type Language = 'en' | 'hi' | 'te';

export interface Avatar {
  id: string;
  name: string;
  gender: 'male' | 'female';
  thumbnail_url: string;
}

export interface AvatarsResponse {
  avatars: Avatar[];
}

export interface CreateSessionRequest {
  job_id: string;
  language: Language;
  avatar_id?: string;
}

export interface CreateSessionResponse {
  session_id: string;
  job_title?: string;
  language?: Language;
}

// WebSocket message shapes — matches plan.md §"API Contracts"

export interface WsConnectedMessage {
  type: 'connected';
  session_id: string;
}

export interface WsTurnMessage {
  type: 'turn';
  speaker: 'interviewer' | 'candidate';
  text: string;
  turn_number?: number;
}

export interface WsCompleteMessage {
  type: 'complete';
  session_id: string;
  message: string;
}

export interface WsErrorMessage {
  type: 'error';
  code: string;
  message: string;
}

// v2 inbound — TTS audio payload for the interviewer's response
export interface WsAudioResponseMessage {
  type: 'audio_response';
  /** Base64-encoded WAV audio data */
  data: string;
  /** Audio format — always 'wav' in v2 */
  format: 'wav';
  /** Matches the turn_number of the preceding 'turn' message */
  turn_number: number;
  /** Sample rate in Hz — server sends 22050 */
  sample_rate: number;
}

// v2 inbound — streaming STT partial transcript (S4-004)
// Emitted zero or more times per candidate turn BEFORE the final `transcript`.
// The text field is the full in-progress transcript so far (not a delta).
// No turn_number — the turn is not yet committed when partials arrive.
export interface WsPartialTranscriptMessage {
  type: 'partial_transcript';
  text: string;
}

// B-034 inbound — emitted once after `complete` when async scoring finishes.
// Carries the scorecard_id the client needs to route to the results page. This
// is the ONLY place the client learns the id (GET /scorecards/{id} needs it).
export interface WsSessionScoredMessage {
  type: 'session.scored';
  scorecard_id: string;
  composite_score?: number;
  /** Per-competency sub-scores; shape is opaque to the WS layer. */
  scores?: unknown;
}

export type WsInboundMessage =
  | WsConnectedMessage
  | WsTurnMessage
  | WsCompleteMessage
  | WsErrorMessage
  | WsChunkReceivedMessage
  | WsPartialTranscriptMessage
  | WsTranscriptMessage
  | WsAudioResponseMessage
  | WsSessionScoredMessage;

// v2 inbound — silent ack for an accepted audio_chunk
export interface WsChunkReceivedMessage {
  type: 'chunk_received';
  seq: number;
}

// v2 inbound — STT transcript from the server after turn_end flush
export interface WsTranscriptMessage {
  type: 'transcript';
  text: string;
  speaker: 'candidate';
  turn_number?: number;
}

// v2 outbound — mic audio slice (500 ms, base64 PCM)
export interface WsAudioChunkMessage {
  type: 'audio_chunk';
  data: string; // base64
  seq: number;
}

// v2 outbound — signals end of candidate speech, triggers STT flush
export interface WsTurnEndMessage {
  type: 'turn_end';
}

// Chat transcript entry (client-side only)
export interface ChatEntry {
  id: string;
  speaker: 'interviewer' | 'candidate';
  text: string;
  timestamp: number;
  /** Correlates with audio_response.turn_number for speaking indicator (S3-010) */
  turn_number?: number;
}
