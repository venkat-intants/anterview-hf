import { useSyncExternalStore } from 'react';

// The resolved hex of the active signal accent (set per theme via the header
// toggle → html[data-theme]). recharts/SVG colour props (fill/stroke/stopColor)
// are presentation ATTRIBUTES, which can't read CSS variables — so charts read
// the concrete hex through this hook instead of var(--accent), and re-render
// when the theme changes (ThemeToggle dispatches 'intants:themechange').

const FALLBACK = '#0088ff';
export const THEME_CHANGE_EVENT = 'intants:themechange';

function read(): string {
  if (typeof document === 'undefined') return FALLBACK;
  const v = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
  return v || FALLBACK;
}

function subscribe(cb: () => void): () => void {
  window.addEventListener(THEME_CHANGE_EVENT, cb);
  return () => window.removeEventListener(THEME_CHANGE_EVENT, cb);
}

/** Live hex value of the themeable signal accent, for SVG/recharts colour props. */
export function useAccentColor(): string {
  return useSyncExternalStore(subscribe, read, () => FALLBACK);
}
