// safeUrl — guard against an open redirect when a server response hands back
// a URL to navigate to. A few endpoints (the candidate's own offer link,
// PH4-A3) return a full URL built from trusted server config, but a client
// that navigates anywhere it is told without checking is the mistake this
// rules out, not the server. Same-origin AND the exact expected pathname, or
// null — never a partial match.
//
// Its own module rather than living beside the one component that uses it:
// exporting a helper from a component file breaks React Fast Refresh (see
// navSections.tsx's own note on the same rule), and this is plain,
// side-effect-free logic worth testing on its own.

/**
 * A link the server handed back to OPEN (a signed download), accepted only as
 * https — or http when this app is itself served over http, as in local
 * development. Anything else (javascript:, data:, a relative path) is refused.
 */
export function downloadUrl(raw: string): string | null {
  try {
    const url = new URL(raw);
    if (url.protocol === 'https:') return url.href;
    if (url.protocol === 'http:' && window.location.protocol === 'http:') return url.href;
    return null;
  } catch {
    return null;
  }
}

export function sameOriginUrl(raw: string, pathname: string): string | null {
  try {
    const url = new URL(raw, window.location.origin);
    if (url.origin !== window.location.origin) return null;
    if (url.pathname !== pathname) return null;
    return url.href;
  } catch {
    return null;
  }
}

/**
 * A server-supplied in-app link, accepted only as a same-app absolute path
 * ("/hr/applicants/…"). react-router treats `scheme:`, a leading `//` and a
 * leading `/\` as EXTERNAL when given to <Link to> / navigate(), so a string
 * from the server must be checked before it is handed over. Returns the path
 * unchanged, or `fallback` (default null) when it is anything else.
 */
export function appPath(raw: unknown): string | null;
export function appPath<F>(raw: unknown, fallback: F): string | F;
export function appPath<F>(raw: unknown, fallback: F | null = null): string | F | null {
  if (typeof raw !== 'string') return fallback;
  // Leading '/', not followed by another '/' or a backslash; then no control
  // character (the URL parser strips TAB/LF/CR, which could re-create '//')
  // and no backslash anywhere.
  if (!raw.startsWith('/') || raw.startsWith('//')) return fallback;
  for (let i = 0; i < raw.length; i += 1) {
    const c = raw.charCodeAt(i);
    // C0 controls, DEL + C1 controls, and backslash (0x5c).
    if (c < 0x20 || (c >= 0x7f && c <= 0x9f) || c === 0x5c) return fallback;
  }
  return raw;
}

// The ONE avatar shape the backend accepts (services/data_gateway/app/routers/
// auth.py `_AVATAR_DATA_URI`): an inline png/jpeg/webp data URI, which is what
// the profile page's client-side downscaler produces. Keep the two in step.
const AVATAR_DATA_URI = /^data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}$/;

/**
 * An avatar to put in <img src>, or null. A remote URL would be a tracking
 * pixel that fires whenever staff open the profile, so only an inline raster
 * data URI is accepted (never svg, never http(s)). Older rows may still hold a
 * remote URL; those render as "no photo" rather than being fetched.
 */
export function safeAvatarSrc(raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  return AVATAR_DATA_URI.test(raw) ? raw : null;
}
