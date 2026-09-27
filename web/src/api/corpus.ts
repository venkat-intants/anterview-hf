// corpus.ts — PH5-E2. The company's own document library (policies,
// handbooks, process notes) that the HR/super-admin console manages and the
// staff copilot searches (`search_company_documents`, being built in this
// same wave — this file is the HR console's OWN CRUD + search box, a
// separate route the copilot does not call).
//
// Field names and error shapes match
// services/data_gateway/app/routers/hr_corpus.py and app/corpus.py EXACTLY —
// this file was written against that code, not against the design sketch.
//
// Routes live under `/hr/library`, NOT `/hr/documents` (its working name
// until the security review) — `/hr/documents/{id}` was already `offers.py`'s
// candidate preboarding documents (candidate PII), an unrelated entity that
// would have shared a URL namespace with this one, distinguished only by
// HTTP verb. The corpus moved rather than the other way round (LOW-8).
//
// `GET /agent/status` carries `corpus_semantic: boolean` (design §9 Q14), but
// the shared `AgentStatus` type in `web/src/api/agent.ts` — owned by another
// wave's agent — does not declare that field yet. Rather than edit a file
// this module doesn't own, `getCorpusSemanticStatus` below hits the same
// endpoint with its own narrow local type for just this one field.
//
// English-only by design (CLAUDE.md — staff consoles are not translated).

import { apiDelete, apiGet, apiPatch, apiPost, ApiError, uploadWithProgress } from './client';
import { pathId } from './pathId';

// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const API_BASE: string = import.meta.env.VITE_API_BASE_URL;

export type CorpusAudience = 'all_staff' | 'hr_only';
export type CorpusDocKind = 'policy' | 'handbook' | 'process' | 'jd_library' | 'other';
export type CorpusVersionStatus = 'parsed' | 'indexing' | 'indexed' | 'failed';

/**
 * One row of `GET /hr/library` (and the shape `GET /hr/library/{id}`,
 * `POST /hr/library` and `POST /hr/library/{id}/versions` also return) —
 * the document's identity plus its CURRENT version's facts. There is no
 * separate "document" vs "version" response shape (`app/corpus.py::
 * _document_out`).
 */
export interface CorpusDocument {
  id: string;
  title: string;
  audience: CorpusAudience;
  doc_kind: CorpusDocKind;
  expires_on: string | null;
  created_at: string;
  updated_at: string;
  version: number | null;
  status: CorpusVersionStatus | null;
  failure_code: string | null;
  original_name: string | null;
  content_type: string | null;
  size_bytes: number | null;
  page_count: number | null;
  chunk_count: number | null;
  injection_markers: number | null;
  uploaded_at: string | null;
  /** A display name, or null when that account no longer exists. */
  uploaded_by_name: string | null;
}

export interface CorpusSearchPassage {
  document_id: string;
  title: string;
  version: number;
  page: number | null;
  heading: string | null;
  text: string;
  contains_instructions: boolean;
}

/** `POST /hr/library/search` response (`app/corpus.py::search_corpus`). */
export interface CorpusSearchResult {
  semantic: boolean;
  passages: CorpusSearchPassage[];
}

export interface CorpusDownload {
  url: string;
  expires_in: number;
}

// ---------------------------------------------------------------------------
// The closed `failure_code` vocabulary a version can carry
// (services/data_gateway/app/corpus.py::FAILURE_CODES / FAILURE_SENTENCES).
//
// Deliberately duplicated here rather than fetched from the server: the copy
// lives beside the screen that shows it, on the design's own instruction, so
// a reviewer editing the sentence a user sees does not have to also open the
// backend module. `quota_exceeded` and `embedding_unavailable` are the only
// two of these that can appear on an already-listed row (every other code is
// a 422 at upload time, before any row exists) — the map still covers all
// eleven so a future backend change that writes a different code here does
// not silently show nothing.
// ---------------------------------------------------------------------------
export const CORPUS_FAILURE_SENTENCES: Record<string, string> = {
  unsupported_type: 'We take PDF, Word (.docx), plain text and Markdown files.',
  too_large: 'The file is larger than 10 MB.',
  active_content:
    'This PDF contains scripts or embedded files. Save or print it as a plain PDF and upload that.',
  encrypted: 'This PDF is password-protected. Remove the password and upload it again.',
  no_text: 'This looks like a scan. We read text, not images — upload a text PDF.',
  parse_timeout: 'We could not read this file in time. Try a smaller or simpler document.',
  parse_error: 'We could not read this file.',
  too_long: 'This document is longer than we index (about 400,000 characters). Split it.',
  too_many_chunks: 'This document is longer than we index (about 400,000 characters). Split it.',
  quota_exceeded: 'Your library is full (200 documents). Delete one first.',
  embedding_unavailable:
    'Search indexing is unavailable. The document is stored and will be indexed automatically.',
};

/** The sentence for a row whose `failure_code` is set. Falls back to a
 *  generic sentence for a code this file does not (yet) recognise, rather
 *  than showing nothing or the raw code. */
export function corpusFailureSentence(code: string | null | undefined): string {
  if (!code) return 'This document could not be processed.';
  return CORPUS_FAILURE_SENTENCES[code] ?? 'This document could not be processed.';
}

/**
 * Non-terminal version statuses — a row in one of these updates on its own,
 * without a page reload, once the background reconciler embeds it
 * (`app/reconciliation.py::_corpus_embed_pass`). `corpusListHasPendingVersion`
 * below is how the Library screen decides whether to keep polling.
 */
const PENDING_VERSION_STATUSES: ReadonlySet<CorpusVersionStatus> = new Set(['parsed', 'indexing']);

/** True while any document in the list is still being indexed — the screen
 *  polls on this and stops the moment it goes false, so an idle library
 *  makes no requests. */
export function corpusListHasPendingVersion(docs: CorpusDocument[] | undefined): boolean {
  return !!docs?.some((d) => d.status !== null && PENDING_VERSION_STATUSES.has(d.status));
}

/**
 * A human sentence for an error thrown by any call below.
 *
 * Every `CorpusError` the server raises already carries one in
 * `detail.message` (`app/corpus.py::CorpusError`/`_corpus_error`), so
 * `ApiError.message` (via `client.ts`'s `splitDetail`) is already
 * human-readable — including for codes outside `FAILURE_CODES`, such as
 * `attestation_required` or `hr_only_requires_hr_manager`, which this file
 * does not otherwise know about and must still show verbatim (design: "the
 * UI ... should surface the server's message if it ever arrives"). Preferring
 * OUR sentence for a code we DO recognise keeps the on-screen copy from
 * drifting from `CORPUS_FAILURE_SENTENCES` above if the server's wording ever
 * changes independently.
 */
export function corpusErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    const detail = error.detail as { failure_code?: string; message?: string } | undefined;
    const code = detail?.failure_code;
    if (code && CORPUS_FAILURE_SENTENCES[code]) return CORPUS_FAILURE_SENTENCES[code];
    return error.message || fallback;
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

// ---------------------------------------------------------------------------
// Routes — services/data_gateway/app/routers/hr_corpus.py, mounted at
// `/hr/library`. Shared by hr_manager and super_admin; the server (not this
// file) refuses an hr_only document from a super_admin — for an upload that
// is a hard rule (422), never a client-side default that happens to agree.
// ---------------------------------------------------------------------------

export function listCorpusDocuments(): Promise<CorpusDocument[]> {
  return apiGet<CorpusDocument[]>('/hr/library');
}

export function getCorpusDocument(documentId: string): Promise<CorpusDocument> {
  return apiGet<CorpusDocument>(`/hr/library/${pathId(documentId)}`);
}

export interface UploadCorpusDocumentInput {
  file: File;
  title: string;
  audience: CorpusAudience;
  /** 'YYYY-MM-DD', or null/omitted for no expiry. */
  expiresOn?: string | null;
  attested: boolean;
}

/**
 * `POST /hr/library` — multipart, rate-limited per company (server-side,
 * fixed 60 s window; `app/rate_limit.py::rate_limit_context`). A 429 here
 * carries the server's own sentence as a plain string detail — `client.ts`
 * promotes it straight to `ApiError.message`, and `corpusErrorMessage` below
 * already prefers that over any fallback, so no special-casing is needed to
 * show it rather than a generic failure.
 *
 * `doc_kind` is left to the server's `'other'` default; this screen does not
 * collect it (out of the wave-3 scope per the design's §6 sketch and the
 * task brief, neither of which lists a document-type field for the upload
 * dialog).
 */
export function uploadCorpusDocument(
  input: UploadCorpusDocumentInput,
  onProgress?: (pct: number) => void,
): Promise<CorpusDocument> {
  const form = new FormData();
  form.append('file', input.file);
  form.append('title', input.title);
  form.append('audience', input.audience);
  form.append('attested', input.attested ? 'true' : 'false');
  if (input.expiresOn) form.append('expires_on', input.expiresOn);
  return uploadWithProgress<CorpusDocument>(`${API_BASE}/hr/library`, form, onProgress);
}

/**
 * `POST /hr/library/{id}/versions` — a replace-upload, also rate-limited per
 * company (same bucket/window as upload — see `uploadCorpusDocument`).
 * Always inserts a NEW version; never overwrites the one it supersedes
 * (`app/corpus.py::add_version`). Takes only a file — title and audience are
 * document-level, not per-version.
 */
export function replaceCorpusDocument(
  documentId: string,
  file: File,
  onProgress?: (pct: number) => void,
): Promise<CorpusDocument> {
  const form = new FormData();
  form.append('file', file);
  return uploadWithProgress<CorpusDocument>(
    `${API_BASE}/hr/library/${pathId(documentId)}/versions`,
    form,
    onProgress,
  );
}

export interface EditCorpusDocumentInput {
  audience: CorpusAudience;
  expiresOn: string | null;
}

/**
 * `PATCH /hr/library/{id}`. Sends the FULL desired state: the server's
 * `DocumentEditIn` requires `audience` on every call, and `expires_on: null`
 * means "no expiry" rather than "leave unchanged"
 * (`app/corpus.py::update_document` docstring) — callers must pre-fill both
 * fields from the current row before letting a person change just one.
 */
export function editCorpusDocument(
  documentId: string,
  input: EditCorpusDocumentInput,
): Promise<CorpusDocument> {
  return apiPatch<CorpusDocument>(`/hr/library/${pathId(documentId)}`, {
    audience: input.audience,
    expires_on: input.expiresOn,
  });
}

/**
 * `POST /hr/library/{id}/reindex` — retry the CURRENT version of a document
 * parked at `failed`, which nothing else can un-park: the reconciler's embed
 * pass only ever selects versions still `parsed`/`indexing`
 * (`app/reconciliation.py::_corpus_embed_pass`), so once `mark_version_failed`
 * wrote `failed` a transient embedder outage had bricked the document for good
 * — while the row's own `embedding_unavailable` sentence went on promising it
 * "will be indexed automatically".
 *
 * The server resets the version to `parsed`, clears `failure_code`, AND deletes
 * the `reconciliation_state` parking row whose `gave_up_at` would otherwise
 * make the next pass skip it anyway (`app/corpus.py::reindex_document`) — which
 * is why this is a real retry and not a status cosmetic. Invalidate the list
 * after it resolves: the row goes back to "Indexing…" and the screen's poll
 * restarts on its own.
 *
 * Returns the updated document. Raises `ApiError` 409 `not_failed` ("This
 * document is not stuck — there is nothing to reindex.") when the current
 * version is in any other state — a stale screen clicking Retry on a row that
 * has since been re-uploaded or has already recovered. `corpusErrorMessage`
 * surfaces that sentence verbatim, since `not_failed` is deliberately not in
 * `CORPUS_FAILURE_SENTENCES` (it is not a failure of the document).
 */
export function reindexCorpusDocument(documentId: string): Promise<CorpusDocument> {
  return apiPost<CorpusDocument>(`/hr/library/${pathId(documentId)}/reindex`, {});
}

/**
 * `DELETE /hr/library/{id}` — immediate, complete purge: chunks,
 * embeddings and the stored object are gone at once, not after a grace
 * window (`app/corpus.py::delete_document`). Matches the confirmation
 * copy this screen shows before calling it.
 */
export function deleteCorpusDocument(documentId: string): Promise<void> {
  return apiDelete<void>(`/hr/library/${pathId(documentId)}`);
}

/** `GET /hr/library/{id}/download` — a short-lived signed link, download
 *  only (`Content-Disposition: attachment`). Never render it in an iframe or
 *  a viewer — that is the isolation control, not an oversight. */
export function getCorpusDownloadUrl(documentId: string, version?: number): Promise<CorpusDownload> {
  const qs = version ? `?version=${version}` : '';
  return apiGet<CorpusDownload>(`/hr/library/${pathId(documentId)}/download${qs}`);
}

/**
 * `POST /hr/library/search` — this screen's own search box. The copilot
 * reaches the same retrieval through the separate `search_company_documents`
 * tool, built in this same wave, not through this route
 * (`app/routers/hr_corpus.py::search_documents` docstring).
 */
export function searchCorpusDocuments(query: string, limit = 4): Promise<CorpusSearchResult> {
  return apiPost<CorpusSearchResult>('/hr/library/search', { query, limit });
}

/**
 * The one field this screen needs from `GET /agent/status` — whether the
 * corpus's semantic search is up right now (cached briefly server-side).
 * A narrow LOCAL type rather than the shared `AgentStatus` in
 * `web/src/api/agent.ts` (see this file's header comment): `apiGet` does not
 * care that the real response carries more fields than this interface
 * declares.
 */
export interface CorpusSemanticStatus {
  corpus_semantic: boolean;
}

export function getCorpusSemanticStatus(): Promise<CorpusSemanticStatus> {
  return apiGet<CorpusSemanticStatus>('/agent/status');
}
