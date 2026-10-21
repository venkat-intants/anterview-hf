// errorMessage - the one place an error becomes a sentence a person can act on
// (audit X1 and X16).
//
// Before this, 168 places put `err.message` straight on screen. A response with
// no `detail` became "HTTP 500", a FastAPI validation failure became "HTTP 422"
// (its `detail` is an array the client did not read), and a dropped connection
// became the browser's "Failed to fetch". Fallbacks came in four styles ("X
// failed", "Could not", "Couldn't", "Failed to").
//
// The rule for callers: show `errorMessage(err, 'Could not <verb> <object>.')`.
// A sentence the server wrote for people wins; otherwise the status decides the
// copy, and the caller's fallback covers everything else. A raw status code or a
// browser transport message is never returned.
//
// This module must not import from '@/api/client' (client imports from here),
// so an ApiError is recognised by shape: an Error with a numeric `status`.

/** A dropped or refused connection: no response was received at all. */
export const NETWORK_ERROR_MESSAGE =
  'Could not reach the server. Check your connection and try again.';

/** The sign-in has lapsed; the client is already redirecting to /login. */
export const SESSION_EXPIRED_MESSAGE = 'Your session has expired. Sign in again.';

const SERVER_TROUBLE = 'The server had a problem. Try again in a moment.';

/**
 * Plain copy for an HTTP status, for when the server sent no sentence of its
 * own. Used by the API client as the message of an ApiError with no `detail`,
 * so even a caller that shows `err.message` directly never shows "HTTP 503".
 */
export function statusMessage(status: number): string {
  switch (status) {
    case 400:
      return 'The request could not be completed. Check the details and try again.';
    case 401:
      return SESSION_EXPIRED_MESSAGE;
    case 403:
      return 'You do not have permission to do that.';
    case 404:
      return 'We could not find that. It may have been removed.';
    case 408:
      return 'The server took too long to answer. Try again.';
    case 409:
      return 'This changed while you were working. Refresh and try again.';
    case 410:
      return 'This link is no longer valid.';
    case 413:
      return 'That file is too large.';
    case 415:
      return 'That file type is not supported.';
    case 422:
      return 'Some details are not valid. Check the form and try again.';
    case 429:
      return 'Too many attempts. Wait a moment and try again.';
    default:
      return status >= 500 ? SERVER_TROUBLE : 'The request could not be completed. Try again.';
  }
}

/** Statuses whose copy says more than any per-action fallback could. */
const STATUS_OVERRIDES_FALLBACK = new Set([401, 403, 404, 409, 410, 413, 415, 429]);

/** `HTTP 502`, `502`, an empty string: what a client says when it has no detail. */
const BARE_STATUS = /^(HTTP\s*)?\d{3}\b[\s:.-]*$/i;

/** What browsers put in a fetch TypeError: Chrome, Firefox, Safari respectively. */
const TRANSPORT_TEXT = /failed to fetch|networkerror|load failed|network request failed/i;

/** The generic body of a 500 (shared/http_observability.py): not worth showing. */
const GENERIC_SERVER_TEXT = /^internal server error\.?$/i;

/** True when `text` is a sentence written for people rather than a code or a stub. */
export function isReadableMessage(text: unknown): text is string {
  if (typeof text !== 'string') return false;
  const t = text.trim();
  return (
    t.length > 0 &&
    !BARE_STATUS.test(t) &&
    !TRANSPORT_TEXT.test(t) &&
    !GENERIC_SERVER_TEXT.test(t) &&
    t !== '[object Object]'
  );
}

/** True for an error that means no response arrived (offline, DNS, CORS, reset). */
export function isNetworkError(err: unknown): boolean {
  if (err instanceof TypeError) return TRANSPORT_TEXT.test(err.message);
  // The XHR upload path rejects with this plain Error (api/client.ts).
  return err instanceof Error && /^network error\b/i.test(err.message);
}

interface StatusError extends Error {
  status: number;
  hasServerMessage?: boolean;
}

function hasStatus(err: unknown): err is StatusError {
  return err instanceof Error && typeof (err as { status?: unknown }).status === 'number';
}

/**
 * The sentence to show for `err`. `fallback` is the action-specific copy, in
 * the house style "Could not <verb> <object>." (with the period).
 *
 *   network failure                -> NETWORK_ERROR_MESSAGE
 *   API error with a server sentence -> that sentence (a 409 "This opening is
 *                                     closed." says more than any fallback)
 *   401/403/404/409/410/413/415/429 -> statusMessage(status)
 *   5xx                            -> fallback + " Try again in a moment."
 *   anything else                  -> fallback
 *
 * A plain `Error` thrown by our own code with a readable message (for example
 * the upload timeout) is shown as written; programming errors (TypeError,
 * RangeError, ...) are not, because their text is for developers.
 */
export function errorMessage(err: unknown, fallback: string): string {
  if (isNetworkError(err)) return NETWORK_ERROR_MESSAGE;

  if (hasStatus(err)) {
    if (err.hasServerMessage !== false && isReadableMessage(err.message)) {
      return err.message.trim();
    }
    if (STATUS_OVERRIDES_FALLBACK.has(err.status)) return statusMessage(err.status);
    if (err.status >= 500) return `${fallback} Try again in a moment.`;
    return fallback;
  }

  if (err instanceof Error) {
    if (err.message === 'Session expired') return SESSION_EXPIRED_MESSAGE;
    if (err.name === 'Error' && isReadableMessage(err.message)) return err.message.trim();
  }
  return fallback;
}

// ---------------------------------------------------------------------------
// FastAPI validation errors
// ---------------------------------------------------------------------------
//
// Every service now answers a validation failure with a top-level `message`
// (shared/http_observability.py, install_validation_error_handler), and the
// client prefers it. This is the fallback for a response without one: a proxy,
// an older deploy, or a route mounted outside the four apps. It mirrors the
// backend's wording for the common cases so the two read the same.

const LOC_SOURCES = new Set(['body', 'query', 'path', 'header', 'cookie']);
const LOC_TYPE_TAGS = new Set([
  'str',
  'int',
  'float',
  'bool',
  'list',
  'dict',
  'set',
  'tuple',
  'bytes',
  'none',
  'literal',
]);
const FIELD_ACRONYMS: Record<string, string> = {
  id: 'ID',
  ids: 'IDs',
  url: 'URL',
  urls: 'URLs',
  jd: 'JD',
  ats: 'ATS',
  ctc: 'CTC',
  mcq: 'MCQ',
  pdf: 'PDF',
  otp: 'OTP',
  sla: 'SLA',
  utc: 'UTC',
};
const IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** `['body', 'max_candidates']` -> "Max candidates"; `['body']` -> null. */
export function humaniseField(loc: readonly unknown[]): string | null {
  const names = loc.filter(
    (part, i): part is string =>
      typeof part === 'string' && IDENTIFIER.test(part) && !(i === 0 && LOC_SOURCES.has(part)),
  );
  while (names.length > 1 && LOC_TYPE_TAGS.has(names[names.length - 1].toLowerCase())) {
    names.pop();
  }
  const last = names[names.length - 1];
  if (!last) return null;
  const words = last
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .toLowerCase()
    .split('_')
    .filter(Boolean)
    .map((w) => FIELD_ACRONYMS[w] ?? w);
  if (words.length === 0) return null;
  words[0] = words[0].charAt(0).toUpperCase() + words[0].slice(1);
  return words.join(' ');
}

interface ValidationItem {
  type?: unknown;
  loc?: unknown;
  msg?: unknown;
  ctx?: unknown;
}

function sentence(text: string): string {
  const t = text.trim();
  const capped = t.charAt(0).toUpperCase() + t.slice(1);
  return /[.!?]$/.test(capped) ? capped : `${capped}.`;
}

/**
 * One sentence for the first entry of a FastAPI validation `detail` array, or
 * null when the array holds nothing usable.
 */
export function validationMessage(detail: readonly unknown[]): string | null {
  const first = detail[0] as ValidationItem | undefined;
  if (!first || typeof first !== 'object') return null;
  const kind = typeof first.type === 'string' ? first.type : '';
  const loc = Array.isArray(first.loc) ? (first.loc as unknown[]) : [];
  const ctx =
    first.ctx && typeof first.ctx === 'object' ? (first.ctx as Record<string, unknown>) : {};
  const field = humaniseField(loc);
  const subject = field ?? 'This value';
  const msg = (typeof first.msg === 'string' ? first.msg : '')
    .replace(/^(Value error|Assertion failed), /, '')
    .trim();

  if (kind === 'missing') {
    return field ? `${field} is required.` : 'Some required information is missing.';
  }
  if (kind === 'string_too_short') {
    const min = ctx.min_length;
    return min === undefined || min === 1
      ? `${subject} cannot be empty.`
      : `${subject} must be at least ${String(min)} characters.`;
  }
  if (kind === 'string_too_long' && ctx.max_length !== undefined) {
    return `${subject} must be at most ${String(ctx.max_length)} characters.`;
  }
  const bounds: Record<string, [string, (v: string) => string]> = {
    greater_than_equal: ['ge', (v) => `${v} or more`],
    greater_than: ['gt', (v) => `more than ${v}`],
    less_than_equal: ['le', (v) => `${v} or less`],
    less_than: ['lt', (v) => `less than ${v}`],
  };
  const bound = bounds[kind];
  if (bound && ctx[bound[0]] !== undefined) {
    return `${subject} must be ${bound[1](String(ctx[bound[0]]))}.`;
  }
  if (kind === 'int_parsing' || kind === 'int_type') return `${subject} must be a whole number.`;
  if (kind === 'float_parsing' || kind === 'float_type') return `${subject} must be a number.`;
  if ((kind === 'literal_error' || kind === 'enum') && typeof ctx.expected === 'string') {
    return `${subject} must be one of ${ctx.expected}.`;
  }
  if (msg.startsWith('value is not a valid email address')) {
    return `${subject} must be a valid email address.`;
  }
  if (!msg) return null;
  return sentence(field ? `${field}: ${msg}` : msg);
}
