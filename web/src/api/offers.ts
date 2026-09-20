// offers.ts — PH4 Wave 4 (A3 offer lifecycle, A4 documents & preboarding).
//
// Three authenticated audiences, three prefixes — matches
// services/data_gateway/app/routers/offers.py exactly:
//   `/hr`    — HR managers: templates, offers, document requirements, review,
//              preboarding completion, the HRMS export.
//   `/admin` — the company super admin: approve or send back (decision D4-2).
//   `/users/me` — a signed-in candidate: their own offers, and a fresh link.
//
// The candidate's own public flow (no login, the emailed link + a one-time
// code) is a DIFFERENT module — see publicOffer.ts — because it deliberately
// bypasses this client's token/refresh machinery; there is no session to
// refresh.
//
// Field names match app/offers.py (`offer_out`, `candidate_out`, `list_offers`,
// `history`) and app/preboarding.py (the checklist, `hr_download`,
// `export_payload`) EXACTLY.

import { apiDelete, apiGet, apiPatch, apiPost } from './client';
import { pathId } from './pathId';

// ---------------------------------------------------------------------------
// Offers
// ---------------------------------------------------------------------------

export type OfferStatus =
  | 'draft'
  | 'pending_approval'
  | 'approved'
  | 'rejected'
  | 'sent'
  | 'accepted'
  | 'declined'
  | 'expired'
  | 'withdrawn';

export type EmploymentType = 'full_time' | 'part_time' | 'contract' | 'internship';
export type PayPeriod = 'annual' | 'monthly' | 'hourly';

/** What HR managers and the approving super admin read — compensation included. */
export interface OfferOut {
  id: string;
  enrolment_id: string;
  requisition_id: string | null;
  template_id: string | null;
  status: OfferStatus;
  job_title: string;
  employment_type: EmploymentType | null;
  start_date: string | null;
  location: string | null;
  /** A decimal string, e.g. "1200000.00" — never a float. */
  base_salary: string | null;
  currency: string | null;
  pay_period: PayPeriod | null;
  bonus: string | null;
  equity: string | null;
  benefits: string | null;
  terms: string | null;
  probation_months: number | null;
  notice_period_days: number | null;
  valid_days: number | null;
  created_by_user_id: string | null;
  submitted_at: string | null;
  decided_at: string | null;
  approval_note: string | null;
  sent_at: string | null;
  expires_at: string | null;
  first_viewed_at: string | null;
  responded_at: string | null;
  decline_reason: string | null;
  withdrawn_at: string | null;
  withdraw_reason: string | null;
  preboarding_completed_at: string | null;
  candidate_name: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface OfferHistoryEntry {
  action: string;
  actor_type: string;
  actor_name: string | null;
  at: string;
  details: Record<string, unknown>;
}

export interface OfferDetail extends OfferOut {
  history: OfferHistoryEntry[];
}

export interface OfferFieldsInput {
  job_title?: string;
  employment_type?: EmploymentType;
  start_date?: string | null;
  location?: string;
  base_salary?: string | number;
  currency?: string;
  pay_period?: PayPeriod;
  bonus?: string;
  equity?: string;
  benefits?: string;
  terms?: string;
  probation_months?: number | null;
  notice_period_days?: number | null;
  valid_days?: number;
}

export interface OfferCreateInput extends OfferFieldsInput {
  template_id?: string | null;
}

// ── Templates ────────────────────────────────────────────────────────────

export interface OfferTemplate {
  id: string;
  name: string;
  employment_type: EmploymentType | null;
  currency: string | null;
  pay_period: PayPeriod | null;
  probation_months: number | null;
  notice_period_days: number | null;
  benefits: string | null;
  terms: string | null;
  valid_days: number | null;
}

export interface TemplateInput {
  name?: string;
  employment_type?: EmploymentType;
  currency?: string;
  pay_period?: PayPeriod;
  probation_months?: number | null;
  notice_period_days?: number | null;
  benefits?: string;
  terms?: string;
  valid_days?: number;
}

export function listOfferTemplates(): Promise<OfferTemplate[]> {
  return apiGet<OfferTemplate[]>('/hr/offer-templates');
}

export function createOfferTemplate(body: TemplateInput): Promise<OfferTemplate> {
  return apiPost<OfferTemplate>('/hr/offer-templates', body);
}

export function updateOfferTemplate(
  templateId: string,
  body: TemplateInput,
): Promise<OfferTemplate> {
  return apiPatch<OfferTemplate>(`/hr/offer-templates/${pathId(templateId)}`, body);
}

export function deleteOfferTemplate(templateId: string): Promise<void> {
  return apiDelete<void>(`/hr/offer-templates/${pathId(templateId)}`);
}

// ── HR — offers ──────────────────────────────────────────────────────────

/** Every filter `GET /hr/offers` accepts, plus `preboarding` (accepted, not yet complete). */
export type OfferListFilter = OfferStatus | 'preboarding';

export interface OfferListItem {
  id: string;
  enrolment_id: string;
  requisition_id: string | null;
  status: OfferStatus;
  job_title: string;
  candidate_name: string;
  sent_at: string | null;
  expires_at: string | null;
  responded_at: string | null;
  preboarding_completed_at: string | null;
  updated_at: string;
  documents: {
    mandatory_total: number;
    mandatory_verified: number;
    awaiting_review: number;
  };
}

export interface OfferListResponse {
  items: OfferListItem[];
  total: number;
  limit: number;
  offset: number;
}

export function listCompanyOffers(opts: {
  status?: OfferListFilter | null;
  limit?: number;
  offset?: number;
} = {}): Promise<OfferListResponse> {
  const p = new URLSearchParams();
  if (opts.status) p.set('status', opts.status);
  if (opts.limit) p.set('limit', String(opts.limit));
  if (opts.offset) p.set('offset', String(opts.offset));
  const q = p.toString();
  return apiGet<OfferListResponse>(`/hr/offers${q ? `?${q}` : ''}`);
}

export function listEnrolmentOffers(enrolmentId: string): Promise<OfferOut[]> {
  return apiGet<OfferOut[]>(`/hr/enrolments/${pathId(enrolmentId)}/offers`);
}

export function createEnrolmentOffer(
  enrolmentId: string,
  body: OfferCreateInput,
): Promise<OfferOut> {
  return apiPost<OfferOut>(`/hr/enrolments/${pathId(enrolmentId)}/offers`, body);
}

export function getOffer(offerId: string): Promise<OfferDetail> {
  return apiGet<OfferDetail>(`/hr/offers/${pathId(offerId)}`);
}

export function updateOffer(offerId: string, fields: OfferFieldsInput): Promise<OfferOut> {
  return apiPatch<OfferOut>(`/hr/offers/${pathId(offerId)}`, fields);
}

export function submitOffer(offerId: string): Promise<OfferOut> {
  return apiPost<OfferOut>(`/hr/offers/${pathId(offerId)}/submit`, {});
}

export function recallOffer(offerId: string): Promise<OfferOut> {
  return apiPost<OfferOut>(`/hr/offers/${pathId(offerId)}/recall`, {});
}

export function reopenOffer(offerId: string): Promise<OfferOut> {
  return apiPost<OfferOut>(`/hr/offers/${pathId(offerId)}/reopen`, {});
}

export function sendOffer(offerId: string): Promise<OfferOut> {
  return apiPost<OfferOut>(`/hr/offers/${pathId(offerId)}/send`, {});
}

/** Retires the old link and lifts a code-attempt lock (security review M1). */
export function resendOffer(offerId: string): Promise<OfferOut> {
  return apiPost<OfferOut>(`/hr/offers/${pathId(offerId)}/resend`, {});
}

export function withdrawOffer(offerId: string, note?: string | null): Promise<OfferOut> {
  return apiPost<OfferOut>(`/hr/offers/${pathId(offerId)}/withdraw`, {
    note: note?.trim() || null,
  });
}

// ---------------------------------------------------------------------------
// Document requirements (per opening) — HR
// ---------------------------------------------------------------------------

export type DocType =
  | 'identity'
  | 'address'
  | 'education'
  | 'employment'
  | 'tax'
  | 'bank'
  | 'photo'
  | 'medical'
  | 'other';

export interface DocumentRequirement {
  id: string;
  name: string;
  doc_type: DocType;
  description: string | null;
  mandatory: boolean;
  requires_expiry: boolean;
  position: number;
}

export interface RequirementInput {
  name?: string;
  doc_type?: DocType;
  description?: string | null;
  mandatory?: boolean;
  requires_expiry?: boolean;
  position?: number;
}

export function listDocumentRequirements(requisitionId: string): Promise<DocumentRequirement[]> {
  return apiGet<DocumentRequirement[]>(
    `/hr/requisitions/${pathId(requisitionId)}/document-requirements`,
  );
}

export function addDocumentRequirement(
  requisitionId: string,
  body: RequirementInput,
): Promise<DocumentRequirement> {
  return apiPost<DocumentRequirement>(
    `/hr/requisitions/${pathId(requisitionId)}/document-requirements`,
    body,
  );
}

export function updateDocumentRequirement(
  requirementId: string,
  body: RequirementInput,
): Promise<DocumentRequirement> {
  return apiPatch<DocumentRequirement>(
    `/hr/document-requirements/${pathId(requirementId)}`,
    body,
  );
}

export function deleteDocumentRequirement(requirementId: string): Promise<void> {
  return apiDelete<void>(`/hr/document-requirements/${pathId(requirementId)}`);
}

// ---------------------------------------------------------------------------
// Documents + preboarding — HR review
// ---------------------------------------------------------------------------

export type DocumentState =
  | 'outstanding'
  | 'submitted'
  | 'verified'
  | 'rejected'
  | 'replacement_requested'
  | 'expired';

export interface DocumentInfo {
  id: string;
  version: number;
  file_name: string | null;
  content_type: string | null;
  size_bytes: number | null;
  expires_on: string | null;
  uploaded_at: string | null;
  reviewed_at: string | null;
  /** The reason for the candidate to act on — present only when rejected or a
   *  replacement was requested. */
  review_note: string | null;
  /** HR's own view only — never sent to the candidate. */
  reviewed_by?: string | null;
}

export interface ChecklistItem {
  requirement_id: string;
  name: string;
  doc_type: DocType;
  description: string | null;
  mandatory: boolean;
  requires_expiry: boolean;
  state: DocumentState;
  document: DocumentInfo | null;
}

export interface DocumentHistoryEntry {
  action: string;
  actor_type: string;
  actor_name: string | null;
  at: string;
  document_id: string | null;
  details: Record<string, unknown>;
}

export interface OfferDocuments {
  offer_status: OfferStatus;
  preboarding_completed_at: string | null;
  items: ChecklistItem[];
  outstanding: string[];
  complete_ready: boolean;
  history: DocumentHistoryEntry[];
  candidate_name: string;
  job_title: string;
}

export function getOfferDocuments(offerId: string): Promise<OfferDocuments> {
  return apiGet<OfferDocuments>(`/hr/offers/${pathId(offerId)}/documents`);
}

export type ReviewAction = 'verify' | 'reject' | 'request_replacement';

export function reviewDocument(
  documentId: string,
  action: ReviewAction,
  note?: string | null,
): Promise<{ document_id: string; state: string }> {
  return apiPost<{ document_id: string; state: string }>(
    `/hr/documents/${pathId(documentId)}/review`,
    { action, note: note?.trim() || null },
  );
}

/** POST, not GET — it records that this person opened it (5-minute signed link). */
export function downloadDocument(
  documentId: string,
): Promise<{ url: string; expires_in: number }> {
  return apiPost<{ url: string; expires_in: number }>(
    `/hr/documents/${pathId(documentId)}/download`,
    {},
  );
}

export function completePreboarding(offerId: string): Promise<OfferDocuments> {
  return apiPost<OfferDocuments>(`/hr/offers/${pathId(offerId)}/preboarding-complete`, {});
}

export interface HrmsExportResult {
  export_id: string;
  algorithm: string;
  key_id: string;
  signature: string;
  payload: Record<string, unknown>;
}

export function hrmsExport(offerId: string): Promise<HrmsExportResult> {
  return apiPost<HrmsExportResult>(`/hr/offers/${pathId(offerId)}/hrms-export`, {});
}

// ---------------------------------------------------------------------------
// Super admin — approval (decision D4-2)
// ---------------------------------------------------------------------------

export function listOfferApprovals(): Promise<OfferOut[]> {
  return apiGet<OfferOut[]>('/admin/offer-approvals');
}

export function getAdminOffer(offerId: string): Promise<OfferDetail> {
  return apiGet<OfferDetail>(`/admin/offers/${pathId(offerId)}`);
}

export function approveOffer(offerId: string, note?: string | null): Promise<OfferOut> {
  return apiPost<OfferOut>(`/admin/offers/${pathId(offerId)}/approve`, {
    note: note?.trim() || null,
  });
}

/** The service requires a note of at least 10 characters. */
export function rejectOffer(offerId: string, note: string): Promise<OfferOut> {
  return apiPost<OfferOut>(`/admin/offers/${pathId(offerId)}/reject`, { note: note.trim() });
}

// ---------------------------------------------------------------------------
// The candidate portal (signed in) — /users/me
// ---------------------------------------------------------------------------

/** What a signed-in candidate reads about their own offer — no approval trail. */
export interface CandidateOffer {
  id: string;
  status: OfferStatus;
  company: string | null;
  job_title: string;
  employment_type: EmploymentType | null;
  start_date: string | null;
  location: string | null;
  base_salary: string | null;
  currency: string | null;
  pay_period: PayPeriod | null;
  bonus: string | null;
  equity: string | null;
  benefits: string | null;
  terms: string | null;
  probation_months: number | null;
  notice_period_days: number | null;
  expires_at: string | null;
  responded_at: string | null;
  preboarding_completed_at: string | null;
}

export function listMyOffers(): Promise<CandidateOffer[]> {
  return apiGet<CandidateOffer[]>('/users/me/offers');
}

/**
 * A fresh link to the candidate's own offer — the old one retired. Validate
 * the result is same-origin with pathname `/offer` before navigating to it
 * (no open redirect): the URL is built server-side from `settings.app_base_url`,
 * which is trusted config, but a client that navigates anywhere it is told
 * without checking is the mistake, not the server here.
 */
export function getMyOfferLink(offerId: string): Promise<{ url: string }> {
  return apiPost<{ url: string }>(`/users/me/offers/${pathId(offerId)}/link`, {});
}
