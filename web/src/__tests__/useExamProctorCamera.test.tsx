// useExamProctor — camera-event wiring (camera-proctoring contract).
//
// The shared MediaPipe module (features/proctoring/useProctoring) and the
// camera stream acquisition (useExamCamera) are both mocked here: this file
// tests useExamProctor's OWN adapter — the part that turns a batch of camera
// events into contract-shaped `/exam/integrity-event` calls, filters the
// vocabulary, and feeds the same violation/relaxation refs the existing
// browser-event path (fullscreen_exit/tab_blur) already used.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { useExamProctor } from '../pages/exam/useExamProctor';
import type { ProctorEventSubmitter } from '../features/proctoring/types';

const sendIntegrityEvent = vi.fn();
vi.mock('../api/publicExam', () => ({
  sendIntegrityEvent: (...a: unknown[]) => sendIntegrityEvent(...a) as unknown,
}));

let capturedSubmit: ProctorEventSubmitter | null = null;
let capturedEnabled: boolean | null = null;
vi.mock('../features/proctoring/useProctoring', () => ({
  useProctoring: (args: { submitEvents: ProctorEventSubmitter; enabled: boolean }) => {
    capturedSubmit = args.submitEvents;
    capturedEnabled = args.enabled;
    return { ready: true, activeWarning: null, calibrating: false };
  },
}));

vi.mock('../pages/exam/useExamCamera', () => ({
  useExamCamera: () => ({
    videoRef: { current: null },
    available: true,
    settled: true,
    denied: false,
  }),
}));

type Args = Parameters<typeof useExamProctor>[0];

function setup(overrides: Partial<Args> = {}) {
  const onAutoSubmit = vi.fn();
  const defaults: Args = {
    enabled: true,
    attemptId: 'att-1',
    token: 'tok-1',
    maxViolations: 3,
    onAutoSubmit,
    cameraEnabled: true,
  };
  const hook = renderHook((props: Args) => useExamProctor(props), {
    initialProps: { ...defaults, ...overrides },
  });
  return { hook, onAutoSubmit };
}

beforeEach(() => {
  vi.clearAllMocks();
  capturedSubmit = null;
  capturedEnabled = null;
});

describe('useExamProctor — camera event submission (camera-proctoring contract §1)', () => {
  it('forwards a known camera event in the contract shape', async () => {
    sendIntegrityEvent.mockResolvedValue({
      accepted: true,
      violation_count: 0,
      max_violations: 3,
      integrity_score: 95,
    });
    setup();
    await waitFor(() => expect(capturedSubmit).not.toBeNull());

    let res: Awaited<ReturnType<ProctorEventSubmitter>> = null;
    await act(async () => {
      res = await capturedSubmit!([
        {
          type: 'face_absent',
          started_at: '2026-01-01T00:00:00.000Z',
          ended_at: '2026-01-01T00:00:05.000Z',
        },
      ]);
    });

    expect(sendIntegrityEvent).toHaveBeenCalledWith('tok-1', {
      attempt_id: 'att-1',
      event_type: 'face_absent',
      started_at: '2026-01-01T00:00:00.000Z',
      ended_at: '2026-01-01T00:00:05.000Z',
    });
    expect(res).toEqual({ integrityScore: 95 });
  });

  it('drops an event type outside the exam ingest vocabulary (e.g. proctor_error)', async () => {
    setup();
    await waitFor(() => expect(capturedSubmit).not.toBeNull());

    let res: Awaited<ReturnType<ProctorEventSubmitter>> = null;
    await act(async () => {
      res = await capturedSubmit!([
        { type: 'proctor_error', started_at: '2026-01-01T00:00:00.000Z', metadata: { stage: 'x' } },
      ]);
    });

    expect(sendIntegrityEvent).not.toHaveBeenCalled();
    expect(res).toBeNull();
  });

  it("never sends image/frame/landmark data — only the contract's known fields", async () => {
    sendIntegrityEvent.mockResolvedValue({
      accepted: true,
      violation_count: 1,
      max_violations: 3,
      integrity_score: 90,
    });
    setup();
    await waitFor(() => expect(capturedSubmit).not.toBeNull());

    await act(async () => {
      await capturedSubmit!([
        {
          type: 'gaze_away',
          started_at: '2026-01-01T00:00:00.000Z',
          ended_at: '2026-01-01T00:00:02.000Z',
        },
      ]);
    });

    const [, body] = sendIntegrityEvent.mock.calls[0] as [string, Record<string, unknown>];
    expect(Object.keys(body).sort()).toEqual([
      'attempt_id',
      'ended_at',
      'event_type',
      'started_at',
    ]);
    expect(JSON.stringify(body)).not.toMatch(/frame|landmark|bitmap|pixel|image/i);
  });

  it('a camera event auto-submits at most once even if the response keeps qualifying', async () => {
    sendIntegrityEvent.mockResolvedValue({
      accepted: true,
      violation_count: 3,
      max_violations: 2,
      integrity_score: 40,
    });
    const { onAutoSubmit } = setup({ maxViolations: 2 });
    await waitFor(() => expect(capturedSubmit).not.toBeNull());

    await act(async () => {
      await capturedSubmit!([
        {
          type: 'gaze_away',
          started_at: '2026-01-01T00:00:00.000Z',
          ended_at: '2026-01-01T00:00:05.000Z',
        },
      ]);
    });
    await act(async () => {
      await capturedSubmit!([
        {
          type: 'gaze_away',
          started_at: '2026-01-01T00:01:00.000Z',
          ended_at: '2026-01-01T00:01:05.000Z',
        },
      ]);
    });

    expect(onAutoSubmit).toHaveBeenCalledTimes(1);
  });

  it('once relaxed, a camera event never auto-submits even if a later response reports a numeric max_violations', async () => {
    sendIntegrityEvent.mockResolvedValueOnce({
      accepted: true,
      violation_count: 1,
      max_violations: null,
      integrity_score: 90,
    });
    const { onAutoSubmit } = setup({ maxViolations: 1 });
    await waitFor(() => expect(capturedSubmit).not.toBeNull());

    await act(async () => {
      await capturedSubmit!([
        {
          type: 'face_absent',
          started_at: '2026-01-01T00:00:00.000Z',
          ended_at: '2026-01-01T00:00:05.000Z',
        },
      ]);
    });

    // Contrived (max_violations is fixed per attempt in practice) but a
    // direct test of postCameraEvent's own relaxation guard, not just the
    // pre-existing browser-event path's.
    sendIntegrityEvent.mockResolvedValueOnce({
      accepted: true,
      violation_count: 9,
      max_violations: 1,
      integrity_score: 10,
    });
    await act(async () => {
      await capturedSubmit!([
        {
          type: 'multiple_faces',
          started_at: '2026-01-01T00:01:00.000Z',
          ended_at: '2026-01-01T00:01:05.000Z',
        },
      ]);
    });

    expect(onAutoSubmit).not.toHaveBeenCalled();
  });

  // Camera-proctoring contract §5: an accommodation that relaxes proctoring
  // must relax the camera signals too, via the SAME mechanism as the
  // existing browser-event path — never a parallel one. This proves the
  // relaxation a CAMERA event learns actually reaches the pre-existing
  // fullscreen_exit/tab_blur handling, not just its own code path.
  it('relaxation learned from a camera event also stops a later browser event (fullscreen_exit) from auto-submitting', async () => {
    sendIntegrityEvent.mockResolvedValueOnce({
      accepted: true,
      violation_count: 1,
      max_violations: null, // this attempt is relaxed (PH4-D2)
      integrity_score: 90,
    });
    const { onAutoSubmit } = setup({ maxViolations: 1 });
    await waitFor(() => expect(capturedSubmit).not.toBeNull());

    await act(async () => {
      await capturedSubmit!([
        {
          type: 'face_absent',
          started_at: '2026-01-01T00:00:00.000Z',
          ended_at: '2026-01-01T00:00:05.000Z',
        },
      ]);
    });

    // A fullscreen_exit next — its OWN response looks like a clear breach
    // (count >= max) — must still not auto-submit, because the attempt was
    // already learned to be relaxed via the camera event above.
    sendIntegrityEvent.mockResolvedValueOnce({
      accepted: true,
      violation_count: 5,
      max_violations: 1,
      integrity_score: 20,
    });
    await act(async () => {
      document.dispatchEvent(new Event('fullscreenchange'));
      // postEvent's response handling runs in a microtask after sendIntegrityEvent resolves.
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(onAutoSubmit).not.toHaveBeenCalled();
  });

  it('auto-submits once a non-relaxed camera event response crosses max_violations', async () => {
    sendIntegrityEvent.mockResolvedValue({
      accepted: true,
      violation_count: 2,
      max_violations: 2,
      integrity_score: 70,
    });
    const { onAutoSubmit } = setup({ maxViolations: 2 });
    await waitFor(() => expect(capturedSubmit).not.toBeNull());

    await act(async () => {
      await capturedSubmit!([
        {
          type: 'gaze_away',
          started_at: '2026-01-01T00:00:00.000Z',
          ended_at: '2026-01-01T00:00:05.000Z',
        },
      ]);
    });

    expect(onAutoSubmit).toHaveBeenCalledTimes(1);
  });

  it("passes cameraEnabled through to the shared module's enabled flag", async () => {
    setup({ cameraEnabled: false });
    await waitFor(() => expect(capturedEnabled).not.toBeNull());
    expect(capturedEnabled).toBe(false);
  });

  it('defaults cameraEnabled to false — a caller that never opts in gets no camera signal at all', async () => {
    setup({ cameraEnabled: undefined });
    await waitFor(() => expect(capturedEnabled).not.toBeNull());
    expect(capturedEnabled).toBe(false);
  });
});
