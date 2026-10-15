// useArmed — do not count a violation against a candidate who has not yet
// been allowed to start.
//
// THE BUG THIS EXISTS TO MAKE STRUCTURALLY IMPOSSIBLE. Both proctors attach
// their `visibilitychange` / `fullscreenchange` listeners the moment their
// `enabled` flag flips true, and that moment is BEFORE the candidate has
// finished being asked for anything:
//
//   * the browser's camera/microphone permission prompt takes focus, and in
//     several browsers that fires `visibilitychange` with `document.hidden`
//     — a `tab_blur` violation recorded for answering a dialog the product
//     itself raised;
//   * fullscreen is requested after `enabled` flips, so between the two there
//     is a window where `document.fullscreenElement` is null and any
//     `fullscreenchange` reads as an exit from a fullscreen never entered.
//
// A candidate opening the link for the first time could therefore arrive at
// the first question already carrying violations, which is both wrong and —
// since these two event types drive auto-submit — capable of ending an exam
// before it is answered.
//
// THE RULE: the threshold events stay suppressed until the session has been
// observed in the state it is supposed to be in. Everything the product asks
// for must have been asked and answered first:
//
//   fullscreen is active (or the browser has no Fullscreen API at all), AND
//   the camera permission prompt has settled (granted or denied), if one was
//   going to be shown.
//
// Then it LATCHES. Once armed, a real exit counts — including the first one,
// which is the behaviour the contract requires (docs/CAMERA-PROCTORING-
// CONTRACT.md §1). Arming is not a grace period and does not expire: it is
// only ever the difference between "has not started yet" and "started".
//
// Only `fullscreen_exit` and `tab_blur` are gated. `copy` and `paste` are not
// violations and are informative whenever they happen; camera-derived events
// carry their own calibration window upstream.

import { useEffect, useRef } from 'react';
import { isFullscreenSupported } from '@/features/interview/useFullscreen';

/** The two event types that count toward the violation threshold, and so the
 *  two that must not fire before the candidate has actually begun. Mirrors
 *  THRESHOLD_EVENTS in useExamProctor and the contract's §1 vocabulary. */
export const GATED_UNTIL_ARMED: ReadonlySet<string> = new Set([
  'fullscreen_exit',
  'tab_blur',
]);

/** How long to wait for the start-up signals before arming regardless.
 *
 *  A BACKSTOP, NOT A GRACE PERIOD, and it is load-bearing. The camera signals
 *  this gate waits on are not guaranteed to arrive: the interview's `ready`
 *  flag is false when the detection model fails to load or the candidate
 *  denies the camera, and in both cases it stays false for the whole session.
 *  Waiting on it unconditionally would turn a denied camera into "no browser
 *  proctoring at all" — silently, and far worse than the early-violation bug
 *  this file exists to fix. So after this long the gate arms anyway, and the
 *  worst case reverts to today's behaviour instead of to no enforcement.
 *
 *  Ten seconds: longer than a permission prompt plus model load on a slow
 *  connection, short enough that a candidate cannot use it as a window. */
const ARM_BACKSTOP_MS = 10_000;

export interface UseArmedArgs {
  /** The proctor's own enabled flag. Disarms (and resets) when false. */
  enabled: boolean;
  /** True when a camera permission prompt is part of this session's start-up.
   *  When false, there is no prompt to wait for. */
  cameraEnabled?: boolean;
  /** True once getUserMedia has settled — RESOLVED OR REJECTED. A denied
   *  camera must arm the gate too, or declining the prompt would suppress
   *  proctoring for the rest of the session. */
  cameraSettled?: boolean;
}

/**
 * A ref that reads true once the session has properly begun.
 *
 * Returned as a ref, not state, so the event listeners can read the current
 * value without being torn down and re-registered each time it changes — the
 * same reason the violation counters next to it are refs.
 */
export function useArmed({
  enabled,
  cameraEnabled = false,
  cameraSettled = false,
}: UseArmedArgs): React.MutableRefObject<boolean> {
  const armed = useRef(false);

  useEffect(() => {
    if (!enabled) {
      // A fresh attempt must re-earn its arming; leaving this latched would
      // carry one session's readiness into the next.
      armed.current = false;
      return;
    }
    if (armed.current) return; // latched

    // The backstop runs for every path below, including the early returns —
    // otherwise a signal that never arrives disarms proctoring for good.
    const backstop = window.setTimeout(() => {
      armed.current = true;
    }, ARM_BACKSTOP_MS);
    const clearBackstop = () => window.clearTimeout(backstop);

    // Waiting on a permission dialog that has not resolved yet.
    if (cameraEnabled && !cameraSettled) return clearBackstop;

    // On a browser with no Fullscreen API there is nothing to wait for and
    // nothing to enforce — the same graceful-degradation choice the rest of
    // the proctoring stack makes rather than locking out a legitimate
    // candidate whose browser cannot comply.
    if (!isFullscreenSupported()) {
      armed.current = true;
      clearBackstop();
      return;
    }

    const stop = () => {
      document.removeEventListener('fullscreenchange', check);
      document.removeEventListener('webkitfullscreenchange', check);
      clearBackstop();
    };

    function check() {
      if (document.fullscreenElement) {
        armed.current = true;
        stop();
      }
    }

    // Already fullscreen by the time we got here — the normal path, since the
    // gate is entered from a user gesture that requested it.
    check();
    if (armed.current) return;

    document.addEventListener('fullscreenchange', check);
    document.addEventListener('webkitfullscreenchange', check);
    return stop;
  }, [enabled, cameraEnabled, cameraSettled]);

  return armed;
}
