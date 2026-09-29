// proctorIndicatorStatus — pure state derivation for ProctorIndicator.tsx.
//
// Kept in its own module (rather than exported alongside the component) so
// this stays unit-testable without a DOM, and so ProctorIndicator.tsx can
// export only the component — a file mixing a component export with a
// runtime helper export defeats React Fast Refresh for that file.

export type ProctorIndicatorStatus = 'unavailable' | 'starting' | 'calibrating' | 'warning' | 'ok';

export interface ProctorIndicatorSignals {
  /** getUserMedia has resolved or rejected. */
  cameraSettled: boolean;
  /** The camera stream itself is attached and playing. */
  cameraAvailable: boolean;
  /** No camera API, or permission/device denied. */
  cameraDenied: boolean;
  /** The on-device face model has loaded and detection is live. */
  cameraModelReady: boolean;
  /** Brief "hold still" startup window. */
  cameraCalibrating: boolean;
  /** The current sustained camera issue, or null. */
  cameraWarning: 'gaze_away' | 'face_absent' | 'multiple_faces' | null;
}

/**
 * Priority order (denied > starting > calibrating > warning > ok). Camera
 * failures of every kind collapse to the SAME "unavailable" status; the exam
 * never surfaces "why", only that standard monitoring is covering the gap.
 */
export function deriveProctorIndicatorStatus(
  signals: ProctorIndicatorSignals,
): ProctorIndicatorStatus {
  if (signals.cameraDenied || (signals.cameraSettled && !signals.cameraAvailable)) {
    return 'unavailable';
  }
  if (!signals.cameraAvailable || !signals.cameraModelReady) {
    return 'starting';
  }
  if (signals.cameraCalibrating) {
    return 'calibrating';
  }
  if (signals.cameraWarning) {
    return 'warning';
  }
  return 'ok';
}
