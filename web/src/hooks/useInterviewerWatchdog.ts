// useInterviewerWatchdog — never leave a candidate alone in an empty room.
//
// The interviewer is an agent dispatched into the LiveKit room by the backend.
// That dispatch can silently fail (worker busy at that instant, process still
// warming, assignment timed out) and LiveKit does not retry it, so before this
// hook the candidate simply sat in the room until it timed out (12.7 minutes
// observed in the 2026-10 load test).
//
// Contract:
//   - While `enabled` (the room is connected) and no interviewer has joined,
//     wait `timeoutMs`. Still nobody? Ask the backend to dispatch again
//     (`redispatch`, idempotent server-side) and wait again.
//   - After `maxRedispatch` re-dispatches with nobody, give up with status
//     'unavailable' so the UI can say so and offer a fresh attempt, instead of
//     spinning forever.
//   - The moment any interviewer is present, status is 'joined' and the timers
//     are gone.
//
// `hasInterviewer` is read at the moment the timer fires (not captured) because
// the answer lives in the LiveKit Room object, not in React state.

import { useCallback, useEffect, useRef, useState } from 'react';

export type InterviewerStatus = 'waiting' | 'retrying' | 'joined' | 'unavailable';

/** How long to wait for the interviewer before asking for another dispatch. */
export const INTERVIEWER_JOIN_TIMEOUT_MS = 20_000;
/** Re-dispatches before giving up (the server enforces its own, larger, ceiling). */
export const MAX_REDISPATCH = 2;

interface Options {
  /** True while the room is connected. Timers only run while enabled. */
  enabled: boolean;
  /** Is an interviewer in the room right now? Read when each timer fires. */
  hasInterviewer: () => boolean;
  /** Ask the backend to dispatch the interviewer again. Failures are non-fatal. */
  redispatch: () => Promise<unknown>;
  /**
   * Is this redispatch failure final (e.g. the server's per-session ceiling,
   * HTTP 429)? Then retrying is pointless: stop waiting and say so, instead of
   * counting down to a generic "unavailable" with no explanation.
   */
  isFinalError?: (error: unknown) => boolean;
  timeoutMs?: number;
  maxRedispatch?: number;
}

export interface InterviewerWatchdog {
  status: InterviewerStatus;
  /** Re-dispatches requested so far (0 until the first timeout). */
  attempt: number;
  maxRedispatch: number;
  /** True when we stopped because the server refused further re-dispatches. */
  limitReached: boolean;
  /** Call when a remote participant appears. */
  markJoined: () => void;
  /** Back to 'waiting' with a fresh budget — call at the start of each connect. */
  reset: () => void;
}

export function useInterviewerWatchdog({
  enabled,
  hasInterviewer,
  redispatch,
  isFinalError,
  timeoutMs = INTERVIEWER_JOIN_TIMEOUT_MS,
  maxRedispatch = MAX_REDISPATCH,
}: Options): InterviewerWatchdog {
  const [status, setStatus] = useState<InterviewerStatus>('waiting');
  const [attempt, setAttempt] = useState(0);
  const [limitReached, setLimitReached] = useState(false);

  // Latest callbacks without re-arming the timer every render.
  const hasInterviewerRef = useRef(hasInterviewer);
  const redispatchRef = useRef(redispatch);
  const isFinalErrorRef = useRef(isFinalError);
  useEffect(() => {
    hasInterviewerRef.current = hasInterviewer;
    redispatchRef.current = redispatch;
    isFinalErrorRef.current = isFinalError;
  }, [hasInterviewer, redispatch, isFinalError]);

  const markJoined = useCallback(() => {
    setStatus('joined');
  }, []);

  const reset = useCallback(() => {
    setStatus('waiting');
    setAttempt(0);
    setLimitReached(false);
  }, []);

  useEffect(() => {
    if (!enabled || status === 'joined' || status === 'unavailable') return;

    const id = setTimeout(() => {
      if (hasInterviewerRef.current()) {
        setStatus('joined');
        return;
      }
      if (attempt >= maxRedispatch) {
        setStatus('unavailable');
        return;
      }
      setAttempt(attempt + 1);
      setStatus('retrying');
      // A failed request just means the next timeout decides - unless the server
      // said it will not try again, which the candidate must be told.
      redispatchRef.current().catch((error: unknown) => {
        if (isFinalErrorRef.current?.(error)) {
          setLimitReached(true);
          setStatus('unavailable');
        }
      });
    }, timeoutMs);

    return () => {
      clearTimeout(id);
    };
    // `attempt` and `status` are the re-arm triggers: each re-dispatch schedules
    // exactly one further timeout.
  }, [enabled, status, attempt, timeoutMs, maxRedispatch]);

  return { status, attempt, maxRedispatch, limitReached, markJoined, reset };
}
