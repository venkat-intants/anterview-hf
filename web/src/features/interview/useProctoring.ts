// useProctoring — client-side proctoring / malpractice detection (Phase B).
//
// Runs entirely in the browser. Two independent signal sources:
//   1. MediaPipe FaceLandmarker on the candidate's own camera frames (~2 fps):
//        - face_absent     (0 faces in view)
//        - multiple_faces  (>1 face)
//        - gaze_away       (head turned away from the screen, debounced)
//      Ranged conditions are debounced (must persist MIN_MS) to cut noise, and
//      emitted as {started_at, ended_at} so the backend can score by duration.
//   2. Browser events (cheap + ~99.9% reliable, no ML):
//        - tab_blur        (candidate switched tab/window)
//        - copy / paste    (clipboard use)
//        - fullscreen_exit (left fullscreen, if it was active)
//
// Events are batched and POSTed to interview_core. The raw camera frames NEVER
// leave the device — only derived events. Detection is best-effort: any failure
// (model load, camera) is swallowed so it can NEVER break the interview.
//
// NOTE: detection runs on the main thread throttled to ~2 fps for simplicity.
// A Web Worker (OffscreenCanvas) is the next optimisation if CPU becomes a
// concern at scale; the event contract would not change.

import { useEffect, useRef, useState } from 'react';
import { FaceLandmarker, FilesetResolver } from '@mediapipe/tasks-vision';
import {
  postIntegrityEvents,
  type IntegrityEventOut,
  type IntegrityEventType,
} from '@/api/integrity';
import {
  advanceCondition,
  averageNeutral,
  closeOpenConditions as closeOpenConditionsPure,
  DEFAULT_NEUTRAL,
  extractGazeSignals,
  freshCondStates,
  isLookingAway,
  pickWarning,
  type GazeSignals,
  type GazeThresholds,
  type NeutralPose,
  type ProctorCondition,
} from '@/features/interview/proctorLogic';

// ── Tunables ─────────────────────────────────────────────────────────────────
const DETECT_INTERVAL_MS = 500; // ~2 fps
const FLUSH_INTERVAL_MS = 5000; // POST batched events every 5s
const MIN_RANGED_MS = 1200; // a ranged condition must persist this long to count

// Primary "facing away" detection: the head's forward unit vector, taken from
// the third column of MediaPipe's facial transformation (rotation) matrix.
//   forwardX → left/right head turn (yaw),  forwardY → up/down tilt (pitch).
// A value of ~0.30 corresponds to roughly a 17° rotation off-centre. This is
// far more robust to head ROTATION than 2D landmark ratios.
// (yaw/pitch ~0.30 ≈ 17° off-centre) primary head-pose; nose-ratio is the
// fallback; eye-gaze blendshape threshold catches eyes-off-screen with a still
// head. All gathered into one GazeThresholds object passed to isLookingAway().
const GAZE_THRESHOLDS: GazeThresholds = {
  poseYaw: 0.32,
  posePitch: 0.34,
  eyeGaze: 0.55,
  horizLow: 0.36,
  horizHigh: 0.64,
  vertLow: 0.28,
  vertHigh: 0.66,
};

// Real-time candidate nudge: how long a condition must persist before we show an
// on-screen "please look back" warning. Longer than the scoring debounce
// (MIN_RANGED_MS) so we flag briefly but only nag the candidate for sustained
// lapses.
const WARN_MS = 5000;

// Candidate calibration: while a single face is visible at the very start, we
// sample the head's forward vector for this long and average it into a neutral
// baseline, so "looking away" is measured relative to THIS candidate's natural
// facing-forward pose (kills false positives from off-angle seating). No events
// are emitted during calibration.
const CALIBRATION_MS = 2500;

// Self-hosted (same-origin) MediaPipe assets — vendored into public/mediapipe by
// web/scripts/fetch-mediapipe.mjs (npm run setup:mediapipe). No runtime CDN call.
const WASM_URL = '/mediapipe/wasm';
const MODEL_URL = '/mediapipe/face_landmarker.task';

/** A sustained-condition warning surfaced to the candidate in real time. */
export type ProctorWarningType = ProctorCondition;

interface UseProctoringArgs {
  sessionId: string;
  videoRef: React.RefObject<HTMLVideoElement>;
  enabled: boolean;
}

export interface UseProctoringReturn {
  /** True once the face model has loaded and detection is live. */
  ready: boolean;
  /** Latest integrity score (0-100) returned by the backend, or null. */
  score: number | null;
  /**
   * The current sustained issue the candidate should be nudged about (active
   * for ≥ WARN_MS), or null. Drives the on-screen "please look back" banner.
   */
  activeWarning: ProctorWarningType | null;
  /** True during the brief startup calibration ("hold still") window. */
  calibrating: boolean;
}

function nowIso(): string {
  return new Date().toISOString();
}

export function useProctoring({
  sessionId,
  videoRef,
  enabled,
}: UseProctoringArgs): UseProctoringReturn {
  const [ready, setReady] = useState(false);
  const [score, setScore] = useState<number | null>(null);
  const [activeWarning, setActiveWarning] = useState<ProctorWarningType | null>(null);
  const [calibrating, setCalibrating] = useState(false);

  // Calibration state: neutral baseline + the samples gathered during the
  // startup window. calibStartRef is the epoch ms the window began (on first
  // face seen), or null before that. calibDoneRef flips true once averaged.
  const neutralRef = useRef<NeutralPose>(DEFAULT_NEUTRAL);
  const calibSamplesRef = useRef<NeutralPose[]>([]);
  const calibStartRef = useRef<number | null>(null);
  const calibDoneRef = useRef(false);
  const loopStartRef = useRef<number | null>(null);

  // Mutable state kept in refs so the detection loop / listeners are stable.
  const queueRef = useRef<IntegrityEventOut[]>([]);
  const condRef = useRef(freshCondStates());
  // Current warning type, mirrored in a ref so the 2 fps loop only calls
  // setActiveWarning when it actually CHANGES (avoids a re-render every tick).
  const warnRef = useRef<ProctorWarningType | null>(null);

  // Push an instantaneous event.
  const pushInstant = (type: IntegrityEventType) => {
    queueRef.current.push({ type, started_at: nowIso() });
  };

  // Drive a ranged condition's debounced state machine for this tick (pure
  // logic in proctorLogic.advanceCondition — see its unit tests).
  const updateCondition = (name: ProctorCondition, isTrue: boolean, t: number) => {
    const { next, emit } = advanceCondition(name, condRef.current[name], isTrue, t, MIN_RANGED_MS);
    condRef.current[name] = next;
    if (emit) queueRef.current.push(emit);
  };

  // Close any still-open ranged conditions (called on stop).
  const closeOpenConditions = () => {
    const events = closeOpenConditionsPure(condRef.current, Date.now());
    if (events.length) queueRef.current.push(...events);
    condRef.current = freshCondStates();
  };

  const flush = async () => {
    if (queueRef.current.length === 0) return;
    const batch = queueRef.current.splice(0, queueRef.current.length);
    const res = await postIntegrityEvents(sessionId, batch);
    if (res && typeof res.integrity_score === 'number') {
      setScore(res.integrity_score);
    }
  };

  // Decide whether to nudge the candidate (highest-priority condition sustained
  // ≥ WARN_MS). Updates React state only on change to avoid a re-render/tick.
  const evaluateWarnings = (t: number) => {
    const next = pickWarning(condRef.current, t, WARN_MS);
    if (next !== warnRef.current) {
      warnRef.current = next;
      setActiveWarning(next);
    }
  };

  const clearWarning = () => {
    warnRef.current = null;
    setActiveWarning(null);
  };

  // ── Browser-event listeners (independent of the camera) ─────────────────────
  useEffect(() => {
    if (!enabled) return;

    const onVisibility = () => {
      if (document.hidden) pushInstant('tab_blur');
    };
    const onCopy = () => pushInstant('copy');
    const onPaste = () => pushInstant('paste');
    const onFullscreen = () => {
      if (!document.fullscreenElement) pushInstant('fullscreen_exit');
    };

    document.addEventListener('visibilitychange', onVisibility);
    document.addEventListener('copy', onCopy);
    document.addEventListener('paste', onPaste);
    document.addEventListener('fullscreenchange', onFullscreen);

    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      document.removeEventListener('copy', onCopy);
      document.removeEventListener('paste', onPaste);
      document.removeEventListener('fullscreenchange', onFullscreen);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  // ── Heartbeat + periodic flush ──────────────────────────────────────────────
  useEffect(() => {
    if (!enabled) return;

    // Immediate "proctoring is active" heartbeat (empty batch). This marks the
    // session as proctored (score 100, no flags) the moment monitoring starts —
    // so a clean interview shows a real score instead of "not enabled", and the
    // session is marked even if the camera/model never initialises.
    void postIntegrityEvents(sessionId, []).then((res) => {
      if (res && typeof res.integrity_score === 'number') setScore(res.integrity_score);
    });

    const id = setInterval(() => void flush(), FLUSH_INTERVAL_MS);
    return () => {
      clearInterval(id);
      // Final flush on teardown: close open conditions then send whatever's left.
      closeOpenConditions();
      void flush();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, sessionId]);

  // ── MediaPipe detection — Web Worker (inference off the main thread) with a
  //    main-thread fallback if Workers/createImageBitmap are unavailable or the
  //    worker fails to initialise. Both paths feed the SAME processSignals(). ──
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let landmarker: FaceLandmarker | null = null;
    let worker: Worker | null = null;
    let intervalId: number | undefined;
    let initTimer: number | undefined;
    let workerReady = false;
    let posting = false; // backpressure: one in-flight frame at a time

    // Shared decision logic — runs on the main thread regardless of where the
    // inference happened. `t` is wall-clock epoch ms (condition timing + ISO).
    const processSignals = (n: number, signals: GazeSignals, t: number) => {
      // Presence is ALWAYS evaluated (independent of calibration).
      updateCondition('face_absent', n === 0, t);
      updateCondition('multiple_faces', n > 1, t);

      // Calibration: sample the neutral head pose while a single face is
      // visible, then average it. Hard timeout so a candidate who is away from
      // the start never blocks gaze detection. Gaze isn't evaluated until done.
      if (!calibDoneRef.current) {
        const loopStart = loopStartRef.current ?? t;
        loopStartRef.current = loopStart;
        if (n === 1 && signals.fwdX !== null && signals.fwdY !== null) {
          if (calibStartRef.current === null) {
            calibStartRef.current = t;
            setCalibrating(true);
          }
          calibSamplesRef.current.push({ fwdX: signals.fwdX, fwdY: signals.fwdY });
        }
        const gathered =
          calibStartRef.current !== null && t - calibStartRef.current >= CALIBRATION_MS;
        const timedOut = t - loopStart >= CALIBRATION_MS + 7000;
        if (gathered || timedOut) {
          neutralRef.current = averageNeutral(calibSamplesRef.current);
          calibDoneRef.current = true;
          setCalibrating(false);
        } else {
          updateCondition('gaze_away', false, t); // don't flag gaze mid-calibration
          evaluateWarnings(t); // presence warnings still apply
          return;
        }
      }

      const gazeAway =
        n === 1 ? isLookingAway(signals, GAZE_THRESHOLDS, neutralRef.current) : false;
      updateCondition('gaze_away', gazeAway, t);
      evaluateWarnings(t);
    };

    // ── Main-thread fallback: run inference inline on the <video> element. ──
    const startMainThread = async () => {
      try {
        const fileset = await FilesetResolver.forVisionTasks(WASM_URL);
        if (cancelled) return;
        landmarker = await FaceLandmarker.createFromOptions(fileset, {
          baseOptions: { modelAssetPath: MODEL_URL },
          runningMode: 'VIDEO',
          numFaces: 2,
          outputFacialTransformationMatrixes: true,
          outputFaceBlendshapes: true,
        });
        if (cancelled) {
          landmarker.close();
          return;
        }
        setReady(true);
        intervalId = window.setInterval(() => {
          const video = videoRef.current;
          if (!landmarker || !video || video.readyState < 2 || video.videoWidth === 0) return;
          let result;
          try {
            result = landmarker.detectForVideo(video, performance.now());
          } catch {
            return;
          }
          const { n, signals } = extractGazeSignals({
            faces: result.faceLandmarks ?? [],
            matrix: result.facialTransformationMatrixes?.[0]?.data as number[] | undefined,
            blendshapes: result.faceBlendshapes?.[0]?.categories,
          });
          processSignals(n, signals, Date.now());
        }, DETECT_INTERVAL_MS);
      } catch {
        setReady(false); // model failed to load — degrade to browser events only
      }
    };

    // ── Preferred path: offload inference to a Web Worker. ──
    const startWorker = (): boolean => {
      if (typeof Worker === 'undefined' || typeof createImageBitmap === 'undefined') return false;
      try {
        // CLASSIC worker (deliberately NOT { type: 'module' }). In a module
        // worker, MediaPipe loads its wasm-loader .js via ESM import(), which
        // Vite's dev server refuses for files served from /public ("…should not
        // be imported from source code…"). A classic worker makes MediaPipe use
        // importScripts() instead — a plain static fetch of the self-hosted
        // /public wasm. Vite still bundles this worker and its imports for both
        // dev and build, so the only behavioural change is how the wasm loads.
        worker = new Worker(new URL('./proctorWorker.ts', import.meta.url));
      } catch {
        return false;
      }
      worker.onmessage = (ev: MessageEvent) => {
        const data = ev.data as
          | { type: 'ready' }
          | { type: 'signals'; n: number; signals: GazeSignals }
          | { type: 'error' };
        if (data.type === 'ready') {
          workerReady = true;
          if (initTimer) clearTimeout(initTimer);
          if (cancelled) return;
          setReady(true);
          intervalId = window.setInterval(() => {
            const video = videoRef.current;
            if (!worker || posting || !video || video.readyState < 2 || video.videoWidth === 0) {
              return;
            }
            posting = true;
            createImageBitmap(video)
              .then((bitmap) => {
                worker?.postMessage({ type: 'frame', bitmap, ts: performance.now() }, [bitmap]);
              })
              .catch(() => {
                posting = false;
              });
          }, DETECT_INTERVAL_MS);
        } else if (data.type === 'signals') {
          posting = false;
          processSignals(data.n, data.signals, Date.now());
        } else {
          posting = false; // transient inference error — skip frame
        }
      };
      worker.onerror = () => {
        // Worker crashed before/while ready → fall back to the main thread once.
        if (!workerReady && !cancelled) {
          workerReady = true; // guard against double fallback
          worker?.terminate();
          worker = null;
          void startMainThread();
        }
      };
      worker.postMessage({ type: 'init', wasmUrl: WASM_URL, modelUrl: MODEL_URL });
      // If 'ready' never arrives, give up on the worker and use the main thread.
      initTimer = window.setTimeout(() => {
        if (!workerReady && !cancelled) {
          workerReady = true;
          worker?.terminate();
          worker = null;
          void startMainThread();
        }
      }, 8000);
      return true;
    };

    if (!startWorker()) void startMainThread();

    return () => {
      cancelled = true;
      if (intervalId !== undefined) clearInterval(intervalId);
      if (initTimer !== undefined) clearTimeout(initTimer);
      if (worker) {
        try {
          worker.terminate();
        } catch {
          /* ignore */
        }
      }
      if (landmarker) {
        try {
          landmarker.close();
        } catch {
          /* ignore */
        }
      }
      setReady(false);
      clearWarning();
      // Reset calibration so a reconnect re-calibrates from scratch.
      neutralRef.current = DEFAULT_NEUTRAL;
      calibSamplesRef.current = [];
      calibStartRef.current = null;
      calibDoneRef.current = false;
      loopStartRef.current = null;
      setCalibrating(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, sessionId]);

  return { ready, score, activeWarning, calibrating };
}
