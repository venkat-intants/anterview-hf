// CorpusDocuments (/hr/library, /superadmin/library) — PH5-E2. The
// company's own document library (policies, handbooks, process notes) that
// the staff copilot can search and cite. Not a candidate record: nothing
// here is keyed on an applicant (services/data_gateway/app/corpus.py).
//
// Route path matches the API's own `/hr/library` (renamed from `/hr/documents`
// — that collided with offers.py's candidate preboarding documents, an
// unrelated entity; see api/corpus.ts's header comment). The citation route
// table (`web/src/api/agent.ts`'s `CITATION_ROUTES.document`) points at
// `/hr/library/{id}`, which App.tsx mounts on THIS component in both consoles:
// a document citation opens the library with that row scrolled to and focused
// (`useDeepLinkedRow`), and says so plainly when the row is not there any more.
// This comment used to claim that already, while the route did not exist and
// every document chip opened the 404 page — hence
// `src/__tests__/citationRoutes.test.ts`, which now checks each citation route
// against the routes App.tsx actually declares.
//
// Shared, unmodified, by hr_manager and super_admin — the SERVER refuses an
// `hr_only` document from a super_admin (422) as a HARD RULE, not a default
// this screen happens to agree with; this screen mirrors that by simply not
// offering the option to one, deriving the restriction from the caller's own
// role rather than a prop, so the same component is exactly what both
// consoles render (design doc §6 "Super admin → Documents: the same
// component").
//
// No in-browser preview, ever. Every document leaves through a short-lived
// signed link with `Content-Disposition: attachment`
// (app/document_storage.py) — a deliberate isolation control (AR-6), not a
// missing feature. Do not add an iframe or a PDF viewer here.
//
// English-only by design (CLAUDE.md — staff consoles are not translated).

import { Fragment, useRef, useState, type FormEvent } from 'react';
import { useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '@/context/AuthContext';
import { ApiError } from '@/api/client';
import { useDeepLinkedRow } from '@/hooks/useDeepLinkedRow';
import { downloadUrl } from '@/lib/safeUrl';
import { ACTIVE_POLL_MS } from '@/lib/polling';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { GlassCard, StatusTag, type TagTone } from '@/design/components/primitives';
import { Reveal } from '@/design/components/Reveal';
import {
  AlertTriangle,
  Download,
  Loader2,
  Pencil,
  RefreshCw,
  Upload as UploadIcon,
} from '@/design/components/icons';
import {
  corpusErrorMessage,
  corpusFailureSentence,
  corpusListHasPendingVersion,
  deleteCorpusDocument,
  editCorpusDocument,
  getCorpusDownloadUrl,
  getCorpusSemanticStatus,
  listCorpusDocuments,
  reindexCorpusDocument,
  replaceCorpusDocument,
  searchCorpusDocuments,
  uploadCorpusDocument,
  type CorpusAudience,
  type CorpusDocument,
  type CorpusVersionStatus,
} from '@/api/corpus';

const ACCEPT =
  '.pdf,.docx,.txt,.md,application/pdf,' +
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document,' +
  'text/plain,text/markdown';

const AUDIENCE_LABEL: Record<CorpusAudience, string> = {
  all_staff: 'All staff',
  hr_only: 'HR only',
};
const AUDIENCE_TONE: Record<CorpusAudience, TagTone> = {
  all_staff: 'neutral',
  hr_only: 'lavender',
};

const STATUS_LABEL: Record<CorpusVersionStatus, string> = {
  parsed: 'Indexing…',
  indexing: 'Indexing…',
  indexed: 'Ready',
  failed: 'Failed',
};
const STATUS_TONE: Record<CorpusVersionStatus, TagTone> = {
  parsed: 'amber',
  indexing: 'amber',
  indexed: 'forest',
  failed: 'ember',
};

const CONTENT_TYPE_LABEL: Record<string, string> = {
  'application/pdf': 'PDF',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'DOCX',
  'text/plain': 'TXT',
  'text/markdown': 'MD',
};

function fileTypeLabel(contentType: string | null): string {
  return contentType ? (CONTENT_TYPE_LABEL[contentType] ?? contentType) : '—';
}

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleDateString() : '—';
}

const DELETE_CONFIRM_TEXT =
  "This removes it from the assistant's search immediately and deletes its text.";
const INJECTION_WARNING_TEXT =
  'This document contains text that tries to instruct an automated reader. It is ignored.';
const SEMANTIC_UNAVAILABLE_TEXT =
  'Semantic search is unavailable — the assistant is matching keywords only.';
// A document citation whose row is no longer in the library: deleted, or (for a
// super admin following an `hr_only` link) not theirs to read. Both are "not in
// your library" from here, and neither should look like an empty screen.
const CITED_DOC_MISSING_TEXT =
  'That document is no longer in your library — it may have been deleted since it was cited.';
const ATTESTATION_LABEL = 'This is a company document, not a record about a candidate.';

/** DOM id for one library row, so a citation can land on it. */
function rowDomId(documentId: string): string {
  return `corpus-doc-${documentId}`;
}

export default function CorpusDocuments(): JSX.Element {
  const { user } = useAuth();
  const roles = user?.roles ?? [];
  // Set when this is a document citation (`/hr/library/{id}` or
  // `/superadmin/library/{id}`), absent on the plain library route.
  const { documentId: citedDocumentId } = useParams<{ documentId?: string }>();
  // The only two roles this route ever renders for (HRRoute / SuperAdminRoute)
  // are hr_manager and super_admin — a super_admin cannot create or read back
  // an `hr_only` document (app/corpus.py CORPUS_AUDIENCE_ROLES, design Q3).
  const canChooseHrOnly = roles.includes('hr_manager');

  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ['hr', 'documents'],
    queryFn: listCorpusDocuments,
    // A just-uploaded document sits at "Indexing…" until the background
    // reconciler embeds it (app/reconciliation.py::_corpus_embed_pass), which
    // nothing on this page otherwise learns about without a manual reload.
    // Poll only while that is true, and stop the moment every row has
    // settled (indexed or failed) — an idle library should make no requests
    // (lib/polling.ts's ACTIVE_POLL_MS, the "watch something finish" cadence).
    refetchInterval: (query) => (corpusListHasPendingVersion(query.state.data) ? ACTIVE_POLL_MS : false),
  });
  const invalidate = () => void qc.invalidateQueries({ queryKey: ['hr', 'documents'] });

  // ---- Upload ----
  const [uploadTitle, setUploadTitle] = useState('');
  const [uploadAudience, setUploadAudience] = useState<CorpusAudience>(
    canChooseHrOnly ? 'hr_only' : 'all_staff',
  );
  const [uploadExpiresOn, setUploadExpiresOn] = useState('');
  const [uploadAttested, setUploadAttested] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const uploadFileInputRef = useRef<HTMLInputElement>(null);

  const uploadMut = useMutation({
    mutationFn: () => {
      if (!uploadFile) throw new Error('Choose a file to upload.');
      return uploadCorpusDocument(
        {
          file: uploadFile,
          title: uploadTitle.trim(),
          audience: canChooseHrOnly ? uploadAudience : 'all_staff',
          expiresOn: uploadExpiresOn || null,
          attested: uploadAttested,
        },
        setUploadProgress,
      );
    },
    onSuccess: () => {
      toast.success('Document uploaded');
      setUploadTitle('');
      setUploadExpiresOn('');
      setUploadAttested(false);
      setUploadAudience(canChooseHrOnly ? 'hr_only' : 'all_staff');
      setUploadFile(null);
      setUploadProgress(null);
      if (uploadFileInputRef.current) uploadFileInputRef.current.value = '';
      invalidate();
    },
    onError: (e: unknown) => {
      setUploadProgress(null);
      toast.error(corpusErrorMessage(e, 'Could not upload this document'));
    },
  });

  // The attestation is a control we committed to (design §4.5): the button
  // stays disabled until it is ticked, not merely rejected on submit.
  const canSubmitUpload =
    !!uploadFile && uploadTitle.trim().length > 0 && uploadAttested && !uploadMut.isPending;

  function handleUploadSubmit(e: FormEvent): void {
    e.preventDefault();
    if (!canSubmitUpload) return;
    uploadMut.mutate();
  }

  // ---- Replace (a new version — never an overwrite) ----
  const replaceMut = useMutation({
    mutationFn: ({ id, file }: { id: string; file: File }) => replaceCorpusDocument(id, file),
    onSuccess: () => {
      toast.success('New version uploaded');
      invalidate();
    },
    onError: (e: unknown) => toast.error(corpusErrorMessage(e, 'Could not upload a new version')),
  });

  // ---- Retry indexing (a version parked at `failed`) ----
  //
  // Only a real retry because the server clears the parking record as well as
  // the status — see `reindexCorpusDocument`'s own comment. Invalidating is
  // what moves the row back to "Indexing…" and restarts the poll above, so the
  // result arrives without a reload.
  const reindexMut = useMutation({
    mutationFn: (id: string) => reindexCorpusDocument(id),
    onSuccess: () => {
      toast.success('Indexing again');
      invalidate();
    },
    onError: (e: unknown) => {
      // A 409 ("not stuck") is the interesting case: the screen was stale, and
      // the server's own sentence says so better than a generic failure would.
      // Refresh either way, so the row stops offering an action it cannot do.
      toast.error(corpusErrorMessage(e, 'Could not retry indexing this document'));
      invalidate();
    },
  });

  // ---- Edit (audience, expiry) ----
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editAudience, setEditAudience] = useState<CorpusAudience>('all_staff');
  const [editExpiresOn, setEditExpiresOn] = useState('');

  function startEdit(doc: CorpusDocument): void {
    setEditingId(doc.id);
    setEditAudience(canChooseHrOnly ? doc.audience : 'all_staff');
    setEditExpiresOn(doc.expires_on ?? '');
  }

  const editMut = useMutation({
    mutationFn: (input: { id: string; audience: CorpusAudience; expiresOn: string | null }) =>
      editCorpusDocument(input.id, { audience: input.audience, expiresOn: input.expiresOn }),
    onSuccess: () => {
      toast.success('Document updated');
      setEditingId(null);
      invalidate();
    },
    onError: (e: unknown) => toast.error(corpusErrorMessage(e, 'Could not update this document')),
  });

  // ---- Delete (immediate, complete purge) ----
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteCorpusDocument(id),
    onSuccess: () => {
      toast.success('Document deleted');
      setConfirmDeleteId(null);
      invalidate();
    },
    onError: (e: unknown) => toast.error(corpusErrorMessage(e, 'Could not delete this document')),
  });

  // ---- Download (signed link, download only — no preview) ----
  const downloadMut = useMutation({
    mutationFn: (id: string) => getCorpusDownloadUrl(id),
    onSuccess: (res) => {
      const url = downloadUrl(res.url);
      if (!url) {
        toast.error('That download link could not be opened.');
        return;
      }
      window.open(url, '_blank', 'noopener,noreferrer');
    },
    onError: (e: unknown) => toast.error(corpusErrorMessage(e, 'Could not open this document')),
  });

  // ---- Semantic-search availability banner. `GET /agent/status`'s
  // `corpus_semantic` (cached briefly server-side) is the on-load signal, so
  // the banner does not need a search to appear. A search's own `semantic`
  // flag is the freshest signal once one has run — reacting to it too means a
  // mid-session outage (or recovery) still surfaces without a page reload. ----
  const statusQuery = useQuery({
    queryKey: ['agent', 'status', 'corpus-semantic'],
    queryFn: getCorpusSemanticStatus,
    staleTime: 60_000,
  });

  // ---- Search — this screen's own box; the copilot reaches the corpus
  // through a separate tool (app/routers/hr_corpus.py::search_documents
  // docstring). ----
  const [query, setQuery] = useState('');
  const searchMut = useMutation({
    mutationFn: (q: string) => searchCorpusDocuments(q),
    onError: (e: unknown) => toast.error(corpusErrorMessage(e, 'Could not search your documents')),
  });

  function handleSearchSubmit(e: FormEvent): void {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    searchMut.mutate(q);
  }

  // The search response is the freshest evidence once one exists; before that,
  // fall back to the standing status flag from page load.
  const semanticUnavailable =
    searchMut.data !== undefined
      ? !searchMut.data.semantic
      : statusQuery.data
        ? !statusQuery.data.corpus_semantic
        : false;

  const docs: CorpusDocument[] = list.data ?? [];

  // ---- A document citation (`/hr/library/{id}`) ----
  //
  // The library is one bounded list, so the cited row is either in it or gone —
  // no second fetch is needed, and `getCorpusDocument` would only tell us the
  // same thing twice. Land on the row when it is there; say so plainly when it
  // is not, because a deleted or expired document is a fact the reader needs,
  // not silence.
  const citedDoc = citedDocumentId ? docs.find((d) => d.id === citedDocumentId) : undefined;
  useDeepLinkedRow(citedDoc ? rowDomId(citedDoc.id) : null, list.isSuccess);
  const citedDocMissing = Boolean(citedDocumentId) && list.isSuccess && citedDoc === undefined;

  return (
    <div className="mx-auto max-w-[1080px] px-0 py-2 space-y-6">
      <Reveal>
        <div>
          <h1 className="text-[28px] font-semibold tracking-[-1px] text-foreground">Documents</h1>
          <p className="mt-1 text-[14px] text-muted-foreground">
            Company policies, handbooks and process notes the staff assistant can search and cite
            — never a candidate record.
          </p>
        </div>
      </Reveal>

      {semanticUnavailable ? (
        <div
          role="status"
          className="flex items-center gap-2 rounded-[10px] border border-[var(--ui-warn)]/30 bg-[rgba(255,183,100,0.12)] px-3 py-2 text-[12.5px] text-[var(--ui-warn)]"
        >
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {SEMANTIC_UNAVAILABLE_TEXT}
        </div>
      ) : null}

      {citedDocMissing ? (
        <div
          role="status"
          className="flex items-center gap-2 rounded-[10px] border border-border bg-[var(--ui-inset-soft)] px-3 py-2 text-[12.5px] text-muted-foreground"
        >
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {CITED_DOC_MISSING_TEXT}
        </div>
      ) : null}

      {/* Upload */}
      <Reveal delay={0.05}>
        <GlassCard className="p-5 space-y-3">
          <h3 className="flex items-center gap-2 text-[15px] font-semibold text-foreground">
            <UploadIcon size={16} aria-hidden="true" />
            Upload a document
          </h3>
          <form className="grid gap-3 sm:grid-cols-2" onSubmit={handleUploadSubmit}>
            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="corpus-upload-title"
                className="text-[12px] font-medium text-[var(--ui-soft)]"
              >
                Title
              </label>
              <input
                id="corpus-upload-title"
                value={uploadTitle}
                onChange={(e) => setUploadTitle(e.target.value)}
                maxLength={200}
                placeholder="e.g. Employee handbook"
                className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="corpus-upload-file"
                className="text-[12px] font-medium text-[var(--ui-soft)]"
              >
                File
              </label>
              <input
                id="corpus-upload-file"
                ref={uploadFileInputRef}
                type="file"
                accept={ACCEPT}
                onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
                className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground"
              />
              <span className="text-[11px] text-[var(--ui-faint)]">
                PDF, Word (.docx), plain text or Markdown, up to 10 MB.
              </span>
            </div>

            <fieldset className="flex flex-col gap-1.5">
              <legend className="text-[12px] font-medium text-[var(--ui-soft)]">Audience</legend>
              {canChooseHrOnly ? (
                <div className="flex items-center gap-4">
                  <label className="flex items-center gap-1.5 text-[13px] text-foreground">
                    <input
                      type="radio"
                      name="corpus-upload-audience"
                      value="hr_only"
                      checked={uploadAudience === 'hr_only'}
                      onChange={() => setUploadAudience('hr_only')}
                    />
                    HR only
                  </label>
                  <label className="flex items-center gap-1.5 text-[13px] text-foreground">
                    <input
                      type="radio"
                      name="corpus-upload-audience"
                      value="all_staff"
                      checked={uploadAudience === 'all_staff'}
                      onChange={() => setUploadAudience('all_staff')}
                    />
                    All staff
                  </label>
                </div>
              ) : (
                <p className="text-[13px] text-foreground">
                  All staff
                  <span className="ml-1.5 text-[11.5px] text-muted-foreground">
                    — HR-only documents are created by HR managers.
                  </span>
                </p>
              )}
            </fieldset>

            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="corpus-upload-expiry"
                className="text-[12px] font-medium text-[var(--ui-soft)]"
              >
                Expiry date (optional)
              </label>
              <input
                id="corpus-upload-expiry"
                type="date"
                value={uploadExpiresOn}
                onChange={(e) => setUploadExpiresOn(e.target.value)}
                className="rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
              />
            </div>

            <label className="sm:col-span-2 flex items-start gap-2 text-[12.5px] text-foreground">
              <input
                type="checkbox"
                checked={uploadAttested}
                onChange={(e) => setUploadAttested(e.target.checked)}
                className="mt-0.5"
              />
              {ATTESTATION_LABEL}
            </label>

            <div className="sm:col-span-2 flex items-center gap-3">
              <button
                type="submit"
                disabled={!canSubmitUpload}
                className="rounded-[10px] bg-primary px-4 py-2 text-[13px] font-semibold text-primary-foreground disabled:opacity-50"
              >
                {uploadMut.isPending ? 'Uploading…' : 'Upload'}
              </button>
              {uploadMut.isPending && uploadProgress !== null ? (
                <span className="text-[12px] text-muted-foreground">{uploadProgress}%</span>
              ) : null}
            </div>
          </form>
        </GlassCard>
      </Reveal>

      {/* Search */}
      <Reveal delay={0.08}>
        <GlassCard className="p-5 space-y-3">
          <h3 className="text-[15px] font-semibold text-foreground">Search your documents</h3>
          <form className="flex flex-wrap items-center gap-2" onSubmit={handleSearchSubmit}>
            <label htmlFor="corpus-search-query" className="sr-only">
              Search your documents
            </label>
            <input
              id="corpus-search-query"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. notice period"
              className="min-w-0 flex-1 rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none"
            />
            <button
              type="submit"
              disabled={searchMut.isPending || !query.trim()}
              className="rounded-[10px] border border-border px-3 py-2 text-[13px] text-foreground disabled:opacity-40"
            >
              {searchMut.isPending ? 'Searching…' : 'Search'}
            </button>
          </form>

          {searchMut.data && searchMut.data.passages.length > 0 ? (
            <ul className="flex flex-col gap-2">
              {searchMut.data.passages.map((p, i) => (
                <li key={`${p.document_id}-${i}`} className="rounded-[10px] border border-border p-3">
                  <p className="text-[12.5px] font-medium text-foreground">
                    {p.title} · v{p.version}
                    {p.page ? ` · page ${p.page}` : ''}
                    {p.heading ? ` · ${p.heading}` : ''}
                  </p>
                  <p className="mt-1 text-[12px] text-muted-foreground">{p.text}</p>
                  {p.contains_instructions ? (
                    <p className="mt-1.5 flex items-center gap-1.5 text-[11.5px] text-[var(--ui-warn)]">
                      <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                      {INJECTION_WARNING_TEXT}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}

          {searchMut.data && searchMut.data.passages.length === 0 ? (
            <p className="text-[12px] text-muted-foreground">No matching passages.</p>
          ) : null}
        </GlassCard>
      </Reveal>

      {/* Library */}
      <Reveal delay={0.1}>
        <GlassCard className="p-5">
          <h3 className="mb-4 text-[15px] font-semibold text-foreground">Library</h3>

          {list.isLoading ? (
            <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              Loading…
            </p>
          ) : list.isError && list.error instanceof ApiError && list.error.status === 403 ? (
            <p className="py-6 text-center text-[13px] text-foreground">
              {list.error.message || "You don't have access to this company's documents."}
            </p>
          ) : list.isError ? (
            <p className="text-[13px] text-[var(--ui-danger)]">
              {corpusErrorMessage(list.error, 'Could not load documents.')}
            </p>
          ) : docs.length === 0 ? (
            <p className="py-6 text-center text-[13px] text-muted-foreground">No documents yet</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left text-[12.5px]">
                <thead>
                  <tr className="border-b border-border text-[11px] uppercase tracking-wide text-[var(--ui-faint)]">
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Title
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Audience
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Type
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Version
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Status
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Updated
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Uploaded by
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {docs.map((d) => {
                    const status = d.status ?? 'parsed';
                    return (
                      <Fragment key={d.id}>
                        <tr
                          // The id + tabIndex are the landing target for a
                          // document citation (`useDeepLinkedRow`); the ring
                          // makes "this is the row you were sent to" visible
                          // rather than something only the scroll position says.
                          id={rowDomId(d.id)}
                          tabIndex={-1}
                          className={cn(
                            'border-b border-border align-top outline-none',
                            citedDoc?.id === d.id &&
                              'bg-[var(--ui-inset-soft)] ring-1 ring-inset ring-[var(--accent)]',
                          )}
                        >
                          <td className="py-2.5 pr-3">
                            <p className="font-medium text-foreground">{d.title}</p>
                            {d.original_name ? (
                              <p className="mt-0.5 text-[11px] text-muted-foreground">
                                {d.original_name}
                              </p>
                            ) : null}
                          </td>
                          <td className="py-2.5 pr-3">
                            <StatusTag tone={AUDIENCE_TONE[d.audience]}>
                              {AUDIENCE_LABEL[d.audience]}
                            </StatusTag>
                          </td>
                          <td className="py-2.5 pr-3 text-foreground">
                            {fileTypeLabel(d.content_type)}
                          </td>
                          <td className="py-2.5 pr-3 text-foreground">
                            {d.version ? `v${d.version}` : '—'}
                          </td>
                          <td className="py-2.5 pr-3">
                            <StatusTag tone={STATUS_TONE[status]} dot>
                              {STATUS_LABEL[status]}
                            </StatusTag>
                            {status === 'failed' ? (
                              // The sentence AND the retry. This comment used to
                              // say `hr_corpus.py` exposed no route that could
                              // drive a Retry, which was true when it was
                              // written and is not now: `POST /hr/library/{id}/
                              // reindex` exists and also clears the
                              // `reconciliation_state` parking row, without
                              // which a status reset alone would be a no-op (the
                              // next embed pass skips anything with `gave_up_at`
                              // set). Still NOT a client-side workaround —
                              // re-uploading the same file would create a new
                              // version instead of retrying this one.
                              <>
                                <p className="mt-1.5 max-w-[220px] text-[11.5px] text-[var(--ui-danger)]">
                                  {corpusFailureSentence(d.failure_code)}
                                </p>
                                <button
                                  type="button"
                                  onClick={() => reindexMut.mutate(d.id)}
                                  disabled={reindexMut.isPending}
                                  aria-label={`Retry indexing ${d.title}`}
                                  className="mt-1.5 inline-flex items-center gap-1 rounded-[8px] border border-border px-2 py-1 text-[11.5px] text-foreground disabled:opacity-40"
                                >
                                  <RefreshCw
                                    className={cn(
                                      'h-3 w-3',
                                      reindexMut.isPending &&
                                        reindexMut.variables === d.id &&
                                        'animate-spin',
                                    )}
                                    aria-hidden="true"
                                  />
                                  Retry
                                </button>
                              </>
                            ) : null}
                            {d.injection_markers ? (
                              <p className="mt-1.5 flex max-w-[220px] items-start gap-1 text-[11px] text-[var(--ui-warn)]">
                                <AlertTriangle
                                  className="mt-0.5 h-3 w-3 shrink-0"
                                  aria-hidden="true"
                                />
                                {INJECTION_WARNING_TEXT}
                              </p>
                            ) : null}
                          </td>
                          <td className="py-2.5 pr-3 text-muted-foreground">
                            {fmtDate(d.updated_at)}
                          </td>
                          <td className="py-2.5 pr-3 text-muted-foreground">
                            {d.uploaded_by_name ?? '—'}
                          </td>
                          <td className="py-2.5 pr-3">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <button
                                type="button"
                                onClick={() => downloadMut.mutate(d.id)}
                                disabled={downloadMut.isPending}
                                className="inline-flex items-center gap-1 rounded-[8px] border border-border px-2 py-1 text-[11.5px] text-foreground disabled:opacity-40"
                              >
                                <Download className="h-3 w-3" aria-hidden="true" />
                                Download
                              </button>

                              <label className="inline-flex cursor-pointer items-center gap-1 rounded-[8px] border border-border px-2 py-1 text-[11.5px] text-foreground">
                                <RefreshCw className="h-3 w-3" aria-hidden="true" />
                                Replace…
                                <input
                                  type="file"
                                  accept={ACCEPT}
                                  aria-label={`Replace ${d.title}`}
                                  className="sr-only"
                                  onChange={(e) => {
                                    const f = e.target.files?.[0];
                                    e.target.value = '';
                                    if (f) replaceMut.mutate({ id: d.id, file: f });
                                  }}
                                />
                              </label>

                              <button
                                type="button"
                                onClick={() => startEdit(d)}
                                className="inline-flex items-center gap-1 rounded-[8px] border border-border px-2 py-1 text-[11.5px] text-foreground"
                              >
                                <Pencil className="h-3 w-3" aria-hidden="true" />
                                Edit
                              </button>

                              {confirmDeleteId === d.id ? (
                                <span
                                  role="group"
                                  aria-label={`Confirm deleting ${d.title}`}
                                  className="flex flex-col gap-1"
                                >
                                  <span className="text-[11px] text-[var(--ui-danger)]">
                                    {DELETE_CONFIRM_TEXT}
                                  </span>
                                  <span className="flex gap-1.5">
                                    <button
                                      type="button"
                                      onClick={() => deleteMut.mutate(d.id)}
                                      disabled={deleteMut.isPending}
                                      className="rounded-[8px] bg-[#e6714f]/20 px-2 py-1 text-[11px] font-semibold text-[#ff8a66] disabled:opacity-50"
                                    >
                                      {deleteMut.isPending ? '…' : 'Delete'}
                                    </button>
                                    <button
                                      type="button"
                                      onClick={() => setConfirmDeleteId(null)}
                                      className="rounded-[8px] bg-[var(--ui-inset)] px-2 py-1 text-[11px] text-[var(--ui-soft)]"
                                    >
                                      Cancel
                                    </button>
                                  </span>
                                </span>
                              ) : (
                                <button
                                  type="button"
                                  onClick={() => setConfirmDeleteId(d.id)}
                                  className="inline-flex items-center gap-1 rounded-[8px] border border-[#e6714f]/30 px-2 py-1 text-[11.5px] text-[#ff8a66]"
                                >
                                  Delete
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                        {editingId === d.id ? (
                          <tr className="border-b border-border bg-[var(--ui-inset-soft)]">
                            <td colSpan={8} className="p-3">
                              <form
                                className="flex flex-wrap items-end gap-3"
                                onSubmit={(e) => {
                                  e.preventDefault();
                                  editMut.mutate({
                                    id: d.id,
                                    audience: canChooseHrOnly ? editAudience : 'all_staff',
                                    expiresOn: editExpiresOn || null,
                                  });
                                }}
                              >
                                <fieldset className="flex flex-col gap-1.5">
                                  <legend className="text-[11.5px] font-medium text-[var(--ui-soft)]">
                                    Audience
                                  </legend>
                                  {canChooseHrOnly ? (
                                    <div className="flex items-center gap-3">
                                      <label className="flex items-center gap-1.5 text-[12.5px] text-foreground">
                                        <input
                                          type="radio"
                                          name={`edit-audience-${d.id}`}
                                          checked={editAudience === 'hr_only'}
                                          onChange={() => setEditAudience('hr_only')}
                                        />
                                        HR only
                                      </label>
                                      <label className="flex items-center gap-1.5 text-[12.5px] text-foreground">
                                        <input
                                          type="radio"
                                          name={`edit-audience-${d.id}`}
                                          checked={editAudience === 'all_staff'}
                                          onChange={() => setEditAudience('all_staff')}
                                        />
                                        All staff
                                      </label>
                                    </div>
                                  ) : (
                                    <p className="text-[12.5px] text-foreground">All staff</p>
                                  )}
                                </fieldset>
                                <div className="flex flex-col gap-1.5">
                                  <label
                                    htmlFor={`edit-expiry-${d.id}`}
                                    className="text-[11.5px] font-medium text-[var(--ui-soft)]"
                                  >
                                    Expiry date
                                  </label>
                                  <input
                                    id={`edit-expiry-${d.id}`}
                                    type="date"
                                    value={editExpiresOn}
                                    onChange={(e) => setEditExpiresOn(e.target.value)}
                                    className="rounded-[8px] border border-border bg-secondary px-2.5 py-1.5 text-[12.5px] text-foreground"
                                  />
                                </div>
                                <button
                                  type="submit"
                                  disabled={editMut.isPending}
                                  className="rounded-[8px] bg-primary px-3 py-1.5 text-[12px] font-medium text-primary-foreground disabled:opacity-50"
                                >
                                  {editMut.isPending ? 'Saving…' : 'Save'}
                                </button>
                                <button
                                  type="button"
                                  onClick={() => setEditingId(null)}
                                  className="rounded-[8px] border border-border px-3 py-1.5 text-[12px] text-muted-foreground hover:text-foreground"
                                >
                                  Cancel
                                </button>
                              </form>
                            </td>
                          </tr>
                        ) : null}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </GlassCard>
      </Reveal>
    </div>
  );
}
