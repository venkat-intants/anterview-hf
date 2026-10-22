// localStorage for per-viewer conveniences (a remembered language, avatar or
// collapsed sidebar), failing soft.
//
// Reading or writing localStorage throws when storage is blocked: private
// modes, site data turned off, some embedded browsers. Unguarded calls at
// render crashed the whole candidate shell (audit CA-L18). Every value kept
// here is a preference, so losing it only means the default is used.

export function readPref(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writePref(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage blocked: the preference lasts for this page only */
  }
}
