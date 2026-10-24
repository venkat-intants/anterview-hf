// CorpusDocuments (/hr/library, /superadmin/library) — PH5-E2. The one
// component both consoles render; the super-admin restriction and every
// other behaviour come from mocking `useAuth` and `api/corpus`, exactly the
// harness CompanyAdminConsole.test.tsx uses for the same role-dependent shape.
//
// Route path matches the API's `/hr/library` (moved off `/hr/documents`,
// which collided with offers.py's candidate documents) and the copilot's
// document-citation route — see api/corpus.ts's header comment.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AuthUser } from '../types/auth';
import type { CorpusDocument, CorpusSearchResult } from '../api/corpus';
import { ApiError } from '../api/client';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const api = {
  listCorpusDocuments: vi.fn(),
  uploadCorpusDocument: vi.fn(),
  replaceCorpusDocument: vi.fn(),
  editCorpusDocument: vi.fn(),
  deleteCorpusDocument: vi.fn(),
  getCorpusDownloadUrl: vi.fn(),
  searchCorpusDocuments: vi.fn(),
  getCorpusSemanticStatus: vi.fn(),
  reindexCorpusDocument: vi.fn(),
};
vi.mock('../api/corpus', async () => {
  const actual = await vi.importActual<typeof import('../api/corpus')>('../api/corpus');
  return {
    ...actual,
    listCorpusDocuments: (...a: unknown[]) => api.listCorpusDocuments(...a) as unknown,
    uploadCorpusDocument: (...a: unknown[]) => api.uploadCorpusDocument(...a) as unknown,
    replaceCorpusDocument: (...a: unknown[]) => api.replaceCorpusDocument(...a) as unknown,
    editCorpusDocument: (...a: unknown[]) => api.editCorpusDocument(...a) as unknown,
    deleteCorpusDocument: (...a: unknown[]) => api.deleteCorpusDocument(...a) as unknown,
    getCorpusDownloadUrl: (...a: unknown[]) => api.getCorpusDownloadUrl(...a) as unknown,
    searchCorpusDocuments: (...a: unknown[]) => api.searchCorpusDocuments(...a) as unknown,
    getCorpusSemanticStatus: (...a: unknown[]) => api.getCorpusSemanticStatus(...a) as unknown,
    reindexCorpusDocument: (...a: unknown[]) => api.reindexCorpusDocument(...a) as unknown,
  };
});

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('../lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a) as unknown,
    success: (...a: unknown[]) => toastSuccess(...a) as unknown,
    info: vi.fn(),
    warning: vi.fn(),
  },
}));

const { mockUseAuth } = vi.hoisted(() => ({ mockUseAuth: vi.fn() }));
vi.mock('../context/AuthContext', () => ({
  useAuth: () => mockUseAuth() as unknown,
}));

// A tiny interval so the "polls while indexing" tests run fast on REAL
// timers rather than fighting RTL's async utilities under fake ones
// (no precedent for that combination in this suite). Production still gets
// the real ACTIVE_POLL_MS from lib/polling.ts — only this test file's copy
// of the constant is shrunk.
vi.mock('../lib/polling', () => ({ ACTIVE_POLL_MS: 30 }));

import CorpusDocuments from '../pages/hr/CorpusDocuments';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function hrUser(): AuthUser {
  return { user_id: 'u-hr', full_name: 'Bhavya Nair', email: 'hr@acme.edu', roles: ['hr_manager'] };
}

function superAdminUser(): AuthUser {
  return {
    user_id: 'u-sa',
    full_name: 'Asha Rao',
    email: 'sa@acme.edu',
    roles: ['super_admin'],
  };
}

function doc(overrides: Partial<CorpusDocument> = {}): CorpusDocument {
  return {
    id: '11111111-1111-4111-8111-111111111111',
    title: 'Employee handbook',
    audience: 'hr_only',
    doc_kind: 'handbook',
    expires_on: null,
    created_at: '2026-09-01T00:00:00.000Z',
    updated_at: '2026-09-10T00:00:00.000Z',
    version: 1,
    status: 'indexed',
    failure_code: null,
    original_name: 'handbook.pdf',
    content_type: 'application/pdf',
    size_bytes: 12_345,
    page_count: 10,
    chunk_count: 8,
    injection_markers: 0,
    uploaded_at: '2026-09-01T00:00:00.000Z',
    uploaded_by_name: 'Bhavya Nair',
    ...overrides,
  };
}

const NO_SEMANTIC: CorpusSearchResult = { semantic: false, passages: [] };
const ATTESTATION = 'This is a company document, not a record about a candidate.';

/** Today as the viewer's calendar day, 'YYYY-MM-DD', offset by `days`. */
/** A UTC calendar day, offset from today: the server's clock for expiry. */
function isoDay(days = 0): string {
  return new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);
}

/**
 * @param path Mounted the way App.tsx mounts it, so `/hr/library/{id}` — the
 *             copilot's document-citation route — exercises the SAME component
 *             with a `:documentId` param rather than a hand-passed prop.
 */
function renderPage(user: AuthUser = hrUser(), path = '/hr/library') {
  mockUseAuth.mockReturnValue({ isAuthenticated: true, isInitializing: false, user });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/hr/library" element={<CorpusDocuments />} />
          <Route path="/hr/library/:documentId" element={<CorpusDocuments />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  api.listCorpusDocuments.mockResolvedValue([]);
  // Default: semantic search is up. Individual tests override to exercise
  // the banner.
  api.getCorpusSemanticStatus.mockResolvedValue({ corpus_semantic: true });
});

// ---------------------------------------------------------------------------
// Loading / empty / error / 403
// ---------------------------------------------------------------------------

describe('CorpusDocuments - list states', () => {
  it('shows an empty state when there are no documents', async () => {
    renderPage();
    expect(await screen.findByText('No documents yet')).toBeInTheDocument();
  });

  it('shows each document with its audience, type, version, status and uploader', async () => {
    api.listCorpusDocuments.mockResolvedValue([doc()]);
    renderPage();
    expect(await screen.findByText('Employee handbook')).toBeInTheDocument();
    const row = screen.getByRole('table');
    expect(within(row).getByText('HR only')).toBeInTheDocument();
    expect(within(row).getByText('PDF')).toBeInTheDocument();
    expect(within(row).getByText('v1')).toBeInTheDocument();
    expect(within(row).getByText('Ready')).toBeInTheDocument();
    expect(within(row).getByText('Bhavya Nair')).toBeInTheDocument();
  });

  it('shows "-" for the uploader when that account no longer exists', async () => {
    api.listCorpusDocuments.mockResolvedValue([doc({ uploaded_by_name: null })]);
    renderPage();
    await screen.findByText('Employee handbook');
    const row = screen.getByRole('table');
    const cells = within(row).getAllByRole('cell');
    expect(cells.some((c) => c.textContent === '-')).toBe(true);
  });

  it('shows a generic error for a non-403 failure', async () => {
    api.listCorpusDocuments.mockRejectedValue(new ApiError('Server exploded', 500));
    renderPage();
    expect(await screen.findByText('Server exploded')).toBeInTheDocument();
  });

  it('shows the 403 path distinctly, with the server’s own message', async () => {
    api.listCorpusDocuments.mockRejectedValue(
      new ApiError('Your account is not assigned to an active company.', 403),
    );
    renderPage();
    expect(
      await screen.findByText('Your account is not assigned to an active company.'),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// A failed row shows its sentence
// ---------------------------------------------------------------------------

describe('CorpusDocuments - failed row', () => {
  it('shows the human sentence for the failure_code, not the code itself', async () => {
    api.listCorpusDocuments.mockResolvedValue([
      doc({ status: 'failed', failure_code: 'embedding_unavailable' }),
    ]);
    renderPage();
    expect(
      await screen.findByText(
        'Could not index this file because search was unavailable. The file is stored; press Try again to index it.',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText('embedding_unavailable')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Retry indexing — POST /hr/library/{id}/reindex
//
// The button was missing while the route existed and was unit-tested
// server-side: a document parked at `failed` could never be indexed again from
// the console, and the sentence beside it ("will be indexed automatically") was
// false for exactly that row.
// ---------------------------------------------------------------------------

describe('CorpusDocuments - retry indexing', () => {
  const FAILED = doc({ status: 'failed', failure_code: 'embedding_unavailable' });

  it('offers Retry only on a failed row', async () => {
    api.listCorpusDocuments.mockResolvedValue([doc({ status: 'indexed' })]);
    renderPage();
    await screen.findByText('Ready');
    expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument();
  });

  it('retries that document and moves the row back to Indexing…', async () => {
    const user = userEvent.setup();
    api.listCorpusDocuments
      .mockResolvedValueOnce([FAILED])
      // What the invalidation re-reads: the server has reset the version to
      // 'parsed' and cleared failure_code.
      .mockResolvedValue([doc({ status: 'parsed', failure_code: null })]);
    api.reindexCorpusDocument.mockResolvedValue(doc({ status: 'parsed', failure_code: null }));

    renderPage();
    await user.click(await screen.findByRole('button', { name: /try indexing .* again/i }));

    await waitFor(() => expect(api.reindexCorpusDocument).toHaveBeenCalledWith(FAILED.id));
    // The list is re-read, not patched locally — the status the server now holds
    // is the one shown.
    expect(await screen.findByText('Indexing…')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('Failed')).not.toBeInTheDocument());
  });

  it('shows the server’s own sentence when the document is not actually stuck', async () => {
    const user = userEvent.setup();
    api.listCorpusDocuments.mockResolvedValue([FAILED]);
    api.reindexCorpusDocument.mockRejectedValue(
      new ApiError('This document is not stuck - there is nothing to reindex.', 409, {
        failure_code: 'not_failed',
        message: 'This document is not stuck - there is nothing to reindex.',
      }),
    );

    renderPage();
    await user.click(await screen.findByRole('button', { name: /try indexing .* again/i }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        'This document is not stuck - there is nothing to reindex.',
      ),
    );
  });
});

// ---------------------------------------------------------------------------
// A document citation lands on the row it names
// ---------------------------------------------------------------------------

describe('CorpusDocuments - document citation deep link', () => {
  const CITED = doc({ id: '22222222-2222-4222-8222-222222222222', title: 'Leave policy' });
  const OTHER = doc({ id: '33333333-3333-4333-8333-333333333333', title: 'Travel policy' });

  it('focuses the cited row', async () => {
    api.listCorpusDocuments.mockResolvedValue([OTHER, CITED]);
    renderPage(hrUser(), `/hr/library/${CITED.id}`);

    await screen.findByText('Leave policy');
    // Focus, not only a scroll: someone arriving from a citation by keyboard
    // must land on the record too.
    await waitFor(() =>
      expect(document.activeElement?.id).toBe(`corpus-doc-${CITED.id}`),
    );
  });

  it('leaves every row unfocused on the plain library route', async () => {
    api.listCorpusDocuments.mockResolvedValue([OTHER, CITED]);
    renderPage();
    await screen.findByText('Leave policy');
    expect(document.activeElement?.id ?? '').not.toContain('corpus-doc-');
  });

  it('says the document is gone rather than showing an unremarkable list', async () => {
    api.listCorpusDocuments.mockResolvedValue([OTHER]);
    renderPage(hrUser(), `/hr/library/${CITED.id}`);
    expect(
      await screen.findByText(/no longer in your library/i),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// The injection warning
// ---------------------------------------------------------------------------

describe('CorpusDocuments - injection warning', () => {
  it('shows the per-row warning when the document contains steering text', async () => {
    api.listCorpusDocuments.mockResolvedValue([doc({ injection_markers: 2 })]);
    renderPage();
    expect(
      await screen.findByText(
        'This document contains text that tries to instruct an automated reader. It is ignored.',
      ),
    ).toBeInTheDocument();
  });

  it('does not show the warning for a clean document', async () => {
    api.listCorpusDocuments.mockResolvedValue([doc({ injection_markers: 0 })]);
    renderPage();
    await screen.findByText('Employee handbook');
    expect(
      screen.queryByText(
        'This document contains text that tries to instruct an automated reader. It is ignored.',
      ),
    ).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Upload — the attestation gate and the audience default
// ---------------------------------------------------------------------------

describe('CorpusDocuments - upload attestation gate', () => {
  it('keeps Upload disabled until the attestation is ticked, even with a file and title', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('No documents yet');

    const file = new File(['%PDF-1.4'], 'handbook.pdf', { type: 'application/pdf' });
    await user.upload(screen.getByLabelText('File'), file);
    await user.type(screen.getByLabelText('Title'), 'Employee handbook');

    const submit = screen.getByRole('button', { name: 'Upload' });
    expect(submit).toBeDisabled();

    await user.click(screen.getByText('This is a company document, not a record about a candidate.'));
    expect(submit).toBeEnabled();
  });

  it('does not call the API while the attestation is unticked', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('No documents yet');

    await user.upload(
      screen.getByLabelText('File'),
      new File(['%PDF-1.4'], 'h.pdf', { type: 'application/pdf' }),
    );
    await user.type(screen.getByLabelText('Title'), 'Handbook');
    await user.click(screen.getByRole('button', { name: 'Upload' }));

    expect(api.uploadCorpusDocument).not.toHaveBeenCalled();
  });
});

describe('CorpusDocuments - audience default', () => {
  it('defaults to HR only for an hr_manager', async () => {
    renderPage(hrUser());
    await screen.findByText('No documents yet');
    const hrOnlyRadio = screen.getByRole('radio', { name: 'HR only' });
    expect(hrOnlyRadio).toBeChecked();
  });

  it('does not offer HR only to a super_admin, and defaults to All staff', async () => {
    renderPage(superAdminUser());
    await screen.findByText('No documents yet');
    expect(screen.queryByRole('radio', { name: 'HR only' })).not.toBeInTheDocument();
    expect(screen.getAllByText('All staff').length).toBeGreaterThan(0);
  });
});

describe('CorpusDocuments - super-admin restriction end to end', () => {
  it('uploads with audience all_staff for a super_admin, and surfaces the server message on refusal', async () => {
    const user = userEvent.setup();
    api.uploadCorpusDocument.mockRejectedValue(
      new ApiError('HR-only documents are created by HR managers.', 422, {
        failure_code: 'hr_only_requires_hr_manager',
        message: 'HR-only documents are created by HR managers.',
      }),
    );
    renderPage(superAdminUser());
    await screen.findByText('No documents yet');

    await user.upload(
      screen.getByLabelText('File'),
      new File(['%PDF-1.4'], 'policy.pdf', { type: 'application/pdf' }),
    );
    await user.type(screen.getByLabelText('Title'), 'Attendance policy');
    await user.click(screen.getByText('This is a company document, not a record about a candidate.'));
    await user.click(screen.getByRole('button', { name: 'Upload' }));

    await waitFor(() =>
      expect(api.uploadCorpusDocument).toHaveBeenCalledWith(
        expect.objectContaining({ audience: 'all_staff' }),
        expect.anything(),
      ),
    );
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('HR-only documents are created by HR managers.'),
    );
  });
});

// ---------------------------------------------------------------------------
// Delete confirmation wording
// ---------------------------------------------------------------------------

describe('CorpusDocuments - delete', () => {
  it('shows what actually happens before deleting, then deletes on confirm', async () => {
    const user = userEvent.setup();
    api.listCorpusDocuments.mockResolvedValue([doc()]);
    api.deleteCorpusDocument.mockResolvedValue(undefined);
    renderPage();
    await screen.findByText('Employee handbook');

    await user.click(screen.getByRole('button', { name: 'Delete' }));
    // The confirm button itself says what happens: gone from search at once.
    const group = screen.getByRole('group', { name: /confirm deleting/i });
    await user.click(within(group).getByRole('button', { name: 'Delete and remove from search' }));

    await waitFor(() =>
      expect(api.deleteCorpusDocument).toHaveBeenCalledWith('11111111-1111-4111-8111-111111111111'),
    );
  });

  it('cancels without deleting', async () => {
    const user = userEvent.setup();
    api.listCorpusDocuments.mockResolvedValue([doc()]);
    renderPage();
    await screen.findByText('Employee handbook');

    await user.click(screen.getByRole('button', { name: 'Delete' }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(api.deleteCorpusDocument).not.toHaveBeenCalled();
    expect(
      screen.queryByRole('button', { name: 'Delete and remove from search' }),
    ).not.toBeInTheDocument();
    // Focus goes back to the trigger rather than dropping to the page (F29).
    expect(screen.getByRole('button', { name: 'Delete' })).toHaveFocus();
  });
});

// ---------------------------------------------------------------------------
// Semantic-unavailable banner
// ---------------------------------------------------------------------------

const SEMANTIC_BANNER_TEXT =
  'Semantic search is unavailable - the assistant is matching keywords only.';

describe('CorpusDocuments - semantic search banner', () => {
  it('shows the banner on page load from GET /agent/status, with no search performed', async () => {
    api.getCorpusSemanticStatus.mockResolvedValue({ corpus_semantic: false });
    renderPage();
    expect(await screen.findByText(SEMANTIC_BANNER_TEXT)).toBeInTheDocument();
    expect(api.searchCorpusDocuments).not.toHaveBeenCalled();
  });

  it('does not show the banner on load when the status says semantic search is up', async () => {
    api.getCorpusSemanticStatus.mockResolvedValue({ corpus_semantic: true });
    renderPage();
    await screen.findByText('No documents yet');
    expect(screen.queryByText(SEMANTIC_BANNER_TEXT)).not.toBeInTheDocument();
  });

  it('still reacts to the search response - a mid-session outage surfaces even though status said up', async () => {
    const user = userEvent.setup();
    api.getCorpusSemanticStatus.mockResolvedValue({ corpus_semantic: true });
    api.searchCorpusDocuments.mockResolvedValue(NO_SEMANTIC);
    renderPage();
    await screen.findByText('No documents yet');
    expect(screen.queryByText(SEMANTIC_BANNER_TEXT)).not.toBeInTheDocument();

    await user.type(screen.getByLabelText('Search your documents'), 'notice period');
    await user.click(screen.getByRole('button', { name: 'Search' }));

    expect(await screen.findByText(SEMANTIC_BANNER_TEXT)).toBeInTheDocument();
  });

  it('clears once a search shows semantic search has recovered', async () => {
    const user = userEvent.setup();
    api.getCorpusSemanticStatus.mockResolvedValue({ corpus_semantic: false });
    api.searchCorpusDocuments.mockResolvedValue({ semantic: true, passages: [] });
    renderPage();
    expect(await screen.findByText(SEMANTIC_BANNER_TEXT)).toBeInTheDocument();

    await user.type(screen.getByLabelText('Search your documents'), 'notice period');
    await user.click(screen.getByRole('button', { name: 'Search' }));

    await screen.findByText('No matching passages.');
    expect(screen.queryByText(SEMANTIC_BANNER_TEXT)).not.toBeInTheDocument();
  });

  it('does not show the banner when semantic search is available', async () => {
    const user = userEvent.setup();
    api.searchCorpusDocuments.mockResolvedValue({ semantic: true, passages: [] });
    renderPage();
    await screen.findByText('No documents yet');

    await user.type(screen.getByLabelText('Search your documents'), 'notice period');
    await user.click(screen.getByRole('button', { name: 'Search' }));

    await screen.findByText('No matching passages.');
    expect(screen.queryByText(SEMANTIC_BANNER_TEXT)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Upload sends the right multipart request (page → api wiring)
// ---------------------------------------------------------------------------

describe('CorpusDocuments - upload wiring', () => {
  it('calls uploadCorpusDocument with the file, title, audience and attestation', async () => {
    const user = userEvent.setup();
    api.uploadCorpusDocument.mockResolvedValue(doc());
    renderPage();
    await screen.findByText('No documents yet');

    const file = new File(['%PDF-1.4'], 'handbook.pdf', { type: 'application/pdf' });
    await user.upload(screen.getByLabelText('File'), file);
    await user.type(screen.getByLabelText('Title'), 'Employee handbook');
    await user.click(screen.getByText('This is a company document, not a record about a candidate.'));
    await user.click(screen.getByRole('button', { name: 'Upload' }));

    await waitFor(() =>
      expect(api.uploadCorpusDocument).toHaveBeenCalledWith(
        expect.objectContaining({
          file,
          title: 'Employee handbook',
          audience: 'hr_only',
          attested: true,
        }),
        expect.anything(),
      ),
    );
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith('Document uploaded'));
  });

  it('surfaces the server\'s rate-limit message on a 429, not a generic failure', async () => {
    const user = userEvent.setup();
    api.uploadCorpusDocument.mockRejectedValue(
      new ApiError('Too many requests. Please wait a minute and try again.', 429),
    );
    renderPage();
    await screen.findByText('No documents yet');

    await user.upload(
      screen.getByLabelText('File'),
      new File(['%PDF-1.4'], 'h.pdf', { type: 'application/pdf' }),
    );
    await user.type(screen.getByLabelText('Title'), 'Handbook');
    await user.click(screen.getByText('This is a company document, not a record about a candidate.'));
    await user.click(screen.getByRole('button', { name: 'Upload' }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('Too many requests. Please wait a minute and try again.'),
    );
    expect(toastError).not.toHaveBeenCalledWith('Could not upload this document');
  });

  it('surfaces the server\'s rate-limit message on Replace…, not a generic failure', async () => {
    const user = userEvent.setup();
    api.listCorpusDocuments.mockResolvedValue([doc()]);
    api.replaceCorpusDocument.mockRejectedValue(
      new ApiError('Too many requests. Please wait a minute and try again.', 429),
    );
    renderPage();
    await screen.findByText('Employee handbook');

    const file = new File(['%PDF-1.4'], 'v2.pdf', { type: 'application/pdf' });
    await user.upload(screen.getByLabelText('Replace Employee handbook'), file);
    // Two attestation checkboxes exist now: the new-version row's (first, in
    // the table) and the upload form's.
    await user.click(screen.getAllByRole('checkbox', { name: ATTESTATION })[0]);
    await user.click(screen.getByRole('button', { name: 'Upload new version' }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('Too many requests. Please wait a minute and try again.'),
    );
    expect(toastError).not.toHaveBeenCalledWith('Could not upload a new version.');
  });
});

// ---------------------------------------------------------------------------
// Polling — a document stuck at "Indexing…" must update itself
// ---------------------------------------------------------------------------

describe('CorpusDocuments - polling while indexing', () => {
  it('polls while a document is indexing, and picks up Ready without a reload', async () => {
    api.listCorpusDocuments
      .mockResolvedValueOnce([doc({ status: 'indexing' })])
      .mockResolvedValueOnce([doc({ status: 'indexed' })]);
    renderPage();

    await screen.findByText('Indexing…');
    expect(await screen.findByText('Ready')).toBeInTheDocument();
    expect(api.listCorpusDocuments.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('stops polling once every row has settled', async () => {
    api.listCorpusDocuments
      .mockResolvedValueOnce([doc({ status: 'parsed' })])
      .mockResolvedValue([doc({ status: 'failed', failure_code: 'embedding_unavailable' })]);
    renderPage();

    await screen.findByText('Indexing…');
    await screen.findByText('Failed');

    const callsOnceSettled = api.listCorpusDocuments.mock.calls.length;
    // Real time, well past several poll intervals (30ms mocked) — a still-firing
    // interval would have pushed the count higher than this.
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(api.listCorpusDocuments.mock.calls.length).toBe(callsOnceSettled);
  });

  it('never polls a library that is already fully settled', async () => {
    api.listCorpusDocuments.mockResolvedValue([doc({ status: 'indexed' })]);
    renderPage();

    await screen.findByText('Ready');
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(api.listCorpusDocuments).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// Audit fixes: PL-F7 (Replace), PL-F8 (expiry), PL-F9, PL-F12, F15, F16, F30
// ---------------------------------------------------------------------------

describe('CorpusDocuments - replace takes the attestation', () => {
  // PL-F7: picking a file used to upload it at once, with no attestation and
  // no pending state, so two picks made two versions.
  it('does not upload on pick; uploads only once attested, sending the attestation', async () => {
    const user = userEvent.setup();
    api.listCorpusDocuments.mockResolvedValue([doc()]);
    api.replaceCorpusDocument.mockResolvedValue(doc({ version: 2 }));
    renderPage();
    await screen.findByText('Employee handbook');

    const file = new File(['%PDF-1.4'], 'v2.pdf', { type: 'application/pdf' });
    await user.upload(screen.getByLabelText('Replace Employee handbook'), file);

    expect(api.replaceCorpusDocument).not.toHaveBeenCalled();
    const upload = screen.getByRole('button', { name: 'Upload new version' });
    expect(upload).toBeDisabled();

    await user.click(screen.getAllByRole('checkbox', { name: ATTESTATION })[0]);
    await user.click(upload);

    await waitFor(() => expect(api.replaceCorpusDocument).toHaveBeenCalledTimes(1));
    const [id, sent, attested] = api.replaceCorpusDocument.mock.calls[0] as [string, File, boolean];
    expect(id).toBe('11111111-1111-4111-8111-111111111111');
    expect(sent).toBe(file);
    expect(attested).toBe(true);
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith('New version uploaded'));
  });

  // F29: the confirm row takes focus, Escape backs out, and focus returns to
  // the Replace control instead of dropping to the page.
  it('moves focus into the confirm row and back to Replace on Escape', async () => {
    const user = userEvent.setup();
    api.listCorpusDocuments.mockResolvedValue([doc()]);
    renderPage();
    await screen.findByText('Employee handbook');

    const input = screen.getByLabelText('Replace Employee handbook');
    await user.upload(input, new File(['%PDF-1.4'], 'v2.pdf', { type: 'application/pdf' }));
    const form = screen.getByRole('form', { name: 'Upload a new version of Employee handbook' });
    await waitFor(() => expect(form).toContainElement(document.activeElement as HTMLElement));

    await user.keyboard('{Escape}');

    expect(
      screen.queryByRole('form', { name: 'Upload a new version of Employee handbook' }),
    ).not.toBeInTheDocument();
    await waitFor(() => expect(input).toHaveFocus());
    expect(api.replaceCorpusDocument).not.toHaveBeenCalled();
  });

  it('disables Replace while a new version is uploading', async () => {
    const user = userEvent.setup();
    api.listCorpusDocuments.mockResolvedValue([doc()]);
    api.replaceCorpusDocument.mockReturnValue(new Promise(() => undefined));
    renderPage();
    await screen.findByText('Employee handbook');

    const input = screen.getByLabelText('Replace Employee handbook');
    await user.upload(input, new File(['%PDF-1.4'], 'v2.pdf', { type: 'application/pdf' }));
    await user.click(screen.getAllByRole('checkbox', { name: ATTESTATION })[0]);
    await user.click(screen.getByRole('button', { name: 'Upload new version' }));

    expect(await screen.findByRole('button', { name: 'Uploading…' })).toBeDisabled();
    expect(input).toBeDisabled();
  });

  // F15: checked on pick, not after uploading the whole file.
  it('refuses an oversized replacement before uploading it', async () => {
    const user = userEvent.setup();
    api.listCorpusDocuments.mockResolvedValue([doc()]);
    renderPage();
    await screen.findByText('Employee handbook');

    const big = new File(['x'], 'big.pdf', { type: 'application/pdf' });
    Object.defineProperty(big, 'size', { value: 11 * 1024 * 1024 });
    await user.upload(screen.getByLabelText('Replace Employee handbook'), big);

    expect(toastError).toHaveBeenCalledWith('The file is larger than 10 MB.');
    expect(screen.queryByRole('button', { name: 'Upload new version' })).not.toBeInTheDocument();
  });
});

describe('CorpusDocuments - upload file checks', () => {
  it('says why a picked file cannot be uploaded and keeps Upload disabled', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('No documents yet');

    await user.type(screen.getByLabelText('Title'), 'Handbook');
    await user.click(screen.getByRole('checkbox', { name: ATTESTATION }));
    await user.upload(screen.getByLabelText('File'), new File([], 'empty.txt'));

    expect(await screen.findByRole('alert')).toHaveTextContent(/empty/);
    expect(screen.getByRole('button', { name: 'Upload' })).toBeDisabled();
  });
});

describe('CorpusDocuments - expiry', () => {
  // PL-F8: past dates were accepted and nothing said the document is deleted.
  it('blocks past dates and says what expiry does', async () => {
    renderPage();
    await screen.findByText('No documents yet');

    const expiry = screen.getByLabelText('Expiry date (optional)');
    expect(expiry).toHaveAttribute('min', isoDay(0));
    expect(
      screen.getByText('The document is deleted automatically after this date.'),
    ).toBeInTheDocument();
  });

  it('shows an Expires column, and Expired rather than Ready once the date has passed', async () => {
    api.listCorpusDocuments.mockResolvedValue([
      doc({ id: 'a', title: 'Old policy', expires_on: isoDay(-1) }),
      doc({ id: 'b', title: 'Current policy', expires_on: null }),
    ]);
    renderPage();
    await screen.findByText('Old policy');

    expect(screen.getByRole('columnheader', { name: 'Expires' })).toBeInTheDocument();
    const oldRow = screen.getByText('Old policy').closest('tr')!;
    expect(within(oldRow).getByText('Expired')).toBeInTheDocument();
    expect(within(oldRow).queryByText('Ready')).not.toBeInTheDocument();
    const currentRow = screen.getByText('Current policy').closest('tr')!;
    expect(within(currentRow).getByText('Never')).toBeInTheDocument();
    expect(within(currentRow).getByText('Ready')).toBeInTheDocument();
  });

  // The server decides expiry on the UTC day. Each instant below is a different
  // local day than UTC somewhere (00:30Z is the previous day west of Greenwich,
  // 23:30Z the next day east of it, as in IST), so a viewer-local "today" fails
  // one of them on any machine that is not on UTC.
  it.each(['2026-10-24T00:30:00.000Z', '2026-10-24T23:30:00.000Z'])(
    'decides Expired on the UTC day, as the server does (at %s)',
    async (now) => {
      vi.useFakeTimers({ toFake: ['Date'] });
      vi.setSystemTime(new Date(now));
      try {
        api.listCorpusDocuments.mockResolvedValue([
          doc({ id: 'a', title: 'Ends today', expires_on: '2026-10-24' }),
          doc({ id: 'b', title: 'Ended yesterday', expires_on: '2026-10-23' }),
        ]);
        renderPage();
        await screen.findByText('Ends today');
        const today = screen.getByText('Ends today').closest('tr')!;
        expect(within(today).getByText('Ready')).toBeInTheDocument();
        expect(within(today).queryByText('Expired')).not.toBeInTheDocument();
        const past = screen.getByText('Ended yesterday').closest('tr')!;
        expect(within(past).getByText('Expired')).toBeInTheDocument();
      } finally {
        vi.useRealTimers();
      }
    },
  );

  it('keeps the page Upload the one primary: row-level Save and Upload new version are outline', async () => {
    const user = userEvent.setup();
    api.listCorpusDocuments.mockResolvedValue([doc()]);
    renderPage();
    await screen.findByText('Employee handbook');
    const primary = /(?:^|\s)bg-primary(?=\s|$)/;

    await user.upload(
      screen.getByLabelText('Replace Employee handbook'),
      new File(['%PDF-1.4'], 'v2.pdf', { type: 'application/pdf' }),
    );
    expect(screen.getByRole('button', { name: 'Upload new version' }).className).not.toMatch(
      primary,
    );

    await user.click(screen.getByRole('button', { name: 'Edit' }));
    expect(screen.getByRole('button', { name: 'Save' }).className).not.toMatch(primary);
    expect(screen.getByRole('button', { name: 'Upload' }).className).toMatch(primary);
  });

  it('prints a date-only expiry on its own calendar day', async () => {
    api.listCorpusDocuments.mockResolvedValue([doc({ expires_on: '2031-03-05' })]);
    renderPage();
    expect(await screen.findByText('5 Mar 2031')).toBeInTheDocument();
  });
});

describe('CorpusDocuments - row copy and states', () => {
  // PL-F9: the failed row promised automatic indexing next to a Retry button.
  it('tells HR to press Retry on a row parked by a search outage', async () => {
    api.listCorpusDocuments.mockResolvedValue([
      doc({ status: 'failed', failure_code: 'embedding_unavailable' }),
    ]);
    renderPage();
    expect(await screen.findByText(/press Try again to index it/)).toBeInTheDocument();
    expect(screen.queryByText(/indexed automatically/)).not.toBeInTheDocument();
  });

  // F30: a row with no current version showed "Indexing…" forever.
  it('shows "-" for a document with no current version, not Indexing…', async () => {
    api.listCorpusDocuments.mockResolvedValue([doc({ status: null, version: null })]);
    renderPage();
    await screen.findByText('Employee handbook');
    expect(screen.queryByText('Indexing…')).not.toBeInTheDocument();
  });

  // PL-F12: the search box had no limit, so a long query came back "HTTP 422".
  it('limits the search box to the server limit', async () => {
    renderPage();
    await screen.findByText('No documents yet');
    expect(screen.getByLabelText('Search your documents')).toHaveAttribute('maxLength', '500');
  });

  // F16: window.open after an await is a popup a blocker may stop.
  it('downloads in the same tab instead of opening a popup', async () => {
    const user = userEvent.setup();
    api.listCorpusDocuments.mockResolvedValue([doc()]);
    api.getCorpusDownloadUrl.mockResolvedValue({
      url: 'https://files.example.com/doc?sig=x',
      expires_in: 300,
    });
    const assign = vi.fn();
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    const original = window.location;
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...original, assign },
    });
    try {
      renderPage();
      await screen.findByText('Employee handbook');
      await user.click(screen.getByRole('button', { name: 'Download' }));
      await waitFor(() => expect(assign).toHaveBeenCalledWith('https://files.example.com/doc?sig=x'));
      expect(open).not.toHaveBeenCalled();
    } finally {
      Object.defineProperty(window, 'location', { configurable: true, value: original });
      open.mockRestore();
    }
  });
});

describe('CorpusDocuments - naming', () => {
  // PL-L9: the nav said "Documents", the same word offers use for candidate
  // preboarding documents ("Documents & preboarding").
  it("is called Document library in both consoles' nav, the same as the page title", async () => {
    const { NAV_SECTIONS } = await import('../components/layout/navSections');
    const libraryItems = NAV_SECTIONS.flatMap((s) => s.items).filter((i) =>
      /\/library$/.test(i.to),
    );
    expect(libraryItems.map((i) => i.to).sort()).toEqual(['/hr/library', '/superadmin/library']);
    libraryItems.forEach((i) => expect(i.label).toBe('Document library'));

    renderPage();
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Document library' }),
    ).toBeInTheDocument();
  });
});
