import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { applyMode, isThemedRoute, useThemeMode } from '@/lib/useThemeMode';

/**
 * Applies the light/dark mode to <html> for the route currently mounted.
 *
 * The mode the visitor chose is honoured everywhere except the live interview,
 * which resolves to `dark` REGARDLESS of the preference: that screen is a video
 * surface where the avatar and the candidate's camera are the content, and a
 * light chrome around them is a worse product, not a preference. The stored
 * choice is untouched, so leaving the session restores it.
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
