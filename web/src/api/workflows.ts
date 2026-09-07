// workflows.ts — the Role-Specific Hiring Workflow Engine's authoring API.
//
// Every opening can carry its own hiring workflow: an ordered chain of rounds,
// each with a pass threshold and a frozen rubric saying what it assesses. HR
// builds it on a canvas; the runner then moves candidates through it without
// anyone chasing them.
//
// Two invariants worth keeping in mind while reading the types below, because
// both are enforced server-side and neither is negotiable from here:
//
//   • A published workflow is IMMUTABLE. Editing means cloning to version n+1;
//     everyone mid-process keeps the rounds, thresholds and rubric they
//     started under. `editable` on Workflow is the single flag the UI reads.
//
//   • NOTHING here can reject a candidate (D-05). There is no auto-reject
//     setting because the field does not exist — a below-threshold result puts
//     someone on `held` for a human to look at, and the settings allow-list
//     lives in the service layer, so no request body can introduce one.
//
// Field names match services/data_gateway/app/routers/hr_workflows.py EXACTLY.

import { apiGet, apiPost, apiPatch, apiPut, apiDelete } from './client';

/** The four things a round can be. `human_review` is a deliberate pause. */
export type RoundKind = 'mcq' | 'coding' | 'ai_interview' | 'human_review';

/** Rounds whose questions come from an exam — those need an exam attached. */
export const EXAM_BACKED_KINDS: readonly RoundKind[] = ['mcq', 'coding'];

export const MAX_ROUNDS = 12;

/** A round assesses at most this many competencies — mirrors the server cap. */
export const MAX_CRITERIA_PER_ROUND = 8;

export type WorkflowStatus = 'draft' | 'published' | 'archived';

/**
 * One competency a round assesses, frozen onto the round at publish time.
 *
 * `anchors` and `probes` travel with it on purpose: a rubric stored as bare
 * labels measures less precisely and cannot be reproduced months later, so the
 * builder freezes the whole thing, not just the names (C8).
 */
export interface Criterion {
  id: string;
  name: string;
  kind: string | null;
  /** 0–1. Renormalised against the round's own set at interview time. */
  weight: number;
  anchors: { low: string; mid: string; high: string } | null;
  probes: string[] | null;
}

export interface Round {
  id: string;
  position: number;
  title: string;
  kind: RoundKind;
  /**
   * ALWAYS a percentage, whatever the round kind. Interview composites are
   * converted at the edge (see workflow_runner.INTERVIEW_SCORE_MAX) — this
   * column carried two different units once, and a 7.5/10 read as 7.5% held a
   * candidate who had passed.
   */
  pass_threshold: number | null;
  time_limit_seconds: number | null;
  deadline_days: number;
  on_pass_next_round_id: string | null;
  exam_round_id: string | null;
  /** An exam-backed round with no exam attached yet — blocks publish. */
  needs_questions: boolean;
  criteria: Criterion[];
}

/** C9 — the automation switches. Note what is absent: anything that rejects. */
export interface WorkflowSettings {
  auto_score_on_apply: boolean;
  auto_assign_first_round: boolean;
  auto_advance_rounds: boolean;
  reminders_enabled: boolean;
  /** 0–10 ATS score at or above which an applicant is auto-shortlisted. */
  shortlist_ats_threshold: number | null;
  /** Percentage points below a threshold that still route to a human, not out. */
  hold_band: number | null;
}

export interface Workflow {
  id: string;
  requisition_id: string;
  version: number;
  status: WorkflowStatus;
  name: string | null;
  /** The only thing the UI should branch on to decide "can I change this?". */
  editable: boolean;
  role_profile_id: string | null;
  settings: WorkflowSettings;
  published_at: string | null;
  rounds: Round[];
}

/** Summary row for the version list beside the canvas. */
export interface WorkflowSummary {
  id: string;
  version: number;
  status: WorkflowStatus;
  name: string | null;
  rounds: number;
  /** Why an archived version still matters: these people are still running it. */
  enrolled_candidates: number;
  published_at: string | null;
  created_at: string;
}

// ── The role model HR picks competencies from ───────────────────────────────

export interface RoleModelCompetency {
  id: string;
  name: string;
  kind: string;
  weight: number;
  anchors: { low: string; mid: string; high: string };
  probes: string[];
}

export interface RoleModel {
  job_title: string;
  domain_family: string;
  domain_label: string;
  source: string;
  competencies: RoleModelCompetency[];
}

export function getRoleModel(requisitionId: string): Promise<RoleModel> {
  return apiGet<RoleModel>(`/hr/requisitions/${requisitionId}/role-model`);
}

// ── Validation + coverage ───────────────────────────────────────────────────

export interface CoverageRow {
  competency_id: string;
  competency_name: string;
  profile_weight: number;
  /** How many rounds assess it. 0 is a gap; 3+ is over-testing. */
  times_assessed: number;
  assessed_in: string[];
}

export interface ValidationReport {
  publishable: boolean;
  /** Blocking. Publish is refused while any of these stand. */
  errors: string[];
  /** Advisory. Publish proceeds; the builder shows them anyway. */
  warnings: string[];
  coverage: CoverageRow[];
}

export function validateWorkflow(workflowId: string): Promise<ValidationReport> {
  return apiGet<ValidationReport>(`/hr/workflows/${workflowId}/validate`);
}

// ── Workflow CRUD ───────────────────────────────────────────────────────────

export function listWorkflows(requisitionId: string): Promise<WorkflowSummary[]> {
  return apiGet<WorkflowSummary[]>(`/hr/requisitions/${requisitionId}/workflows`);
}

export function getWorkflow(workflowId: string): Promise<Workflow> {
  return apiGet<Workflow>(`/hr/workflows/${workflowId}`);
}

/** Start a draft. 409 when the opening already has one — there is only ever one. */
export function startDraft(requisitionId: string, name?: string): Promise<Workflow> {
  const q = name ? `?name=${encodeURIComponent(name)}` : '';
  return apiPost<Workflow>(`/hr/requisitions/${requisitionId}/workflows${q}`, {});
}

export function updateSettings(
  workflowId: string,
  fields: Partial<WorkflowSettings & { name: string }>,
): Promise<Workflow> {
  return apiPatch<Workflow>(`/hr/workflows/${workflowId}`, fields);
}

/** Throw away a draft. Refused (409) for anything published. */
export function discardDraft(workflowId: string): Promise<void> {
  return apiDelete<void>(`/hr/workflows/${workflowId}`);
}

// ── Rounds ──────────────────────────────────────────────────────────────────

export interface CriterionInput {
  id: string;
  name: string;
  kind?: string | null;
  weight: number;
  anchors?: { low: string; mid: string; high: string } | null;
  probes?: string[] | null;
}

export interface RoundInput {
  title: string;
  kind: RoundKind;
  pass_threshold?: number | null;
  time_limit_seconds?: number | null;
  deadline_days?: number;
  exam_round_id?: string | null;
  criteria?: CriterionInput[];
}

/**
 * Every round mutation returns the WHOLE workflow rather than the changed row,
 * so the canvas re-renders from one authoritative shape instead of patching
 * local state — positions, the next-round chain and `needs_questions` all shift
 * when a round moves, and reassembling that client-side is how canvases drift.
 */
export function addRound(workflowId: string, body: RoundInput): Promise<Workflow> {
  return apiPost<Workflow>(`/hr/workflows/${workflowId}/rounds`, body);
}

/**
 * The fields of a round that can change after it exists. `kind` is absent on
 * purpose: changing an MCQ round into an interview would silently invalidate
 * its attached questions and its rubric, so that is a delete and an add.
 */
export type RoundPatch = Partial<Omit<RoundInput, 'kind' | 'criteria'>>;

export function updateRound(
  workflowId: string,
  roundId: string,
  fields: RoundPatch,
): Promise<Workflow> {
  return apiPatch<Workflow>(`/hr/workflows/${workflowId}/rounds/${roundId}`, fields);
}

export function removeRound(workflowId: string, roundId: string): Promise<Workflow> {
  return apiDelete<Workflow>(`/hr/workflows/${workflowId}/rounds/${roundId}`);
}

export function reorderRounds(workflowId: string, roundIds: string[]): Promise<Workflow> {
  return apiPut<Workflow>(`/hr/workflows/${workflowId}/rounds/order`, { round_ids: roundIds });
}

/** Replace a round's whole rubric — this is the freeze (C8). */
export function setRoundCriteria(
  workflowId: string,
  roundId: string,
  criteria: CriterionInput[],
): Promise<Workflow> {
  return apiPut<Workflow>(`/hr/workflows/${workflowId}/rounds/${roundId}/criteria`, {
    criteria,
  });
}

// ── Publish / clone ─────────────────────────────────────────────────────────

/**
 * Publish a draft, archiving whatever it replaces.
 *
 * Rejects with 422 when the draft is not publishable, carrying the whole
 * ValidationReport — see `validationFromError`, which the builder uses to
 * render those errors inline rather than as a bare failure toast.
 */
export function publishWorkflow(
  workflowId: string,
): Promise<Workflow & { validation: ValidationReport }> {
  return apiPost<Workflow & { validation: ValidationReport }>(
    `/hr/workflows/${workflowId}/publish`,
    {},
  );
}

/** Open a published workflow for editing as version n+1. The original is untouched. */
export function cloneWorkflow(workflowId: string): Promise<Workflow> {
  return apiPost<Workflow>(`/hr/workflows/${workflowId}/clone`, {});
}

/**
 * Pull the ValidationReport out of a failed publish, or null.
 *
 * The 422's detail IS the report, but `ApiError` keeps only `detail`'s string
 * form — so this reads the parsed body when a caller has one and returns null
 * otherwise, which tells the builder to refetch `validateWorkflow` instead of
 * guessing. Null is a normal outcome here, not a failure.
 */
export function validationFromError(err: unknown): ValidationReport | null {
  const detail = (err as { detail?: unknown } | null)?.detail;
  if (detail !== null && typeof detail === 'object' && 'publishable' in detail) {
    return detail as ValidationReport;
  }
  return null;
}

// ── Decision queue + holds ──────────────────────────────────────────────────

/**
 * One person waiting on a human.
 *
 * Advanced and held candidates share this list deliberately: held candidates
 * behind their own tab are held candidates nobody opens, which would make
 * "every candidate reaches a human decision" true on paper and false in
 * practice (D-05).
 */
export interface DecisionQueueRow {
  enrolment_id: string;
  full_name: string;
  email: string | null;
  status: 'new' | 'shortlisted' | 'interviewed' | 'held';
  held: boolean;
  held_reason: string | null;
  ats_overall: number | null;
  rounds_taken: number;
  best_percent: number | null;
}

export function getDecisionQueue(requisitionId: string): Promise<DecisionQueueRow[]> {
  return apiGet<DecisionQueueRow[]>(`/hr/requisitions/${requisitionId}/decision-queue`);
}

/**
 * Let a held candidate continue.
 *
 * There is no automated caller for this anywhere in the codebase, and there
 * must not be: deciding that a below-threshold candidate should proceed is
 * exactly the judgement D-05 reserves for a person.
 */
export function releaseHold(
  enrolmentId: string,
  opts: { to_status?: string; reason?: string } = {},
): Promise<{ action: string; enrolment_id: string; reason?: string }> {
  return apiPost<{ action: string; enrolment_id: string; reason?: string }>(
    `/hr/enrolments/${enrolmentId}/release-hold`,
    { to_status: opts.to_status ?? 'shortlisted', reason: opts.reason?.trim() || null },
  );
}
