// ProctorIndicator — the candidate-visible "camera is on, here is what the
// system sees" indicator (camera-proctoring contract §4).
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import ProctorIndicator from '../pages/exam/ProctorIndicator';
import {
  deriveProctorIndicatorStatus,
  type ProctorIndicatorSignals,
} from '../pages/exam/proctorIndicatorStatus';

const BASE: ProctorIndicatorSignals = {
  cameraSettled: true,
  cameraAvailable: true,
  cameraDenied: false,
  cameraModelReady: true,
  cameraCalibrating: false,
  cameraWarning: null,
};

describe('deriveProctorIndicatorStatus — priority order', () => {
  it('is "ok" once everything is settled, available and no warning', () => {
    expect(deriveProctorIndicatorStatus(BASE)).toBe('ok');
  });

  it('is "unavailable" when denied, regardless of anything else', () => {
    expect(
      deriveProctorIndicatorStatus({ ...BASE, cameraDenied: true, cameraWarning: 'gaze_away' }),
    ).toBe('unavailable');
  });

  it('is "unavailable" when settled but the stream never became available', () => {
    expect(
      deriveProctorIndicatorStatus({ ...BASE, cameraAvailable: false, cameraSettled: true }),
    ).toBe('unavailable');
  });

  it('is "starting" before getUserMedia has settled at all (not yet unavailable)', () => {
    expect(
      deriveProctorIndicatorStatus({ ...BASE, cameraAvailable: false, cameraSettled: false }),
    ).toBe('starting');
  });

  it('is "starting" while the camera is up but the face model has not loaded', () => {
    expect(deriveProctorIndicatorStatus({ ...BASE, cameraModelReady: false })).toBe('starting');
  });

  it('is "calibrating" once the model is ready but calibration is in progress', () => {
    expect(deriveProctorIndicatorStatus({ ...BASE, cameraCalibrating: true })).toBe('calibrating');
  });

  it('is "warning" when a sustained issue is active and calibration is done', () => {
    expect(deriveProctorIndicatorStatus({ ...BASE, cameraWarning: 'face_absent' })).toBe('warning');
  });

  it('prioritises calibrating over warning (no warning is shown mid-calibration upstream, but the indicator itself must not contradict that)', () => {
    expect(
      deriveProctorIndicatorStatus({
        ...BASE,
        cameraCalibrating: true,
        cameraWarning: 'gaze_away',
      }),
    ).toBe('calibrating');
  });
});

describe('ProctorIndicator — rendering', () => {
  it('is an accessible, non-interruptive live region', () => {
    render(<ProctorIndicator {...BASE} />);
    const el = screen.getByRole('status');
    expect(el).toHaveAttribute('aria-live', 'polite');
  });

  it('says the candidate is visible when everything is fine', () => {
    render(<ProctorIndicator {...BASE} />);
    expect(screen.getByText(/camera on/i)).toBeInTheDocument();
    expect(screen.getByText(/you are visible/i)).toBeInTheDocument();
  });

  it('degrades to a neutral "unavailable" message rather than naming a cause', () => {
    render(<ProctorIndicator {...BASE} cameraDenied />);
    expect(screen.getByText(/camera unavailable/i)).toBeInTheDocument();
    expect(screen.getByText(/standard monitoring/i)).toBeInTheDocument();
  });

  it('shows the neutral nudge copy for a sustained gaze_away warning, not an accusation', () => {
    render(<ProctorIndicator {...BASE} cameraWarning="gaze_away" />);
    expect(screen.getByText(/please look at the screen/i)).toBeInTheDocument();
  });

  it('shows the face_absent warning copy', () => {
    render(<ProctorIndicator {...BASE} cameraWarning="face_absent" />);
    expect(screen.getByText(/we can't see you/i)).toBeInTheDocument();
  });

  it('shows the multiple_faces warning copy', () => {
    render(<ProctorIndicator {...BASE} cameraWarning="multiple_faces" />);
    expect(screen.getByText(/more than one person/i)).toBeInTheDocument();
  });

  it('shows a calibrating message', () => {
    render(<ProctorIndicator {...BASE} cameraCalibrating />);
    expect(screen.getByText(/calibrating/i)).toBeInTheDocument();
  });
});
