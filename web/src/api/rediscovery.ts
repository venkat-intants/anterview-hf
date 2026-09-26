// rediscovery.ts — PH5-E3. HR's "search candidates who opted in to be found
// again" screen, and the two candidate-facing doors that control whether
// anyone can be found at all.
//
// Field names match services/data_gateway/app/schemas/rediscovery.py EXACTLY
// (the shipped commit e3c2c69) for search/universe. The candidate doors
// (`/users/me/rediscovery`) were built in `routers/candidate_applications.py`
// while this file was in progress and are verified against that code too —
// `RediscoveryCompanyOut`/`RediscoveryListOut`/`RediscoveryToggleIn` match the
// types below field-for-field, including `{"on": bool}` as the PUT body.
//
// THREE THINGS THAT ARE LOAD-BEARING, not decoration:
//
// 1. `response_model_exclude_none=True` on the search route. A field the
//    server did not compute is OMITTED, never `null` — so every optional
//    field below is `?:`, and every renderer uses a presence check
//    (`'x' in item`, `item.x !== undefined`), never `=== null`.
// 2. `resume_terms.snippet` carries `[[…]]` bracket markers, NOT `<b>` tags —
//    `parseBracketHighlights` below is how a screen turns that into a
//    highlighted span. NEVER `dangerouslySetInnerHTML` on it: it is
//    candidate-authored CV text.
// 3. An `ai_interview` signal's `contribution` is always 0 and it never
//    carries a `score` — it is shown as context (a session happened), never
//    as a reason the row matched.
//
// WHAT IS NOT IN HERE, deliberately: no "why this matched" sentence written
// by a model. Nothing on a rediscovery result is model output — `note` on the
// similarity leg is one fixed sentence the server always sends, not a
// generated one.

import type { Citation } from './agent';
import { apiGet, apiPost, apiPut, ApiError } from './client';
import { pathId } from './pathId';

// ---------------------------------------------------------------------------
// Shared vocabulary
// ---------------------------------------------------------------------------

export type RediscoveryFreshness = 'fresh' | 'ageing' | 'stale' | 'unverifiable';
/** The row header's own band also allows 'none' — no evidence at all. */
export type RediscoveryHeaderFreshness = RediscoveryFreshness | 'none';

export type RediscoverySignal =
  | 'resume_similarity'
  | 'resume_terms'
  | 'interviewer_scorecard'
  | 'round_result'
  | 'exam_attempt'
  | 'ai_interview';

/**
 * Where a `why` item's claim can be checked. Reuses `Citation['kind']` from
 * `api/agent.ts` rather than declaring a new vocabulary — design §5.3.4: every
 * citation this feature emits (`applicant`, `interviewer_scorecard`,
 * `exam_attempt`) is already one of the twelve closed members there, and this
 * build must not add a thirteenth.
 *
 * `href` is present on a LIVE result's citation and absent on a FROZEN one
 * (a pool member's `match_reason` snapshot, design §6.4: the snapshot must
 * not become a second copy of candidate prose, so it names the record without
 * a link to it). Render a citation with no `href` as plain text, never a
 * disabled-looking link.
 */
export interface RediscoveryCitation {
  kind: Citation['kind'];
  id: string;
  label: string;
  href?: string;
}

/**
 * One contribution to one match. A single flat shape for every signal
 * (matching `schemas/rediscovery.py::WhyItem` exactly) rather than a
 * discriminated union: the server already made that call — "most fields
 * optional" — precisely so a scorecard item does not have to carry six empty
 * exam fields over the wire, and a renderer switches on `signal` either way.
 */
export interface RediscoveryWhyItem {
  signal: RediscoverySignal;
  /** Percentage points of the final 0-100 score. Always 0 for `ai_interview`. */
  contribution: number;
  /** False for exactly one signal: `resume_similarity`. */
  explainable: boolean;
  locator?: string;
  recorded_at?: string;
  freshness?: RediscoveryFreshness;
  /** Why there is no band — an undated item, or content that no longer exists. */
  freshness_reason?: string;
  /** 'live' | 'purged' | 'redacted' | 'superseded', server-defined. */
  lifecycle?: string;
  /** 'human' | 'candidate' | 'ai'. */
  produced_by?: string;
  /** The fixed sentence for the similarity leg. Never model output. */
  note?: string;
  terms_matched?: string[];
  /** `[[…]]` bracket markers — see `parseBracketHighlights`. */
  snippet?: string;
  competency_id?: string;
  /** The human-readable competency name — prefer this over `competency_id`
   *  for display, falling back to the id when a snapshot predates it. */
  competency?: string;
  competency_ids?: string[];
  score?: number;
  of?: number;
  percent?: number;
  passed?: boolean;
  round_title?: string;
  round_kind?: string;
  content_hidden_reason?: string;
  citation?: RediscoveryCitation;
}

export interface RediscoveryEligibility {
  opted_in_at?: string;
  expires_at?: string;
}

export interface RediscoveryWeights {
  semantic: number;
  lexical: number;
  evidence_max: number;
}

export interface RediscoveryBreakdown {
  semantic: number;
  lexical: number;
  evidence_boost: number;
  coverage: number;
  freshness_factor: number;
  covered_competencies: string[];
  semantic_available: boolean;
  weights: RediscoveryWeights;
}

export interface RediscoveryResult {
  applicant_id: string;
  full_name: string;
  current_title?: string;
  current_company?: string;
  years_experience?: number;
  score: number;
  /**
   * False when the only contribution is CV similarity. That row renders under
   * its own "Matched on similarity only" heading, sorts below explained rows
   * at equal score, and cannot be invited without the acknowledgement gate.
   */
  explained: boolean;
  breakdown: RediscoveryBreakdown;
  why: RediscoveryWhyItem[];
  /** The WORST band among contributing items — never the best, never a mean. */
  evidence_freshness: RediscoveryHeaderFreshness;
  /** Computed server-side from the bands and the explanation, never claimed. */
  requires_review: boolean;
  review_reasons: string[];
  unexplained_note?: string;
  eligibility: RediscoveryEligibility;
}

export interface RediscoveryUniverse {
  eligible: number;
  total: number;
}

export interface RediscoveryTarget {
  requisition_id: string;
  title: string;
  competency_ids: string[];
  competencies: number;
  source: 'frozen_round_criteria' | 'none';
  reason?: string;
}

export interface RediscoverySearchResponse {
  /** False when the embedder was unreachable — results came from full text
   *  alone, and the screen must say so (design §10.3's third line). */
  semantic: boolean;
  universe: RediscoveryUniverse;
  matched: number;
  returned: number;
  weights: RediscoveryWeights;
  target?: RediscoveryTarget;
  results: RediscoveryResult[];
}

export interface RediscoveryUniverseResponse {
  universe: RediscoveryUniverse;
  /** The opt-in window in months, so the screen's copy and the query cannot
   *  disagree — always read from here, never hardcoded as "12". */
  consent_months: number;
}

/**
 * The frozen "why matched" snapshot stored on a pool member added from a
 * search (`talent_pool_members.match_reason`). NOT the live shape above:
 * prose is stripped (`why[].snippet`/`.note` are always absent) and every
 * citation in it carries no `href` — see `RediscoveryCitation`'s own note.
 *
 * `frozen_at` is the moment to show ("Added from a rediscovery search on 24
 * Sep 2026") — never re-derive a date from anything inside `why`.
 */
export interface RediscoveryMatchReasonSnapshot {
  frozen_at: string;
  score: number;
  explained: boolean;
  breakdown: RediscoveryBreakdown;
  evidence_freshness: RediscoveryHeaderFreshness;
  requires_review: boolean;
  review_reasons: string[];
  why: RediscoveryWhyItem[];
}

// ---------------------------------------------------------------------------
// HR routes — services/data_gateway/app/routers/hr_rediscovery.py,
// `hr_manager` ONLY (a company super_admin gets 403: a result names a
// candidate, and `candidate_pii` is `{hr_manager}` alone — CLAUDE.md).
// ---------------------------------------------------------------------------

export interface RediscoverySearchInput {
  query: string;
  /** The opening HR is searching for — supplies the competency targets and
   *  is the only thing that turns the evidence boost on. */
  requisitionId?: string | null;
  /** 1-50, default 20 server-side. */
  limit?: number;
}

/**
 * `POST /hr/rediscovery/search`. A POST, not a GET, on purpose: this box is a
 * place HR may type a candidate's name, and a query string in a URL is
 * recorded by every proxy and access log in the path — the audit row this
 * writes deliberately carries only the query's LENGTH, never the text.
 *
 * 404 (never 403) when `requisitionId` is not this company's opening.
 */
export function searchRediscovery(input: RediscoverySearchInput): Promise<RediscoverySearchResponse> {
  return apiPost<RediscoverySearchResponse>('/hr/rediscovery/search', {
    query: input.query,
    requisition_id: input.requisitionId ?? null,
    ...(input.limit !== undefined ? { limit: input.limit } : {}),
  });
}

/**
 * `GET /hr/rediscovery/universe` — how many of this company's applicants a
 * search can even see. Feeds the empty state and the always-on "Searching N
 * of M applicants" counter; both are load-bearing on day one, when the
 * honest answer is usually zero.
 */
export function getRediscoveryUniverse(): Promise<RediscoveryUniverseResponse> {
  return apiGet<RediscoveryUniverseResponse>('/hr/rediscovery/universe');
}

// ---------------------------------------------------------------------------
// Candidate doors — the existing `/users/me` router
// (`candidate_applications.py`, already gates `guest_candidate`). Verified
// against the shipped route: `GET .../rediscovery` and `PUT
// .../rediscovery/{company_id}` are NOT served with
// `response_model_exclude_none`, so an absent value arrives as an explicit
// `null` rather than a missing key — the optional typing below and the
// truthy/presence checks this screen uses read either shape identically, so
// nothing had to change once that became knowable.
// ---------------------------------------------------------------------------

export type MyRediscoveryState = 'on' | 'off';

/** One company the candidate has applied to, whether or not they opted in. */
export interface MyRediscoveryCompany {
  company_id: string;
  company_name: string;
  applicant_id: string;
  state: MyRediscoveryState;
  opted_in_at?: string;
  expires_at?: string;
  withdrawn_at?: string;
}

export interface MyRediscoveryResponse {
  companies: MyRediscoveryCompany[];
  consent_months: number;
}

/** `GET /users/me/rediscovery` — one row per company applied to. */
export function listMyRediscovery(): Promise<MyRediscoveryResponse> {
  return apiGet<MyRediscoveryResponse>('/users/me/rediscovery');
}

/**
 * `PUT /users/me/rediscovery/{company_id}` — `{on: true}` opts in
 * (`record_opt_in(source="my_applications")`); `{on: false}` withdraws
 * (`revoke_opt_ins(company_id=…)`). Always succeeds either way for the
 * signed-in owner — the "not re-granted after withdrawal" refusal is a
 * public-apply-form-only rule (contract §3) and never reaches this call.
 */
export function setMyRediscovery(companyId: string, on: boolean): Promise<MyRediscoveryCompany> {
  return apiPut<MyRediscoveryCompany>(`/users/me/rediscovery/${pathId(companyId)}`, { on });
}

// ---------------------------------------------------------------------------
// Errors — the `CorpusError`/`RediscoveryError` shape used across this
// service: `{"detail": {"failure_code": "…", "message": "…"}}`. Same reading
// as `api/corpus.ts::corpusErrorMessage` — deliberately not a second
// implementation of "read a structured detail off an ApiError".
// ---------------------------------------------------------------------------

export const REDISCOVERY_FAILURE_SENTENCES: Record<string, string> = {
  bad_requisition_id: 'That opening id is not valid.',
  stale_evidence_unreviewed: 'This evidence has not been reviewed recently.',
  consent_not_active: "This person's rediscovery consent is no longer active.",
  requisition_not_open: 'That opening is not open for new applications.',
};

export function rediscoveryErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    const detail = error.detail as { failure_code?: string; message?: string } | undefined;
    const code = detail?.failure_code;
    if (code && REDISCOVERY_FAILURE_SENTENCES[code]) return REDISCOVERY_FAILURE_SENTENCES[code];
    return error.message || fallback;
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

// ---------------------------------------------------------------------------
// `[[…]]` bracket highlighting — the ONLY sanctioned way to render
// `resume_terms.snippet`. Never `dangerouslySetInnerHTML`: this is
// candidate-authored CV text, and the server sends bracket markers rather
// than HTML specifically so nothing here has to trust a tag.
// ---------------------------------------------------------------------------

export interface HighlightSegment {
  text: string;
  highlighted: boolean;
}

export function parseBracketHighlights(raw: string): HighlightSegment[] {
  const segments: HighlightSegment[] = [];
  const re = /\[\[([^\]]*)\]\]/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(raw)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ text: raw.slice(lastIndex, match.index), highlighted: false });
    }
    segments.push({ text: match[1], highlighted: true });
    lastIndex = re.lastIndex;
  }
  if (lastIndex < raw.length) {
    segments.push({ text: raw.slice(lastIndex), highlighted: false });
  }
  return segments;
}
