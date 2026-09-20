// publicOffer.ts — the candidate's own offer, reached ONLY by the emailed
// link. NO LOGIN, so — same rule as publicApply.ts and the exam/interview
// magic-link clients — this module:
//
// 1. NEVER uses the central client (client.ts). There is no session to
//    refresh, and its 401 handling redirects to /login, which for a candidate
//    with no account would replace the offer they were reading with a
//    sign-in page.
// 2. Sends the link's credential as a HEADER, never a query string or path
//    segment, so it never lands in an access log or a Referer header. The
//    page itself reads it from the URL #fragment (see PublicOffer.tsx) —
//    fragments are never sent to a server at all.
//
// Documents need a SECOND credential once the offer is accepted: a session
// token traded for an emailed code, valid for an hour. It travels as
// X-Offer-Session alongside X-Offer-Token, and — like the offer token — is
// kept in memory only, never localStorage/sessionStorage/a URL.
//
// Field names match services/data_gateway/app/routers/offers.py's
// `public_router` and app/offers.py's `candidate_out` / `checklist(...,
// for_candidate=True)` EXACTLY.

import { pathId } from './pathId';
import { ApiError } from './client';
import type { DocType, DocumentState, EmploymentType, OfferStatus, PayPeriod } from './offers';

// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const API_BASE: string = import.meta.env.VITE_API_BASE_URL;

async function readError(res: Response): Promise<never> {
  const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
  const detail = typeof body.detail === 'string' ? body.detail : `HTTP ${res.status}`;
  throw new ApiError(detail, res.status);
}

function offerHeaders(token: string, extra?: Record<string, string>): HeadersInit {
  return { 'X-Offer-Token': token, ...(extra ?? {}) };
}

function documentsHeaders(
  token: string,
  session: string,
  extra?: Record<string, string>,
): HeadersInit {
  return { 'X-Offer-Token': token, 'X-Offer-Session': session, ...(extra ?? {}) };
}

/** What the candidate reads: their offer, never how it was approved. */
export interface PublicOffer {
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

export async function viewOffer(token: string): Promise<PublicOffer> {
  const res = await fetch(`${API_BASE}/offer`, { headers: offerHeaders(token) });
  if (!res.ok) return readError(res);
  return (await res.json()) as PublicOffer;
}

export type CodePurpose = 'accept' | 'decline';

export interface CodeSent {
  sent: boolean;
  minutes: number;
}

export async function requestOfferCode(token: string, purpose: CodePurpose): Promise<CodeSent> {
  const res = await fetch(`${API_BASE}/offer/code`, {
    method: 'POST',
    headers: offerHeaders(token, { 'Content-Type': 'application/json' }),
    body: JSON.stringify({ purpose }),
  });
  if (!res.ok) return readError(res);
  return (await res.json()) as CodeSent;
}

export async function acceptOffer(
  token: string,
  code: string,
  fullName: string,
  /** The page's language — an account made for them at acceptance takes it. */
  language?: 'en' | 'hi' | 'te',
): Promise<PublicOffer> {
  const res = await fetch(`${API_BASE}/offer/accept`, {
    method: 'POST',
    headers: offerHeaders(token, { 'Content-Type': 'application/json' }),
    body: JSON.stringify({ code, full_name: fullName, ...(language ? { language } : {}) }),
  });
  if (!res.ok) return readError(res);
  return (await res.json()) as PublicOffer;
}

export async function declineOffer(
  token: string,
  code: string,
  reason?: string,
): Promise<PublicOffer> {
  const res = await fetch(`${API_BASE}/offer/decline`, {
    method: 'POST',
    headers: offerHeaders(token, { 'Content-Type': 'application/json' }),
    body: JSON.stringify({ code, reason: reason?.trim() || null }),
  });
  if (!res.ok) return readError(res);
  return (await res.json()) as PublicOffer;
}

/** Emails a code that opens the documents checklist for an hour. */
export async function requestDocumentsCode(token: string): Promise<CodeSent> {
  const res = await fetch(`${API_BASE}/offer/documents/code`, {
    method: 'POST',
    headers: offerHeaders(token),
  });
  if (!res.ok) return readError(res);
  return (await res.json()) as CodeSent;
}

export interface DocumentsSession {
  /** Keep in memory ONLY — never localStorage, sessionStorage or a URL. */
  session_token: string;
  expires_at: string;
}

export async function openDocumentsSession(token: string, code: string): Promise<DocumentsSession> {
  const res = await fetch(`${API_BASE}/offer/documents/session`, {
    method: 'POST',
    headers: offerHeaders(token, { 'Content-Type': 'application/json' }),
    body: JSON.stringify({ code }),
  });
  if (!res.ok) return readError(res);
  return (await res.json()) as DocumentsSession;
}

/** The candidate's own read of one requirement — no `reviewed_by` (that is HR's). */
export interface PublicDocumentInfo {
  id: string;
  version: number;
  file_name: string | null;
  content_type: string | null;
  size_bytes: number | null;
  expires_on: string | null;
  uploaded_at: string | null;
  reviewed_at: string | null;
  review_note: string | null;
}

export interface PublicChecklistItem {
  requirement_id: string;
  name: string;
  doc_type: DocType;
  description: string | null;
  mandatory: boolean;
  requires_expiry: boolean;
  state: DocumentState;
  document: PublicDocumentInfo | null;
}

export interface PublicDocumentsChecklist {
  offer_status: OfferStatus;
  preboarding_completed_at: string | null;
  items: PublicChecklistItem[];
  outstanding: string[];
  complete_ready: boolean;
}

export async function getMyDocuments(
  token: string,
  session: string,
): Promise<PublicDocumentsChecklist> {
  const res = await fetch(`${API_BASE}/offer/documents`, {
    headers: documentsHeaders(token, session),
  });
  if (!res.ok) return readError(res);
  return (await res.json()) as PublicDocumentsChecklist;
}

/** Withdraw consent to share documents (DPDP §11) — nothing already sent is
 *  deleted, but nothing further can be. Needs the link and a live session, the
 *  same credential an upload needs. */
export async function withdrawDocumentsConsent(token: string, session: string): Promise<void> {
  const res = await fetch(`${API_BASE}/offer/documents/consent/withdraw`, {
    method: 'POST',
    headers: documentsHeaders(token, session),
  });
  if (!res.ok) return readError(res);
}

export interface UploadResult {
  document_id: string;
  version: number;
  state: string;
}

/** PDF/JPEG/PNG up to 10 MB, checked here for the candidate's sake — the
 *  server is the authority and checks by content, not by extension. */
export async function uploadMyDocument(
  token: string,
  session: string,
  requirementId: string,
  file: File,
  expiresOn?: string | null,
): Promise<UploadResult> {
  const form = new FormData();
  form.append('file', file);
  if (expiresOn) form.append('expires_on', expiresOn);
  // No Content-Type: the browser must set the multipart boundary.
  const res = await fetch(`${API_BASE}/offer/documents/${pathId(requirementId)}`, {
    method: 'POST',
    headers: documentsHeaders(token, session),
    body: form,
  });
  if (!res.ok) return readError(res);
  return (await res.json()) as UploadResult;
}
