// useFragmentToken — read a one-time secret from the URL #fragment, then remove
// it from the address bar.
//
// A fragment keeps the secret off the wire, out of access logs and out of
// Referer, but it stays in the address bar and in the browser history entry —
// readable by the next person at a shared machine. So: read it once, scrub it
// at once, and hold it in memory for the component's lifetime. It is never
// written to storage, a cookie, or any other URL.
//
// The read is a PURE useState initialiser and the scrub is an effect, on
// purpose. React 18 StrictMode invokes initialisers twice and mounts effects
// twice: a scrub inside the initialiser would leave the second invocation with
// an empty hash (and an empty token), whereas an idempotent effect is harmless
// run twice, and the state set by the first render survives the simulated
// remount. A genuinely new mount after the scrub (navigate away, come back)
// sees no fragment and returns '' — the same as the link having been opened
// without one.
//
// Format: the whole fragment IS the token (`/path#<token>`), which is what
// every emailed link in the app uses today.

import { useEffect, useState } from 'react';

function readFragment(): string {
  if (typeof window === 'undefined') return '';
  return window.location.hash.replace(/^#/, '').trim();
}

/** Remove the fragment, keeping pathname, search and router state; no new history entry. */
export function scrubFragment(): void {
  if (typeof window === 'undefined' || !window.location.hash) return;
  const { pathname, search } = window.location;
  window.history.replaceState(window.history.state, '', `${pathname}${search}`);
}

/** The fragment token ('' when absent). Stable for the component's lifetime. */
export function useFragmentToken(): string {
  const [token] = useState(readFragment);
  useEffect(() => {
    scrubFragment();
  }, []);
  return token;
}
