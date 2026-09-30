// useExamCamera — acquires the candidate's own camera for ON-DEVICE exam
// proctoring only.
//
// No WebRTC, no room, nothing published anywhere: getUserMedia() attaches a
// local stream to a <video> element purely so useProctoring (MediaPipe,
// running against that same element) can read frames from it. The stream
// itself, and every frame in it, never leaves this tab.
//
// Graceful degradation is the entire point of this hook: no camera, a denied
// permission prompt, or any getUserMedia failure must NEVER block the exam —
// `available` simply stays false and the caller (useExamProctor) falls back
// to browser-event-only proctoring, exactly as if camera proctoring were not
// enabled at all.

import { useEffect, useRef, useState } from 'react';

export interface UseExamCameraReturn {
  /** Attach to a <video> element (can be visually hidden) for MediaPipe to read. */
  videoRef: React.RefObject<HTMLVideoElement>;
  /** True once the stream is attached and the video element is playing. */
  available: boolean;
  /** True once getUserMedia has settled (resolved OR rejected) — lets the
   *  caller distinguish "still asking" from "denied / unavailable". */
  settled: boolean;
  /** True when the browser has no getUserMedia, or the call rejected
   *  (permission denied, no device, camera in use elsewhere, etc). */
  denied: boolean;
}

/**
 * @param enabled Only requests the camera while true (candidate has both
 *   consented AND the round requires camera proctoring). Flipping back to
 *   false tears the stream down immediately.
 */
export function useExamCamera({ enabled }: { enabled: boolean }): UseExamCameraReturn {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [available, setAvailable] = useState(false);
  const [settled, setSettled] = useState(false);
  const [denied, setDenied] = useState(false);
  const streamRef = useRef<MediaStream | null>(null);

  useEffect(() => {
    if (!enabled) {
      setAvailable(false);
      setSettled(false);
      setDenied(false);
      return;
    }

    let cancelled = false;
    // Captured once, at effect-setup time — by the time cleanup runs,
    // videoRef.current may already point somewhere else (or nowhere), so the
    // element this effect attached the stream to is the one it must detach it
    // from, not whatever the ref currently holds.
    const videoEl = videoRef.current;

    if (!navigator.mediaDevices?.getUserMedia) {
      setDenied(true);
      setSettled(true);
      return;
    }

    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: 'user' }, audio: false })
      .then((stream) => {
        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        streamRef.current = stream;
        if (videoEl) {
          videoEl.srcObject = stream;
          videoEl.play().catch(() => {
            // Autoplay blocked without a fresh gesture — MediaPipe simply
            // won't see frames until playback starts. Non-fatal; never
            // blocks the exam.
          });
        }
        setAvailable(true);
        setSettled(true);
      })
      .catch(() => {
        // NotAllowedError (denied), NotFoundError (no camera), or any other
        // getUserMedia failure — all degrade the same way: no camera signal,
        // exam proceeds on browser events only.
        if (cancelled) return;
        setDenied(true);
        setSettled(true);
      });

    return () => {
      cancelled = true;
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      if (videoEl) videoEl.srcObject = null;
      setAvailable(false);
    };
  }, [enabled]);

  return { videoRef, available, settled, denied };
}
