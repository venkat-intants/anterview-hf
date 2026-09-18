// stageSla.ts — PH4-O1: stage owners, SLAs, and the exception paths raised
// against them.
//
// A stage is a round of a workflow version, or the final human decision after
// the last one (`round_id: null`). Owners and SLAs are operational, not part
// of the frozen rubric a candidate is assessed against — they live in their
// own table and stay editable on a live version, same as the interview kit
// (see RoundInspector's KitEditor).
//
// Exceptions record that normal processing cannot proceed for one application.
// Raising, resolving or reassigning one changes no status and no round — it is
// information for a person, never a decision (D-05). Say so in the UI copy
// wherever one of these is shown.

import { apiGet, apiPatch, apiPost, apiPut } from './client';
import { pathId } from './pathId';

export type SlaState = 'on_track' | 'due_soon' | 'overdue';

export interface StageSla {
  state: SlaState;
  sla_hours: number;
  entered_at: string;
  due_at: string;
  hours_remaining: number;
}

export type StageKind = 'mcq' | 'coding' | 'ai_interview' | 'human_review' | 'decision';

export interface StageSetting {
  /** null is the final-decision stage, after the last round. */
  round_id: string | null;
  stage: string;
  kind: StageKind;
  owner_user_id: string | null;
  owner_name: string | null;
  sla_hours: number | null;
}

/** Every stage of a version — each round in order, then the final decision. */
export function getStages(workflowId: string): Promise<StageSetting[]> {
  return apiGet<StageSetting[]>(`/hr/workflows/${pathId(workflowId)}/stages`);
}

export interface StagePatch {
  round_id: string | null;
  owner_user_id?: string | null;
  sla_hours?: number | null;
}

/** Set (or clear) one stage's owner and SLA. Allowed on a live version too. */
export function setStage(workflowId: string, body: StagePatch): Promise<StageSetting[]> {
  return apiPut<StageSetting[]>(`/hr/workflows/${pathId(workflowId)}/stages`, body);
}

export interface StageOwner {
  user_id: string;
  full_name: string;
  email: string;
}

/** Active HR managers — who a stage or an exception can be handed to. */
export function listStageOwners(): Promise<StageOwner[]> {
  return apiGet<StageOwner[]>('/hr/stage-owners');
}

export interface SlaBoardRow {
  enrolment_id: string;
  requisition_id: string;
  opening_title: string;
  full_name: string;
  stage: string;
  owner_user_id: string | null;
  owner_name: string | null;
  open_exceptions: number;
  state: SlaState;
  sla_hours: number;
  entered_at: string;
  due_at: string;
  hours_remaining: number;
}

/** Applications against their stage SLA, worst first — HR's "at risk" view. */
export function getSlaBoard(
  opts: { requisitionId?: string; state?: SlaState[] } = {},
): Promise<SlaBoardRow[]> {
  const params = new URLSearchParams();
  if (opts.requisitionId) params.set('requisition_id', pathId(opts.requisitionId));
  if (opts.state?.length) params.set('state', opts.state.join(','));
  const qs = params.toString();
  return apiGet<SlaBoardRow[]>(`/hr/stage-sla${qs ? `?${qs}` : ''}`);
}

// ── Exceptions ──────────────────────────────────────────────────────────────

export interface StageException {
  exception_id: string;
  enrolment_id: string;
  stage: string;
  reason: string;
  status: 'open' | 'resolved';
  owner_user_id: string | null;
  owner_name: string | null;
  raised_by_name: string | null;
  raised_at: string;
  resolved_by_name: string | null;
  resolved_at: string | null;
  resolution_note: string | null;
}

/** Newest first (raised_at DESC server-side) — NOT open first: callers that
 *  want open exceptions on top sort for it (ExceptionsSection's openFirst). */
export function listExceptions(enrolmentId: string): Promise<StageException[]> {
  return apiGet<StageException[]>(`/hr/enrolments/${pathId(enrolmentId)}/exceptions`);
}

export interface RaiseExceptionResult {
  exception_id: string;
  status: 'open';
  stage: string;
  owner_user_id: string;
}

/** Record that this application cannot proceed normally. Changes nothing else. */
export function raiseException(
  enrolmentId: string,
  body: { reason: string; owner_user_id?: string | null },
): Promise<RaiseExceptionResult> {
  return apiPost<RaiseExceptionResult>(`/hr/enrolments/${pathId(enrolmentId)}/exceptions`, {
    reason: body.reason.trim(),
    owner_user_id: body.owner_user_id || null,
  });
}

/** Close an exception with how it was dealt with. Changes nothing else. */
export function resolveException(
  exceptionId: string,
  note?: string,
): Promise<{ exception_id: string; status: 'resolved' }> {
  return apiPost<{ exception_id: string; status: 'resolved' }>(
    `/hr/exceptions/${pathId(exceptionId)}/resolve`,
    { note: note?.trim() || null },
  );
}

/** Give an open exception to someone else. */
export function reassignException(
  exceptionId: string,
  ownerUserId: string,
): Promise<{ exception_id: string; owner_user_id: string }> {
  return apiPatch<{ exception_id: string; owner_user_id: string }>(
    `/hr/exceptions/${pathId(exceptionId)}`,
    { owner_user_id: ownerUserId },
  );
}
