// requisitions.ts — job requisitions and the enrolments inside them (Group B).
//
// A requisition is the first-class "opening" this product did not have before
// Phase 2: applicants used to reference a role by free-text title, so nothing
// could be counted, configured or reported per job. Everything on the workflow
// side hangs off one of these.
//
// Field names match the data_gateway contract in
// services/data_gateway/app/routers/hr_requisitions.py EXACTLY.

import { apiGet, apiPost, apiPatch, apiPut, apiDelete } from './client';

/** Lifecycle of an opening. Closing one does NOT reject the people in it. */
export type RequisitionStatus = 'open' | 'paused' | 'closed';

/**
 * Every status an enrolment may hold.
 *
 * `held` is written only by the workflow runner, when a candidate lands inside
 * the hold band under a round's pass threshold. No HR action produces it and
 * no automation clears it — releasing a hold is a human decision (D-05).
 */
export type EnrolmentStatus = 'new' | 'shortlisted' | 'interviewed' | 'held' | 'hired' | 'rejected';

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
export type EmploymentType = 'full_time' | 'part_time' | 'contract' | 'internship' | 'temporary';

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
  /** The owner's name, for display. */
  owner_name?: string | null;
  /** True when the Group B backfill inferred this opening rather than HR creating it. */
  from_backfill: boolean;
  /** Whether the open web may apply. Off until HR turns it on per opening. */
  public_apply_enabled: boolean;
  created_at: string;
  total_enrolments: number;
  hired: number;
  /**
   * People waiting on a person right now: held below a threshold, finished
   * every round, or on a human-review round. Not new or mid-round candidates.
   */
  awaiting_decision: number;
  /** Everyone without a final decision — what closing the opening must account for. */
  unresolved?: number;
  funnel: FunnelStage[];
  /**
   * Projected hiring throughput against the closing date (E3).
   *
   * Null means the projection cannot honestly be made — no closing date, no
   * target, or the opening is too new to have a rate — and MUST render as
   * nothing rather than as "on track".
   */
  delivery_risk?: 'on_track' | 'at_risk' | 'off_track' | null;
  // ── Approval (PH3-B2) ────────────────────────────────────────────────
  // A separate axis from `status`: approved does not imply open, and closing
  // an opening does not un-approve it. Openings that predate the approval gate
  // were grandfathered as approved, so this is never empty on an old row.
  approval_status: ApprovalStatus;
  submitted_for_approval_at?: string | null;
  submitted_by_name?: string | null;
  approval_decided_at?: string | null;
  approval_decided_by_name?: string | null;
  approval_note?: string | null;
  // ── Budget (PH3-B2) ──────────────────────────────────────────────────
  // HR-facing only. Never rendered on a careers page or an application form —
  // there is no visibility flag because there is no case for showing it.
  budget_amount?: number | null;
  budget_currency?: string | null;
  budget_basis?: BudgetBasis | null;
  budget_period?: BudgetPeriod | null;
  budget_notes?: string | null;
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
  /** Who is responsible for filling it. Must be an active HR user at this
   *  company; defaults to whoever creates it. Null clears it (update only). */
  owner_user_id?: string | null;
  public_apply_enabled?: boolean;
  // PH3-B2. An amount needs all three of the others or the server refuses it
  // with a 422 — an amount alone is a number nobody can act on.
  budget_amount?: number | null;
  budget_currency?: string | null;
  budget_basis?: BudgetBasis | null;
  budget_period?: BudgetPeriod | null;
  budget_notes?: string | null;
}

/** An HR user at this company — who an opening can be assigned to. */
export interface TeamMember {
  id: string;
  full_name: string | null;
  email: string;
}

export function listTeam(): Promise<TeamMember[]> {
  return apiGet<TeamMember[]>('/hr/team');
}

/** A date input's `YYYY-MM-DD` as the END of that day, local time, in ISO — an
 *  opening that "closes on the 30th" is still open during the 30th. */
export function closingDateToIso(day: string): string | null {
  return day ? new Date(`${day}T23:59:59`).toISOString() : null;
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
  body: Partial<RequisitionInput>,
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
    return await apiPost<Requisition & { unresolved?: number }>(`/hr/requisitions/${id}/status`, {
      status,
      ...(opts.reason ? { reason: opts.reason } : {}),
      ...(opts.acknowledgeUnresolved ? { acknowledge_unresolved: true } : {}),
    });
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

/**
 * Move one candidate. Recorded against the signed-in human, never automated.
 *
 * `reasonCode` (O4) is required by the server when `status` is 'hired' or
 * 'rejected' — no caller in this codebase currently targets either status
 * through this endpoint (both terminal decisions go through
 * recordFinalDecision / setApplicantDecision instead), so this is wired for
 * contract completeness rather than exercised today.
 */
export function setEnrolmentStatus(
  enrolmentId: string,
  status: EnrolmentStatus,
  reason?: string,
  reasonCode?: string,
): Promise<Enrolment> {
  return apiPost<Enrolment>(`/hr/enrolments/${enrolmentId}/status`, {
    status,
    reason: reason?.trim() || null,
    ...(reasonCode ? { reason_code: reasonCode } : {}),
  });
}

// ── Backfill review + duplicate applicants ──────────────────────────────────

/** One requisition the backfill minted by grouping applicants on a normalised title. */
/** One move in an application's history, from the transition ledger (B2). */
export interface StageHistoryEntry {
  /** The `stage_transitions` row's own id (PH5-E5). For a real (non-automated)
   *  hire or reject row, this is exactly the `decision_id`
   *  `GET /hr/decisions/{decision_id}/trace` takes — the row id for any other
   *  move is a stable per-row key but is not itself traceable. */
  id: number;
  occurred_at: string;
  from_status: string | null;
  to_status: string;
  /** Round titles, when the move was between rounds. */
  from_round: string | null;
  to_round: string | null;
  /** True when the system made the move; `actor` is then null. */
  automated: boolean;
  actor: string | null;
  reason: string | null;
  /** O4 — set only on a hire/reject move made under the structured taxonomy.
   *  Historical rows predate it and carry null; render fine without it. */
  reason_code: string | null;
  /** Display label for reason_code at the time it was recorded — retiring the
   *  reason later does not change what a past decision shows here. */
  reason_label: string | null;
}

export function getEnrolmentHistory(enrolmentId: string): Promise<StageHistoryEntry[]> {
  return apiGet<StageHistoryEntry[]>(`/hr/enrolments/${enrolmentId}/history`);
}

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
  /** Applicants filed under no opening at all (e.g. no job title on record). */
  unfiled_applicants?: number;
}

/** An inferred opening is right as it is: take it out of the review queue. */
export function confirmRequisition(requisitionId: string): Promise<Requisition> {
  return apiPost<Requisition>(`/hr/requisitions/${requisitionId}/confirm`, {});
}

export interface MergeRequisitionResult {
  into_requisition_id: string;
  title: string;
  moved: number;
}

/**
 * Fold one opening into another that is really the same job. Every candidate
 * moves; the source is closed and retired. Refused (409, with the reason) when
 * the source has its own workflow or someone applied to both.
 */
export function mergeRequisition(
  requisitionId: string,
  intoRequisitionId: string,
): Promise<MergeRequisitionResult> {
  return apiPost<MergeRequisitionResult>(`/hr/requisitions/${requisitionId}/merge`, {
    into_requisition_id: intoRequisitionId,
  });
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
  // enrolment_ids names the candidates to move; source_titles picks them by
  // the spelling they applied under. One of the two.
  body: {
    enrolment_ids?: string[];
    source_titles?: string[];
    new_title: string;
    level?: string | null;
  },
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

export interface DashboardProgress {
  applications: number;
  /** Inside an automated round right now. */
  in_progress: number;
  awaiting_decision: number;
  held: number;
  hired: number;
  rejected: number;
  not_started: number;
  /** Still finishing an earlier workflow version. */
  on_older_version: number;
  target_hires: number | null;
}

export interface StageTiming {
  key: string;
  label: string;
  /** Median days from application; null when nobody has reached it yet. */
  median_days: number | null;
  count: number;
}

export interface DashboardScores {
  avg_ats: number | null;
  scored_applications: number;
  /** Mean of each assessed candidate's scored rounds. A summary, never a decision. */
  avg_composite: number | null;
  assessed_candidates: number;
}

export interface HeldCandidate {
  enrolment_id: string;
  applicant_id: string;
  full_name: string;
  held_reason: string | null;
  round_title: string | null;
  ats_overall: number | null;
  held_days: number | null;
}

export type AttentionSeverity = 'critical' | 'warning' | 'info';

export interface DashboardAttention {
  key: string;
  severity: AttentionSeverity;
  title: string;
  body: string;
  link: string | null;
}

export interface ManualStep {
  key: string;
  count: number;
  label: string;
  link: string | null;
}

export interface ActivityEntry {
  occurred_at: string;
  automated: boolean;
  /** Named only when a person made the move. */
  actor: string | null;
  candidate: string | null;
  enrolment_id: string;
  from_status: string | null;
  to_status: string;
  from_round: string | null;
  to_round: string | null;
  reason: string | null;
}

export interface RequisitionDashboard {
  requisition: Requisition;
  rounds: RoundProgress[];
  has_published_workflow: boolean;
  /** Median, so one candidate parked for months does not distort it. */
  median_days_in_stage: number | null;
  /** Resumes stored but not yet scored — an empty ATS column, explained. */
  still_being_read: number;
  progress: DashboardProgress;
  workflow_state: { published_version: number | null; draft_version: number | null };
  stage_timing: StageTiming[];
  scores: DashboardScores;
  held_pool: HeldCandidate[];
  attention: DashboardAttention[];
  manual_steps: ManualStep[];
  activity: ActivityEntry[];
  activity_summary: {
    automated_7d: number;
    manual_7d: number;
    last_automated_at: string | null;
  };
}

export function getRequisitionDashboard(id: string): Promise<RequisitionDashboard> {
  return apiGet<RequisitionDashboard>(`/hr/requisitions/${id}/dashboard`);
}

// ---------------------------------------------------------------------------
// Requisition approval — PH3-B2
//
// Two audiences. HR submits; the company's super admin decides. The split is
// enforced on the server by the dependency each route declares, so these
// functions are convenience rather than control — a console that called the
// wrong one would get a 403, not a surprise.
// ---------------------------------------------------------------------------
export type ApprovalStatus = 'draft' | 'pending_approval' | 'approved' | 'rejected';

/** One row of the super admin's approval queue. */
export interface PendingApproval {
  id: string;
  title: string;
  level: string;
  department: string | null;
  location: string | null;
  target_hires: number | null;
  budget_amount: number | null;
  budget_currency: string | null;
  budget_basis: BudgetBasis | null;
  budget_period: BudgetPeriod | null;
  budget_notes: string | null;
  submitted_at: string | null;
  submitted_by_name: string | null;
  note: string | null;
}

export type BudgetBasis = 'per_hire' | 'total';
export type BudgetPeriod = 'annual' | 'monthly' | 'one_time';

export function submitRequisitionForApproval(id: string, note?: string): Promise<Requisition> {
  return apiPost<Requisition>(`/hr/requisitions/${id}/approval/submit`, { note: note ?? null });
}

export function listPendingApprovals(): Promise<PendingApproval[]> {
  return apiGet<PendingApproval[]>('/hr/requisitions/approvals/pending');
}

export function approveRequisition(id: string, note?: string): Promise<Requisition> {
  return apiPost<Requisition>(`/hr/requisitions/${id}/approval/approve`, {
    note: note ?? null,
  });
}

export function rejectRequisition(id: string, note?: string): Promise<Requisition> {
  return apiPost<Requisition>(`/hr/requisitions/${id}/approval/reject`, {
    note: note ?? null,
  });
}

// ---------------------------------------------------------------------------
// JD versions — PH3-B3 / PH3-B6
//
// The requisition's own jd_text stays the LIVE advert; these are its history
// and its drafting surface. A draft is invisible to candidates precisely
// because it does not touch the requisition.
// ---------------------------------------------------------------------------
export interface JdVersion {
  id: string;
  version: number;
  status: 'draft' | 'published' | 'archived';
  jd_text: string | null;
  responsibilities: string[];
  required_skills: string[];
  nice_to_have_skills: string[];
  change_note: string | null;
  created_by_user_id: string | null;
  created_by_name: string | null;
  created_at: string;
  /** When this version went live. Null for a draft. */
  published_at: string | null;
  /** When it stopped being live. With published_at this bounds the window in
   *  which this wording was the one candidates saw. */
  superseded_at: string | null;
}

export interface JdHistory {
  requisition_id: string;
  published_version_id: string | null;
  versions: JdVersion[];
}

export interface JdDraftInput {
  jd_text?: string | null;
  responsibilities?: string[];
  required_skills?: string[];
  nice_to_have_skills?: string[];
  change_note?: string | null;
}

export function getJdHistory(requisitionId: string): Promise<JdHistory> {
  return apiGet<JdHistory>(`/hr/requisitions/${requisitionId}/jd/versions`);
}

export function getJdDraft(requisitionId: string): Promise<JdVersion | null> {
  return apiGet<JdVersion | null>(`/hr/requisitions/${requisitionId}/jd/draft`);
}

export function saveJdDraft(requisitionId: string, input: JdDraftInput): Promise<JdVersion> {
  return apiPut<JdVersion>(`/hr/requisitions/${requisitionId}/jd/draft`, input);
}

export function discardJdDraft(requisitionId: string): Promise<void> {
  return apiDelete<void>(`/hr/requisitions/${requisitionId}/jd/draft`);
}

export function publishJdVersion(requisitionId: string, versionId: string): Promise<JdVersion> {
  return apiPost<JdVersion>(
    `/hr/requisitions/${requisitionId}/jd/versions/${versionId}/publish`,
    {},
  );
}

// ---------------------------------------------------------------------------
// Scheduled publishing — PH3-B4a
// ---------------------------------------------------------------------------
export interface PublishSchedule {
  requisition_id: string;
  /** When it is asked to go live. Null when nothing is scheduled. */
  publish_at: string | null;
  /** When it actually went live automatically. Null if it never did. */
  published_at: string | null;
  public_apply_enabled: boolean;
  /**
   * The server's own sentence about how precise the timing is. RENDER IT next
   * to any scheduled time: the publisher is an interval loop, so "09:00" is
   * approximately rather than exactly when this happens, and a UI that shows
   * only the time is making a promise the architecture does not offer.
   */
  tolerance: string;
}

export function getPublishSchedule(requisitionId: string): Promise<PublishSchedule> {
  return apiGet<PublishSchedule>(`/hr/requisitions/${requisitionId}/publish-schedule`);
}

export function setPublishSchedule(
  requisitionId: string,
  publishAt: string,
): Promise<PublishSchedule> {
  return apiPut<PublishSchedule>(`/hr/requisitions/${requisitionId}/publish-schedule`, {
    publish_at: publishAt,
  });
}

export function cancelPublishSchedule(requisitionId: string): Promise<PublishSchedule> {
  return apiDelete<PublishSchedule>(`/hr/requisitions/${requisitionId}/publish-schedule`);
}
