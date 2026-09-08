import { useThemeMode, readMode, type ThemeMode } from '@/lib/useThemeMode'

/**
 * The landing page's light/dark choice IS the app's mode — one preference, not
 * two. A visitor who picks light on the marketing page and then signs in should
 * land on a light dashboard; when these were separate they did not.
 *
 * The landing root still stamps `data-landing-theme` so its own `--lp-*` tokens
 * (landing/styles/anthire.css) resolve without depending on <html>. That
 * attribute mirrors the shared mode rather than owning it.
 */

export type LandingTheme = ThemeMode

/** Kept for the pre-mount read in pages/Landing.tsx. */
export function readLandingTheme(): LandingTheme {
  return readMode()
}

export function useLandingTheme(): [LandingTheme, (next: LandingTheme) => void] {
  return useThemeMode()
}
