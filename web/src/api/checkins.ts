// checkins.ts — the 90-day hire check-in (PH5 wave-1 follow-up A). Records
// whether a hire is still with the company and a broad performance level,
// 80+ days after their start. Aggregate-only by design (the metric layer's
// checkin_coverage / retention_90d / performance_90d read these same rows);
// nothing here changes an application or a decision. Field names match the
// data_gateway contract EXACTLY.

import { apiGet, apiPost } from './client';

/** A hire at least 80 days in with no live check-in yet, oldest first. */
export interface CheckinDueRow {
  enrolment_id: string;
  applicant_id: string;
  full_name: string;
  job_title: string;
  employment_start: string;
  due_at: string;
  days_since_start: number;
}

/** Capped at 200, oldest first. */
export function listCheckinsDue(): Promise<CheckinDueRow[]> {
  return apiGet<CheckinDueRow[]>('/hr/checkins/due');
}

export type CheckinEmployment = 'employed' | 'left';
export type CheckinLeftReason = 'voluntary' | 'involuntary' | 'unknown';
export type CheckinPerformance = 'below' | 'meets' | 'exceeds';

export interface CheckinOut {
  checkin_id: string;
  enrolment_id: string;
  kind: string;
  employment: CheckinEmployment;
  left_reason: CheckinLeftReason | null;
  performance: CheckinPerformance | null;
  recorded_by_user_id: string;
  /** Null for a staff account since removed — show "a former team member",
   *  never the raw id. */
  recorded_by_name: string | null;
  recorded_at: string;
  /** The row this one corrects, when it is itself a correction. */
  supersedes_id: string | null;
  /** True once a later correction has replaced this row — the read side
   *  shows it collapsed under "Earlier versions". */
  superseded: boolean;
}

/** The window this hire may be checked in during: from `start` (employment
 *  start) to `closes_at` (start + 180d), with `employed_from` (start + 80d)
 *  the earliest "still employed" may be recorded — "left" is allowed any
 *  time inside the window. All four fields are null/false (open: false) when
 *  the employment start itself is unknown. */
export interface CheckinWindow {
  start: string | null;
  employed_from: string | null;
  closes_at: string | null;
  open: boolean;
}

export interface EnrolmentCheckins {
  checkins: CheckinOut[];
  /** Shown verbatim above the form — server copy, not ours to paraphrase. */
  notice: string;
  window: CheckinWindow;
}

export function getEnrolmentCheckins(enrolmentId: string): Promise<EnrolmentCheckins> {
  return apiGet<EnrolmentCheckins>(`/hr/enrolments/${enrolmentId}/checkins`);
}

export interface CheckinInput {
  employment: CheckinEmployment;
  /** Required iff `employment === 'left'`. */
  left_reason?: CheckinLeftReason;
  /** Required iff `employment === 'employed'`. */
  performance?: CheckinPerformance;
}

/**
 * 201 CheckinOut. 409 when: the hire no longer stands; the candidate is
 * erased; a live check-in already exists; or `employed` is recorded before
 * day 80 (`left` is allowed any time). 422 on a vocabulary error.
 */
export function createCheckin(enrolmentId: string, body: CheckinInput): Promise<CheckinOut> {
  return apiPost<CheckinOut>(`/hr/enrolments/${enrolmentId}/checkins`, body);
}

/**
 * Same shape as a create (security review, PH5 wave-1 follow-up: no
 * free-text `correction_reason` — the earlier row is kept as a previous
 * version instead of being explained). 200 CheckinOut (the new, now-live
 * row). 409 if the check-in being corrected is already superseded, the hire
 * no longer stands, or the candidate is erased.
 */
export function correctCheckin(checkinId: string, body: CheckinInput): Promise<CheckinOut> {
  return apiPost<CheckinOut>(`/hr/checkins/${checkinId}/correct`, body);
}
