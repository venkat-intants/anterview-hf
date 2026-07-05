// useExamProctor — client-side proctoring for the candidate exam-taking flow.
//
// Mirrors the interview useProctoring pattern but scoped to exams:
//   - Requests fullscreen when `enabled` flips true (must be called from a
//     user-gesture context, i.e. after the "Start exam" button click).
//   - Listens for `fullscreenchange` (exit), `visibilitychange` (tab switch),
//     `copy`, and `paste` — POSTs each as an integrity-event.
//   - Only fullscreen_exit and tab_blur count toward the violation threshold.
//   - When violations reach `maxViolations`, calls `onAutoSubmit()` once.
//   - Exposes `isFullscreen` (drives the blocking "Return to fullscreen" overlay)
//     and `violationCount` for the warning badge.
//   - Debounces duplicate rapid events (100 ms window) so a single exit
//     doesn't fire the listener twice across standard/webkit events.
//   - Never throws — proctoring must not break the exam.

import { useCallback, useEffect, useRef, useState } from 'react';
import { useFullscreen, requestFullscreen } from '@/features/interview/useFullscreen';
import { sendIntegrityEvent } from '@/api/publicExam';

/** Event types that count toward the violation threshold. */
const THRESHOLD_EVENTS = new Set(['fullscreen_exit', 'tab_blur']);

/** Minimum ms between two of the same event_type to avoid double-firing. */
const DEBOUNCE_MS = 100;

interface UseExamProctorArgs {
  /** Whether proctoring is active (flips true when the attempt starts). */
  enabled: boolean;
  /** The attempt ID returned by POST /exam/start — needed for integrity events. */
  attemptId: string;
  /** The magic-link token forwarded as X-Exam-Token. */
  token: string;
  /** Max combined fullscreen_exit + tab_blur violations before auto-submit. */
  maxViolations: number;
  /** Called exactly once when the violation count reaches maxViolations. */
  onAutoSubmit: () => void;
}

export interface UseExamProctorReturn {
  /** True while the document is in fullscreen. */
  isFullscreen: boolean;
  /** Whether the Fullscreen API is available in this browser. */
  fullscreenSupported: boolean;
  /** Number of threshold violations so far (fullscreen_exit + tab_blur). */
  violationCount: number;
  /** Call this to (re-)enter fullscreen after a user gesture. */
  enterFullscreen: () => Promise<void>;
}

export function useExamProctor({
  enabled,
  attemptId,
  token,
  maxViolations,
  onAutoSubmit,
}: UseExamProctorArgs): UseExamProctorReturn {
  const { isFullscreen, supported: fullscreenSupported } = useFullscreen();
  const [violationCount, setViolationCount] = useState(0);

  // Track violations in a ref so the listeners always see the current value
  // without needing to be re-registered every time the count changes.
  const violationRef = useRef(0);
  // Guard: auto-submit fires at most once.
  const autoSubmittedRef = useRef(false);
  // Per-event-type debounce timestamps.
  const lastEmitRef = useRef<Record<string, number>>({});

  const postEvent = useCallback(
    (eventType: string) => {
      if (!enabled || !attemptId) return;

      // Debounce: skip if the same event type fired within DEBOUNCE_MS.
      const now = Date.now();
      const last = lastEmitRef.current[eventType] ?? 0;
      if (now - last < DEBOUNCE_MS) return;
      lastEmitRef.current[eventType] = now;

      const startedAt = new Date(now).toISOString();

      // Fire-and-forget — best effort, never awaited.
      void sendIntegrityEvent(token, {
        attempt_id: attemptId,
        event_type: eventType,
        started_at: startedAt,
      }).then((res) => {
        // The server's violation_count is authoritative; sync our local count.
        if (res && typeof res.violation_count === 'number') {
          violationRef.current = res.violation_count;
          setViolationCount(res.violation_count);
          if (!autoSubmittedRef.current && res.violation_count >= res.max_violations) {
            autoSubmittedRef.current = true;
            onAutoSubmit();
          }
        }
      });

      // Locally increment the threshold counter immediately for a responsive UI,
      // but only for events that count toward the threshold.
      if (THRESHOLD_EVENTS.has(eventType)) {
        const next = violationRef.current + 1;
        violationRef.current = next;
        setViolationCount(next);
        if (!autoSubmittedRef.current && next >= maxViolations) {
          autoSubmittedRef.current = true;
          onAutoSubmit();
        }
      }
    },
    [enabled, attemptId, token, maxViolations, onAutoSubmit],
  );

  // ── Browser event listeners ──────────────────────────────────────────────
  useEffect(() => {
    if (!enabled) return;

    const onFullscreenChange = () => {
      // Only emit an event when we LEAVE fullscreen (not when entering).
      if (!document.fullscreenElement) {
        postEvent('fullscreen_exit');
      }
    };

    const onVisibility = () => {
      if (document.hidden) {
        postEvent('tab_blur');
      }
    };

    const onCopy = () => postEvent('copy');
    const onPaste = () => postEvent('paste');

    document.addEventListener('fullscreenchange', onFullscreenChange);
    document.addEventListener('webkitfullscreenchange', onFullscreenChange);
    document.addEventListener('visibilitychange', onVisibility);
    document.addEventListener('copy', onCopy);
    document.addEventListener('paste', onPaste);

    return () => {
      document.removeEventListener('fullscreenchange', onFullscreenChange);
      document.removeEventListener('webkitfullscreenchange', onFullscreenChange);
      document.removeEventListener('visibilitychange', onVisibility);
      document.removeEventListener('copy', onCopy);
      document.removeEventListener('paste', onPaste);
    };
  }, [enabled, postEvent]);

  // ── Fullscreen entry helper (must be called from a user gesture) ──────────
  const enterFullscreen = useCallback(async () => {
    await requestFullscreen();
  }, []);

  return {
    isFullscreen,
    fullscreenSupported,
    violationCount,
    enterFullscreen,
  };
}
