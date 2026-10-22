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

import { Fragment, useEffect, useRef, useState, type FormEvent } from 'react';
import { useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '@/context/AuthContext';
import { ApiError } from '@/api/client';
import { useDeepLinkedRow } from '@/hooks/useDeepLinkedRow';
import { downloadUrl } from '@/lib/safeUrl';
import { ACTIVE_POLL_MS } from '@/lib/polling';
import { toast } from '@/lib/toast';
import { cn } from '@/lib/utils';
import { invalidateAfterHrMutation } from '@/lib/queryKeys';
import { Button } from '@/components/ui/button';
import { ConfirmDeleteButton } from '@/components/ConfirmDeleteButton';
import { QueryErrorPanel } from '@/components/QueryState';
import { Skeleton } from '@/components/ui/skeleton';
import { StatusTag, type TagTone } from '@/design/components/primitives';
import { PageHeader, SectionCard, EmptyState, Toolbar, Page } from '@/design/components/page';
import { Reveal } from '@/design/components/Reveal';
import {
  AlertTriangle,
  Download,
  Pencil,
  RefreshCw,
  Upload as UploadIcon,
} from '@/design/components/icons';
import {
  corpusErrorMessage,
  corpusFailureSentence,
  corpusFileProblem,
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
import { formatDate } from '@/lib/formatters';

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
  return contentType ? (CONTENT_TYPE_LABEL[contentType] ?? contentType) : '-';
}

// The confirm button says what actually happens: the text goes and search
// stops finding it at once, not after a grace window (app/corpus.py::
// delete_document).
const DELETE_CONFIRM_TEXT = 'Delete and remove from search';
const DELETE_TITLE =
  "Deleting removes it from the assistant's search immediately and deletes its text.";
// What an expiry date does, said where the date is picked (audit PL-F8): the
// nightly purge deletes the document permanently once the date has passed.
const EXPIRY_HELP_TEXT = 'The document is deleted automatically after this date.';
const EXPIRED_NOTE_TEXT = 'No longer searchable. It is deleted tonight.';
/** The server's search query limit (hr_corpus.py `SearchIn.query`). */
const SEARCH_MAX = 500;
/** The server's title limit (corpus.py `ingest_document`). */
const TITLE_MAX = 200;
const INJECTION_WARNING_TEXT =
  'This document contains text that tries to instruct an automated reader. It is ignored.';
const SEMANTIC_UNAVAILABLE_TEXT =
  'Semantic search is unavailable - the assistant is matching keywords only.';
// A document citation whose row is no longer in the library: deleted, or (for a
// super admin following an `hr_only` link) not theirs to read. Both are "not in
// your library" from here, and neither should look like an empty screen.
const CITED_DOC_MISSING_TEXT =
  'That document is no longer in your library - it may have been deleted since it was cited.';
const ATTESTATION_LABEL = 'This is a company document, not a record about a candidate.';

/** DOM id for one library row, so a citation can land on it. */
function rowDomId(documentId: string): string {
  return `corpus-doc-${documentId}`;
}

/** Today as the viewer's own calendar day, 'YYYY-MM-DD' — the shape a date
 *  input's `min` and the API's `expires_on` both use. */
function todayIso(): string {
  const d = new Date();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${mm}-${dd}`;
}

/** An `expires_on` calendar date for display. Built as a LOCAL date: handing
 *  the bare 'YYYY-MM-DD' to `new Date` reads it as UTC midnight, which is the
 *  previous day west of Greenwich. */
function formatCalendarDate(value: string | null): string {
  const m = value ? /^(\d{4})-(\d{2})-(\d{2})$/.exec(value) : null;
  if (!m) return '-';
  return formatDate(new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).toISOString());
}

/** True once a document's expiry day has passed: retrieval already excludes
 *  it (corpus.py `search_corpus`) and the nightly purge deletes it. */
function isExpired(doc: CorpusDocument, today: string): boolean {
  return doc.expires_on !== null && doc.expires_on < today;
}

const ROW_ACTION_CLASS =
  'inline-flex items-center gap-1 rounded-[8px] border border-border px-2 py-1 text-[12px] text-foreground hover:bg-[var(--ui-inset-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-40';

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
    refetchInterval: (query) =>
      corpusListHasPendingVersion(query.state.data) ? ACTIVE_POLL_MS : false,
  });
  const invalidate = () => void invalidateAfterHrMutation(qc);
  const today = todayIso();

  // ---- Upload ----
  const [uploadTitle, setUploadTitle] = useState('');
  const [uploadAudience, setUploadAudience] = useState<CorpusAudience>(
    canChooseHrOnly ? 'hr_only' : 'all_staff',
  );
  const [uploadExpiresOn, setUploadExpiresOn] = useState('');
  const [uploadAttested, setUploadAttested] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadFileError, setUploadFileError] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const uploadFileInputRef = useRef<HTMLInputElement>(null);

  // Size, emptiness and extension are checked as the file is picked, so a
  // 50 MB file is not sent in full only to be refused (audit F15).
  function pickUploadFile(file: File | null): void {
    const problem = file ? corpusFileProblem(file) : null;
    setUploadFileError(problem);
    setUploadFile(problem ? null : file);
    if (problem && uploadFileInputRef.current) uploadFileInputRef.current.value = '';
  }

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
      setUploadFileError(null);
      setUploadProgress(null);
      if (uploadFileInputRef.current) uploadFileInputRef.current.value = '';
      invalidate();
    },
    onError: (e: unknown) => {
      setUploadProgress(null);
      toast.error(corpusErrorMessage(e, 'Could not upload this document.'));
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
  //
  // Picking a file no longer uploads it on the spot (audit PL-F7). It opens a
  // confirm row carrying the same attestation the first upload requires, which
  // the server now enforces too, with progress, and with the control disabled
  // while a version is on its way so a second pick cannot make two versions.
  const [replacing, setReplacing] = useState<{ id: string; file: File } | null>(null);
  const [replaceAttested, setReplaceAttested] = useState(false);
  const [replaceProgress, setReplaceProgress] = useState<number | null>(null);
  // When the confirm row closes, focus goes back to the Replace control it
  // came from rather than dropping to the page (audit F29).
  const replaceInputs = useRef(new Map<string, HTMLInputElement>());
  const [replaceReturnId, setReplaceReturnId] = useState<string | null>(null);
  const replaceAttestRef = useRef<HTMLInputElement>(null);
  const replacingId = replacing?.id ?? null;
  useEffect(() => {
    if (!replacingId) return;
    // Deferred a tick: closing the file picker hands focus back to the file
    // input after its change event, which would otherwise win.
    const timer = window.setTimeout(() => replaceAttestRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [replacingId]);
  useEffect(() => {
    if (!replaceReturnId) return;
    replaceInputs.current.get(replaceReturnId)?.focus();
    setReplaceReturnId(null);
  }, [replaceReturnId]);

  function closeReplace(): void {
    if (replacing) setReplaceReturnId(replacing.id);
    setReplacing(null);
    setReplaceAttested(false);
  }

  function pickReplaceFile(id: string, file: File | undefined): void {
    if (!file) return;
    const problem = corpusFileProblem(file);
    if (problem) {
      toast.error(problem);
      return;
    }
    setReplaceAttested(false);
    setReplacing({ id, file });
  }

  const replaceMut = useMutation({
    mutationFn: ({ id, file }: { id: string; file: File }) =>
      replaceCorpusDocument(id, file, replaceAttested, setReplaceProgress),
    onSuccess: () => {
      toast.success('New version uploaded');
      closeReplace();
      setReplaceProgress(null);
      invalidate();
    },
    onError: (e: unknown) => {
      setReplaceProgress(null);
      toast.error(corpusErrorMessage(e, 'Could not upload a new version.'));
    },
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
      toast.error(corpusErrorMessage(e, 'Could not retry indexing this document.'));
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
    onError: (e: unknown) => toast.error(corpusErrorMessage(e, 'Could not update this document.')),
  });

  // ---- Delete (immediate, complete purge) ----
  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteCorpusDocument(id),
    onSuccess: () => {
      toast.success('Document deleted');
      invalidate();
    },
    onError: (e: unknown) => toast.error(corpusErrorMessage(e, 'Could not delete this document.')),
  });

  // ---- Download (signed link, download only — no preview) ----
  //
  // Same-tab navigation, not `window.open`: the link arrives after an awaited
  // request, outside the click, where popup blockers (Safari's especially)
  // stop a new window (audit F16). The signed link is
  // `Content-Disposition: attachment`, so the browser saves the file and the
  // page stays where it is.
  const downloadMut = useMutation({
    mutationFn: (id: string) => getCorpusDownloadUrl(id),
    onSuccess: (res) => {
      const url = downloadUrl(res.url);
      if (!url) {
        toast.error('That download link could not be opened.');
        return;
      }
      window.location.assign(url);
    },
    onError: (e: unknown) => toast.error(corpusErrorMessage(e, 'Could not download this document.')),
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
    onError: (e: unknown) => toast.error(corpusErrorMessage(e, 'Could not search your documents.')),
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
    <Page width="default">
      <PageHeader
        title="Document library"
        description="Company policies, handbooks and process notes the staff assistant can search and cite - never a candidate record."
      />

      {semanticUnavailable ? (
        <div
          role="status"
          className="flex items-center gap-2 rounded-[12px] border border-[var(--ui-warn)] bg-[var(--ui-warn-wash)] px-3 py-2.5 text-[13px] text-foreground"
        >
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {SEMANTIC_UNAVAILABLE_TEXT}
        </div>
      ) : null}

      {citedDocMissing ? (
        <div
          role="status"
          className="flex items-center gap-2 rounded-[12px] border border-border bg-[var(--ui-inset-soft)] px-3 py-2.5 text-[13px] text-muted-foreground"
        >
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {CITED_DOC_MISSING_TEXT}
        </div>
      ) : null}

      {/* Search — find a passage across the library. */}
      <div>
        <Toolbar>
          <form className="flex w-full flex-wrap items-center gap-2" onSubmit={handleSearchSubmit}>
            <label htmlFor="corpus-search-query" className="sr-only">
              Search your documents
            </label>
            <input
              id="corpus-search-query"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              maxLength={SEARCH_MAX}
              placeholder="Search your documents, e.g. notice period"
              className="min-h-[40px] min-w-0 flex-1 rounded-[10px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            />
            <Button
              type="submit"
              variant="outline"
              disabled={searchMut.isPending || !query.trim()}
            >
              {searchMut.isPending ? 'Searching…' : 'Search'}
            </Button>
          </form>
        </Toolbar>

        <div className="space-y-3">
          {searchMut.data && searchMut.data.passages.length > 0 ? (
            <ul className="flex flex-col gap-2">
              {searchMut.data.passages.map((p, i) => (
                <li
                  key={`${p.document_id}-${i}`}
                  className="rounded-[10px] border border-border p-3"
                >
                  <p className="text-[12.5px] font-medium text-foreground">
                    {p.title} · v{p.version}
                    {p.page ? ` · page ${p.page}` : ''}
                    {p.heading ? ` · ${p.heading}` : ''}
                  </p>
                  <p className="mt-1 text-[12px] text-muted-foreground">{p.text}</p>
                  {p.contains_instructions ? (
                    <p className="mt-1.5 flex items-center gap-1.5 text-[12px] text-[var(--ui-warn)]">
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
        </div>
      </div>

      {/* Library */}
      <Reveal delay={0.1}>
        <SectionCard title="Library">
          {list.isLoading ? (
            <div className="space-y-2" aria-busy="true" aria-label="Loading documents">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : list.isError && list.error instanceof ApiError && list.error.status === 403 ? (
            <p className="py-6 text-center text-[13px] text-foreground">
              {list.error.message || "You don't have access to this company's documents."}
            </p>
          ) : list.isError ? (
            <QueryErrorPanel
              title="Could not load documents."
              error={list.error}
              onRetry={() => void list.refetch()}
              retrying={list.isFetching}
            />
          ) : docs.length === 0 ? (
            <EmptyState
              title="No documents yet"
              description="Upload a policy or handbook below and the staff assistant can search it."
            />
          ) : (
            <div className="max-h-[70vh] overflow-auto">
              <table className="w-full min-w-[940px] border-collapse text-left text-[12.5px]">
                <thead>
                  <tr className="border-b border-border text-[12px] uppercase tracking-wide text-muted-foreground">
                    <th scope="col" className="sticky top-0 z-[1] bg-card py-2.5 pr-3 font-medium">
                      Title
                    </th>
                    <th scope="col" className="sticky top-0 z-[1] bg-card py-2.5 pr-3 font-medium">
                      Audience
                    </th>
                    <th scope="col" className="sticky top-0 z-[1] bg-card py-2.5 pr-3 font-medium">
                      Type
                    </th>
                    <th scope="col" className="sticky top-0 z-[1] bg-card py-2.5 pr-3 font-medium">
                      Version
                    </th>
                    <th scope="col" className="sticky top-0 z-[1] bg-card py-2.5 pr-3 font-medium">
                      Status
                    </th>
                    <th scope="col" className="sticky top-0 z-[1] bg-card py-2.5 pr-3 font-medium">
                      Updated
                    </th>
                    <th scope="col" className="sticky top-0 z-[1] bg-card py-2.5 pr-3 font-medium">
                      Expires
                    </th>
                    <th scope="col" className="sticky top-0 z-[1] bg-card py-2.5 pr-3 font-medium">
                      Uploaded by
                    </th>
                    <th scope="col" className="sticky top-0 z-[1] bg-card py-2.5 pr-3 font-medium">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {docs.map((d) => {
                    // No current version (an upload that died before its
                    // repoint) has no status to show: '-' rather than an
                    // "Indexing…" that polling never resolves (audit F30).
                    const status = d.status;
                    const expired = isExpired(d, today);
                    const replacingThis = replacing?.id === d.id;
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
                            'border-b border-border align-top outline-none hover:bg-[var(--ui-inset-soft)]',
                            citedDoc?.id === d.id &&
                              'bg-[var(--ui-inset-soft)] ring-1 ring-inset ring-[var(--accent)]',
                          )}
                        >
                          <td className="py-2.5 pr-3">
                            <p className="font-medium text-foreground">{d.title}</p>
                            {d.original_name ? (
                              <p className="mt-0.5 text-[12px] text-muted-foreground">
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
                            {d.version ? `v${d.version}` : '-'}
                          </td>
                          <td className="py-2.5 pr-3">
                            {expired ? (
                              // Past its expiry, search already skips it and
                              // tonight's purge deletes it, so "Ready" would
                              // be untrue (audit PL-F8).
                              <>
                                <StatusTag tone="neutral" dot>
                                  Expired
                                </StatusTag>
                                <p className="mt-1.5 max-w-[220px] text-[12px] text-muted-foreground">
                                  {EXPIRED_NOTE_TEXT}
                                </p>
                              </>
                            ) : status ? (
                              <StatusTag tone={STATUS_TONE[status]} dot>
                                {STATUS_LABEL[status]}
                              </StatusTag>
                            ) : (
                              <span className="text-muted-foreground">-</span>
                            )}
                            {status === 'failed' && !expired ? (
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
                                <p className="mt-1.5 max-w-[220px] text-[12px] text-[var(--ui-danger)]">
                                  {corpusFailureSentence(d.failure_code)}
                                </p>
                                <button
                                  type="button"
                                  onClick={() => reindexMut.mutate(d.id)}
                                  disabled={reindexMut.isPending}
                                  aria-label={`Retry indexing ${d.title}`}
                                  className="mt-1.5 inline-flex items-center gap-1 rounded-[8px] border border-border px-2 py-1 text-[12px] text-foreground disabled:opacity-40"
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
                              <p className="mt-1.5 flex max-w-[220px] items-start gap-1 text-[12px] text-[var(--ui-warn)]">
                                <AlertTriangle
                                  className="mt-0.5 h-3 w-3 shrink-0"
                                  aria-hidden="true"
                                />
                                {INJECTION_WARNING_TEXT}
                              </p>
                            ) : null}
                          </td>
                          <td className="py-2.5 pr-3 text-muted-foreground">
                            {formatDate(d.updated_at)}
                          </td>
                          <td className="py-2.5 pr-3 text-muted-foreground">
                            {d.expires_on ? formatCalendarDate(d.expires_on) : 'Never'}
                          </td>
                          <td className="py-2.5 pr-3 text-muted-foreground">
                            {d.uploaded_by_name ?? '-'}
                          </td>
                          <td className="py-2.5 pr-3">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <button
                                type="button"
                                onClick={() => downloadMut.mutate(d.id)}
                                disabled={downloadMut.isPending}
                                className={ROW_ACTION_CLASS}
                              >
                                <Download className="h-3 w-3" aria-hidden="true" />
                                Download
                              </button>

                              <label
                                className={cn(
                                  ROW_ACTION_CLASS,
                                  'focus-within:ring-2 focus-within:ring-[var(--accent)]',
                                  replaceMut.isPending
                                    ? 'cursor-not-allowed opacity-40'
                                    : 'cursor-pointer',
                                )}
                              >
                                <RefreshCw className="h-3 w-3" aria-hidden="true" />
                                Replace…
                                <input
                                  type="file"
                                  accept={ACCEPT}
                                  aria-label={`Replace ${d.title}`}
                                  ref={(el) => {
                                    if (el) replaceInputs.current.set(d.id, el);
                                    else replaceInputs.current.delete(d.id);
                                  }}
                                  className="sr-only"
                                  disabled={replaceMut.isPending}
                                  onChange={(e) => {
                                    const f = e.target.files?.[0];
                                    e.target.value = '';
                                    pickReplaceFile(d.id, f);
                                  }}
                                />
                              </label>

                              <button
                                type="button"
                                onClick={() => startEdit(d)}
                                className={ROW_ACTION_CLASS}
                              >
                                <Pencil className="h-3 w-3" aria-hidden="true" />
                                Edit
                              </button>

                              <span className="ml-2">
                                <ConfirmDeleteButton
                                  label="Delete"
                                  title={DELETE_TITLE}
                                  confirmText={DELETE_CONFIRM_TEXT}
                                  confirmGroupLabel={`Confirm deleting ${d.title}`}
                                  onConfirm={() => deleteMut.mutate(d.id)}
                                  pending={deleteMut.isPending && deleteMut.variables === d.id}
                                />
                              </span>
                            </div>
                          </td>
                        </tr>
                        {replacingThis && replacing ? (
                          <tr className="border-b border-border bg-[var(--ui-inset-soft)]">
                            <td colSpan={9} className="p-3">
                              <form
                                className="flex flex-col gap-2"
                                aria-label={`Upload a new version of ${d.title}`}
                                onKeyDown={(e) => {
                                  if (e.key === 'Escape' && !replaceMut.isPending) {
                                    e.stopPropagation();
                                    closeReplace();
                                  }
                                }}
                                onSubmit={(e) => {
                                  e.preventDefault();
                                  if (!replaceAttested || replaceMut.isPending) return;
                                  replaceMut.mutate(replacing);
                                }}
                              >
                                <p className="text-[12.5px] text-foreground">
                                  New version of {d.title}: {replacing.file.name}
                                </p>
                                <label className="flex items-start gap-2 text-[12.5px] text-foreground">
                                  <input
                                    type="checkbox"
                                    ref={replaceAttestRef}
                                    checked={replaceAttested}
                                    onChange={(e) => setReplaceAttested(e.target.checked)}
                                    className="mt-0.5"
                                  />
                                  {ATTESTATION_LABEL}
                                </label>
                                <div className="flex flex-wrap items-center gap-2">
                                  <Button
                                    type="submit"
                                    size="sm"
                                    disabled={!replaceAttested || replaceMut.isPending}
                                    aria-busy={replaceMut.isPending}
                                  >
                                    {replaceMut.isPending ? 'Uploading…' : 'Upload new version'}
                                  </Button>
                                  <Button
                                    type="button"
                                    size="sm"
                                    variant="ghost"
                                    disabled={replaceMut.isPending}
                                    onClick={closeReplace}
                                  >
                                    Cancel
                                  </Button>
                                  {replaceMut.isPending && replaceProgress !== null ? (
                                    <span className="text-[12px] text-muted-foreground">
                                      {replaceProgress}%
                                    </span>
                                  ) : null}
                                </div>
                              </form>
                            </td>
                          </tr>
                        ) : null}
                        {editingId === d.id ? (
                          <tr className="border-b border-border bg-[var(--ui-inset-soft)]">
                            <td colSpan={9} className="p-3">
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
                                  <legend className="text-[13px] font-medium text-[var(--ui-soft)]">
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
                                    className="text-[13px] font-medium text-[var(--ui-soft)]"
                                  >
                                    Expiry date
                                  </label>
                                  <input
                                    id={`edit-expiry-${d.id}`}
                                    type="date"
                                    value={editExpiresOn}
                                    // An unchanged past date stays valid (the
                                    // server lets it through too); a new one
                                    // cannot be before today.
                                    min={
                                      editExpiresOn && editExpiresOn === d.expires_on
                                        ? undefined
                                        : today
                                    }
                                    onChange={(e) => setEditExpiresOn(e.target.value)}
                                    aria-describedby={`edit-expiry-help-${d.id}`}
                                    className="rounded-[8px] border border-border bg-secondary px-2.5 py-1.5 text-[12.5px] text-foreground"
                                  />
                                  <span
                                    id={`edit-expiry-help-${d.id}`}
                                    className="text-[12px] text-[var(--ui-faint)]"
                                  >
                                    {EXPIRY_HELP_TEXT}
                                  </span>
                                </div>
                                <Button type="submit" size="sm" disabled={editMut.isPending}>
                                  {editMut.isPending ? 'Saving…' : 'Save'}
                                </Button>
                                <Button
                                  type="button"
                                  size="sm"
                                  variant="ghost"
                                  onClick={() => setEditingId(null)}
                                >
                                  Cancel
                                </Button>
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
        </SectionCard>
      </Reveal>
      {/* Upload */}
      <Reveal delay={0.05}>
        <SectionCard title="Upload a document" icon={UploadIcon}>
          <form className="grid gap-3 sm:grid-cols-2" onSubmit={handleUploadSubmit}>
            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="corpus-upload-title"
                className="text-[13px] font-medium text-[var(--ui-soft)]"
              >
                Title
              </label>
              <input
                id="corpus-upload-title"
                value={uploadTitle}
                onChange={(e) => setUploadTitle(e.target.value)}
                maxLength={TITLE_MAX}
                placeholder="e.g. Employee handbook"
                className="min-h-[40px] rounded-[12px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="corpus-upload-file"
                className="text-[13px] font-medium text-[var(--ui-soft)]"
              >
                File
              </label>
              <input
                id="corpus-upload-file"
                ref={uploadFileInputRef}
                type="file"
                accept={ACCEPT}
                onChange={(e) => pickUploadFile(e.target.files?.[0] ?? null)}
                aria-invalid={uploadFileError ? true : undefined}
                aria-describedby="corpus-upload-file-help"
                className="min-h-[40px] rounded-[12px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground"
              />
              {uploadFileError ? (
                <span
                  id="corpus-upload-file-help"
                  role="alert"
                  className="text-[12px] text-[var(--ui-danger)]"
                >
                  {uploadFileError}
                </span>
              ) : (
                <span id="corpus-upload-file-help" className="text-[12px] text-[var(--ui-faint)]">
                  PDF, Word (.docx), plain text or Markdown, up to 10 MB.
                </span>
              )}
            </div>

            <fieldset className="flex flex-col gap-1.5">
              <legend className="text-[13px] font-medium text-[var(--ui-soft)]">Audience</legend>
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
                  <span className="ml-1.5 text-[12px] text-muted-foreground">
                    - HR-only documents are created by HR managers.
                  </span>
                </p>
              )}
            </fieldset>

            <div className="flex flex-col gap-1.5">
              <label
                htmlFor="corpus-upload-expiry"
                className="text-[13px] font-medium text-[var(--ui-soft)]"
              >
                Expiry date (optional)
              </label>
              <input
                id="corpus-upload-expiry"
                type="date"
                value={uploadExpiresOn}
                min={today}
                onChange={(e) => setUploadExpiresOn(e.target.value)}
                aria-describedby="corpus-upload-expiry-help"
                className="min-h-[40px] rounded-[12px] border border-border bg-secondary px-3 py-2 text-[13px] text-foreground focus:border-[var(--accent)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              />
              <span id="corpus-upload-expiry-help" className="text-[12px] text-[var(--ui-faint)]">
                {EXPIRY_HELP_TEXT}
              </span>
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
              <Button type="submit" disabled={!canSubmitUpload} aria-busy={uploadMut.isPending}>
                {uploadMut.isPending ? 'Uploading…' : 'Upload'}
              </Button>
              {uploadMut.isPending && uploadProgress !== null ? (
                <span className="text-[12px] text-muted-foreground">{uploadProgress}%</span>
              ) : null}
            </div>
          </form>
        </SectionCard>
      </Reveal>
    </Page>
  );
}
