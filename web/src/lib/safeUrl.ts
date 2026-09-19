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
