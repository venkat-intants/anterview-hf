// pipeline.ts — HR hiring pipeline + analytics API (HR workflow Phase 4).
// One row per APPLICATION (B5) spanning the whole funnel (ATS → exam →
// interview → decision), plus aggregate funnel metrics. A person who applied to
// two openings is two rows, each with its own status, score, exam and interview. Tenant-scoped server-side by the
// HR's company. Field names match the data_gateway frozen contract EXACTLY.

import { apiGet, apiPost } from './client';

/** Applicant lifecycle status (Phase 4 adds the terminal decision states). */
export type PipelineStatus =
  | 'new'
  | 'shortlisted'
  | 'rejected'
  | 'interviewed'
  | 'hired'
  | 'held';

/** Stage filter for the pipeline list. */
export type PipelineStage = 'all' | 'shortlisted' | 'exam_passed' | 'interviewed' | 'decided';

/** HR hire/reject decision verbs. */
export type ApplicantDecision = 'hired' | 'rejected';

/** One pipeline row — one application flattened across all four stages. */
export interface PipelineRow {
  applicant_id: string;
  /** The application. Null only for someone filed under no opening. */
  enrolment_id?: string | null;
  requisition_id?: string | null;
  /** The opening's current title (falls back to the role applied for). */
  opening_title?: string | null;
  full_name: string;
  target_job_title: string;
  target_level: string;
  status: PipelineStatus; // DERIVED display value (incl. 'interviewed')
  // Stage 1 — ATS resume screening
  ats_overall: number | null; // 0-100
  ats_recommendation: string | null;
  // Stage 2 — MCQ exam
  best_exam_percent: number | null; // 0-100
  exam_passed: boolean | null;
  total_exam_attempts: number;
  // Stage 3 — AI interview
  interview_status: string | null; // invited|consumed|completed|expired|revoked
  interview_score: number | null; // 0-10 composite
  scorecard_id: string | null;
  updated_at: string;
}

export interface PipelineResponse {
  items: PipelineRow[];
  count: number;
  limit: number;
  offset: number;
}

/** Company-scoped funnel counts + averages. */
export interface HrAnalytics {
  funnel: {
    /** People. */
    total_applicants: number;
    /** Applications — what every other funnel count is a count of. */
    total_applications?: number;
    shortlisted: number;
    exam_taken: number;
    exam_passed: number;
    interview_invited: number;
    interview_completed: number;
    hired: number;
    rejected: number;
  };
  averages: {
    avg_ats: number | null; // 0-100
    avg_exam_percent: number | null; // 0-100
    avg_interview_composite: number | null; // 0-10
  };
}

export interface PipelineQuery {
  stage?: PipelineStage;
  status?: PipelineStatus;
  limit?: number;
  offset?: number;
}

/** Paginated pipeline list, optionally filtered by stage/status. */
export function getPipeline(opts: PipelineQuery = {}): Promise<PipelineResponse> {
  const p = new URLSearchParams();
  if (opts.stage) p.set('stage', opts.stage);
  if (opts.status) p.set('status', opts.status);
  if (opts.limit !== undefined) p.set('limit', String(opts.limit));
  if (opts.offset !== undefined) p.set('offset', String(opts.offset));
  const q = p.toString();
  return apiGet<PipelineResponse>(`/hr/pipeline${q ? `?${q}` : ''}`);
}

/** Company-scoped funnel metrics + averages for the analytics panel. */
export function getHrAnalytics(): Promise<HrAnalytics> {
  return apiGet<HrAnalytics>('/hr/analytics');
}

/**
 * Record an HR hire/reject decision (POST, audit-logged server-side).
 * Returns the updated applicant (the page only needs success → it refetches).
 *
 * `enrolmentId` names the application decided on. Send it whenever the row has
 * one: for someone with several applications the server refuses a decision
 * that does not say which.
 */
export function setApplicantDecision(
  applicantId: string,
  decision: ApplicantDecision,
  rationale?: string,
  enrolmentId?: string | null,
): Promise<{ id: string; status: PipelineStatus }> {
  return apiPost<{ id: string; status: PipelineStatus }>(
    `/hr/applicants/${applicantId}/decision`,
    {
      decision,
      rationale: rationale?.trim() || null,
      ...(enrolmentId ? { enrolment_id: enrolmentId } : {}),
    },
  );
}
