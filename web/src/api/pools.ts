// pools.ts — PH5-E3. A company's own talent pools: HR-curated lists of
// candidates to contact about future openings, built by hand or from a
// rediscovery search.
//
// Field names match `services/data_gateway/app/schemas/pools.py` +
// `app/routers/hr_pools.py` (prefix `/hr/pools`), verified against the
// shipped code, not only the contract sketch. Every nullable server field is
// still typed `?:` rather than `| null`: unlike the rediscovery search route,
// `hr_pools.py` does NOT use `response_model_exclude_none` (its own module
// docstring says so) — a null field arrives as an explicit `null`, not an
// absent key. Typing it optional and reading it with a presence/truthy check
// (never `=== null`) is correct either way, so nothing here had to change
// once that became knowable; only this comment did.
//
// KNOWN GAP (report this, do not silently work around it further): the
// contract's criterion-14 acknowledgement gate — inviting a member whose
// evidence `requires_review` should 422 `stale_evidence_unreviewed` until HR
// sends `acknowledged_stale: true` — is NOT implemented server-side.
// `MemberInviteIn` (schemas/pools.py) takes only `requisition_id` and is
// `extra="forbid"`, and `talent_pools.py::invite_member` has no staleness
// check at all. `inviteMember` below still supports `acknowledgedStale`
// (sent only when explicitly true, so the normal path never trips the
// server's `extra="forbid"`) and `TalentPools.tsx` still reacts to the 422 if
// it ever arrives — but today that branch is unreachable dead code, and HR
// can invite a member with two-year-old evidence with no server-side gate at
// all. This needs a backend change, not a frontend workaround.
//
// `hr_manager` ONLY. A pool names candidates, so it is `candidate_pii`
// (`shared/agents/schema.py::DATA_CLASS_ROLES`), and a company `super_admin`
// is deliberately NOT a superset of HR (CLAUDE.md) — narrower than
// `hr_corpus.py` on purpose.

import type {
  RediscoveryMatchReasonSnapshot,
  RediscoveryHeaderFreshness,
  RediscoveryResult,
} from './rediscovery';
import { apiDelete, apiGet, apiPatch, apiPost, ApiError } from './client';
import { pathId } from './pathId';

/**
 * What this CLIENT can know about a match reason: everything except
 * `frozen_at`, which is the moment the server records the row, not the
 * moment the browser clicked "Add to pool" — the server's own
 * `freeze_match_reason()` stamps it (and strips prose the design says a
 * snapshot must not carry: `why[].snippet`, `.note`, and every citation's
 * `href`). This client sends the raw live shape; freezing is a server
 * decision, not a client-side rewrite that could drift from it.
 */
export type MatchReasonInput = Omit<RediscoveryMatchReasonSnapshot, 'frozen_at'>;

/** Build the `match_reason` a "Add to pool" call sends from a live search
 *  result — everything the frozen snapshot needs except the timestamp. */
export function matchReasonFromResult(row: RediscoveryResult): MatchReasonInput {
  return {
    score: row.score,
    explained: row.explained,
    breakdown: row.breakdown,
    evidence_freshness: row.evidence_freshness,
    requires_review: row.requires_review,
    review_reasons: row.review_reasons,
    why: row.why,
  };
}

export interface PoolOut {
  id: string;
  name: string;
  description?: string;
  member_count: number;
  created_by_name?: string;
  created_at: string;
  updated_at: string;
  archived_at?: string;
}

export interface PoolLimits {
  max_per_company: number;
  max_members: number;
}

export interface PoolsListResponse {
  pools: PoolOut[];
  limits: PoolLimits;
}

export type PoolMemberSource = 'manual' | 'rediscovery';

export type PoolIneligibleReason = 'consent_withdrawn' | 'consent_expired' | 'erasure_requested';

/**
 * One member row. `match_reason` is present only for `source: 'rediscovery'`
 * — the frozen snapshot from the search that added them (see
 * `RediscoveryMatchReasonSnapshot`'s own note on why its citations carry no
 * `href`). `evidence_freshness` here is the row's OWN band as at add time —
 * present for a manually added member too, when the server could compute one.
 *
 * `eligible: false` means the rediscovery affordances lapse (no evidence
 * panel, no Review evidence, no Invite) even though the name still renders —
 * HR already holds this person under the APPLICATION consent, a separate
 * purpose from rediscovery (design §4.3). Only `Remove` is ever offered on an
 * ineligible row.
 */
export interface PoolMemberOut {
  member_id: string;
  applicant_id: string;
  full_name: string;
  current_title?: string;
  current_company?: string;
  source: PoolMemberSource;
  added_at: string;
  added_by_name?: string;
  note?: string;
  match_reason?: RediscoveryMatchReasonSnapshot;
  evidence_freshness?: RediscoveryHeaderFreshness;
  evidence_reviewed_at?: string;
  evidence_reviewed_by_name?: string;
  eligible: boolean;
  ineligible_reason?: PoolIneligibleReason;
  opted_in_at?: string;
  expires_at?: string;
  /** The newest enrolment for this applicant — what "Review evidence" needs
   *  for `/hr/enrolments/{id}/evidence`. Absent when there is none yet. */
  enrolment_id?: string;
}

export interface PoolDetailResponse {
  pool: PoolOut;
  members: PoolMemberOut[];
}

export interface CreatePoolInput {
  name: string;
  description?: string | null;
}

export interface UpdatePoolInput {
  name?: string;
  description?: string | null;
  archived?: boolean;
}

export interface DeletePoolResult {
  deleted: true;
  members_removed: number;
}

export type AddMemberSkipReason = 'already_a_member' | 'not_this_company' | 'pool_full' | 'not_eligible';

export interface AddMembersResult {
  added: string[];
  skipped: Array<{ applicant_id: string; reason: AddMemberSkipReason }>;
}

export interface AddMembersInput {
  applicantIds: string[];
  source: PoolMemberSource;
  note?: string | null;
  /** Only for `source: 'rediscovery'` — the live result's own breakdown/why;
   *  see `matchReasonFromResult`. Omit for a manual add. The route's body
   *  carries one `match_reason` per call, so adding several rediscovery
   *  results to a pool at once means one call per applicant, each with its
   *  own snapshot — never one shared snapshot for a batch of different
   *  people. */
  matchReason?: MatchReasonInput | null;
}

export interface InviteResult {
  enrolment_id: string;
  requisition_id: string;
  already_enrolled: boolean;
}

export interface InviteInput {
  requisitionId: string;
  /**
   * The criterion-14 gate (design §6.6): omitted on the first attempt, then
   * sent as `true` once HR has seen and accepted the "evidence has not been
   * reviewed" prompt following a 422 `stale_evidence_unreviewed`. NOT part of
   * the contract's documented request body — the server, not this client, is
   * the source of truth for whether a member needs the acknowledgement; see
   * this module's own `inviteMember` doc for how the two calls compose.
   */
  acknowledgedStale?: boolean;
}

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

export function listPools(includeArchived = false): Promise<PoolsListResponse> {
  const qs = includeArchived ? '?include_archived=true' : '';
  return apiGet<PoolsListResponse>(`/hr/pools${qs}`);
}

export function createPool(input: CreatePoolInput): Promise<PoolOut> {
  return apiPost<PoolOut>('/hr/pools', { name: input.name, description: input.description ?? null });
}

export function getPool(poolId: string): Promise<PoolDetailResponse> {
  return apiGet<PoolDetailResponse>(`/hr/pools/${pathId(poolId)}`);
}

export function updatePool(poolId: string, input: UpdatePoolInput): Promise<PoolOut> {
  const body: Record<string, unknown> = {};
  if (input.name !== undefined) body.name = input.name;
  if (input.description !== undefined) body.description = input.description;
  if (input.archived !== undefined) body.archived = input.archived;
  return apiPatch<PoolOut>(`/hr/pools/${pathId(poolId)}`, body);
}

/** Removes the pool AND every member row with it — the confirm dialog before
 *  this call must name `members_removed` from the pool's own `member_count`,
 *  never a generic "are you sure". */
export function deletePool(poolId: string): Promise<DeletePoolResult> {
  return apiDelete<DeletePoolResult>(`/hr/pools/${pathId(poolId)}`);
}

export function addPoolMembers(poolId: string, input: AddMembersInput): Promise<AddMembersResult> {
  return apiPost<AddMembersResult>(`/hr/pools/${pathId(poolId)}/members`, {
    applicant_ids: input.applicantIds,
    source: input.source,
    note: input.note ?? null,
    match_reason: input.matchReason ?? null,
  });
}

/**
 * Soft removal (`removed_at`) — re-adding the same applicant later is a new
 * row, so the pool's history survives. `DELETE` with a JSON body: the one
 * place this API needs to carry a reason on a delete, mirroring how
 * `client.ts`'s typed helpers already pass `ClientOptions` through untouched.
 */
export function removePoolMember(
  poolId: string,
  memberId: string,
  reason?: string | null,
): Promise<{ removed: true }> {
  return apiDelete<{ removed: true }>(`/hr/pools/${pathId(poolId)}/members/${pathId(memberId)}`, {
    body: JSON.stringify({ reason: reason ?? null }),
  });
}

export function markEvidenceReviewed(poolId: string, memberId: string): Promise<PoolMemberOut> {
  return apiPost<PoolMemberOut>(
    `/hr/pools/${pathId(poolId)}/members/${pathId(memberId)}/evidence-reviewed`,
    {},
  );
}

/**
 * `POST /hr/pools/{pool_id}/members/{member_id}/invite`.
 *
 * The criterion-14 gate lives on the SERVER: a first call with no
 * `acknowledgedStale` on a member whose evidence `requires_review` comes back
 * 422 `stale_evidence_unreviewed`. The caller (TalentPools.tsx) reacts to
 * that specific failure by showing the acknowledgement prompt and retrying
 * with `acknowledgedStale: true` — never by pre-computing "does this member
 * need it?" from the list response, which is not this client's decision to
 * make and would silently drift from the server's own rule.
 */
export function inviteMember(
  poolId: string,
  memberId: string,
  input: InviteInput,
): Promise<InviteResult> {
  const body: Record<string, unknown> = { requisition_id: input.requisitionId };
  if (input.acknowledgedStale) body.acknowledged_stale = true;
  return apiPost<InviteResult>(`/hr/pools/${pathId(poolId)}/members/${pathId(memberId)}/invite`, body);
}

// ---------------------------------------------------------------------------
// Errors — `{"detail": {"failure_code": "…", "message": "…"}}`, the
// `CorpusError`/`RediscoveryError` shape (`api/corpus.ts::corpusErrorMessage`
// is the same reading, not a second implementation of it).
// ---------------------------------------------------------------------------

export const POOL_FAILURE_SENTENCES: Record<string, string> = {
  pool_name_taken: 'A pool with that name already exists.',
  pool_full: 'This pool already has as many members as it can hold.',
  pool_limit_reached: "Your company has reached its limit on talent pools.",
  pool_not_found: 'That pool could not be found.',
  member_not_found: 'That member could not be found.',
  not_eligible: 'This candidate cannot be added right now — their rediscovery consent is not active.',
  invalid_name: 'Give the pool a name.',
  requisition_not_open: 'That opening is not open for new applications.',
  stale_evidence_unreviewed: 'This evidence has not been reviewed recently.',
  consent_not_active: "This person's rediscovery consent is no longer active.",
};

export function poolErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    const detail = error.detail as { failure_code?: string; message?: string } | undefined;
    const code = detail?.failure_code;
    if (code && POOL_FAILURE_SENTENCES[code]) return POOL_FAILURE_SENTENCES[code];
    return error.message || fallback;
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

/** True when an `ApiError` carries the criterion-14 gate's own failure code —
 *  the one 422 `inviteMember`'s caller must treat as "ask, don't fail". */
export function isStaleEvidenceUnreviewed(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  const detail = error.detail as { failure_code?: string } | undefined;
  return detail?.failure_code === 'stale_evidence_unreviewed';
}
