// useExamProctor — client-side proctoring for the candidate exam-taking flow.
//
// Contract: docs/CAMERA-PROCTORING-CONTRACT.md (§1 vocabulary and submission,
// §4 the no-frame guarantee, §5 accommodations).
//
// Mirrors the interview useProctoring pattern but scoped to exams:
//   - Requests fullscreen when `enabled` flips true (must be called from a
//     user-gesture context, i.e. after the "Start exam" button click).
//   - Listens for `fullscreenchange` (exit), `visibilitychange` (tab switch),
//     `copy`, and `paste` — POSTs each as an integrity-event.
//   - Only fullscreen_exit and tab_blur count toward the violation threshold
//     (copy/paste are restored, scored server-side, but never violations —
//     see app/exam_camera.py's module docstring for why).
//   - When violations reach `maxViolations`, calls `onAutoSubmit()` once.
//   - Exposes `isFullscreen` (drives the blocking "Return to fullscreen" overlay)
//     and `violationCount` for the warning badge.
//   - Debounces duplicate rapid events (100 ms window) so a single exit
//     doesn't fire the listener twice across standard/webkit events.
//   - Never throws — proctoring must not break the exam.
//
// VOCABULARY GUARD (code review FIX 1, 2026-09-29). `sendIntegrityEvent`
// swallows a failed POST and returns null (proctoring must never break the
// exam) — which means a 422 from an event_type the server does not
// recognise is INDISTINGUISHABLE from a dropped network packet: nothing
// anywhere says so. That is exactly how this hook's own `copy`/`paste`
// posts went silently unrecorded for a time: the server vocabulary was
// tightened without checking what this file already sent. `KNOWN_EVENT_TYPES`
// below is this hook's own copy of the server's vocabulary
// (app/exam_camera.py::KNOWN_EVENT_TYPES) and every outgoing event — browser
// or camera-derived — is checked against it BEFORE ever calling
// sendIntegrityEvent; anything not in it is dropped locally with a visible
// diagnostic (`warnUnsupportedEventType`) instead of being posted and
// silently rejected. The two vocabularies still have to be kept in step BY
// HAND (there is no shared codegen between the two services), but a future
// drift now fails loudly in the browser console instead of vanishing.
//
// Camera proctoring (face_absent / multiple_faces / gaze_away) is layered on
// top via the SAME shared, transport-agnostic module the interview uses
// (features/proctoring/useProctoring) — camera-proctoring contract §1/§4.
// It is wired as an entirely separate signal path from the browser events
// above: `browserEvents: false` on the shared hook so tab/copy/paste/
// fullscreen never get emitted twice down two pipelines, and its own
// `postCameraEvent` (below) so a camera event round-trips through the exact
// same `/exam/integrity-event` violation/relaxation handling as
// fullscreen_exit and tab_blur do — an accommodation that relaxes
// auto-submit (PH4-D2) therefore relaxes camera events too, automatically,
// with no parallel mechanism to keep in sync (contract §5). Camera detection
// itself (getUserMedia, MediaPipe) degrades to "off" on ANY failure — no
// camera, denied permission, or a model that never loads all just mean this
// hook never enables it, never blocking the exam (contract's graceful-
// degradation constraint).

import { useCallback, useEffect, useRef, useState } from 'react';
import { useFullscreen, requestFullscreen } from '@/features/interview/useFullscreen';
import { sendIntegrityEvent, type ExamIntegrityResult } from '@/api/publicExam';
import { useProctoring } from '@/features/proctoring/useProctoring';
import type { ProctorEventSubmitter } from '@/features/proctoring/types';
import { useExamCamera } from './useExamCamera';

/** Event types that count toward the violation threshold. Deliberately NOT
 *  copy/paste, mirroring app/exam_camera.py::VIOLATION_EVENT_TYPES — they are
 *  scored (see KNOWN_EVENT_TYPES below) but a bare clipboard event has too
 *  many innocent explanations to force an auto-submit on its own. */
const THRESHOLD_EVENTS = new Set(['fullscreen_exit', 'tab_blur']);

/** Minimum ms between two of the same event_type to avoid double-firing. */
const DEBOUNCE_MS = 100;

/** The FULL event vocabulary POST /exam/integrity-event accepts — the four
 *  browser signals this hook's own listeners emit, plus the three
 *  camera-derived ones the shared proctoring module can emit
 *  (camera-proctoring contract §1). Kept in lockstep BY HAND with
 *  app/exam_camera.py::KNOWN_EVENT_TYPES; every event this hook would send,
 *  from either source, is checked against this set first — see the
 *  VOCABULARY GUARD note at the top of this file. */
const KNOWN_EVENT_TYPES = new Set([
  'fullscreen_exit',
  'tab_blur',
  'copy',
  'paste',
  'face_absent',
  'multiple_faces',
  'gaze_away',
]);

/**
 * Drop an event type this hook cannot send — LOUDLY, not silently. The
 * failure mode this exists to prevent: `sendIntegrityEvent` swallows a 422
 * and returns null, so an unposted event previously vanished with no trace
 * anywhere, on every exam, camera-proctored or not. `console.warn` is
 * deliberate here rather than attempting the POST anyway (which would just
 * 422 again) — it survives in the browser console and in whatever
 * session-replay or error-monitoring tool is watching it, which a
 * silently-dropped request does not.
 */
function warnUnsupportedEventType(eventType: string): void {
  // eslint-disable-next-line no-console -- deliberate, visible diagnostic; see the docstring above.
  console.warn(
    `useExamProctor: dropping unsupported event_type ${JSON.stringify(eventType)} — ` +
      'the client and server proctoring vocabularies have drifted (see KNOWN_EVENT_TYPES).',
  );
}

interface UseExamProctorArgs {
  /** Whether proctoring is active (flips true when the attempt starts). */
  enabled: boolean;
  /** The attempt ID returned by POST /exam/start — needed for integrity events. */
  attemptId: string;
  /** The magic-link token forwarded as X-Exam-Token. */
  token: string;
  /** Max combined fullscreen_exit + tab_blur violations before auto-submit,
   *  or null when this attempt is relaxed and never auto-submits on count
   *  (PH4-D2). Never compare a null numerically: `n >= null` is `n >= 0`. */
  maxViolations: number | null;
  /** Called exactly once when the violation count reaches maxViolations. */
  onAutoSubmit: () => void;
  /** Whether camera proctoring should run: the round requires it AND the
   *  candidate gave the dedicated camera consent AND the attempt is in
   *  progress. Independent of `enabled` above only in that BOTH must be true
   *  for the camera to ever start — this hook never requests a camera on its
   *  own initiative. Defaults to `false` (no camera signal at all) so a
   *  caller that never passes it gets exactly today's browser-events-only
   *  behaviour. */
  cameraEnabled?: boolean;
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
  /** Attach to a (may be visually hidden) <video> element for the camera self-view. */
  cameraVideoRef: React.RefObject<HTMLVideoElement>;
  /** True once the camera stream itself is attached and playing. */
  cameraAvailable: boolean;
  /** True once getUserMedia has settled (resolved or rejected). */
  cameraSettled: boolean;
  /** True when the browser has no camera API, or the candidate/OS denied it —
   *  degrades to browser-events-only; never blocks the exam. */
  cameraDenied: boolean;
  /** True once the on-device face model has loaded and detection is live. */
  cameraModelReady: boolean;
  /** The current sustained camera issue to nudge the candidate about, or null. */
  cameraWarning: 'gaze_away' | 'face_absent' | 'multiple_faces' | null;
  /** True during the brief startup "hold still" calibration window. */
  cameraCalibrating: boolean;
}

export function useExamProctor({
  enabled,
  attemptId,
  token,
  maxViolations,
  onAutoSubmit,
  cameraEnabled = false,
}: UseExamProctorArgs): UseExamProctorReturn {
  const { isFullscreen, supported: fullscreenSupported } = useFullscreen();
  const [violationCount, setViolationCount] = useState(0);

  // Track violations in a ref so the listeners always see the current value
  // without needing to be re-registered every time the count changes.
  const violationRef = useRef(0);
  // Guard: auto-submit fires at most once.
  const autoSubmittedRef = useRef(false);
  // PH4-D2: set once an integrity-event response reveals this attempt's
  // auto-submit is relaxed (max_violations: null) — the candidate never sees
  // that fact directly (GET /exam does not expose relax_auto_submit), so this
  // is the only way the client learns it, and only after the first violation
  // round-trips. Once true, no path here may auto-submit again.
  const autoSubmitRelaxedRef = useRef(false);
  // Per-event-type debounce timestamps.
  const lastEmitRef = useRef<Record<string, number>>({});

  const postEvent = useCallback(
    (eventType: string) => {
      if (!enabled || !attemptId) return;

      // Vocabulary guard (code review FIX 1) — checked BEFORE the debounce
      // and BEFORE sendIntegrityEvent, so an unsupported type never even
      // reaches the network layer that would otherwise swallow its 422.
      if (!KNOWN_EVENT_TYPES.has(eventType)) {
        warnUnsupportedEventType(eventType);
        return;
      }

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
          if (res.max_violations === null) {
            // PH4-D2: relaxed for this attempt — never auto-submit on
            // violation count alone, no matter what a stale local count says.
            autoSubmitRelaxedRef.current = true;
          } else if (
            !autoSubmittedRef.current &&
            !autoSubmitRelaxedRef.current &&
            res.violation_count >= res.max_violations
          ) {
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
        // `maxViolations === null` is the attempt's own frozen answer from
        // /exam/start: relaxed, so the local counter must never auto-submit.
        // Checked BEFORE the comparison, because `next >= null` coerces to
        // `next >= 0` and would fire on the very first violation.
        if (
          !autoSubmittedRef.current &&
          !autoSubmitRelaxedRef.current &&
          maxViolations !== null &&
          next >= maxViolations
        ) {
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

  // ── Camera-based proctoring (face_absent / multiple_faces / gaze_away) ──
  // A dedicated post path, NOT postEvent above: ranged camera events arrive
  // already debounced (proctorLogic) with their own started_at/ended_at, so
  // there's nothing to locally de-dupe or optimistically count — only the
  // server's authoritative violation_count/max_violations matters, synced
  // through the SAME refs postEvent uses so relaxation (PH4-D2) and the
  // auto-submit guard stay in lockstep across every event source.
  const postCameraEvent = useCallback(
    async (
      eventType: string,
      startedAt: string,
      endedAt?: string,
    ): Promise<ExamIntegrityResult | null> => {
      if (!enabled || !attemptId) return null;
      const res = await sendIntegrityEvent(token, {
        attempt_id: attemptId,
        event_type: eventType,
        started_at: startedAt,
        ...(endedAt ? { ended_at: endedAt } : {}),
      });
      if (res && typeof res.violation_count === 'number') {
        violationRef.current = res.violation_count;
        setViolationCount(res.violation_count);
        if (res.max_violations === null) {
          autoSubmitRelaxedRef.current = true;
        } else if (
          !autoSubmittedRef.current &&
          !autoSubmitRelaxedRef.current &&
          res.violation_count >= res.max_violations
        ) {
          autoSubmittedRef.current = true;
          onAutoSubmit();
        }
      }
      return res;
    },
    [enabled, attemptId, token, onAutoSubmit],
  );

  // Adapter satisfying the shared module's transport-agnostic submitter
  // contract (features/proctoring/types.ProctorEventSubmitter). Filters to
  // the SAME KNOWN_EVENT_TYPES vocabulary guard postEvent above uses (code
  // review FIX 1) — e.g. the `proctor_error` diagnostic the shared module can
  // also emit is not in that vocabulary, so it is dropped here, loudly, rather
  // than sent to an endpoint that would reject it.
  const submitCameraEvents: ProctorEventSubmitter = useCallback(
    async (events) => {
      let last: ExamIntegrityResult | null = null;
      for (const ev of events) {
        if (!KNOWN_EVENT_TYPES.has(ev.type)) {
          warnUnsupportedEventType(ev.type);
          continue;
        }
        // Sequential, not Promise.all: preserves the order events occurred
        // in, and postCameraEvent's own violationRef/autoSubmit guard reads
        // are not safe to run concurrently against each other.
        const res = await postCameraEvent(ev.type, ev.started_at, ev.ended_at);
        if (res) last = res;
      }
      return last ? { integrityScore: last.integrity_score } : null;
    },
    [postCameraEvent],
  );

  // getUserMedia — local-only self-view, never published anywhere (see
  // useExamCamera). Degrades to `available: false` on ANY failure.
  const camera = useExamCamera({ enabled: cameraEnabled });

  // The shared, transport-agnostic camera module (features/proctoring) — the
  // SAME module the interview uses. `browserEvents: false`: this hook's own
  // listeners above already cover tab/copy/paste/fullscreen, so the shared
  // module here only ever runs the camera (MediaPipe) signal path.
  const {
    ready: cameraModelReady,
    activeWarning: cameraWarning,
    calibrating: cameraCalibrating,
  } = useProctoring({
    videoRef: camera.videoRef,
    enabled: cameraEnabled && camera.available,
    submitEvents: submitCameraEvents,
    browserEvents: false,
  });

  // ── Fullscreen entry helper (must be called from a user gesture) ──────────
  const enterFullscreen = useCallback(async () => {
    await requestFullscreen();
  }, []);

  return {
    isFullscreen,
    fullscreenSupported,
    violationCount,
    enterFullscreen,
    cameraVideoRef: camera.videoRef,
    cameraAvailable: camera.available,
    cameraSettled: camera.settled,
    cameraDenied: camera.denied,
    cameraModelReady,
    cameraWarning,
    cameraCalibrating,
  };
}
