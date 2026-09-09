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
 * The whole application is now built for both modes — the staff consoles were
 * swept onto these tokens too — so the preference applies everywhere EXCEPT the
 * live interview, which is pinned dark. That screen is a video surface where
 * the candidate's camera and the avatar are the content; a light chrome around
 * them is not a preference, it is a worse product. `ThemeModeGate` enforces
 * that one exclusion.
 *
 * The key is `anthire:mode`; the older `intants:*` keys keep their prefix
 * deliberately (renaming them would drop every visitor's saved language and
 * accent), so both prefixes exist and that is not an oversight.
 */

export type ThemeMode = 'light' | 'dark';

export const MODE_KEY = 'anthire:mode';
export const MODE_CHANGE_EVENT = 'anthire:modechange';

/**
 * Routes pinned to dark whatever the visitor chose. An exclusion list, not an
 * allow-list: it inverted when the consoles were swept, and a list of the two
 * exceptions cannot silently omit a new page the way a list of forty could.
 *
 * `/interview/:id` is the live session. `/interview-invite` and
 * `/interview/:id/complete` are ordinary pages and are NOT pinned — the prefix
 * check below is exact-segment for that reason.
 */
export const DARK_ONLY_PREFIXES = ['/interview'] as const

/** Does this path follow the visitor's mode? Everything but the live interview. */
export function isThemedRoute(pathname: string): boolean {
  const segments = pathname.split('/').filter(Boolean)
  // /interview/<id> — but not /interview-invite (different first segment) and
  // not /interview/<id>/complete (three segments, an ordinary page).
  const isLiveInterview = segments[0] === 'interview' && segments.length === 2
  return !isLiveInterview
}

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

/** The stored mode, live. Not necessarily the mode being rendered — a dark-only
 *  route overrides it (see `isThemedRoute`). */
export function useThemeMode(): [ThemeMode, (next: ThemeMode) => void] {
  const mode = useSyncExternalStore(subscribe, readMode, () => 'light' as ThemeMode);
  const choose = useCallback((next: ThemeMode) => setMode(next), []);
  return [mode, choose];
}
