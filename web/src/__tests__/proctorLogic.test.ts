// Unit tests for the pure proctoring decision logic (proctorLogic.ts).
import { describe, it, expect } from 'vitest';
import {
  advanceCondition,
  averageNeutral,
  closeOpenConditions,
  DEFAULT_NEUTRAL,
  extractGazeSignals,
  freshCondStates,
  isLookingAway,
  pickWarning,
  type CondState,
  type GazeThresholds,
} from '../features/interview/proctorLogic';

const T0 = 1_780_000_000_000; // arbitrary fixed epoch ms
const MIN = 1200;
const WARN = 5000;

const TH: GazeThresholds = {
  poseYaw: 0.32,
  posePitch: 0.34,
  eyeGaze: 0.55,
  horizLow: 0.36,
  horizHigh: 0.64,
  vertLow: 0.28,
  vertHigh: 0.66,
};

describe('advanceCondition', () => {
  it('records since on first true but does not open before minRangedMs', () => {
    const { next, emit } = advanceCondition('gaze_away', { since: null, openIso: null }, true, T0, MIN);
    expect(next.since).toBe(T0);
    expect(next.openIso).toBeNull(); // not yet past the debounce
    expect(emit).toBeNull();
  });

  it('opens once the condition has persisted >= minRangedMs', () => {
    const state: CondState = { since: T0, openIso: null };
    const { next } = advanceCondition('gaze_away', state, true, T0 + MIN, MIN);
    expect(next.since).toBe(T0); // original start preserved
    expect(next.openIso).toBe(new Date(T0).toISOString());
  });

  it('emits a ranged event spanning [openIso, now] when an open condition ends', () => {
    const opened: CondState = { since: T0, openIso: new Date(T0).toISOString() };
    const { next, emit } = advanceCondition('face_absent', opened, false, T0 + 8000, MIN);
    expect(emit).not.toBeNull();
    expect(emit?.type).toBe('face_absent');
    expect(emit?.started_at).toBe(new Date(T0).toISOString());
    expect(emit?.ended_at).toBe(new Date(T0 + 8000).toISOString());
    expect(next).toEqual({ since: null, openIso: null }); // reset
  });

  it('emits nothing for a brief blip that never opened', () => {
    const brief: CondState = { since: T0, openIso: null }; // true but < minRangedMs
    const { next, emit } = advanceCondition('gaze_away', brief, false, T0 + 300, MIN);
    expect(emit).toBeNull();
    expect(next).toEqual({ since: null, openIso: null });
  });
});

describe('closeOpenConditions', () => {
  it('emits events only for still-open conditions', () => {
    const states = freshCondStates();
    states.gaze_away = { since: T0, openIso: new Date(T0).toISOString() };
    states.face_absent = { since: T0, openIso: null }; // open-pending, not yet opened
    const events = closeOpenConditions(states, T0 + 5000);
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe('gaze_away');
    expect(events[0].ended_at).toBe(new Date(T0 + 5000).toISOString());
  });
});

describe('pickWarning', () => {
  it('returns null when nothing is sustained long enough', () => {
    const states = freshCondStates();
    states.gaze_away = { since: T0 - 1000, openIso: null }; // only 1s
    expect(pickWarning(states, T0, WARN)).toBeNull();
  });

  it('warns when a condition has been active >= warnMs', () => {
    const states = freshCondStates();
    states.gaze_away = { since: T0 - WARN, openIso: null };
    expect(pickWarning(states, T0, WARN)).toBe('gaze_away');
  });

  it('prioritises face_absent over multiple_faces over gaze_away', () => {
    const states = freshCondStates();
    states.gaze_away = { since: T0 - WARN, openIso: null };
    states.multiple_faces = { since: T0 - WARN, openIso: null };
    states.face_absent = { since: T0 - WARN, openIso: null };
    expect(pickWarning(states, T0, WARN)).toBe('face_absent');
    states.face_absent = { since: null, openIso: null };
    expect(pickWarning(states, T0, WARN)).toBe('multiple_faces');
  });
});

describe('isLookingAway', () => {
  const base = { fwdX: 0, fwdY: 0, eyeMax: 0, horiz: 0.5, vert: 0.45 };

  it('is false when facing forward with centred eyes', () => {
    expect(isLookingAway(base, TH)).toBe(false);
  });

  it('flags head turned (yaw) via head pose', () => {
    expect(isLookingAway({ ...base, fwdX: 0.5 }, TH)).toBe(true);
  });

  it('flags head tilted (pitch) via head pose', () => {
    expect(isLookingAway({ ...base, fwdY: 0.5 }, TH)).toBe(true);
  });

  it('flags eyes deviated even with a still head (eye-gaze)', () => {
    expect(isLookingAway({ ...base, eyeMax: 0.7 }, TH)).toBe(true);
  });

  it('uses the nose-ratio fallback only when head pose is absent', () => {
    // matrix absent → fwd null; nose pushed far right → away
    expect(isLookingAway({ fwdX: null, fwdY: null, eyeMax: 0, horiz: 0.9, vert: 0.45 }, TH)).toBe(true);
    // matrix present and centred → fallback ignored even if nose ratio looks off
    expect(isLookingAway({ fwdX: 0, fwdY: 0, eyeMax: 0, horiz: 0.9, vert: 0.45 }, TH)).toBe(false);
  });

  it('measures head pose RELATIVE to the calibrated neutral', () => {
    const neutral = { fwdX: 0.4, fwdY: 0.0 }; // candidate sits turned ~0.4 to one side
    // Sitting at their neutral → NOT away, even though |fwdX|=0.4 > poseYaw.
    expect(isLookingAway({ ...base, fwdX: 0.4 }, TH, neutral)).toBe(false);
    // Turning a further 0.4 beyond neutral → away.
    expect(isLookingAway({ ...base, fwdX: 0.8 }, TH, neutral)).toBe(true);
    // Without the neutral, that same 0.4 pose WOULD be flagged.
    expect(isLookingAway({ ...base, fwdX: 0.4 }, TH)).toBe(true);
  });
});

describe('extractGazeSignals', () => {
  it('reports 0 faces with empty signals when no face is present', () => {
    const { n, signals } = extractGazeSignals({ faces: [] });
    expect(n).toBe(0);
    expect(signals).toEqual({ fwdX: null, fwdY: null, eyeMax: null, horiz: null, vert: null });
  });

  it('reports >1 faces with empty signals (gaze not evaluated)', () => {
    const { n, signals } = extractGazeSignals({ faces: [[], []] });
    expect(n).toBe(2);
    expect(signals.fwdX).toBeNull();
  });

  it('reads head-pose forward vector from the transformation matrix', () => {
    // column-major 4x4; indices 8,9 are the forward x,y.
    const matrix: number[] = Array.from({ length: 16 }, () => 0);
    matrix[8] = 0.5;
    matrix[9] = -0.2;
    const { n, signals } = extractGazeSignals({ faces: [[]], matrix });
    expect(n).toBe(1);
    expect(signals.fwdX).toBe(0.5);
    expect(signals.fwdY).toBe(-0.2);
    expect(signals.horiz).toBeNull(); // matrix present → no nose-ratio fallback
  });

  it('falls back to nose-ratio when no matrix, and reads eye blendshapes', () => {
    const faceLm = Array.from({ length: 478 }, () => ({ x: 0.5, y: 0.5 }));
    faceLm[234] = { x: 0.2, y: 0.5 };
    faceLm[454] = { x: 0.8, y: 0.5 };
    faceLm[1] = { x: 0.5, y: 0.5 };
    faceLm[10] = { x: 0.5, y: 0.2 };
    faceLm[152] = { x: 0.5, y: 0.9 };
    const { signals } = extractGazeSignals({
      faces: [faceLm],
      blendshapes: [{ categoryName: 'eyeLookOutLeft', score: 0.8 }],
    });
    expect(signals.fwdX).toBeNull();
    expect(signals.horiz).toBeCloseTo(0.5); // nose centred
    expect(signals.eyeMax).toBeGreaterThan(0); // picked up the eye blendshape
  });
});

describe('averageNeutral', () => {
  it('returns the default (0,0) when there are no samples', () => {
    expect(averageNeutral([])).toEqual(DEFAULT_NEUTRAL);
  });

  it('averages the collected head-pose samples', () => {
    const n = averageNeutral([
      { fwdX: 0.2, fwdY: 0.1 },
      { fwdX: 0.4, fwdY: 0.3 },
    ]);
    expect(n.fwdX).toBeCloseTo(0.3);
    expect(n.fwdY).toBeCloseTo(0.2);
  });
});
