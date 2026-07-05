// proctorLogic — pure, dependency-free proctoring decision logic.
//
// Extracted from useProctoring so the tricky bits (the debounced ranged-event
// state machine, the warning selection, and the "looking away" decision) can be
// unit-tested without MediaPipe, timers, the DOM, or the camera. The hook is a
// thin shell that feeds real signals into these functions.
//
// All functions are pure: timestamps come in as epoch-ms (number) and ISO
// strings are derived deterministically from them — no Date.now()/random.

export type ProctorCondition = 'gaze_away' | 'face_absent' | 'multiple_faces';

export interface CondState {
  /** epoch ms the condition first became true (pre-debounce), or null. */
  since: number | null;
  /** ISO start once the condition has been true for >= minRangedMs, else null. */
  openIso: string | null;
}

export interface RangedEventOut {
  type: ProctorCondition;
  started_at: string;
  ended_at: string;
}

export function freshCondStates(): Record<ProctorCondition, CondState> {
  return {
    gaze_away: { since: null, openIso: null },
    face_absent: { since: null, openIso: null },
    multiple_faces: { since: null, openIso: null },
  };
}

/**
 * Advance one condition's debounced state machine by a single tick.
 *
 * - While the condition is true, it "opens" once it has persisted for
 *   `minRangedMs` (recording the original start as ISO).
 * - When it flips to false, an open condition emits a ranged event spanning
 *   [openIso, now]; brief blips that never opened emit nothing.
 *
 * Returns the next state and an event to emit (or null). Pure: `nowMs` drives
 * both timing and the derived ISO timestamps.
 */
export function advanceCondition(
  type: ProctorCondition,
  state: CondState,
  isTrue: boolean,
  nowMs: number,
  minRangedMs: number,
): { next: CondState; emit: RangedEventOut | null } {
  if (isTrue) {
    const since = state.since ?? nowMs;
    const openIso =
      state.openIso ?? (nowMs - since >= minRangedMs ? new Date(since).toISOString() : null);
    return { next: { since, openIso }, emit: null };
  }
  let emit: RangedEventOut | null = null;
  if (state.openIso !== null) {
    emit = { type, started_at: state.openIso, ended_at: new Date(nowMs).toISOString() };
  }
  return { next: { since: null, openIso: null }, emit };
}

/** Close any still-open conditions (session teardown). Returns events to emit. */
export function closeOpenConditions(
  states: Record<ProctorCondition, CondState>,
  nowMs: number,
): RangedEventOut[] {
  const out: RangedEventOut[] = [];
  (Object.keys(states) as ProctorCondition[]).forEach((type) => {
    const st = states[type];
    if (st.openIso !== null) {
      out.push({ type, started_at: st.openIso, ended_at: new Date(nowMs).toISOString() });
    }
  });
  return out;
}

/**
 * The highest-priority condition that has been continuously active for
 * >= warnMs (drives the on-screen candidate nudge), or null.
 * Priority: face absent > multiple faces > looking away.
 */
export function pickWarning(
  states: Record<ProctorCondition, CondState>,
  nowMs: number,
  warnMs: number,
): ProctorCondition | null {
  const sustained = (c: ProctorCondition): boolean => {
    const since = states[c].since;
    return since !== null && nowMs - since >= warnMs;
  };
  if (sustained('face_absent')) return 'face_absent';
  if (sustained('multiple_faces')) return 'multiple_faces';
  if (sustained('gaze_away')) return 'gaze_away';
  return null;
}

export interface GazeThresholds {
  poseYaw: number;
  posePitch: number;
  eyeGaze: number;
  horizLow: number;
  horizHigh: number;
  vertLow: number;
  vertHigh: number;
}

export interface GazeSignals {
  /** Head-pose forward vector (rotation matrix 3rd column), or null if absent. */
  fwdX: number | null;
  fwdY: number | null;
  /** Max eye-blendshape deviation across directions (0-1), or null. */
  eyeMax: number | null;
  /** Nose-position ratio fallback (used only when head pose is unavailable). */
  horiz: number | null;
  vert: number | null;
}

/** One detected frame's data, destructured from a MediaPipe FaceLandmarker result. */
export interface FrameData {
  /** faceLandmarks — array (one per face) of normalized {x,y} landmark arrays. */
  faces: Array<Array<{ x: number; y: number }>>;
  /** facialTransformationMatrixes[0].data — column-major 4x4, or undefined. */
  matrix?: number[];
  /** faceBlendshapes[0].categories — {categoryName, score}, or undefined. */
  blendshapes?: Array<{ categoryName: string; score: number }>;
}

/**
 * Parse one MediaPipe frame into the gaze signals + face count. Pure (no
 * MediaPipe/DOM imports) so it is shared identically by the Web Worker path and
 * the main-thread fallback, and is unit-testable. Returns face count `n` plus
 * the signals consumed by isLookingAway.
 */
export function extractGazeSignals(frame: FrameData): { n: number; signals: GazeSignals } {
  const n = frame.faces.length;
  const empty: GazeSignals = { fwdX: null, fwdY: null, eyeMax: null, horiz: null, vert: null };
  if (n !== 1) return { n, signals: empty };

  const hasMatrix = Array.isArray(frame.matrix) && frame.matrix.length >= 11;
  const fwdX = hasMatrix ? frame.matrix![8] : null;
  const fwdY = hasMatrix ? frame.matrix![9] : null;

  let horiz: number | null = null;
  let vert: number | null = null;
  if (!hasMatrix) {
    const lm = frame.faces[0];
    const leftX = lm[234]?.x ?? 0;
    const rightX = lm[454]?.x ?? 1;
    const noseX = lm[1]?.x ?? 0.5;
    const topY = lm[10]?.y ?? 0;
    const chinY = lm[152]?.y ?? 1;
    const noseY = lm[1]?.y ?? 0.5;
    horiz = rightX !== leftX ? (noseX - leftX) / (rightX - leftX) : 0.5;
    vert = chinY !== topY ? (noseY - topY) / (chinY - topY) : 0.45;
  }

  let eyeMax: number | null = null;
  if (frame.blendshapes) {
    const bs = (name: string): number =>
      frame.blendshapes!.find((c) => c.categoryName === name)?.score ?? 0;
    const lookLeft = (bs('eyeLookOutLeft') + bs('eyeLookInRight')) / 2;
    const lookRight = (bs('eyeLookOutRight') + bs('eyeLookInLeft')) / 2;
    const lookUp = (bs('eyeLookUpLeft') + bs('eyeLookUpRight')) / 2;
    const lookDown = (bs('eyeLookDownLeft') + bs('eyeLookDownRight')) / 2;
    eyeMax = Math.max(lookLeft, lookRight, lookUp, lookDown);
  }

  return { n, signals: { fwdX, fwdY, eyeMax, horiz, vert } };
}

/**
/** A calibrated neutral head-pose baseline (the candidate's "facing forward"). */
export interface NeutralPose {
  fwdX: number;
  fwdY: number;
}

/** Uncalibrated default — assumes the camera is dead-ahead. */
export const DEFAULT_NEUTRAL: NeutralPose = { fwdX: 0, fwdY: 0 };

/**
 * Average collected head-pose samples into a neutral baseline. Returns
 * DEFAULT_NEUTRAL when there are no samples, so an aborted/empty calibration
 * gracefully degrades to the uncalibrated behaviour.
 */
export function averageNeutral(samples: NeutralPose[]): NeutralPose {
  if (samples.length === 0) return { ...DEFAULT_NEUTRAL };
  const sum = samples.reduce(
    (acc, s) => ({ fwdX: acc.fwdX + s.fwdX, fwdY: acc.fwdY + s.fwdY }),
    { fwdX: 0, fwdY: 0 },
  );
  return { fwdX: sum.fwdX / samples.length, fwdY: sum.fwdY / samples.length };
}

/**
 * Decide whether the candidate is "looking away" from one frame's signals.
 * Head pose is primary and measured RELATIVE to `neutral` (the calibrated
 * facing-forward pose) so a candidate who naturally sits slightly off-angle
 * isn't perpetually flagged. Nose-ratio is the fallback when the pose matrix is
 * unavailable; eye-gaze (iris blendshapes, already self-relative) is
 * OR-combined so eyes pointed off-screen count even with a still head.
 */
export function isLookingAway(
  s: GazeSignals,
  t: GazeThresholds,
  neutral: NeutralPose = DEFAULT_NEUTRAL,
): boolean {
  let away = false;
  if (s.fwdX !== null && s.fwdY !== null) {
    away =
      Math.abs(s.fwdX - neutral.fwdX) > t.poseYaw ||
      Math.abs(s.fwdY - neutral.fwdY) > t.posePitch;
  } else if (s.horiz !== null && s.vert !== null) {
    away = s.horiz < t.horizLow || s.horiz > t.horizHigh || s.vert < t.vertLow || s.vert > t.vertHigh;
  }
  if (s.eyeMax !== null) away = away || s.eyeMax > t.eyeGaze;
  return away;
}
