// pathId — the one way an id enters an API path in the staff consoles.
//
// Encoding alone is not enough: encodeURIComponent('..') is '..', and the
// browser resolves a '..' segment against the API origin. Every id these
// endpoints take is a server-issued UUID, so anything else is refused before a
// request is built — thrown synchronously, which React Query surfaces as the
// query's or mutation's error like any other failure.
//
// React Router decodes %2F in a route param, so `/hr/exams/..%2F..%2Fauth%2Fme`
// reaches a page as the string `../../auth/me`; every id interpolated into an
// API path must therefore go through pathId (UUIDs) or pathSegment (slugs).

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isUuid(value: string): boolean {
  return UUID.test(value);
}

export function pathId(id: string): string {
  if (!UUID.test(id)) {
    throw new Error('That link does not point at a valid record.');
  }
  return id;
}

/**
 * For a path segment that is legitimately NOT a UUID — a slug or a code
 * (feature-flag key, decision-reason code, careers company slug, task item
 * key). Percent-encodes the value, and refuses an empty segment or a dot
 * segment ('.', '..', and their encoded forms), which encodeURIComponent
 * leaves intact and the URL parser resolves against the parent path.
 */
export function pathSegment(value: string): string {
  const encoded = encodeURIComponent(value);
  const decoded = (() => {
    try {
      return decodeURIComponent(encoded);
    } catch {
      return encoded;
    }
  })();
  if (decoded === '' || decoded === '.' || decoded === '..' || /^[.]+$/.test(decoded)) {
    throw new Error('That link does not point at a valid record.');
  }
  return encoded;
}

/** For a numeric id (an integer primary key such as a stage-transition id). */
export function pathInt(id: number): string {
  if (!Number.isSafeInteger(id) || id < 0) {
    throw new Error('That link does not point at a valid record.');
  }
  return String(id);
}
