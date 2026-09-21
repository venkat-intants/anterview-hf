// useExamProctor — PH4-D2: an attempt whose auto-submit is relaxed must never
// be auto-submitted on violation count alone, however that fact reaches the
// client. There are two places that could fire it, and both are covered:
//
//   1. The async path (the server's own integrity-event response). Before
//      this fix, `max_violations: null` (the server's "relaxed" signal) was
//      typed as `number`, so `violation_count >= max_violations` compared
//      against `undefined`/`null` coerced to 0 — meaning a RELAXED attempt
//      auto-submitted on its very FIRST violation, the opposite of the
//      intended effect.
//   2. The local, immediate counter (kept for a responsive UI) — it must stop
//      tripping once an earlier response has revealed the attempt is relaxed.
//
// What this file does NOT claim: the very first violation's local, synchronous
// counter cannot know an attempt is relaxed before the first server round trip
// resolves (GET /exam deliberately never exposes relax_auto_submit to the
// candidate — see api/publicExam.ts's ExamAdjustments). That is a real,
// disclosed limitation, not something these tests paper over.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { useExamProctor } from '../pages/exam/useExamProctor';

const sendIntegrityEvent = vi.fn();
vi.mock('../api/publicExam', () => ({
  sendIntegrityEvent: (...a: unknown[]) => sendIntegrityEvent(...a) as unknown,
}));

function tabBlur() {
  act(() => {
    Object.defineProperty(document, 'hidden', { value: true, configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
}

/** jsdom has no Fullscreen API, so `document.fullscreenElement` is always
 *  null — exactly the "left fullscreen" condition the listener checks for. */
function fullscreenExit() {
  act(() => {
    document.dispatchEvent(new Event('fullscreenchange'));
  });
}

afterEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(document, 'hidden', { value: false, configurable: true });
});

describe('useExamProctor — not relaxed (baseline)', () => {
  it('auto-submits once the server confirms the threshold is reached', async () => {
    sendIntegrityEvent.mockResolvedValue({
      accepted: true,
      violation_count: 2,
      max_violations: 2,
      integrity_score: 85,
    });
    const onAutoSubmit = vi.fn();
    // maxViolations prop set high so the LOCAL eager counter cannot itself
    // trip — isolates the async path.
    renderHook(() =>
      useExamProctor({ enabled: true, attemptId: 'a1', token: 'tok', maxViolations: 99, onAutoSubmit }),
    );

    tabBlur();

    await waitFor(() => expect(onAutoSubmit).toHaveBeenCalledTimes(1));
  });
});

describe('useExamProctor — PH4-D2 relaxed auto-submit', () => {
  it('never auto-submits from the async response alone when max_violations is null', async () => {
    // Before the fix: `1 >= null` is `1 >= 0` in JS, which is true — this
    // reproduces the exact false-positive the type-and-logic fix closes.
    sendIntegrityEvent.mockResolvedValue({
      accepted: true,
      violation_count: 1,
      max_violations: null,
      integrity_score: 100,
    });
    const onAutoSubmit = vi.fn();
    renderHook(() =>
      useExamProctor({ enabled: true, attemptId: 'a1', token: 'tok', maxViolations: 99, onAutoSubmit }),
    );

    tabBlur();

    await waitFor(() => expect(sendIntegrityEvent).toHaveBeenCalledTimes(1));
    expect(onAutoSubmit).not.toHaveBeenCalled();
  });

  it('stops the local eager counter too, once an earlier response reveals the attempt is relaxed', async () => {
    sendIntegrityEvent.mockResolvedValue({
      accepted: true,
      violation_count: 1,
      max_violations: null,
      integrity_score: 100,
    });
    const onAutoSubmit = vi.fn();
    // maxViolations=2: the FIRST violation's local counter (1) does not trip
    // on its own — by the time the SECOND violation's local counter would
    // trip (2 >= 2), the first response has already revealed the attempt is
    // relaxed, and this table documents that no path fires after that.
    const { result } = renderHook(() =>
      useExamProctor({ enabled: true, attemptId: 'a1', token: 'tok', maxViolations: 2, onAutoSubmit }),
    );

    tabBlur();
    await waitFor(() => expect(sendIntegrityEvent).toHaveBeenCalledTimes(1));

    fullscreenExit();
    await waitFor(() => expect(result.current.violationCount).toBe(2));

    expect(onAutoSubmit).not.toHaveBeenCalled();
  });

  it('never auto-submits a relaxed attempt, even when every event POST fails', async () => {
    // The gap the security review found: the client used to learn "relaxed"
    // ONLY from an integrity-event response, and sendIntegrityEvent swallows
    // network failures. On a poor connection the candidate never learned it
    // and was cut off at the global threshold — the original bug, reproduced
    // by packet loss. The threshold now comes from /exam/start, frozen on the
    // attempt, so it is known before any violation and survives a dead link.
    sendIntegrityEvent.mockResolvedValue(null); // every POST fails
    const onAutoSubmit = vi.fn();
    const { result } = renderHook(() =>
      useExamProctor({
        enabled: true,
        attemptId: 'a1',
        token: 'tok',
        maxViolations: null, // relaxed, straight from /exam/start
        onAutoSubmit,
      }),
    );

    tabBlur();
    await waitFor(() => expect(result.current.violationCount).toBe(1));
    fullscreenExit();
    await waitFor(() => expect(result.current.violationCount).toBe(2));
    // Past the 100 ms per-event-type debounce, so this is a third counted
    // violation rather than a collapsed repeat — one more than the global
    // threshold of 3 would allow if the relaxation were not honoured.
    await new Promise((r) => setTimeout(r, 150));
    tabBlur();
    await waitFor(() => expect(result.current.violationCount).toBe(3));

    expect(onAutoSubmit).not.toHaveBeenCalled();
  });
});
