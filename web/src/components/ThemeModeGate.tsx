import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { applyMode, isThemedRoute, useThemeMode } from '@/lib/useThemeMode';

/**
 * Applies the light/dark mode to <html> for the route currently mounted.
 *
 * The mode the visitor chose is only honoured on routes that have a light
 * design. On a staff console or the live interview it resolves to `dark`
 * REGARDLESS of the preference: those pages are written against the dark
 * palette, and lighting them without the accompanying sweep would produce
 * white-on-white, not a light theme. The stored preference is untouched, so
 * walking back to /dashboard restores it.
 *
 * Renders nothing — it only owns the attribute.
 */
export default function ThemeModeGate() {
  const [mode] = useThemeMode();
  const { pathname } = useLocation();

  useEffect(() => {
    applyMode(isThemedRoute(pathname) ? mode : 'dark');
  }, [mode, pathname]);

  return null;
}
