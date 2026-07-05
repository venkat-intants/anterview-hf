// useFullscreen — fullscreen gating for the proctored interview.
//
// The interview must run in fullscreen:
//   - ENTERING is gated on a user gesture — browsers only allow
//     requestFullscreen() from a click/tap, so the intro's Begin/Skip button is
//     the entry point. If fullscreen is denied, the interview does not proceed.
//   - LEAVING fullscreen mid-interview is treated as a task-switch: useProctoring
//     already emits a `fullscreen_exit` integrity event on the same
//     `fullscreenchange`, and LiveKitInterview blocks the session behind a
//     "return to fullscreen" overlay until the candidate is back.
//
// All calls are best-effort + cross-browser (standard + WebKit-prefixed) and
// never throw, so a browser without the Fullscreen API degrades gracefully
// (request resolves false → the caller decides how to handle it).

import { useEffect, useState } from 'react';

type FsDocument = Document & {
  webkitFullscreenElement?: Element | null;
  webkitExitFullscreen?: () => Promise<void> | void;
};
type FsElement = HTMLElement & {
  webkitRequestFullscreen?: () => Promise<void> | void;
};

function currentFullscreenElement(): Element | null {
  const d = document as FsDocument;
  return document.fullscreenElement ?? d.webkitFullscreenElement ?? null;
}

/**
 * Whether this browser implements the Fullscreen API at all. We enforce
 * fullscreen only when it's supported — on a browser that literally cannot go
 * fullscreen, blocking the interview would lock out a legitimate candidate with
 * no recourse, so we degrade gracefully instead. (Also: jsdom has no Fullscreen
 * API, so tests take this path.)
 */
export function isFullscreenSupported(): boolean {
  const el = document.documentElement as FsElement;
  return typeof el.requestFullscreen === 'function' || typeof el.webkitRequestFullscreen === 'function';
}

/** Tracks fullscreen state live, plus whether the API is supported at all. */
export function useFullscreen(): { isFullscreen: boolean; supported: boolean } {
  const [isFullscreen, setIsFullscreen] = useState<boolean>(
    () => currentFullscreenElement() !== null,
  );
  const [supported] = useState<boolean>(() => isFullscreenSupported());
  useEffect(() => {
    const onChange = () => setIsFullscreen(currentFullscreenElement() !== null);
    document.addEventListener('fullscreenchange', onChange);
    document.addEventListener('webkitfullscreenchange', onChange);
    return () => {
      document.removeEventListener('fullscreenchange', onChange);
      document.removeEventListener('webkitfullscreenchange', onChange);
    };
  }, []);
  return { isFullscreen, supported };
}

/**
 * Request fullscreen on the whole page. MUST be called from a user gesture
 * (click/tap) or the browser rejects it. Returns true once fullscreen is active,
 * false if unsupported or denied. Never throws.
 */
export async function requestFullscreen(): Promise<boolean> {
  if (currentFullscreenElement() !== null) return true;
  const el = document.documentElement as FsElement;
  try {
    if (el.requestFullscreen) await el.requestFullscreen();
    else if (el.webkitRequestFullscreen) await el.webkitRequestFullscreen();
    else return false;
  } catch {
    return false;
  }
  return currentFullscreenElement() !== null;
}

/** Leave fullscreen (best-effort, never throws). No-op if not in fullscreen. */
export async function exitFullscreen(): Promise<void> {
  if (currentFullscreenElement() === null) return;
  const d = document as FsDocument;
  try {
    if (document.exitFullscreen) await document.exitFullscreen();
    else if (d.webkitExitFullscreen) await d.webkitExitFullscreen();
  } catch {
    /* ignore */
  }
}
