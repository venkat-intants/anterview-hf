// generationError - what a failed AI question-generation request means to the
// person who clicked Generate.
//
// All four generation routes (bank MCQ, bank coding, exam MCQ, exam coding)
// answer a failure with a fixed, human sentence in `detail`:
//
//   502  the model failed or returned nothing usable          -> try again
//   503  usage limits are temporarily unavailable             -> try again soon
//   429  the per-minute cap                                   -> try again soon
//   429  "Your company has reached today's limit ..."         -> NOT retryable
//
// The sentence is the message (ApiError already carries it), so a generator
// shows it inline. A retry button is offered for everything except the daily
// cap, where retrying cannot help and would only invite a second failure.
// A raw status code is never shown.

import { ApiError } from '@/api/client';
import { isReadableMessage } from '@/lib/errorMessage';

const DAILY_LIMIT = /today['’]s limit/i;

/** True for the DAILY cap: status 429 AND the sentence names today's limit. */
export function isDailyGenerationLimit(e: unknown): boolean {
  return e instanceof ApiError && e.status === 429 && DAILY_LIMIT.test(e.message);
}

export interface GenerationFailure {
  /** The sentence to show, inline. Never a status code. */
  message: string;
  /** Whether "Try again" can help. False only for the daily limit. */
  canRetry: boolean;
}

export function toGenerationFailure(e: unknown, fallback: string): GenerationFailure {
  // The server's own sentence when it sent one. The client's status copy (an
  // ApiError with hasServerMessage false), a bare "HTTP 502" and a browser
  // transport message all lose to the generator's specific fallback.
  const fromServer = !(e instanceof ApiError) || e.hasServerMessage;
  const raw = e instanceof Error ? e.message.trim() : '';
  const message = fromServer && isReadableMessage(raw) ? raw : fallback;
  return { message, canRetry: !isDailyGenerationLimit(e) };
}
