import { useCallback, useSyncExternalStore } from 'react';

/**
 * Light / dark mode — AntHire's two designs.
 *
 * Distinct from `components/ThemeToggle`, which picks the SIGNAL ACCENT
 * (blue / purple / grey) and writes `html[data-theme]`. That is a different
 * axis and both can be set at once: mode chooses the palette, theme chooses
 * the accent within it.
 *
 * Applied as `html[data-mode]` plus the `.dark` class, because shadcn's
 * `dark:` variants key off the class while our token palettes key off the
 * attribute, and the two must never disagree.
 *
 * The preference applies to EVERY route. There is no exclusion list and no
 * route-aware gate: a page either uses these tokens or it paints itself, and
 * that is a property of the component, not of the URL.
 *
 * One component paints itself — `features/interview/LiveKitInterview`, where
 * the avatar video fills the viewport and the HUD floats on top of it. Its
 * contrast is set by the video frame underneath, not by our palette, so it
 * hardcodes light-on-scrim for the same reason subtitles do. It says so at the
 * point it does it, which is where a reader will ask.
 *
 * The key is `anthire:mode`; the older `intants:*` keys keep their prefix
 * deliberately (renaming them would drop every visitor's saved language and
 * accent), so both prefixes exist and that is not an oversight.
 */

export type ThemeMode = 'light' | 'dark';

export const MODE_KEY = 'anthire:mode';
export const MODE_CHANGE_EVENT = 'anthire:modechange';

/** The visitor's stored preference. Light is the default — see the README. */
export function readMode(): ThemeMode {
  try {
    return localStorage.getItem(MODE_KEY) === 'dark' ? 'dark' : 'light';
  } catch {
    // Private mode / storage blocked: the default still applies for this visit.
    return 'light';
  }
}

/** Write the mode to <html>. Exported so the pre-mount path can use it too. */
export function applyMode(mode: ThemeMode): void {
  const root = document.documentElement;
  root.setAttribute('data-mode', mode);
  root.classList.toggle('dark', mode === 'dark');
  root.style.colorScheme = mode;
}

export function setMode(mode: ThemeMode): void {
  try {
    localStorage.setItem(MODE_KEY, mode);
  } catch {
    /* not persisted — still applies for this visit */
  }
  window.dispatchEvent(new Event(MODE_CHANGE_EVENT));
}

function subscribe(cb: () => void): () => void {
  window.addEventListener(MODE_CHANGE_EVENT, cb);
  window.addEventListener('storage', cb); // another tab changed it
  return () => {
    window.removeEventListener(MODE_CHANGE_EVENT, cb);
    window.removeEventListener('storage', cb);
  };
}

/** The stored mode, live. Every route renders in it. */
export function useThemeMode(): [ThemeMode, (next: ThemeMode) => void] {
  const mode = useSyncExternalStore(subscribe, readMode, () => 'light' as ThemeMode);
  const choose = useCallback((next: ThemeMode) => setMode(next), []);
  return [mode, choose];
}
