// Tests for resume.ts and jd.ts upload API helpers.
// Mocks XMLHttpRequest to simulate progress, success, and error responses.
// The token is now injected from the token store (not passed explicitly),
// so we stub getToken() to return a known value for assertion.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { uploadResume } from '../api/resume';
import { uploadJd } from '../api/jd';

// ---------------------------------------------------------------------------
// Prime tokenStore with a known token so uploadWithProgress injects it.
// ---------------------------------------------------------------------------
import { setToken } from '../api/tokenStore';

// ---------------------------------------------------------------------------
// XHR mock factory
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

interface XhrMockOptions {
  status: number;
  responseText: string;
  triggerError?: boolean;
}

function createXhrMock(opts: XhrMockOptions): FakeXhr {
  const mock: FakeXhr = {
    open: vi.fn(),
    setRequestHeader: vi.fn(),
    send: vi.fn(),
    withCredentials: false,
    upload: { onprogress: null },
    onload: null,
    onerror: null,
    status: opts.status,
    responseText: opts.responseText,
  };

  mock.send.mockImplementation(() => {
    void Promise.resolve().then(() => {
      if (opts.triggerError && mock.onerror) {
        mock.onerror();
      } else if (mock.onload) {
        mock.onload();
      }
    });
  });

  return mock;
}

// ---------------------------------------------------------------------------
// uploadResume
// ---------------------------------------------------------------------------

describe('uploadResume', () => {
  const OrigXHR = global.XMLHttpRequest;

  beforeEach(() => {
    vi.clearAllMocks();
    // Prime the token store so uploadWithProgress sets the Authorization header.
    setToken('token-abc');
  });

  afterEach(() => {
    global.XMLHttpRequest = OrigXHR;
  });

  it('resolves with the parsed response on 200', async () => {
    const mock = createXhrMock({
      status: 200,
      responseText: JSON.stringify({
        message: 'ok',
        resume_s3_key: 'resumes/abc.pdf',
        text_length: 1500,
      }),
    });
    global.XMLHttpRequest = vi.fn(() => mock) as unknown as typeof XMLHttpRequest;

    const file = new File(['%PDF-1.4'], 'cv.pdf', { type: 'application/pdf' });
    const result = await uploadResume(file);

    expect(result.text_length).toBe(1500);
    expect(result.resume_s3_key).toBe('resumes/abc.pdf');
    // Token injected from store
    expect(mock.setRequestHeader).toHaveBeenCalledWith('Authorization', 'Bearer token-abc');
    // Must NOT manually set Content-Type
    const contentTypeCalls = (mock.setRequestHeader.mock.calls as [string, string][]).filter(
      ([header]) => header.toLowerCase() === 'content-type',
    );
    expect(contentTypeCalls).toHaveLength(0);
  });

  it('throws an Error with the server detail message on 400', async () => {
    const mock = createXhrMock({
      status: 400,
      responseText: JSON.stringify({ detail: 'Only PDF files are accepted.' }),
    });
    global.XMLHttpRequest = vi.fn(() => mock) as unknown as typeof XMLHttpRequest;

    const file = new File(['data'], 'doc.docx', { type: 'application/octet-stream' });
    await expect(uploadResume(file)).rejects.toThrow('Only PDF files are accepted.');
  });

  it('throws an Error with the server detail on 401', async () => {
    const mock = createXhrMock({
      status: 401,
      responseText: JSON.stringify({ detail: 'Not authenticated' }),
    });
    global.XMLHttpRequest = vi.fn(() => mock) as unknown as typeof XMLHttpRequest;

    const file = new File(['%PDF'], 'cv.pdf', { type: 'application/pdf' });
    await expect(uploadResume(file)).rejects.toThrow('Not authenticated');
  });

  it('throws a network error when XHR fires onerror', async () => {
    const mock = createXhrMock({
      status: 0,
      responseText: '',
      triggerError: true,
    });
    global.XMLHttpRequest = vi.fn(() => mock) as unknown as typeof XMLHttpRequest;

    const file = new File(['%PDF'], 'cv.pdf', { type: 'application/pdf' });
    await expect(uploadResume(file)).rejects.toThrow(/network error/i);
  });

  it('calls onProgress with the upload percentage', async () => {
    const mock = createXhrMock({
      status: 200,
      responseText: JSON.stringify({
        message: 'ok',
        resume_s3_key: 'r.pdf',
        text_length: 100,
      }),
    });

    const progressCb = vi.fn();

    // Override send to fire a progress event before onload
    mock.send.mockImplementation(() => {
      void Promise.resolve().then(() => {
        if (mock.upload.onprogress) {
          mock.upload.onprogress({
            lengthComputable: true,
            loaded: 500,
            total: 1000,
          } as ProgressEvent);
        }
        if (mock.onload) mock.onload();
      });
    });

    global.XMLHttpRequest = vi.fn(() => mock) as unknown as typeof XMLHttpRequest;

    const file = new File(['%PDF'], 'cv.pdf', { type: 'application/pdf' });
    await uploadResume(file, undefined, progressCb);

    expect(progressCb).toHaveBeenCalledWith(50);
  });
});

// ---------------------------------------------------------------------------
// uploadJd
// ---------------------------------------------------------------------------

describe('uploadJd', () => {
  const OrigXHR = global.XMLHttpRequest;

  beforeEach(() => {
    vi.clearAllMocks();
    // Prime the token store so uploadWithProgress sets the Authorization header.
    setToken('token-abc');
  });

  afterEach(() => {
    global.XMLHttpRequest = OrigXHR;
  });

  it('resolves with the parsed response on 200', async () => {
    const mock = createXhrMock({
      status: 200,
      responseText: JSON.stringify({
        message: 'ok',
        jd_s3_key: 'jds/job123.pdf',
        text_length: 4200,
      }),
    });
    global.XMLHttpRequest = vi.fn(() => mock) as unknown as typeof XMLHttpRequest;

    const file = new File(['%PDF-1.4'], 'jd.pdf', { type: 'application/pdf' });
    const result = await uploadJd('job-uuid-123', file);

    expect(result.text_length).toBe(4200);
    expect(result.jd_s3_key).toBe('jds/job123.pdf');
    // Check correct URL was opened (includes job ID)
    expect(mock.open).toHaveBeenCalledWith('POST', expect.stringContaining('job-uuid-123'));
    // Must NOT manually set Content-Type
    const contentTypeCalls = (mock.setRequestHeader.mock.calls as [string, string][]).filter(
      ([header]) => header.toLowerCase() === 'content-type',
    );
    expect(contentTypeCalls).toHaveLength(0);
  });

  it('throws with the detail from a 404 response', async () => {
    const mock = createXhrMock({
      status: 404,
      responseText: JSON.stringify({ detail: 'Job job-uuid-999 not found.' }),
    });
    global.XMLHttpRequest = vi.fn(() => mock) as unknown as typeof XMLHttpRequest;

    const file = new File(['%PDF-1.4'], 'jd.pdf', { type: 'application/pdf' });
    await expect(uploadJd('job-uuid-999', file)).rejects.toThrow('Job job-uuid-999 not found.');
  });

  it('throws a network error when XHR fires onerror', async () => {
    const mock = createXhrMock({
      status: 0,
      responseText: '',
      triggerError: true,
    });
    global.XMLHttpRequest = vi.fn(() => mock) as unknown as typeof XMLHttpRequest;

    const file = new File(['%PDF'], 'jd.pdf', { type: 'application/pdf' });
    await expect(uploadJd('job-id', file)).rejects.toThrow(/network error/i);
  });
});
