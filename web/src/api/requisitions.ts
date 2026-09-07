// requisitions.ts — job requisitions and the enrolments inside them (Group B).
//
// A requisition is the first-class "opening" this product did not have before
// Phase 2: applicants used to reference a role by free-text title, so nothing
// could be counted, configured or reported per job. Everything on the workflow
// side hangs off one of these.
//
// Field names match the data_gateway contract in
// services/data_gateway/app/routers/hr_requisitions.py EXACTLY.

import { apiGet, apiPost, apiPatch } from './client';

/** Lifecycle of an opening. Closing one does NOT reject the people in it. */
export type RequisitionStatus = 'open' | 'paused' | 'closed';

/**
 * Every status an enrolment may hold.
 *
 * `held` is written only by the workflow runner, when a candidate lands inside
 * the hold band under a round's pass threshold. No HR action produces it and
 * no automation clears it — releasing a hold is a human decision (D-05).
 */
export type EnrolmentStatus =
  | 'new'
  | 'shortlisted'
  | 'interviewed'
  | 'held'
  | 'hired'
  | 'rejected';

/** Statuses that end a candidacy. Only a person may write these (D-05). */
export const TERMINAL_ENROLMENT_STATUSES: readonly EnrolmentStatus[] = ['hired', 'rejected'];

export interface FunnelStage {
  status: string;
  count: number;
}

/**
 * Employment types the board filters on. Mirrors the server's EMPLOYMENT_TYPES
 * and the database check constraint — a value outside this union is a 422, so
 * the dropdown and the API cannot drift apart silently.
 */
export type EmploymentType =
  | 'full_time'
  | 'part_time'
  | 'contract'
  | 'internship'
  | 'temporary';

export const EMPLOYMENT_TYPE_LABELS: Record<EmploymentType, string> = {
  full_time: 'Full-time',
  part_time: 'Part-time',
  contract: 'Contract',
  internship: 'Internship',
  temporary: 'Temporary',
};

/**
 * The advert half of an opening: what a candidate needs in order to decide
 * whether the role is worth opening.
 *
 * Everything is optional. These arrived after thousands of openings already
 * existed — including backfilled ones created from nothing but a job title —
 * so a board has to be able to render "not specified" rather than assume.
 */
export interface PostingFields {
  department: string | null;
  location: string | null;
  employment_type: EmploymentType | null;
  experience_min_years: number | null;
  experience_max_years: number | null;
  /**
   * Present on a read only when the opening publishes its salary. The server
   * omits the numbers entirely when `salary_visible` is off — a band recorded
   * for internal planning is not an advert.
   */
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
  salary_visible: boolean;
  responsibilities: string[];
  required_skills: string[];
  nice_to_have_skills: string[];
}

export interface Requisition extends PostingFields {
  id: string;
  title: string;
  level: string;
  status: RequisitionStatus;
  jd_text: string | null;
  target_hires: number | null;
  closes_at: string | null;
  owner_user_id: string | null;
  /** True when the Group B backfill inferred this opening rather than HR creating it. */
  from_backfill: boolean;
  /** Whether the open web may apply. Off until HR turns it on per opening. */
  public_apply_enabled: boolean;
  created_at: string;
  total_enrolments: number;
  hired: number;
  /** People sitting on a completed workflow or on hold, waiting for a human. */
  awaiting_decision: number;
  funnel: FunnelStage[];
  /**
   * Projected hiring throughput against the closing date (E3).
   *
   * Null means the projection cannot honestly be made — no closing date, no
   * target, or the opening is too new to have a rate — and MUST render as
   * nothing rather than as "on track".
   */
  delivery_risk?: 'on_track' | 'at_risk' | 'off_track' | null;
}

export interface Enrolment {
  id: string;
  applicant_id: string;
  full_name: string;
  email: string | null;
  status: EnrolmentStatus;
  target_job_title: string;
  ats_overall: number | null;
  ats_recommendation: string | null;
  days_in_stage: number | null;
  created_at: string;
}

export interface RequisitionInput extends Partial<PostingFields> {
  title: string;
  level?: string;
  jd_text?: string | null;
  target_hires?: number | null;
  closes_at?: string | null;
  public_apply_enabled?: boolean;
}

/**
 * A field the server refuses. Ranges are checked in two places — pydantic for
 * the message, a database constraint for everything that is not this API — so
 * an inverted range comes back as a 422 naming the field rather than a 500.
 */
export function rangeError(
  min: number | null | undefined,
  max: number | null | undefined,
  label: string,
): string | null {
  if (min == null || max == null || min <= max) return null;
  return `Minimum ${label} cannot be more than the maximum.`;
}

// ── Requisitions ────────────────────────────────────────────────────────────

export function listRequisitions(
  opts: { status?: RequisitionStatus; limit?: number; offset?: number } = {},
): Promise<Requisition[]> {
  const p = new URLSearchParams();
  if (opts.status) p.set('status', opts.status);
  if (opts.limit !== undefined) p.set('limit', String(opts.limit));
  if (opts.offset !== undefined) p.set('offset', String(opts.offset));
  const q = p.toString();
  return apiGet<Requisition[]>(`/hr/requisitions${q ? `?${q}` : ''}`);
}

export function getRequisition(id: string): Promise<Requisition> {
  return apiGet<Requisition>(`/hr/requisitions/${id}`);
}

export function createRequisition(body: RequisitionInput): Promise<Requisition> {
  return apiPost<Requisition>('/hr/requisitions', body);
}

export function updateRequisition(
  id: string,
  body: Partial<RequisitionInput> & { owner_user_id?: string },
): Promise<Requisition> {
  return apiPatch<Requisition>(`/hr/requisitions/${id}`, body);
}

/**
 * Raised when closing an opening that still has candidates mid-process.
 *
 * Not an error in the usual sense — it is the AC-12 prompt. The caller is
 * expected to show `unresolved` to the HR manager, offer the decision queue,
 * and only then retry with `acknowledgeUnresolved`.
 */
export class UnresolvedCandidatesError extends Error {
  readonly unresolved: number;
  constructor(unresolved: number, message: string) {
    super(message);
    this.name = 'UnresolvedCandidatesError';
    this.unresolved = unresolved;
  }
}

/**
 * Open, pause or close an opening.
 *
 * Closing is REFUSED with 409 while candidates are still mid-process (AC-12);
 * that surfaces here as {@link UnresolvedCandidatesError} carrying the count.
 * Pass `acknowledgeUnresolved` to close anyway. Nobody is rejected either way —
 * held and in-flight candidates stay in the decision queue.
 */
export async function setRequisitionStatus(
  id: string,
  status: RequisitionStatus,
  opts: { reason?: string; acknowledgeUnresolved?: boolean } = {},
): Promise<Requisition & { unresolved?: number }> {
  try {
    return await apiPost<Requisition & { unresolved?: number }>(
      `/hr/requisitions/${id}/status`,
      {
        status,
        ...(opts.reason ? { reason: opts.reason } : {}),
        ...(opts.acknowledgeUnresolved ? { acknowledge_unresolved: true } : {}),
      },
    );
  } catch (err) {
    const detail = (err as { status?: number; detail?: unknown })?.detail;
    if (
      (err as { status?: number })?.status === 409 &&
      detail &&
      typeof detail === 'object' &&
      (detail as { error?: string }).error === 'unresolved_candidates'
    ) {
      const d = detail as { unresolved?: number; message?: string };
      throw new UnresolvedCandidatesError(
        d.unresolved ?? 0,
        d.message ?? 'Candidates are still in this opening.',
      );
    }
    throw err;
  }
}

// ── Enrolments ──────────────────────────────────────────────────────────────

export function listEnrolments(
  requisitionId: string,
  opts: { status?: EnrolmentStatus; limit?: number; offset?: number } = {},
): Promise<Enrolment[]> {
  const p = new URLSearchParams();
  if (opts.status) p.set('status', opts.status);
  if (opts.limit !== undefined) p.set('limit', String(opts.limit));
  if (opts.offset !== undefined) p.set('offset', String(opts.offset));
  const q = p.toString();
  return apiGet<Enrolment[]>(`/hr/requisitions/${requisitionId}/enrolments${q ? `?${q}` : ''}`);
}

/** Move one candidate. Recorded against the signed-in human, never automated. */
export function setEnrolmentStatus(
  enrolmentId: string,
  status: EnrolmentStatus,
  reason?: string,
): Promise<Enrolment> {
  return apiPost<Enrolment>(`/hr/enrolments/${enrolmentId}/status`, {
    status,
    reason: reason?.trim() || null,
  });
}

// ── Backfill review + duplicate applicants ──────────────────────────────────

/** One requisition the backfill minted by grouping applicants on a normalised title. */
export interface BackfilledRequisition {
  id: string;
  title: string;
  status: RequisitionStatus;
  enrolments: number;
  /**
   * How many distinct spellings were folded into this opening. >1 means the
   * backfill made a judgement call — usually right, occasionally two real jobs.
   */
  distinct_source_titles: number;
  source_titles: string[];
}

/** Several applicant rows at one company sharing an email — probably one person. */
export interface MergeCandidate {
  email: string;
  applicant_ids: string[];
  names: string[];
  enrolment_count: number;
  /** Any exam attempt, assignment or interview invite exists on these rows. */
  has_history: boolean;
}

export interface BackfillReview {
  backfilled_requisitions: BackfilledRequisition[];
  merge_candidates: MergeCandidate[];
}

export function getBackfillReview(): Promise<BackfillReview> {
  return apiGet<BackfillReview>('/hr/requisitions/review');
}

export interface MergeResult {
  survivor_id: string;
  absorbed: number;
  /** Rows repointed onto the survivor, by table. */
  moved: Record<string, number>;
}

/**
 * Fold duplicate applicant rows into one person.
 *
 * IRREVERSIBLE — it repoints exam and interview history onto the survivor and
 * soft-deletes the rest. The server refuses (409) when two of the rows are
 * enrolled in the same opening, because picking which application survives is
 * a judgement about someone's candidacy, not a data-cleaning step.
 */
export function mergeApplicants(survivorId: string, absorbedIds: string[]): Promise<MergeResult> {
  return apiPost<MergeResult>('/hr/applicants/merge', {
    survivor_id: survivorId,
    absorbed_ids: absorbedIds,
  });
}

export interface SplitResult {
  requisition_id: string;
  title: string;
  moved: number;
  left_behind: number;
}

/**
 * Take a mis-grouped opening apart: move the candidates who applied under
 * `sourceTitles` into a new requisition of their own.
 *
 * Merge fixes duplicate *people*; this fixes duplicate *openings*. Nothing is
 * deleted and candidates keep every assessment they have — but the server
 * refuses (409) when anyone being moved is already part-way through the old
 * opening's workflow, because re-filing them would silently change what they
 * are being assessed against.
 */
export function splitRequisition(
  requisitionId: string,
  body: { source_titles: string[]; new_title: string; level?: string | null },
): Promise<SplitResult> {
  return apiPost<SplitResult>(`/hr/requisitions/${requisitionId}/split`, body);
}

// ── Per-opening dashboard (E1) ──────────────────────────────────────────────

export interface RoundProgress {
  round_id: string;
  position: number;
  title: string;
  kind: string;
  /** Candidates sitting at this round right now. */
  at_this_round: number;
  attempted: number;
  passed: number;
  /**
   * null when nobody has sat it yet. Deliberately not 0: "0% pass rate" and
   * "nobody has taken it" render identically on a bar and mean opposite things.
   */
  pass_rate: number | null;
}

export interface RequisitionDashboard {
  requisition: Requisition;
  rounds: RoundProgress[];
  has_published_workflow: boolean;
  /** Median, so one candidate parked for months does not distort it. */
  median_days_in_stage: number | null;
  /** Resumes stored but not yet scored — an empty ATS column, explained. */
  still_being_read: number;
}

export function getRequisitionDashboard(id: string): Promise<RequisitionDashboard> {
  return apiGet<RequisitionDashboard>(`/hr/requisitions/${id}/dashboard`);
}
