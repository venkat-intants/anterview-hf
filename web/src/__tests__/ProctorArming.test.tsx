// A candidate cannot collect violations before they are allowed to begin.
//
// THE REPORTED BUG: open an interview or exam link for the first time, and
// before the product has finished asking for camera permission or put the page
// into fullscreen, the proctor has already recorded violations against you.
// Both event types involved — tab_blur and fullscreen_exit — count toward
// auto-submit, so a candidate could lose an exam to a permission dialog the
// product itself raised.
//
// Two mechanisms produced it, and this file pins both shut:
//
//   1. the camera permission prompt takes focus, firing `visibilitychange`
//      with document.hidden → tab_blur;
//   2. the listeners attached the moment the session was "enabled", which was
//      BEFORE fullscreen had been entered, so `fullscreenchange` read as an
//      exit from a fullscreen never entered.
//
// And the thing that must NOT be true as a result: once the candidate has
// properly started, the FIRST real exit counts. An over-eager gate that kept
// suppressing would be a worse bug than the one it replaced — proctoring that
// silently does nothing — so that is asserted here too, alongside the backstop
// that covers a start-up signal which never arrives.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useArmed, GATED_UNTIL_ARMED } from '../features/proctoring/useArmed';

/** jsdom has no Fullscreen API. Install one we can drive, since "is the
 *  browser in fullscreen" is the whole question this gate turns on. */
function installFullscreenApi(): void {
  (document.documentElement as unknown as Record<string, unknown>).requestFullscreen =
    vi.fn();
  Object.defineProperty(document, 'fullscreenElement', {
    configurable: true,
    writable: true,
    value: null,
  });
}

function removeFullscreenApi(): void {
  delete (document.documentElement as unknown as Record<string, unknown>).requestFullscreen;
  Object.defineProperty(document, 'fullscreenElement', {
    configurable: true,
    writable: true,
    value: null,
  });
}

function setFullscreen(on: boolean): void {
  Object.defineProperty(document, 'fullscreenElement', {
    configurable: true,
    writable: true,
    value: on ? document.documentElement : null,
  });
  act(() => {
    document.dispatchEvent(new Event('fullscreenchange'));
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  installFullscreenApi();
});

afterEach(() => {
  vi.useRealTimers();
  removeFullscreenApi();
});

describe('the gate holds until the candidate has actually started', () => {
  it('is not armed the instant the session is enabled', () => {
    // The exact window the bug lived in: enabled, listeners live, nothing
    // asked for yet.
    const { result } = renderHook(() => useArmed({ enabled: true }));
    expect(result.current.current).toBe(false);
  });

  it('stays unarmed while the camera prompt is still open', () => {
    const { result } = renderHook(() =>
      useArmed({ enabled: true, cameraEnabled: true, cameraSettled: false }),
    );
    setFullscreen(true);
    expect(result.current.current).toBe(false);
  });

  it('arms once fullscreen is entered and the prompt has settled', () => {
    const { result, rerender } = renderHook(
      ({ settled }) => useArmed({ enabled: true, cameraEnabled: true, cameraSettled: settled }),
      { initialProps: { settled: false } },
    );
    setFullscreen(true);
    expect(result.current.current).toBe(false);

    rerender({ settled: true });
    expect(result.current.current).toBe(true);
  });

  it('arms on a DENIED camera too, not just a granted one', () => {
    // cameraSettled means "resolved OR rejected". If a denial left this
    // unarmed, declining the prompt would switch proctoring off for the whole
    // session — which is why the signal is "settled" and not "available".
    const { result } = renderHook(() =>
      useArmed({ enabled: true, cameraEnabled: true, cameraSettled: true }),
    );
    setFullscreen(true);
    expect(result.current.current).toBe(true);
  });

  it('arms immediately when the browser has no Fullscreen API', () => {
    removeFullscreenApi();
    const { result } = renderHook(() => useArmed({ enabled: true }));
    // Nothing to wait for and nothing to enforce — blocking here would lock
    // out a candidate whose browser simply cannot comply.
    expect(result.current.current).toBe(true);
  });
});

describe('once armed it stays armed', () => {
  it('does not disarm when fullscreen is later left', () => {
    // NAMED FOR WHAT IT ACTUALLY PROVES. This asserts the ref stays true, and
    // it would pass even against a gate that re-evaluated on every
    // fullscreenchange — because arming detaches the listener, so the later
    // change is never seen here. (Verified: both a no-latch and a
    // track-fullscreen mutation survive this file.)
    //
    // The property that matters — that the first real exit after arming is
    // actually RECORDED — cannot be seen from this hook, which only returns a
    // boolean. It is tested where it is observable, against the real emit
    // path: useExamProctor.test.ts, "records the FIRST real exit once the
    // attempt has properly started".
    const { result } = renderHook(() => useArmed({ enabled: true }));
    setFullscreen(true);
    expect(result.current.current).toBe(true);

    setFullscreen(false);
    expect(result.current.current).toBe(true);
  });

  it('disarms when the session ends, so the next attempt re-earns it', () => {
    const { result, rerender } = renderHook(
      ({ on }) => useArmed({ enabled: on }),
      { initialProps: { on: true } },
    );
    setFullscreen(true);
    expect(result.current.current).toBe(true);

    rerender({ on: false });
    expect(result.current.current).toBe(false);
  });
});

describe('the backstop', () => {
  it('arms anyway when a start-up signal never arrives', () => {
    // The interview's `ready` flag is false forever when the detection model
    // fails to load or the camera is denied. Waiting on it unconditionally
    // would mean no browser proctoring at all, silently — strictly worse than
    // the early-violation bug.
    const { result } = renderHook(() =>
      useArmed({ enabled: true, cameraEnabled: true, cameraSettled: false }),
    );
    expect(result.current.current).toBe(false);

    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(result.current.current).toBe(true);
  });

  it('does not fire once the normal path has already armed', () => {
    const { result } = renderHook(() => useArmed({ enabled: true }));
    setFullscreen(true);
    expect(result.current.current).toBe(true);

    // Leaving fullscreen before the backstop would have fired must not be
    // undone by a stale timer.
    setFullscreen(false);
    act(() => {
      vi.advanceTimersByTime(30_000);
    });
    expect(result.current.current).toBe(true);
  });
});

describe('what the gate covers', () => {
  it('gates exactly the two threshold events', () => {
    // copy/paste are not violations and are informative whenever they happen;
    // suppressing them would lose real signal for no benefit.
    expect([...GATED_UNTIL_ARMED].sort()).toEqual(['fullscreen_exit', 'tab_blur']);
  });
});
