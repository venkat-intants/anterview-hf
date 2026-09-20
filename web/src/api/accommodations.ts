// accommodations.ts — PH4-D2 candidate accommodations. HR only — there is no
// candidate, interviewer, super-admin or agent route on this feature
// (services/data_gateway/app/accommodations.py module docstring).
//
// Disability-adjacent data: `interviewer_note`, `internal_note` and
// `other_adjustment` reach different audiences and none of the three is ever
// sent to the candidate — see AccommodationsSection.tsx for where that is
// enforced in the UI. The candidate's own view (PublicExam) reads a
// completely different, fact-only shape served by GET /exam — see
// api/publicExam.ts's `TakeExam.adjustments`, not this module.
//
// Field names match app/routers/accommodations.py's request bodies and
// app/accommodations.py's `_row_out` EXACTLY.

import { apiGet, apiPost } from './client';
import { pathId } from './pathId';

export type AccommodationBasis = 'candidate_request' | 'hr_initiated';
export type AccommodationStatus = 'active' | 'revoked';

/** One row of an applicant's accommodation history — HR's full view. */
export interface Accommodation {
  id: string;
  applicant_id: string;
  enrolment_id: string | null;
  round_id: string | null;
  exam_round_id: string | null;
  extra_time_percent: number | null;
  deadline_extension_days: number | null;
  relax_auto_submit: boolean;
  other_adjustment: string | null;
  interviewer_note: string | null;
  internal_note: string | null;
  basis: AccommodationBasis;
  requested_on: string | null;
  effective_from: string;
  effective_until: string | null;
  status: AccommodationStatus;
  recorded_by_user_id: string | null;
  revoked_by_user_id: string | null;
  revoked_at: string | null;
  revoke_reason: string | null;
  supersedes_id: string | null;
  superseded_at: string | null;
  superseded_by_id: string | null;
  redacted_at: string | null;
  created_at: string;
  updated_at: string;
}

/** Every accommodation ever recorded for this applicant, newest first —
 *  including revoked and superseded rows. */
export function listAccommodations(applicantId: string): Promise<Accommodation[]> {
  return apiGet<Accommodation[]>(`/hr/applicants/${pathId(applicantId)}/accommodations`);
}

export interface AccommodationRecordInput {
  enrolment_id?: string | null;
  round_id?: string | null;
  exam_round_id?: string | null;
  extra_time_percent?: number | null;
  deadline_extension_days?: number | null;
  relax_auto_submit?: boolean;
  other_adjustment?: string | null;
  interviewer_note?: string | null;
  internal_note?: string | null;
  basis: AccommodationBasis;
  requested_on?: string | null;
  /** ISO with a timezone offset. Omitted = now. */
  effective_from?: string | null;
  effective_until?: string | null;
}

/** A round-scoped or exam-round-scoped input needs `enrolment_id` — the
 *  server 422s "A round-scoped adjustment needs an application." /
 *  "An exam-round-scoped adjustment needs an application." otherwise. Build
 *  the input so that can't happen rather than showing the server's refusal. */
export function recordAccommodation(
  applicantId: string,
  body: AccommodationRecordInput,
): Promise<{ id: string }> {
  return apiPost<{ id: string }>(`/hr/applicants/${pathId(applicantId)}/accommodations`, body);
}

export interface AccommodationReviseInput {
  extra_time_percent?: number | null;
  deadline_extension_days?: number | null;
  relax_auto_submit?: boolean;
  other_adjustment?: string | null;
  interviewer_note?: string | null;
  internal_note?: string | null;
  effective_from?: string | null;
  effective_until?: string | null;
}

/** Replaces an active accommodation with a new row carrying new parameters,
 *  in the SAME scope and basis — neither can move on a revision. The old row
 *  is superseded, never edited, so an attempt already taken keeps pointing at
 *  what applied when it was taken. */
export function reviseAccommodation(
  accommodationId: string,
  body: AccommodationReviseInput,
): Promise<{ id: string }> {
  return apiPost<{ id: string }>(`/hr/accommodations/${pathId(accommodationId)}/revise`, body);
}

/** Ends an active accommodation. Anything already started or minted under it
 *  keeps what it already has (frozen on the attempt/assignment/invite). */
export function revokeAccommodation(
  accommodationId: string,
  reason?: string | null,
): Promise<{ status: 'revoked' }> {
  return apiPost<{ status: 'revoked' }>(`/hr/accommodations/${pathId(accommodationId)}/revoke`, {
    reason: reason?.trim() || null,
  });
}

/** What would apply right now for this application (and, optionally, one
 *  workflow round) — the HR-facing preview. Never the notes or the basis. */
export interface EffectiveAccommodation {
  effective: boolean;
  accommodation_id?: string;
  extra_time_percent?: number | null;
  deadline_extension_days?: number | null;
  relax_auto_submit?: boolean;
}

export function getEffectiveAccommodation(
  enrolmentId: string,
  roundId?: string | null,
): Promise<EffectiveAccommodation> {
  const p = new URLSearchParams();
  if (roundId) p.set('round_id', pathId(roundId));
  const q = p.toString();
  return apiGet<EffectiveAccommodation>(
    `/hr/enrolments/${pathId(enrolmentId)}/accommodations/effective${q ? `?${q}` : ''}`,
  );
}
