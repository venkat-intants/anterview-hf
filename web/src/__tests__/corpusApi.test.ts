// corpus.ts — PH5-E2. The multipart shape sent for an upload/replace, and the
// error-copy helpers the Documents screen relies on to show a human sentence
// for every failure_code the backend can return.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  CORPUS_FAILURE_SENTENCES,
  corpusErrorMessage,
  corpusFailureSentence,
  corpusListHasPendingVersion,
  getCorpusSemanticStatus,
  replaceCorpusDocument,
  uploadCorpusDocument,
  type CorpusDocument,
} from '../api/corpus';
import { ApiError } from '../api/client';
import { setToken } from '../api/tokenStore';

// No Toaster is mounted in these tests and a 401 path would toast.
vi.mock('../lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

// ---------------------------------------------------------------------------
// XHR mock — same shape as uploadApis.test.ts, this module's own precedent
// for testing a multipart call without a real network.
// ---------------------------------------------------------------------------

interface FakeXhr {
  open: ReturnType<typeof vi.fn>;
  setRequestHeader: ReturnType<typeof vi.fn>;
  send: ReturnType<typeof vi.fn>;
  withCredentials: boolean;
  upload: { onprogress: ((e: ProgressEvent) => void) | null };
  onload: (() => void) | null;
  onerror: (() => void) | null;
  status: number;
  responseText: string;
}

function createXhrMock(status: number, responseText: string): FakeXhr {
  const mock: FakeXhr = {
    open: vi.fn(),
    setRequestHeader: vi.fn(),
    send: vi.fn(),
    withCredentials: false,
    upload: { onprogress: null },
    onload: null,
    onerror: null,
    status,
    responseText,
  };
  mock.send.mockImplementation(() => {
    void Promise.resolve().then(() => mock.onload?.());
  });
  return mock;
}

describe('uploadCorpusDocument — multipart shape', () => {
  const OrigXHR = global.XMLHttpRequest;

  beforeEach(() => {
    vi.clearAllMocks();
    setToken('token-abc');
  });

  afterEach(() => {
    global.XMLHttpRequest = OrigXHR;
  });

  it('posts every field the server expects, and omits expires_on when unset', async () => {
    const mock = createXhrMock(
      201,
      JSON.stringify({ id: 'doc-1', title: 'Leave policy', audience: 'hr_only' }),
    );
    global.XMLHttpRequest = vi.fn(() => mock) as unknown as typeof XMLHttpRequest;

    const file = new File(['%PDF-1.4'], 'leave.pdf', { type: 'application/pdf' });
    await uploadCorpusDocument({
      file,
      title: 'Leave policy',
      audience: 'hr_only',
      attested: true,
    });

    expect(mock.open).toHaveBeenCalledWith('POST', expect.stringContaining('/hr/library'));
    // Never the old, collided path.
    expect(mock.open).not.toHaveBeenCalledWith('POST', expect.stringContaining('/hr/documents'));
    const sent = mock.send.mock.calls[0]?.[0] as FormData;
    expect(sent.get('file')).toBe(file);
    expect(sent.get('title')).toBe('Leave policy');
    expect(sent.get('audience')).toBe('hr_only');
    expect(sent.get('attested')).toBe('true');
    expect(sent.get('expires_on')).toBeNull();

    // Must NOT manually set Content-Type — the browser sets the multipart
    // boundary itself (client.ts's own rule for every multipart call).
    const contentTypeCalls = (mock.setRequestHeader.mock.calls as [string, string][]).filter(
      ([header]) => header.toLowerCase() === 'content-type',
    );
    expect(contentTypeCalls).toHaveLength(0);
  });

  it('includes expires_on and audience=all_staff when set', async () => {
    const mock = createXhrMock(201, JSON.stringify({ id: 'doc-2' }));
    global.XMLHttpRequest = vi.fn(() => mock) as unknown as typeof XMLHttpRequest;

    const file = new File(['%PDF-1.4'], 'handbook.pdf', { type: 'application/pdf' });
    await uploadCorpusDocument({
      file,
      title: 'Handbook',
      audience: 'all_staff',
      expiresOn: '2027-01-01',
      attested: true,
    });

    const sent = mock.send.mock.calls[0]?.[0] as FormData;
    expect(sent.get('audience')).toBe('all_staff');
    expect(sent.get('expires_on')).toBe('2027-01-01');
  });

  it('sends attested=false when the caller has not ticked it', async () => {
    const mock = createXhrMock(201, JSON.stringify({ id: 'doc-3' }));
    global.XMLHttpRequest = vi.fn(() => mock) as unknown as typeof XMLHttpRequest;

    await uploadCorpusDocument({
      file: new File(['x'], 'x.txt', { type: 'text/plain' }),
      title: 'Draft',
      audience: 'hr_only',
      attested: false,
    });

    const sent = mock.send.mock.calls[0]?.[0] as FormData;
    expect(sent.get('attested')).toBe('false');
  });

  it('rejects with the server\'s own rate-limit sentence on a 429 (per-company throttle)', async () => {
    const mock = createXhrMock(
      429,
      JSON.stringify({ detail: 'Too many requests. Please wait a minute and try again.' }),
    );
    global.XMLHttpRequest = vi.fn(() => mock) as unknown as typeof XMLHttpRequest;

    const promise = uploadCorpusDocument({
      file: new File(['%PDF-1.4'], 'h.pdf', { type: 'application/pdf' }),
      title: 'Handbook',
      audience: 'hr_only',
      attested: true,
    });

    await expect(promise).rejects.toThrow('Too many requests. Please wait a minute and try again.');
    await promise.catch((err: unknown) => {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(429);
      // corpusErrorMessage prefers this over any fallback — see its own tests.
      expect(corpusErrorMessage(err, 'fallback')).toBe(
        'Too many requests. Please wait a minute and try again.',
      );
    });
  });
});

describe('replaceCorpusDocument — multipart shape', () => {
  const OrigXHR = global.XMLHttpRequest;

  beforeEach(() => {
    vi.clearAllMocks();
    setToken('token-abc');
  });

  afterEach(() => {
    global.XMLHttpRequest = OrigXHR;
  });

  const DOC_ID = '11111111-1111-4111-8111-111111111111';

  it('posts only the file, to the versions sub-route', async () => {
    const mock = createXhrMock(201, JSON.stringify({ id: DOC_ID, version: 2 }));
    global.XMLHttpRequest = vi.fn(() => mock) as unknown as typeof XMLHttpRequest;

    const file = new File(['%PDF-1.4'], 'v2.pdf', { type: 'application/pdf' });
    await replaceCorpusDocument(DOC_ID, file);

    expect(mock.open).toHaveBeenCalledWith(
      'POST',
      expect.stringContaining(`/hr/library/${DOC_ID}/versions`),
    );
    const sent = mock.send.mock.calls[0]?.[0] as FormData;
    expect(sent.get('file')).toBe(file);
    expect(sent.get('title')).toBeNull();
  });
});

describe('corpusFailureSentence', () => {
  it('has a sentence for every failure_code the backend can return', () => {
    // Mirrors services/data_gateway/app/corpus.py::FAILURE_CODES exactly —
    // a code missing here is a silent blank on the Documents screen.
    const BACKEND_FAILURE_CODES = [
      'unsupported_type', 'too_large', 'active_content', 'encrypted', 'no_text',
      'parse_timeout', 'parse_error', 'too_long', 'too_many_chunks',
      'quota_exceeded', 'embedding_unavailable',
    ];
    for (const code of BACKEND_FAILURE_CODES) {
      expect(CORPUS_FAILURE_SENTENCES[code]).toBeTruthy();
      expect(corpusFailureSentence(code)).toBe(CORPUS_FAILURE_SENTENCES[code]);
    }
  });

  it('falls back to a generic sentence for an unrecognised or missing code', () => {
    expect(corpusFailureSentence('some_new_code')).toBe('This document could not be processed.');
    expect(corpusFailureSentence(null)).toBe('This document could not be processed.');
    expect(corpusFailureSentence(undefined)).toBe('This document could not be processed.');
  });
});

describe('corpusErrorMessage', () => {
  it('prefers our own sentence for a failure_code it recognises', () => {
    const err = new ApiError('some server text', 422, {
      failure_code: 'too_large',
      message: 'some server text',
    });
    expect(corpusErrorMessage(err, 'fallback')).toBe(CORPUS_FAILURE_SENTENCES.too_large);
  });

  it("surfaces the server's own message for a code outside FAILURE_CODES", () => {
    const err = new ApiError('HR-only documents are created by HR managers.', 422, {
      failure_code: 'hr_only_requires_hr_manager',
      message: 'HR-only documents are created by HR managers.',
    });
    expect(corpusErrorMessage(err, 'fallback')).toBe(
      'HR-only documents are created by HR managers.',
    );
  });

  it('falls back to a plain Error message when the error is not an ApiError', () => {
    expect(corpusErrorMessage(new Error('network blip'), 'fallback')).toBe('network blip');
  });

  it('falls back to the given fallback for a value with no message', () => {
    expect(corpusErrorMessage('nope', 'fallback text')).toBe('fallback text');
  });
});

describe('getCorpusSemanticStatus', () => {
  const OrigFetch = global.fetch;

  beforeEach(() => {
    vi.clearAllMocks();
    setToken('token-abc');
  });

  afterEach(() => {
    global.fetch = OrigFetch;
  });

  it('reads corpus_semantic from GET /agent/status', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => null },
      json: () => Promise.resolve({
        enabled: true,
        model_configured: true,
        console: 'hr_manager',
        surfaces: [],
        capabilities: ['read', 'draft'],
        note: 'x',
        corpus_semantic: false,
      }),
    } as unknown as Response);

    const result = await getCorpusSemanticStatus();
    expect(result.corpus_semantic).toBe(false);

    const calledUrl = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0]?.[0] as string;
    expect(calledUrl).toContain('/agent/status');
  });
});

describe('corpusListHasPendingVersion', () => {
  function mkDoc(status: CorpusDocument['status']): CorpusDocument {
    return {
      id: '11111111-1111-4111-8111-111111111111',
      title: 'Handbook',
      audience: 'hr_only',
      doc_kind: 'handbook',
      expires_on: null,
      created_at: '2026-09-01T00:00:00.000Z',
      updated_at: '2026-09-01T00:00:00.000Z',
      version: 1,
      status,
      failure_code: null,
      original_name: 'h.pdf',
      content_type: 'application/pdf',
      size_bytes: 100,
      page_count: 1,
      chunk_count: 1,
      injection_markers: 0,
      uploaded_at: '2026-09-01T00:00:00.000Z',
      uploaded_by_name: 'Bhavya Nair',
    };
  }

  it('is false for undefined data (nothing loaded yet)', () => {
    expect(corpusListHasPendingVersion(undefined)).toBe(false);
  });

  it('is false for an empty list', () => {
    expect(corpusListHasPendingVersion([])).toBe(false);
  });

  it('is true while any row is parsed or indexing', () => {
    expect(corpusListHasPendingVersion([mkDoc('indexed'), mkDoc('parsed')])).toBe(true);
    expect(corpusListHasPendingVersion([mkDoc('indexing')])).toBe(true);
  });

  it('is false once every row has settled (indexed or failed)', () => {
    expect(corpusListHasPendingVersion([mkDoc('indexed'), mkDoc('failed')])).toBe(false);
  });
});
