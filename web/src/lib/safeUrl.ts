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
