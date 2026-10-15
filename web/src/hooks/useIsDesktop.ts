// useIsDesktop — the `lg` breakpoint (1024px), without a resize listener per
// instance. Where `matchMedia` does not exist (jsdom) the desktop layout is
// assumed, so tests see every region a desktop user does.

import { useCallback, useSyncExternalStore } from 'react';

const DESKTOP = '(min-width: 1024px)';

export function useIsDesktop(): boolean {
  const subscribe = useCallback((notify: () => void) => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => undefined;
    const mq = window.matchMedia(DESKTOP);
    mq.addEventListener('change', notify);
    return () => mq.removeEventListener('change', notify);
  }, []);
  return useSyncExternalStore(
    subscribe,
    () => (typeof window.matchMedia === 'function' ? window.matchMedia(DESKTOP).matches : true),
    () => true,
  );
}
