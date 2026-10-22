// proposalGuard — the last check before a copilot proposal is fired.
//
// A proposal carries a method and path chosen server-side (shared/agents
// CommitSpec). The frontend fires it with the user's bearer token, so a path
// that escapes the API origin would hand that token to another host. The
// backend validator rejects absolute URLs, but string concatenation
// (`${base}${path}`) is where it goes wrong on the production build, where the
// base is the empty string and the path alone becomes the URL: the WHATWG URL
// parser treats `/\evil.example/x` and `/<TAB>/evil.example/x` as another host.
//
// So the path is held to a strict allowlist and then resolved exactly the way
// the request will be, and refused unless that resolution stays on the API
// origin and on the very path that was asked for. Defence in depth: the regex
// alone is enough today; the resolution check is what keeps it enough if the
// regex is ever loosened.

/** Methods a proposal legitimately uses (every CommitSpec in the backend). */
const ALLOWED_METHODS = ['POST', 'PATCH', 'PUT'] as const;
export type ProposalMethod = (typeof ALLOWED_METHODS)[number];

/**
 * Path prefixes proposals are issued under. Every CommitSpec constructor in
 * services/data_gateway/app/agents/{tools,workflow_tools}.py targets /hr/…
 * (staff-only endpoints); a new prefix must be added here deliberately.
 */
const ALLOWED_PREFIXES = ['/hr/'] as const;

// Slash-led, letters/digits/`_-.` only. No backslash, control character, `?`,
// `#`, `%` or whitespace — nothing the URL parser treats specially.
const SAFE_PATH = /^\/[A-Za-z0-9/_\-.]*$/;

export class ProposalRejectedError extends Error {
  constructor(reason: string) {
    super(`This proposal was not sent: ${reason}.`);
    this.name = 'ProposalRejectedError';
  }
}

function hasDotOrEmptySegment(path: string): boolean {
  // path starts with '/', so segment 0 is always the empty string before it.
  return path
    .split('/')
    .slice(1)
    .some((seg) => seg === '' || seg === '.' || seg === '..');
}

/**
 * Validate a proposal's method and path against the API it will be sent to.
 *
 * `base` is the configured API base URL — possibly '' (the production Space
 * build, same-origin), in which case the API origin is `origin`.
 * Returns the validated method and path; throws ProposalRejectedError otherwise.
 */
export function assertSafeProposalRequest(
  method: string,
  path: string,
  base: string,
  origin: string,
): { method: ProposalMethod; path: string } {
  if (!(ALLOWED_METHODS as readonly string[]).includes(method)) {
    throw new ProposalRejectedError('unsupported request method');
  }
  if (!SAFE_PATH.test(path) || hasDotOrEmptySegment(path)) {
    throw new ProposalRejectedError('unsafe request path');
  }
  if (!ALLOWED_PREFIXES.some((p) => path.startsWith(p))) {
    throw new ProposalRejectedError('request path is outside the allowed API area');
  }

  let apiOrigin: string;
  let apiPrefix: string;
  let resolved: URL;
  try {
    const baseUrl = new URL(base || '/', origin);
    apiOrigin = baseUrl.origin;
    apiPrefix = baseUrl.pathname.replace(/\/+$/, '');
    resolved = new URL(`${base}${path}`, origin);
  } catch {
    throw new ProposalRejectedError('request could not be resolved');
  }
  if (
    resolved.origin !== apiOrigin ||
    resolved.pathname !== `${apiPrefix}${path}` ||
    resolved.search !== '' ||
    resolved.hash !== ''
  ) {
    throw new ProposalRejectedError('request would leave the API');
  }
  return { method: method as ProposalMethod, path };
}

/** One path segment that is a record id (the backend's ids are UUIDs). */
const ID = '[0-9A-Za-z-]+';

/**
 * The exact endpoints each proposal kind may fire, as (method, path pattern).
 *
 * The checks above keep a proposal on the API and under `/hr/`; this one keeps
 * it on the endpoint its kind is ABOUT, so an "Interview invite" card can only
 * ever create an interview invite, whatever a mis-written server tool put in
 * its CommitSpec (audit CP-L4). Each entry mirrors one CommitSpec constructor
 * in services/data_gateway/app/agents/{tools,workflow_tools}.py. A kind with
 * no entry has no drafting tool today and is refused; adding a drafting tool
 * means adding its endpoint here, deliberately.
 */
const KIND_ENDPOINTS: Readonly<Record<string, ReadonlyArray<readonly [ProposalMethod, RegExp]>>> = {
  interview_invite: [['POST', /^\/hr\/interviews$/]],
  shortlist: [['PATCH', new RegExp(`^/hr/applicants/${ID}$`)]],
  workflow: [['POST', new RegExp(`^/hr/requisitions/${ID}/workflows/apply$`)]],
  workflow_round: [
    ['POST', new RegExp(`^/hr/workflows/${ID}/rounds$`)],
    ['PUT', new RegExp(`^/hr/workflows/${ID}/rounds/${ID}/criteria$`)],
  ],
  workflow_settings: [['PATCH', new RegExp(`^/hr/workflows/${ID}$`)]],
};

/**
 * Refuse a proposal whose request is not one its kind is allowed to make.
 * Run after `assertSafeProposalRequest`, on the method and path it returned.
 */
export function assertProposalMatchesKind(kind: string, method: ProposalMethod, path: string): void {
  const allowed = Object.prototype.hasOwnProperty.call(KIND_ENDPOINTS, kind)
    ? KIND_ENDPOINTS[kind]
    : undefined;
  if (!allowed) {
    throw new ProposalRejectedError('this kind of proposal cannot be sent from here');
  }
  if (!allowed.some(([m, pattern]) => m === method && pattern.test(path))) {
    throw new ProposalRejectedError('the request does not match what this proposal is for');
  }
}
