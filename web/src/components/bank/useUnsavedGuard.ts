// useUnsavedGuard - stop someone losing typed work by leaving an editor.
//
// The app mounts a plain <BrowserRouter>, which has no navigation blocker, so
// three separate nets are used:
//   1. `beforeunload`, for a reload, a closed tab or a typed URL;
//   2. a capture-phase click listener that asks before an in-app link is
//      followed while edits are held;
//   3. `confirmLeave()`, which the page calls before it changes what is on
//      screen itself (another stage, another question, another method);
//   4. a history "sentinel" entry that makes the browser Back button ask first
//      (see the popstate effect below).
//
// Dirty state is reported by whichever editor holds the edits, through the
// returned `setDirty`. Several editors never hold edits at once (one panel is
// open at a time), so one flag is enough.

import { useCallback, useEffect, useRef, useState } from 'react';

export const LEAVE_PROMPT = 'You have unsaved changes. Leave and lose them?';

export interface UnsavedGuard {
  dirty: boolean;
  setDirty: (dirty: boolean) => void;
  /** True when it is fine to go on: nothing unsaved, or the person agreed to lose it. */
  confirmLeave: () => boolean;
}

export function useUnsavedGuard(): UnsavedGuard {
  const [dirty, setDirtyState] = useState(false);
  const ref = useRef(false);

  const setDirty = useCallback((next: boolean) => {
    ref.current = next;
    setDirtyState(next);
  }, []);

  const confirmLeave = useCallback((): boolean => {
    if (!ref.current) return true;
    const ok = window.confirm(LEAVE_PROMPT);
    if (ok) {
      ref.current = false;
      setDirtyState(false);
    }
    return ok;
  }, []);

  useEffect(() => {
    if (!dirty) return undefined;
    const onBeforeUnload = (e: BeforeUnloadEvent): void => {
      e.preventDefault();
      // Required by older engines; the text itself is never shown.
      e.returnValue = '';
    };
    const onClick = (e: MouseEvent): void => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
      const target = e.target instanceof Element ? e.target.closest('a[href]') : null;
      if (!(target instanceof HTMLAnchorElement)) return;
      if (target.target === '_blank' || target.hasAttribute('download')) return;
      const url = new URL(target.href, window.location.href);
      if (url.origin !== window.location.origin) return;
      // Same page, different query (the stage links on this very page) is the
      // page's own business: it calls confirmLeave itself.
      if (url.pathname === window.location.pathname) return;
      if (!window.confirm(LEAVE_PROMPT)) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    document.addEventListener('click', onClick, true);
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload);
      document.removeEventListener('click', onClick, true);
    };
  }, [dirty]);

  // 4. The browser Back/Forward buttons. `beforeunload` does not fire for an
  // in-app history move, and BrowserRouter cannot block one, so the guard keeps
  // a duplicate "sentinel" entry on top of the history while edits are held.
  // Back then only consumes the sentinel - the page stays put, and we ask. If
  // the person cancels the sentinel is pushed back; if they agree they are
  // still here (on the original entry) and a second Back leaves.
  const unmounting = useRef(false);
  const sentinel = useRef(false);
  useEffect(() => {
    unmounting.current = false;
    return () => {
      unmounting.current = true;
    };
  }, []);
  useEffect(() => {
    if (!dirty) return undefined;
    const keep = window.history.state as unknown;
    window.history.pushState(keep, '', window.location.href);
    sentinel.current = true;
    const onPopState = (): void => {
      if (window.confirm(LEAVE_PROMPT)) {
        sentinel.current = false;
        ref.current = false;
        setDirtyState(false);
      } else {
        window.history.pushState(keep, '', window.location.href);
      }
    };
    window.addEventListener('popstate', onPopState);
    return () => {
      window.removeEventListener('popstate', onPopState);
      // Edits were saved or discarded in place: take the sentinel off again so
      // the history does not grow by one entry per edit. Never on unmount - by
      // then the page is already going somewhere else.
      if (sentinel.current && !unmounting.current) {
        sentinel.current = false;
        window.history.back();
      }
    };
  }, [dirty]);

  return { dirty, setDirty, confirmLeave };
}
