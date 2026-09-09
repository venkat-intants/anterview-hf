import { useEffect } from 'react';
import { applyMode, useThemeMode } from '@/lib/useThemeMode';

/**
 * Applies the light/dark mode to <html>.
 *
 * It used to consult the route, because some pages had no light design and had
 * to be pinned. They all have one now, so the route is no longer an input:
 * whether a surface follows the mode is a property of the component, not of the
 * URL. The one component that paints its own colours — the live interview's
 * full-bleed video player — does so in its own markup and says why there.
 *
 * Renders nothing; it only owns the attribute.
 */
export default function ThemeModeGate() {
  const [mode] = useThemeMode();

  useEffect(() => {
    applyMode(mode);
  }, [mode]);

  return null;
}
