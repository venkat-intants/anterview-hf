import { useCallback, useEffect, useState } from 'react'

/**
 * Light/dark choice for the public landing page ONLY.
 *
 * The signed-in app is a single dark theme (`<html class="dark">` plus the
 * accent-only picker in `components/ThemeToggle`), so this deliberately does
 * NOT touch <html>: it drives `data-landing-theme` on the landing root, whose
 * tokens live in `landing/styles/anterview.css`. Nothing here can recolour a
 * page behind the login.
 *
 * Default is LIGHT. A first-time visitor gets the grey/white page rather than
 * the system preference, because the marketing page has a chosen look and
 * `prefers-color-scheme: dark` is a statement about the visitor's OS, not
 * about which of our two treatments they want. The choice they make here is
 * remembered.
 */

export type LandingTheme = 'light' | 'dark'

export const LANDING_THEME_KEY = 'intants:landing-theme'

export function readLandingTheme(): LandingTheme {
  try {
    return localStorage.getItem(LANDING_THEME_KEY) === 'dark' ? 'dark' : 'light'
  } catch {
    // Private mode / storage disabled — the default still applies for this visit.
    return 'light'
  }
}

export function useLandingTheme(): [LandingTheme, (next: LandingTheme) => void] {
  const [theme, setTheme] = useState<LandingTheme>(readLandingTheme)

  // Keep the browser chrome (form controls, scrollbars) in step with the page.
  useEffect(() => {
    const root = document.documentElement
    const previous = root.style.colorScheme
    root.style.colorScheme = theme
    return () => {
      root.style.colorScheme = previous
    }
  }, [theme])

  const choose = useCallback((next: LandingTheme) => {
    setTheme(next)
    try {
      localStorage.setItem(LANDING_THEME_KEY, next)
    } catch {
      /* not persisted — the page still switches for this visit */
    }
  }, [])

  return [theme, choose]
}
