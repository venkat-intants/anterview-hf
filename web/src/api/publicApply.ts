// publicApply.ts — the candidate-facing front door. NO LOGIN.
//
// Everything else in this app is behind auth. This is not: a stranger opens a
// link, reads a posting, and uploads a CV. Two consequences shape this module.
//
// 1. NO TOKEN, NO REFRESH. These calls deliberately bypass the central client's
//    401 → refresh → retry machinery. There is no session to refresh, and the
//    shared client's failure path redirects to /login — which for an applicant
//    would replace the form they were filling in with a sign-in page for an
//    account they do not have.
//
// 2. CONSENT IS A FIELD. The API refuses an application without it, and this
//    module never defaults it to true. The checkbox is the lawful basis for
//    storing the person's CV (DPDP Act 2023); sending it because the form was
//    submitted would make the record a lie.

import { ApiError } from './client';

const API_BASE: string = import.meta.env.VITE_API_BASE_URL;

export type QuestionKind =
  | 'short_text'
  | 'long_text'
  | 'number'
  | 'single_choice'
  | 'multi_choice'
  | 'yes_no';

/** A recruiter-defined question, as the candidate sees it. */
export interface PostingQuestion {
  id: string;
  prompt: string;
  kind: QuestionKind;
  help_text: string | null;
  required: boolean;
  options: string[];
}

/**
 * An answer, in the shape the server validates. A multi-choice is an array; a
 * yes/no is a boolean; everything else is a string, including numbers — the
 * input gives us text and the server parses it, so this does not guess.
 */
export type AnswerValue = string | boolean | string[];

/** The posting, as anyone with the link sees it. */
export interface Posting {
  requisition_id: string;
  title: string;
  level: string;
  company_name: string;
  jd_text: string | null;
  closes_at: string | null;
  // The advert. Everything below is candidate-facing by definition — it is
  // what HR wrote in order to be read by strangers — with one exception.
  department: string | null;
  location: string | null;
  employment_type: string | null;
  experience_min_years: number | null;
  experience_max_years: number | null;
  responsibilities: string[];
  required_skills: string[];
  nice_to_have_skills: string[];
  // The exception. Null here means "not disclosed", and the server sends null
  // for both a withheld band and an unrecorded one — deliberately, so this
  // page cannot tell the difference and therefore cannot leak it.
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
  /**
   * What this opening asks, in the order HR put them in — already ordered, so
   * never sort by anything here. Empty for most openings.
   */
  questions: PostingQuestion[];
  /**
   * The acquisition channel this view was tracked under (PH3-B1), already
   * normalised by the server. Echoed rather than re-derived here so there is
   * one implementation of the vocabulary and it is the server's — a client
   * that normalised `?src=` itself would be a second one, and the two would
   * eventually disagree about what "linkedin" means.
   *
   * Carry it to submitApplication. Nothing is stored on the view itself; a
   * page view is not an application.
   */
  source: string;
  source_detail: string | null;
}

export interface ApplicationResult {
  applicant_id: string;
  enrolment_id: string | null;
  full_name: string;
  /** True when this email had already applied. Not an error — a reassurance. */
  already_applied: boolean;
  message: string;
}

async function readError(res: Response): Promise<never> {
  const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
  const detail = typeof body.detail === 'string' ? body.detail : `HTTP ${res.status}`;
  throw new ApiError(detail, res.status);
}

/**
 * Fetch the posting. 404 covers every reason an opening is not taking
 * applications — closed, paused, never published publicly, or not a real id —
 * because distinguishing them would let anyone with a URL enumerate a
 * company's private roles.
 */
export async function getPosting(requisitionId: string, src?: string | null): Promise<Posting> {
  // The tracked link is this GET: a recruiter shares /apply/<id>?src=linkedin.
  // Passed through unvalidated — the server never refuses a malformed campaign
  // tag, because losing a real applicant to a recruiter's typo is a far worse
  // outcome than an unattributed application.
  const qs = src ? `?src=${encodeURIComponent(src)}` : '';
  const res = await fetch(`${API_BASE}/apply/${requisitionId}${qs}`);
  if (!res.ok) return readError(res);
  return (await res.json()) as Posting;
}

export interface ApplicationInput {
  fullName: string;
  email: string;
  resume: File;
  /** The applicant's own act. Never defaulted. */
  consentGranted: boolean;
  /** The language of the emails about this application. English when omitted. */
  language?: 'en' | 'hi' | 'te';
  /**
   * The rest of the multi-step form. Every one optional, deliberately: a
   * required field here turns "I would rather not say what I earn now" into
   * "you may not apply". Omitted fields are left out of the request entirely
   * rather than sent empty — on a second application the server fills gaps
   * without overwriting, and an empty string would wipe last time's answer.
   */
  phone?: string;
  yearsExperience?: number | null;
  currentCompany?: string;
  currentTitle?: string;
  linkedinUrl?: string;
  githubUrl?: string;
  /** Keyed by question id. Omitted entirely when the opening asks nothing. */
  answers?: Record<string, AnswerValue>;
  /**
   * The channel from the posting fetch (PH3-B1). Send back what `Posting.source`
   * said rather than the raw `?src=` — by submit time the original parameter is
   * several screens back, and the server has already told us what it will
   * record.
   */
  source?: string;
  sourceDetail?: string | null;
}

export async function submitApplication(
  requisitionId: string,
  input: ApplicationInput,
): Promise<ApplicationResult> {
  const form = new FormData();
  form.append('full_name', input.fullName);
  form.append('email', input.email);
  form.append('resume', input.resume);
  form.append('consent_granted', String(input.consentGranted));
  if (input.language) form.append('language', input.language);
  // The normalised channel goes back as `src`; the server normalises again,
  // which is why sending the already-normalised value is safe and sending
  // nothing simply means "direct".
  if (input.source) form.append('src', input.source);

  // Only what they actually answered. An untouched input is '' and appending
  // that would be an answer — see the note on ApplicationInput.
  const optional: Record<string, string | undefined> = {
    phone: input.phone,
    current_company: input.currentCompany,
    current_title: input.currentTitle,
    linkedin_url: input.linkedinUrl,
    github_url: input.githubUrl,
    years_experience:
      input.yearsExperience === null || input.yearsExperience === undefined
        ? undefined
        : String(input.yearsExperience),
  };
  for (const [key, value] of Object.entries(optional)) {
    if (value !== undefined && value.trim() !== '') form.append(key, value.trim());
  }

  // JSON inside a form field because the request is multipart — it carries a
  // file, and multipart has no way to express a nested object.
  if (input.answers && Object.keys(input.answers).length > 0) {
    form.append('answers', JSON.stringify(input.answers));
  }

  // No Content-Type header: the browser must set the multipart boundary.
  const res = await fetch(`${API_BASE}/apply/${requisitionId}`, {
    method: 'POST',
    body: form,
  });
  if (!res.ok) return readError(res);
  return (await res.json()) as ApplicationResult;
}

// ---------------------------------------------------------------------------
// Account activation
//
// Applying creates a placeholder account so the consent ledger has a user to
// hang off. These two calls turn it into one the applicant can sign in to,
// which is what makes the applications page possible. Still no session — the
// emailed token is the only credential, so they belong in this module rather
// than with the authenticated clients.
// ---------------------------------------------------------------------------

/** Who a link belongs to, so the page can greet them before they type. */
export interface ActivationTarget {
  full_name: string;
  email: string;
  job_title: string;
  company_name: string;
}

export interface ActivationResult {
  email: string;
  /**
   * 'existing' when this address already had an account and the application
   * was attached to it — the person signs in with the password they already
   * have, and the one they just typed is not used. 'new' when the applicant's
   * own placeholder became the account.
   */
  linked: 'existing' | 'new';
  message: string;
}

/**
 * Check a link without consuming it, so an expired one is reported on arrival
 * rather than after somebody has chosen a password.
 */
export async function getActivationTarget(token: string): Promise<ActivationTarget> {
  const res = await fetch(`${API_BASE}/apply/activate/target?token=${encodeURIComponent(token)}`);
  if (!res.ok) return readError(res);
  return (await res.json()) as ActivationTarget;
}

export async function activateAccount(
  token: string,
  newPassword: string,
): Promise<ActivationResult> {
  const res = await fetch(`${API_BASE}/apply/activate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, new_password: newPassword }),
  });
  if (!res.ok) return readError(res);
  return (await res.json()) as ActivationResult;
}


// ---------------------------------------------------------------------------
// Save & resume, and the confirmation step — PH3-B4c / PH3-B5
//
// CONSENT COMES FIRST HERE. Starting a draft stores the candidate's email, so
// the consent checkbox moves to that moment rather than to submission. The
// server refuses a draft without it; this client does not try to be clever
// about that, it just sends what the person ticked.
//
// The resume token is the only credential. It is returned once, by
// startDraft, and is never recoverable from the server — the database holds
// only its hash. A caller that loses it has lost the draft.
// ---------------------------------------------------------------------------

/** What the CV parser read, for the candidate to check. */
export interface ParsedDetails {
  full_name: string | null;
  email: string | null;
}

export interface ApplicationDraft {
  requisition_id: string;
  title: string;
  company_name: string;
  email: string;
  full_name: string | null;
  phone: string | null;
  years_experience: number | null;
  current_company: string | null;
  current_title: string | null;
  linkedin_url: string | null;
  github_url: string | null;
  language: string;
  answers: Record<string, AnswerValue>;
  resume_filename: string | null;
  has_resume: boolean;
  /** What we read off the CV. Every field may be null — an unparsed field
   *  renders as an empty editable box and never blocks the application. */
  parsed: ParsedDetails;
  confirmed: boolean;
  expires_at: string;
}

export interface DraftStarted {
  /** Returned ONCE. Not recoverable — the server stores only its hash. */
  resume_token: string;
  draft: ApplicationDraft;
}

export interface DraftFields {
  full_name?: string | null;
  phone?: string | null;
  years_experience?: number | null;
  current_company?: string | null;
  current_title?: string | null;
  linkedin_url?: string | null;
  github_url?: string | null;
  language?: 'en' | 'hi' | 'te';
  answers?: Record<string, AnswerValue>;
}

export async function startDraft(
  requisitionId: string,
  input: { email: string; consentGranted: boolean; language?: 'en' | 'hi' | 'te'; src?: string | null },
): Promise<DraftStarted> {
  const res = await fetch(`${API_BASE}/apply/${requisitionId}/draft`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      email: input.email,
      consent_granted: input.consentGranted,
      language: input.language ?? 'en',
      src: input.src ?? null,
    }),
  });
  if (!res.ok) return readError(res);
  return (await res.json()) as DraftStarted;
}

/**
 * The resume token travels in a HEADER, never in the URL.
 *
 * It is a live credential to the holder's name, phone, employer, screening
 * answers and CV. In a path it lands in the server's access log, the Space and
 * Railway edge logs, browser history and any cross-origin `Referer`. This is
 * the pattern the exam and interview clients already use (`X-Exam-Token`,
 * `X-Interview-Token`); the shared link carries it in the URL *fragment*,
 * which browsers do not send to servers, and the page moves it into this
 * header.
 */
function draftHeaders(token: string, extra?: Record<string, string>): HeadersInit {
  return { 'X-Draft-Token': token, ...(extra ?? {}) };
}

export async function getDraft(token: string): Promise<ApplicationDraft> {
  const res = await fetch(`${API_BASE}/apply/draft`, { headers: draftHeaders(token) });
  if (!res.ok) return readError(res);
  return (await res.json()) as ApplicationDraft;
}

export async function saveDraft(
  token: string,
  fields: DraftFields,
): Promise<ApplicationDraft> {
  const res = await fetch(`${API_BASE}/apply/draft`, {
    method: 'PATCH',
    headers: draftHeaders(token, { 'Content-Type': 'application/json' }),
    body: JSON.stringify(fields),
  });
  if (!res.ok) return readError(res);
  return (await res.json()) as ApplicationDraft;
}

/**
 * Confirm the details read off the CV — PH3-B5.
 *
 * Whatever is sent here WINS over what the parser produced: a name read out of
 * a PDF is a guess and a person's own answer is not.
 */
export async function confirmDraft(
  token: string,
  corrections: DraftFields,
): Promise<ApplicationDraft> {
  const res = await fetch(`${API_BASE}/apply/draft/confirm`, {
    method: 'POST',
    headers: draftHeaders(token, { 'Content-Type': 'application/json' }),
    body: JSON.stringify(corrections),
  });
  if (!res.ok) return readError(res);
  return (await res.json()) as ApplicationDraft;
}

/**
 * Throw the saved application away — the data principal exercising erasure on
 * their own draft, which DPDP gives them a right to DO rather than merely wait
 * thirty days for. The CV object goes with the row.
 */
export async function deleteDraft(token: string): Promise<void> {
  const res = await fetch(`${API_BASE}/apply/draft`, {
    method: 'DELETE',
    headers: draftHeaders(token),
  });
  if (!res.ok) return readError(res);
}

export async function uploadDraftResume(
  token: string,
  resume: File,
): Promise<ApplicationDraft> {
  const form = new FormData();
  form.append('resume', resume);
  // No Content-Type: the browser must set the multipart boundary.
  const res = await fetch(`${API_BASE}/apply/draft/resume-upload`, {
    method: 'POST',
    headers: draftHeaders(token),
    body: form,
  });
  if (!res.ok) return readError(res);
  return (await res.json()) as ApplicationDraft;
}

export async function submitDraft(token: string): Promise<ApplicationResult> {
  const res = await fetch(`${API_BASE}/apply/draft/submit`, {
    method: 'POST',
    headers: draftHeaders(token),
  });
  if (!res.ok) return readError(res);
  return (await res.json()) as ApplicationResult;
}
