// scheduling.ts — PH4 Wave 3: interview loops + sessions (A2), panel
// workload + capacity + calibration (O5).
//
// Field names match services/data_gateway/app/interview_scheduling.py and
// panel_workload.py EXACTLY — `_loop_out`, `_session_out`, `_candidate_session`,
// `workload_rows` and `calibrate` are the source of truth for every shape here.
//
// Three audiences, three prefixes, same as the router: `/hr` (any interviewer
// or candidate), `/interviewer` (the caller's own sessions/availability only),
// `/users/me` (the candidate's own — no route here can move or cancel a
// booked session; that is HR's, audited).
//
// Every id interpolated into a URL goes through `pathId` — see pathId.ts.

import { apiDelete, apiGet, apiPatch, apiPost, apiPut, fetchBlobWithAuth } from './client';
import { pathId } from './pathId';

// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const API_BASE: string = import.meta.env.VITE_API_BASE_URL;

function rangeQuery(opts: { start?: string; end?: string } = {}): string {
  const p = new URLSearchParams();
  if (opts.start) p.set('start', opts.start);
  if (opts.end) p.set('end', opts.end);
  const q = p.toString();
  return q ? `?${q}` : '';
}

// ---------------------------------------------------------------------------
// Availability windows — shared shape between HR-on-anyone and the
// interviewer's own console.
// ---------------------------------------------------------------------------

export interface AvailabilityWindow {
  id: string;
  starts_at: string;
  ends_at: string;
}

export interface WindowInput {
  /** ISO with a timezone offset — never a naive local string. */
  starts_at: string;
  ends_at: string;
}

/** Any interviewer's availability windows — HR only. */
export function getInterviewerAvailability(
  userId: string,
  opts: { start?: string; end?: string } = {},
): Promise<AvailabilityWindow[]> {
  return apiGet<AvailabilityWindow[]>(
    `/hr/interviewers/${pathId(userId)}/availability${rangeQuery(opts)}`,
  );
}

export function addInterviewerAvailability(
  userId: string,
  body: WindowInput,
): Promise<AvailabilityWindow> {
  return apiPost<AvailabilityWindow>(`/hr/interviewers/${pathId(userId)}/availability`, body);
}

/** HR removing ANY interviewer's window. */
export function removeInterviewerAvailability(windowId: string): Promise<void> {
  return apiDelete<void>(`/hr/availability/${pathId(windowId)}`);
}

/** The signed-in interviewer's own availability. */
export function getMyAvailability(
  opts: { start?: string; end?: string } = {},
): Promise<AvailabilityWindow[]> {
  return apiGet<AvailabilityWindow[]>(`/interviewer/availability${rangeQuery(opts)}`);
}

export function addMyAvailability(body: WindowInput): Promise<AvailabilityWindow> {
  return apiPost<AvailabilityWindow>('/interviewer/availability', body);
}

export function removeMyAvailability(windowId: string): Promise<void> {
  return apiDelete<void>(`/interviewer/availability/${pathId(windowId)}`);
}

// ---------------------------------------------------------------------------
// The interviewer's own sessions (O5 #6 — never anyone else's)
// ---------------------------------------------------------------------------

export type SessionStatus = 'awaiting_slot' | 'scheduled' | 'cancelled' | 'completed' | 'no_show';

export interface InterviewerSession {
  id: string;
  title: string;
  starts_at: string | null;
  ends_at: string | null;
  duration_minutes: number;
  location: string | null;
  status: SessionStatus;
  candidate: string;
  job_title: string;
  scorecard_id: string | null;
}

export function getMySessions(
  opts: { start?: string; end?: string } = {},
): Promise<InterviewerSession[]> {
  return apiGet<InterviewerSession[]>(`/interviewer/sessions${rangeQuery(opts)}`);
}

// ---------------------------------------------------------------------------
// Loops + sessions (HR)
// ---------------------------------------------------------------------------

export type LoopStatus = 'draft' | 'scheduled' | 'completed' | 'cancelled';

export interface SessionInterviewer {
  user_id: string;
  name: string;
  scorecard_id: string | null;
  scorecard_status: string | null;
}

export interface InterviewSession {
  id: string;
  round_id: string;
  round_title: string;
  title: string;
  position: number;
  duration_minutes: number;
  starts_at: string | null;
  ends_at: string | null;
  location: string | null;
  status: SessionStatus;
  booked_by: 'hr' | 'candidate' | null;
  interviewers: SessionInterviewer[];
}

export interface InterviewLoop {
  id: string;
  enrolment_id: string;
  title: string;
  status: LoopStatus;
  candidate_timezone: string;
  buffer_minutes: number;
  self_schedule: boolean;
  sent_at: string | null;
  itinerary_version: number;
  cancelled_at: string | null;
  sessions: InterviewSession[];
}

export interface LoopInput {
  title?: string;
  candidate_timezone?: string;
  buffer_minutes?: number;
  self_schedule?: boolean;
}

export function listLoopsForEnrolment(enrolmentId: string): Promise<InterviewLoop[]> {
  return apiGet<InterviewLoop[]>(`/hr/enrolments/${pathId(enrolmentId)}/loops`);
}

export function createLoop(enrolmentId: string, body: LoopInput): Promise<InterviewLoop> {
  return apiPost<InterviewLoop>(`/hr/enrolments/${pathId(enrolmentId)}/loops`, body);
}

export function getLoop(loopId: string): Promise<InterviewLoop> {
  return apiGet<InterviewLoop>(`/hr/loops/${pathId(loopId)}`);
}

export interface SessionInput {
  round_id: string;
  title?: string | null;
  duration_minutes: number;
  interviewer_user_ids: string[];
  /** ISO with a timezone offset. Omit only when the loop lets the candidate
   *  choose their own time (self_schedule). */
  starts_at?: string | null;
  location?: string | null;
  allow_outside_availability?: boolean;
}

/** Returns the whole loop, refreshed — same shape as getLoop. */
export function addSession(loopId: string, body: SessionInput): Promise<InterviewLoop> {
  return apiPost<InterviewLoop>(`/hr/loops/${pathId(loopId)}/sessions`, body);
}

export interface SendLoopResult {
  loop_id: string;
  status: LoopStatus;
  sent: 'itinerary' | 'slot_request';
  version: number;
}

/** Gives the candidate the loop: the itinerary, or the request to pick times. */
export function sendLoop(loopId: string): Promise<SendLoopResult> {
  return apiPost<SendLoopResult>(`/hr/loops/${pathId(loopId)}/send`, {});
}

export interface CancelLoopResult {
  loop_id: string;
  status: 'cancelled';
  sessions_cancelled: number;
}

export function cancelLoop(loopId: string, reason?: string | null): Promise<CancelLoopResult> {
  return apiPost<CancelLoopResult>(`/hr/loops/${pathId(loopId)}/cancel`, {
    reason: reason?.trim() || null,
  });
}

export interface RescheduleInput {
  /** ISO with a timezone offset. */
  starts_at: string;
  duration_minutes?: number;
  location?: string | null;
  allow_outside_availability?: boolean;
}

export interface RescheduleResult {
  session_id: string;
  starts_at: string;
}

/** HR moves a session (A2 #24). There is no equivalent for a candidate (#23). */
export function rescheduleSession(
  sessionId: string,
  body: RescheduleInput,
): Promise<RescheduleResult> {
  return apiPatch<RescheduleResult>(`/hr/sessions/${pathId(sessionId)}`, body);
}

export type SessionOutcome = 'cancelled' | 'completed' | 'no_show';

export interface OutcomeResult {
  session_id: string;
  status: SessionOutcome;
  loop_status: LoopStatus;
}

/** completed/no_show are refused by the server until the session has started. */
export function setSessionOutcome(
  sessionId: string,
  outcome: SessionOutcome,
  reason?: string | null,
): Promise<OutcomeResult> {
  return apiPost<OutcomeResult>(`/hr/sessions/${pathId(sessionId)}/outcome`, {
    outcome,
    reason: reason?.trim() || null,
  });
}

/** Start times, on a 15-minute grid, at which this session could be booked now. */
export function getSessionSlots(sessionId: string): Promise<string[]> {
  return apiGet<{ slots: string[] }>(`/hr/sessions/${pathId(sessionId)}/slots`).then(
    (r) => r.slots,
  );
}

// ---------------------------------------------------------------------------
// Calendar files — authenticated binary downloads (see fetchBlobWithAuth).
// ---------------------------------------------------------------------------

async function downloadIcs(path: string, filename: string): Promise<void> {
  const res = await fetchBlobWithAuth(`${API_BASE}${path}`);
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    const detail = typeof body.detail === 'string' ? body.detail : `HTTP ${res.status}`;
    throw new Error(detail);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function downloadHrLoopIcs(loopId: string): Promise<void> {
  return downloadIcs(`/hr/loops/${pathId(loopId)}/calendar.ics`, `interviews-${loopId}.ics`);
}

export function downloadMyLoopIcs(loopId: string): Promise<void> {
  return downloadIcs(`/users/me/interview-loops/${pathId(loopId)}/calendar.ics`, 'interviews.ics');
}

// ---------------------------------------------------------------------------
// Panel workload + capacity (O5)
// ---------------------------------------------------------------------------

export interface WorkloadDefaults {
  max_per_day: number;
  max_per_week: number;
}

export type WorkloadFlag =
  | 'over_allocated'
  | 'outside_availability'
  | 'overdue_scorecards'
  | 'conflict';

export interface WorkloadRow {
  user_id: string;
  name: string;
  role: 'interviewer' | 'hr_manager';
  sessions: number;
  hours: number;
  loops: number;
  by_day: Record<string, number>;
  open_scorecards: number;
  overdue_scorecards: number;
  max_per_day: number;
  max_per_week: number;
  over_allocated_days: string[];
  over_allocated_weeks: string[];
  outside_availability: number;
  conflicts: number;
  flags: WorkloadFlag[];
}

export interface WorkloadResponse {
  start: string;
  end: string;
  timezone: string;
  defaults: WorkloadDefaults;
  interviewers: WorkloadRow[];
}

/** start/end: ISO with a timezone offset, up to a 92-day span. */
export function getWorkload(start: string, end: string): Promise<WorkloadResponse> {
  const p = new URLSearchParams({ start, end });
  return apiGet<WorkloadResponse>(`/hr/panel/workload?${p.toString()}`);
}

export interface CapacityInput {
  max_sessions_per_day?: number | null;
  max_sessions_per_week?: number | null;
}

export interface CapacityResult {
  user_id: string;
  max_sessions_per_day: number | null;
  max_sessions_per_week: number | null;
}

/** Null clears a limit back to the company default shown in `defaults`. */
export function setInterviewerCapacity(
  userId: string,
  body: CapacityInput,
): Promise<CapacityResult> {
  return apiPut<CapacityResult>(`/hr/interviewers/${pathId(userId)}/capacity`, body);
}

// ---------------------------------------------------------------------------
// Calibration (O5, extended PH5-E4) — read-only; never changes a scorecard
// or a decision. Field names match app.panel_workload.calibration/judgements
// and app.calibration_core EXACTLY.
// ---------------------------------------------------------------------------

export interface CalibrationSpecRef {
  name: string;
  version: number;
}

export interface CalibrationCohort {
  basis: 'scorecard_submitted';
  from: string;
  to: string;
}

export interface CalibrationFilters {
  requisition_id: string | null;
  round_id: string | null;
}

export interface CalibrationRules {
  min_pairs: number;
  meaningful_delta: number;
  scale: string;
  /** Fewer distinct candidates than this and the row is suppressed. */
  min_candidates: number;
  /** PH5-E4 — the share of shared judgements that must point the same way
   *  before a "higher"/"lower" flag is raised; a single wild score cannot
   *  trip it alone. */
  min_same_direction_share: number;
  /** PH5-E4 — a panel baseline needs at least this many distinct
   *  interviewers, or it is one person's scores, not a panel's. */
  min_interviewers_for_baseline: number;
  /** PH5-E4 — a criterion's "wide disagreement" signal fires at or above
   *  this mean (max-min) range on shared judgements. */
  wide_disagreement_range: number;
  min_span_days: number;
  max_span_days: number;
}

/** One interviewer's paired gap on a single frozen criterion
 *  `(round_id, competency_id)` — omitted from `by_criterion` below
 *  `rules.min_candidates`, the same convention `by_competency` uses. */
export interface CriterionGap {
  criterion_key: string;
  shared_judgements: number;
  candidates: number;
  gap: number;
  signal: 'higher' | 'lower' | null;
}

export interface CalibrationRow {
  user_id: string;
  name: string;
  scorecards: number;
  /** Withheld (null) with the other figures when `suppressed` — a count over
   *  one candidate says how many criteria they were scored on. */
  scores: number | null;
  /** Distinct candidates this interviewer scored in the period — the number
   *  `suppressed` and the security fix's `min_candidates` rule are about. */
  candidates: number;
  /** True when `candidates` is below `rules.min_candidates`: every figure
   *  below that could otherwise re-identify a specific candidate's scores
   *  from just one or two data points, so the server sends nulls/empties
   *  instead of small numbers. Say so in words — never render zeros. */
  suppressed: boolean;
  mean: number | null;
  not_assessed_rate: number | null;
  /** Keyed "1".."5"; null when suppressed. */
  distribution: Record<string, number> | null;
  /** May omit a competency that rests on fewer than `rules.min_candidates`
   *  candidates, even for a row that is not itself suppressed. Empty when
   *  suppressed, or when no `round_id` filter is set (mixes different
   *  rounds' anchors under one competency id otherwise). */
  by_competency: Record<string, number>;
  pairs: number;
  mean_delta: number | null;
  /** PH5-E4 — the share of `pairs` whose gap points the same way as
   *  `mean_delta`; null exactly when `mean_delta` is. */
  same_direction_share: number | null;
  /** PH5-E4 — the server-computed COUNT behind `same_direction_share` (e.g.
   *  6 of `pairs`); null exactly when `same_direction_share` is. Render this
   *  directly for "in N of pairs the gap was the same way" — never
   *  `Math.round(same_direction_share * pairs)`, which can silently disagree
   *  with the server's own count. */
  same_direction_count: number | null;
  flag: 'higher' | 'lower' | null;
  /** PH5-E4 — the same paired-gap method, one frozen criterion at a time. */
  by_criterion: CriterionGap[];
}

/** PH5-E4 — the panel's OWN baseline on one frozen criterion
 *  `(round_id, competency_id)`, never an interviewer's figure. */
export interface CalibrationCriterion {
  criterion_key: string;
  round_id: string;
  competency_id: string;
  competency_name: string;
  round_title: string | null;
  round_kind: string | null;
  workflow_version: number | null;
  requisition_id: string | null;
  requisition_title: string | null;
  suppressed: boolean;
  candidates: number;
  interviewers: number;
  /** Withheld (null) with the other figures when `suppressed`. */
  scores: number | null;
  not_assessed: number;
  distribution: Record<string, number> | null;
  mean: number | null;
  shared_judgements: number;
  disagreement: number | null;
  signal: 'wide_disagreement' | null;
}

export interface CalibrationResponse {
  spec: CalibrationSpecRef;
  registry_hash: string;
  cohort: CalibrationCohort;
  filters: CalibrationFilters;
  start: string;
  end: string;
  rules: CalibrationRules;
  /** competency_id -> name, for the `by_competency` keys above. Filled only
   *  when a `round_id` filter is set (otherwise ambiguous across rounds). */
  competencies: Record<string, string>;
  /** PH5-E4 — the panel's own baseline per frozen criterion. */
  criteria: CalibrationCriterion[];
  interviewers: CalibrationRow[];
}

/** The server 422s a period shorter than 7 days — callers must not offer one. */
export function getCalibration(opts: {
  start: string;
  end: string;
  requisitionId?: string | null;
  roundId?: string | null;
}): Promise<CalibrationResponse> {
  const p = new URLSearchParams({ start: opts.start, end: opts.end });
  if (opts.requisitionId) p.set('requisition_id', pathId(opts.requisitionId));
  if (opts.roundId) p.set('round_id', pathId(opts.roundId));
  return apiGet<CalibrationResponse>(`/hr/panel/calibration?${p.toString()}`);
}

// ---------------------------------------------------------------------------
// Calibration judgements drill-down (PH5-E4) — one interviewer's row, or one
// interviewer x criterion cell, named down to the candidate. 404s for an
// interviewer outside the company; 422s ("Too few candidates to show") when
// that cell is suppressed in the aggregate report — show that message
// plainly, not as a generic error.
// ---------------------------------------------------------------------------

export interface CalibrationJudgementRow {
  enrolment_id: string;
  applicant_id: string;
  candidate_name: string;
  requisition_title: string | null;
  round_id: string;
  round_title: string;
  criterion_key: string;
  competency_name: string;
  score: number;
  panel_mean: number | null;
  panel_size: number;
  gap: number | null;
  scorecard_id: string;
  submitted_at: string;
  evidence_href: string;
}

export interface CalibrationJudgementsResponse {
  spec: CalibrationSpecRef;
  cohort: CalibrationCohort;
  filters: CalibrationFilters & { criterion_key: string | null };
  interviewer: { user_id: string; name: string };
  total: number;
  /** Rows are capped (newest first) — `total` is the full count. */
  truncated: boolean;
  rows: CalibrationJudgementRow[];
}

export function getCalibrationJudgements(opts: {
  interviewerId: string;
  start: string;
  end: string;
  requisitionId?: string | null;
  roundId?: string | null;
  criterionKey?: string | null;
}): Promise<CalibrationJudgementsResponse> {
  const p = new URLSearchParams({
    interviewer_id: pathId(opts.interviewerId),
    start: opts.start,
    end: opts.end,
  });
  if (opts.requisitionId) p.set('requisition_id', pathId(opts.requisitionId));
  if (opts.roundId) p.set('round_id', pathId(opts.roundId));
  if (opts.criterionKey) p.set('criterion_key', opts.criterionKey);
  return apiGet<CalibrationJudgementsResponse>(`/hr/panel/calibration/judgements?${p.toString()}`);
}

// ---------------------------------------------------------------------------
// The candidate's own side — no route here moves or cancels a booked session
// (A2 #23); that is HR's, audited.
// ---------------------------------------------------------------------------

export interface CandidateSession {
  id: string;
  title: string;
  duration_minutes: number;
  starts_at: string | null;
  ends_at: string | null;
  location: string | null;
  status: SessionStatus;
  /** Names only — no ids, no scorecard state (see `_candidate_session`). */
  interviewers: string[];
}

export interface CandidateLoop {
  id: string;
  title: string;
  job_title: string;
  status: LoopStatus;
  timezone: string;
  self_schedule: boolean;
  sessions: CandidateSession[];
}

export function listMyInterviewLoops(): Promise<CandidateLoop[]> {
  return apiGet<CandidateLoop[]>('/users/me/interview-loops');
}

/**
 * Once the application has moved past a decision (or HR cancelled the
 * session), this can answer an empty list rather than an error — there is
 * simply nothing left to offer. Callers show that as "no times available",
 * not as a failure.
 */
export function getMySessionSlots(loopId: string, sessionId: string): Promise<string[]> {
  return apiGet<{ slots: string[] }>(
    `/users/me/interview-loops/${pathId(loopId)}/sessions/${pathId(sessionId)}/slots`,
  ).then((r) => r.slots);
}

export interface BookInput {
  session_id: string;
  /** The EXACT ISO string offered by getMySessionSlots — the server checks
   *  it against the slots it would offer right now and refuses anything else. */
  starts_at: string;
  timezone?: string | null;
}

export interface BookResult {
  session_id: string;
  starts_at: string;
  all_booked: boolean;
}

/**
 * Can answer 409 "This schedule is not open for choosing times." — the
 * application was decided, or HR cancelled the loop/session, after the
 * candidate opened this screen. Callers show the server's sentence and stop
 * offering the booking control, same as any other 409 here.
 */
export function bookMySlot(loopId: string, body: BookInput): Promise<BookResult> {
  return apiPost<BookResult>(`/users/me/interview-loops/${pathId(loopId)}/book`, body);
}
