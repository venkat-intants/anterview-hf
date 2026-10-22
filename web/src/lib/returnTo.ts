// Where to go after signing in (audit CA-M27).
//
// A signed-out visit to a protected page (an emailed /applications or
// /scorecard/:id link) is sent to /login. Without this, signing in always
// landed on the role's home and the link the person followed was lost.
//
// Only a same-app path is ever honoured: it must start with one "/" (so not
// "//evil.example" or a full URL), carry no backslash, and not point back into
// the sign-in pages. Anything else is ignored and the role's home is used.

const KEY = 'intants:return-to';

const AUTH_PATHS = [
  '/login',
  '/register',
  '/auth',
  '/change-password',
  '/forgot-password',
  '/reset-password',
];

/** `value` if it is a safe in-app path to return to, otherwise null. */
export function safeReturnPath(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  if (value.length === 0 || value.length > 2048) return null;
  if (!value.startsWith('/') || value.startsWith('//')) return null;
  if (value.includes('\\')) return null;
  // Control characters (a tab or newline can turn into "//" once stripped).
  for (let i = 0; i < value.length; i += 1) {
    if (value.charCodeAt(i) < 0x20) return null;
  }
  const path = value.split(/[?#]/, 1)[0];
  if (AUTH_PATHS.some((p) => path === p || path.startsWith(`${p}/`))) return null;
  return value;
}

/** Remember the path across the Google round trip, for this tab only. */
export function rememberReturnPath(path: string | null): void {
  try {
    if (path) sessionStorage.setItem(KEY, path);
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage blocked: the person lands on their home page instead */
  }
}

/** The remembered path, once. Always clears it. */
export function takeReturnPath(): string | null {
  try {
    const value = sessionStorage.getItem(KEY);
    sessionStorage.removeItem(KEY);
    return safeReturnPath(value);
  } catch {
    return null;
  }
}
