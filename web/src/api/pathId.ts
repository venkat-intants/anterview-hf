// pathId — the one way an id enters an API path in the PH4 staff consoles.
//
// Encoding alone is not enough: encodeURIComponent('..') is '..', and the
// browser resolves a '..' segment against the API origin. Every id these
// endpoints take is a server-issued UUID, so anything else is refused before a
// request is built — thrown synchronously, which React Query surfaces as the
// query's or mutation's error like any other failure.

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
