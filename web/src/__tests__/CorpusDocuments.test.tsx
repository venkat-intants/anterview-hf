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

describe('CorpusDocuments — list states', () => {
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

  it('shows "—" for the uploader when that account no longer exists', async () => {
    api.listCorpusDocuments.mockResolvedValue([doc({ uploaded_by_name: null })]);
    renderPage();
    await screen.findByText('Employee handbook');
    const row = screen.getByRole('table');
    const cells = within(row).getAllByRole('cell');
    expect(cells.some((c) => c.textContent === '—')).toBe(true);
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

describe('CorpusDocuments — failed row', () => {
  it('shows the human sentence for the failure_code, not the code itself', async () => {
    api.listCorpusDocuments.mockResolvedValue([
      doc({ status: 'failed', failure_code: 'embedding_unavailable' }),
    ]);
    renderPage();
    expect(
      await screen.findByText(
        'Search indexing is unavailable. The document is stored and will be indexed automatically.',
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

describe('CorpusDocuments — retry indexing', () => {
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
    await user.click(await screen.findByRole('button', { name: /retry indexing/i }));

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
      new ApiError('This document is not stuck — there is nothing to reindex.', 409, {
        failure_code: 'not_failed',
        message: 'This document is not stuck — there is nothing to reindex.',
      }),
    );

    renderPage();
    await user.click(await screen.findByRole('button', { name: /retry indexing/i }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        'This document is not stuck — there is nothing to reindex.',
      ),
    );
  });
});

// ---------------------------------------------------------------------------
// A document citation lands on the row it names
// ---------------------------------------------------------------------------

describe('CorpusDocuments — document citation deep link', () => {
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

describe('CorpusDocuments — injection warning', () => {
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

describe('CorpusDocuments — upload attestation gate', () => {
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

describe('CorpusDocuments — audience default', () => {
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

describe('CorpusDocuments — super-admin restriction end to end', () => {
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

describe('CorpusDocuments — delete', () => {
  it('shows what actually happens before deleting, then deletes on confirm', async () => {
    const user = userEvent.setup();
    api.listCorpusDocuments.mockResolvedValue([doc()]);
    api.deleteCorpusDocument.mockResolvedValue(undefined);
    renderPage();
    await screen.findByText('Employee handbook');

    await user.click(screen.getByRole('button', { name: 'Delete' }));
    expect(
      screen.getByText(
        "This removes it from the assistant's search immediately and deletes its text.",
      ),
    ).toBeInTheDocument();

    const group = screen.getByRole('group', { name: /confirm deleting/i });
    await user.click(within(group).getByRole('button', { name: 'Delete' }));

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
      screen.queryByText("This removes it from the assistant's search immediately and deletes its text."),
    ).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Semantic-unavailable banner
// ---------------------------------------------------------------------------

const SEMANTIC_BANNER_TEXT =
  'Semantic search is unavailable — the assistant is matching keywords only.';

describe('CorpusDocuments — semantic search banner', () => {
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

  it('still reacts to the search response — a mid-session outage surfaces even though status said up', async () => {
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

describe('CorpusDocuments — upload wiring', () => {
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

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('Too many requests. Please wait a minute and try again.'),
    );
    expect(toastError).not.toHaveBeenCalledWith('Could not upload a new version');
  });
});

// ---------------------------------------------------------------------------
// Polling — a document stuck at "Indexing…" must update itself
// ---------------------------------------------------------------------------

describe('CorpusDocuments — polling while indexing', () => {
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
