// publicTask.ts — the candidate's own job-simulation/portfolio task, reached
// ONLY by the emailed link. NO LOGIN, so — same rule as publicOffer.ts and
// publicApply.ts — this module:
//
// 1. NEVER uses the central client (client.ts). There is no session to
//    refresh, and its 401 handling redirects to /login, which for a
//    candidate with no account would replace the task they were working on
//    with a sign-in page.
// 2. Sends the link's credential as a HEADER (X-Task-Token), never a query
//    string or path segment, so it never lands in an access log or a Referer
//    header. The page reads it from the URL #fragment (see PublicTask.tsx) —
//    fragments are never sent to a server at all.
//
// Field names match services/data_gateway/app/routers/job_tasks.py's
// `public_router` and app/job_tasks.py's `candidate_view` EXACTLY.

import { ApiError } from './client';
import type { TaskItem, TaskKind, TaskLinkKind, TaskResponseOut } from './jobTasks';

// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const API_BASE: string = import.meta.env.VITE_API_BASE_URL;

async function readError(res: Response): Promise<never> {
  const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
  const detail = typeof body.detail === 'string' ? body.detail : `HTTP ${res.status}`;
  throw new ApiError(detail, res.status);
}

function taskHeaders(token: string, extra?: Record<string, string>): HeadersInit {
  return { 'X-Task-Token': token, ...(extra ?? {}) };
}

export interface PublicTaskMaterial {
  id: string;
  title: string;
  original_name: string | null;
  content_type: string | null;
  size_bytes: number | null;
  position: number;
}

/**
 * `GET /task` (`app.job_tasks.by_token`) only ever returns a submission whose
 * status is one of these three — anything else (`expired`, `withdrawn`, or a
 * status this build has never heard of) reads as the SAME 404 every other
 * failure does, before `candidate_view` ever runs. Deliberately narrower than
 * `TaskSubmissionStatus` (jobTasks.ts), which is the wider HR/staff-facing
 * union that genuinely does include those two.
 */
export type PublicTaskStatus = 'assigned' | 'in_progress' | 'submitted';

/** What the candidate reads: their own task, never an evaluation of it. */
export interface PublicTask {
  status: PublicTaskStatus;
  kind: TaskKind;
  round_title: string | null;
  company: string | null;
  /** Already resolved to the candidate's language where HR gave a translation. */
  brief: string;
  items: TaskItem[];
  min_artifacts: number | null;
  max_artifacts: number | null;
  allow_files: boolean;
  allow_links: boolean;
  allowed_link_domains: string[] | null;
  materials: PublicTaskMaterial[];
  due_at: string | null;
  time_limit_seconds: number | null;
  started_at: string | null;
  submitted_at: string | null;
  /** PH4-D4 wave 5 — true once the candidate has withdrawn consent for this
   *  task, whether that happened while it was still `in_progress` or after
   *  it was `submitted`. Always false for `assigned` (nothing to withdraw
   *  yet — consent is only given at `startTask`). */
  consent_withdrawn: boolean;
  /** PH4-D2 — told as a fact only, the same rule publicExam.adjustmentNotice
   *  follows: never a percentage or a note. */
  adjustments: {
    extra_time_seconds: number | null;
    deadline_extended: boolean;
  };
  responses: TaskResponseOut[];
}

export async function viewTask(token: string): Promise<PublicTask> {
  const res = await fetch(`${API_BASE}/task`, { headers: taskHeaders(token) });
  if (!res.ok) return readError(res);
  return (await res.json()) as PublicTask;
}

/**
 * Begins the clock (`started_at`) AND is the moment consent is given —
 * security review, PH4-D4: nothing is ever stored (no autosaved answer, no
 * uploaded file) before this call succeeds, so consent has to exist before
 * it, not at submit. `consent` mirrors `submitTask`'s field name; the caller
 * gates this behind an explicit, checked checkbox (see PublicTask.tsx) and
 * must not call it otherwise. Refused (409) once the window has closed, and
 * (once the server catches up) refused without `consent: true`.
 */
export async function startTask(token: string, consent: boolean): Promise<PublicTask> {
  const res = await fetch(`${API_BASE}/task/start`, {
    method: 'POST',
    headers: taskHeaders(token, { 'Content-Type': 'application/json' }),
    body: JSON.stringify({ consent }),
  });
  if (!res.ok) return readError(res);
  return (await res.json()) as PublicTask;
}

export interface SaveResponseResult {
  item_key: string;
  saved: true;
}

/** Autosave one item's answer — a text answer or a link, never a file (a
 *  file item's answer goes through `addTaskArtifact` with its `itemKey`). */
export async function saveTaskResponse(
  token: string,
  itemKey: string,
  body: { text_value?: string | null; link_url?: string | null },
): Promise<SaveResponseResult> {
  const res = await fetch(`${API_BASE}/task/responses/${encodeURIComponent(itemKey)}`, {
    method: 'PUT',
    headers: taskHeaders(token, { 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      text_value: body.text_value ?? null,
      link_url: body.link_url ?? null,
    }),
  });
  if (!res.ok) return readError(res);
  return (await res.json()) as SaveResponseResult;
}

export interface AddArtifactResult {
  id: string;
}

/**
 * A file or an approved link. Two different things share this endpoint:
 *
 * - Without `itemKey`: a free-form portfolio artifact, counted against the
 *   round's `max_artifacts`. Only a portfolio round accepts these.
 * - With `itemKey` (files only): the answer to ONE item whose
 *   `response_type` is `"file"` — the one shape `saveTaskResponse` can never
 *   carry. It counts toward that item, not `max_artifacts`, in either kind
 *   of round, and uploading again REPLACES the item's earlier file (the
 *   server deletes the old one) rather than adding a second.
 */
export async function addTaskArtifact(
  token: string,
  body:
    | { kind: 'file'; file: File; itemKey?: string; title?: string; description?: string }
    | {
        kind: 'link';
        link_url: string;
        link_kind?: TaskLinkKind;
        title?: string;
        description?: string;
      },
): Promise<AddArtifactResult> {
  const form = new FormData();
  if (body.kind === 'file') {
    form.append('file', body.file);
    if (body.itemKey) form.append('item_key', body.itemKey);
  } else {
    form.append('link_url', body.link_url);
    if (body.link_kind) form.append('link_kind', body.link_kind);
  }
  if (body.title) form.append('title', body.title);
  if (body.description) form.append('description', body.description);
  // No Content-Type: the browser must set the multipart boundary.
  const res = await fetch(`${API_BASE}/task/artifacts`, {
    method: 'POST',
    headers: taskHeaders(token),
    body: form,
  });
  if (!res.ok) return readError(res);
  return (await res.json()) as AddArtifactResult;
}

export async function removeTaskArtifact(token: string, responseId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/task/artifacts/${encodeURIComponent(responseId)}`, {
    method: 'DELETE',
    headers: taskHeaders(token),
  });
  if (!res.ok) return readError(res);
}

/**
 * Sends the finished work to the hiring team.
 *
 * Consent was already given at `startTask` (security review, PH4-D4) — this
 * is a confirmation step, never a second consent gate, so the caller shows
 * "Send this to the hiring team?" rather than another checkbox. `consent` is
 * still sent `true` on the body (the field the server's current `SubmitIn`
 * schema still reads) so this keeps working across either side of the
 * backend's own change landing first. After this, the candidate never sees
 * a reviewer, a score, or a note; only the submission's own status (see
 * PublicTask.tsx).
 */
export async function submitTask(token: string): Promise<PublicTask> {
  const res = await fetch(`${API_BASE}/task/submit`, {
    method: 'POST',
    headers: taskHeaders(token, { 'Content-Type': 'application/json' }),
    body: JSON.stringify({ consent: true }),
  });
  if (!res.ok) return readError(res);
  return (await res.json()) as PublicTask;
}

export interface WithdrawConsentResult {
  withdrawn: true;
}

/**
 * The candidate withdraws their consent to send this task's work to the
 * hiring team (DPDP §11) — the same `X-Task-Token` credential as a save,
 * since most candidates here have no account to sign in with (mirrors
 * PublicOffer.tsx's document-consent withdrawal). Succeeds from either of
 * two states, each with a different effect:
 * - `in_progress`: stops `saveTaskResponse`, `addTaskArtifact` and
 *   `submitTask` from here on — the server refuses each of those with a 409
 *   once this has succeeded.
 * - `submitted` (PH4-D4 wave 5): the hiring team can no longer see the
 *   work, and it cannot be used to pass the round.
 * Either way, this deletes nothing already saved or sent: that is what
 * erasure and retention are for, not this call. A repeat call, or a task
 * never started, is a 409.
 */
export async function withdrawTaskConsent(token: string): Promise<WithdrawConsentResult> {
  const res = await fetch(`${API_BASE}/task/consent/withdraw`, {
    method: 'POST',
    headers: taskHeaders(token, { 'Content-Type': 'application/json' }),
  });
  if (!res.ok) return readError(res);
  return (await res.json()) as WithdrawConsentResult;
}

export interface SignedDownload {
  url: string;
  expires_in: number;
}

export async function downloadTaskMaterial(
  token: string,
  materialId: string,
): Promise<SignedDownload> {
  const res = await fetch(`${API_BASE}/task/materials/${encodeURIComponent(materialId)}/download`, {
    headers: taskHeaders(token),
  });
  if (!res.ok) return readError(res);
  return (await res.json()) as SignedDownload;
}
